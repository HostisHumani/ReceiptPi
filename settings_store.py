"""
Central settings layer: reads/writes settings.json instead of each module
keeping its own settings in code or in config.py.

Deliberately lives OUTSIDE the project directory, under config.STATE_DIR
(FHS-compliant, e.g. /var/lib/receiptpi - the same place the watch
scripts keep their state). Reason: app.py runs under systemd with
ProtectSystem=strict and no ReadWritePaths for the source directory - if
settings.json ended up there, the service would need a write exception
for its own directory again.

Contains three areas:
  - print_rules: quiet-hour rules (each with its own weekdays + time
    window, see quiet_hours_rules below), rate limit, duplicate
    suppression - apply to EVERY print job regardless of module, hence
    centralized instead of duplicated per module (see
    print_queue.check_print_rules()).
  - weather: freely definable locations for the weather report.
  - language: UI language ("de"/"en"), see i18n.py.
"""
import json
import os
import threading
import uuid

import config

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "print_rules": {
        # List of independent quiet-hour rules instead of one single
        # window, so e.g. "weekends off" and "weekdays 9-18" can coexist
        # - see print_queue._active_quiet_hours_rule() for how they're
        # evaluated. Each rule: {"id", "label", "enabled", "days"
        # (list of 0=Monday..6=Sunday), "start" ("HH:MM"), "end"
        # ("HH:MM", may be earlier than start for an overnight window)}.
        "quiet_hours_rules": [],
        "max_jobs_per_hour": 20,
        "duplicate_window_seconds": 60,
    },
    "weather": {
        "locations": {
            "Standard": {"lat": 53.30, "lon": 9.96}
        },
        "default_location": "Standard",
    },
    "language": "de",
    "system_report": {
        # Three FIXED roles (not a generic host list) - each backed by
        # different SSH commands in modules/system/routes.py (Proxmox:
        # pct/qm list, piNAS: docker ps, PBS: proxmox-backup-manager).
        "ssh_hosts": {
            "proxmox": {"host": "", "user": "root"},
            "pinas": {"host": "", "user": "root"},
            "pbs": {"host": "", "user": "root"},
        },
        "migrated_from_config": False,
    },
    "github_watch": {
        "repos": [],
        "migrated_from_config": False,
    },
    "logos": {
        "enabled": False,
        "modules": {
            "shopping": {"enabled": False},
            "message": {"enabled": False},
            "wifi": {"enabled": False},
            "weather": {"enabled": False},
            "system": {"enabled": False},
        },
    },
}

# Protects concurrent writes within the Flask process (e.g. two parallel
# Gunicorn threads). A threading.Lock() only works WITHIN this one Python
# process - a separately cron-started watcher script (its own process)
# wouldn't know about it. The watchers don't currently write to
# settings.json, so this isn't a real problem yet; if that changes, a
# file lock (e.g. fcntl.flock) would be needed too, not just an
# in-process lock.
_settings_lock = threading.Lock()


# Keys whose content is NOT merged field-by-field with the defaults,
# because they're open, user-managed collections rather than a fixed
# schema of individual fields (like print_rules). Without this
# exception, a weather location the user deleted ("Standard") would
# reappear on every get_settings() call, because the recursive merge
# would interpret it as a "missing field" and re-insert it from
# DEFAULT_SETTINGS.
_OPAQUE_KEYS = {"locations"}


def _deep_merge_defaults(data, defaults):
    """Recursively fills in missing fields from DEFAULT_SETTINGS, not
    just at the top level - otherwise newly added sub-fields (e.g. an
    extra print_rules field in a later version) would never show up for
    existing installations, because the top-level key ("print_rules")
    already exists and setdefault() no longer kicks in. Keys in
    _OPAQUE_KEYS are deliberately NOT merged recursively (see comment
    there)."""
    for key, value in defaults.items():
        if key not in data:
            data[key] = json.loads(json.dumps(value))  # defensive copy
        elif key in _OPAQUE_KEYS:
            pass  # existing content stays as-is, no field-level merge
        elif isinstance(value, dict) and isinstance(data.get(key), dict):
            _deep_merge_defaults(data[key], value)
    return data


