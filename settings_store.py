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
  - print_rules: quiet hours, rate limit, duplicate suppression - apply
    to EVERY print job regardless of module, hence centralized instead
    of duplicated per module (see print_queue.check_print_rules()).
  - weather: freely definable locations for the weather report.
  - language: UI language ("de"/"en"), see i18n.py.
"""
import json
import os
import threading

import config

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "print_rules": {
        "quiet_hours_enabled": False,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
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
    return _deep_merge_defaults(data, DEFAULT_SETTINGS)


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