def _migrate_legacy_quiet_hours(data):
    """One-time migration: before this version, print_rules had a single
    quiet_hours_enabled/start/end triple instead of a list of rules
    (quiet_hours_rules). Converts an already-configured legacy window
    into an equivalent single rule covering all 7 weekdays, so upgrading
    doesn't silently drop or change a quiet-hours window that's already
    live on an existing installation.

    Mutates data in place. Returns True if anything changed (so the
    caller knows to persist it), False if there was nothing to migrate
    (fresh install, or already migrated on an earlier call)."""
    pr = data.get("print_rules")
    if not isinstance(pr, dict):
        return False
    if "quiet_hours_enabled" not in pr and "quiet_hours_start" not in pr and "quiet_hours_end" not in pr:
        return False  # nothing legacy left to migrate

    legacy_enabled = pr.pop("quiet_hours_enabled", False)
    legacy_start = pr.pop("quiet_hours_start", "22:00")
    legacy_end = pr.pop("quiet_hours_end", "07:00")
    if legacy_enabled:
        pr.setdefault("quiet_hours_rules", [])
        pr["quiet_hours_rules"].append({
            "id": uuid.uuid4().hex[:12],
            "label": "",
            "enabled": True,
            "days": [0, 1, 2, 3, 4, 5, 6],
            "start": legacy_start,
            "end": legacy_end,
        })
    return True


def _migrate_legacy_config_values(data):
    """One-time seed: if system_report/github_watch are still untouched
    (no prior migration ran), copy over whatever's already configured in
    config.py, so upgrading an existing deployment doesn't blank out a
    working setup that used to be configured only by editing config.py.
    Uses an explicit "migrated_from_config" flag rather than "is it
    still empty" as the trigger - otherwise a user who deliberately
    clears a field via the settings UI would have it silently refilled
    from config.py on the next load. Once the flag is set, config.py's
    copies of these values are inert - settings.json is the sole source
    of truth from then on. Mutates data in place, returns True if
    anything changed."""
    changed = False

    sr = data.setdefault("system_report", {})
    if not sr.get("migrated_from_config"):
        hosts = sr.setdefault("ssh_hosts", {})
        for key, host_attr, user_attr in (
            ("proxmox", "SSH_PROXMOX_HOST", "SSH_PROXMOX_USER"),
            ("pinas", "SSH_PINAS_HOST", "SSH_PINAS_USER"),
            ("pbs", "SSH_PBS_HOST", "SSH_PBS_USER"),
        ):
            legacy_host = getattr(config, host_attr, "")
            legacy_user = getattr(config, user_attr, "") or "root"
            if legacy_host:
                hosts[key] = {"host": legacy_host, "user": legacy_user}
        sr["migrated_from_config"] = True
        changed = True

    gh = data.setdefault("github_watch", {})
    if not gh.get("migrated_from_config"):
        legacy_repos = getattr(config, "GITHUB_REPOS", None)
        if not legacy_repos:
            legacy_owner = getattr(config, "GITHUB_OWNER", "")
            legacy_repo_name = getattr(config, "GITHUB_REPO", "")
            if legacy_owner and legacy_repo_name:
                legacy_repos = [{"owner": legacy_owner, "repo": legacy_repo_name}]
        if legacy_repos:
            gh["repos"] = legacy_repos
        gh["migrated_from_config"] = True
        changed = True

    return changed


def _ensure_file():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(SETTINGS_FILE):
        _write(DEFAULT_SETTINGS)


def _write(data):
    """Atomic write (temp file + os.replace) so the file doesn't end up
    corrupted if power is lost mid-write."""
    tmp_path = SETTINGS_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, SETTINGS_FILE)


def get_settings():
    _ensure_file()
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = json.loads(json.dumps(DEFAULT_SETTINGS))  # defensive copy
    # Both migrations must always run (not short-circuited by `or`) - each
    # guards its own section independently via its own flag.
    migrated_quiet_hours = _migrate_legacy_quiet_hours(data)
    migrated_config_values = _migrate_legacy_config_values(data)
    migrated = migrated_quiet_hours or migrated_config_values
    data = _deep_merge_defaults(data, DEFAULT_SETTINGS)
    if migrated:
        _write(data)  # persist once, so the next read doesn't re-migrate
    return data


def save_settings(data):
    with _settings_lock:
        _write(data)


def update_section(section, updates):
    """Updates only one section (e.g. 'print_rules'), the rest stays
    unchanged. Read+modify+write happens under the same lock, so two
    concurrent calls can't overwrite each other. Returns the complete,
    updated settings structure."""
    with _settings_lock:
        data = get_settings()
        data.setdefault(section, {})
        data[section].update(updates)
        _write(data)
        return data


def update_settings_transaction(mutate_fn):
    """For changes more complex than a simple dict.update() (e.g. adding
    or removing a weather location). mutate_fn(data) mutates the
    settings structure in-place; everything runs under the same lock as
    update_section(), so these read-modify-write flows can't overwrite
    each other either. Returns the updated settings structure."""
    with _settings_lock:
        data = get_settings()
        mutate_fn(data)
        _write(data)
        return data
