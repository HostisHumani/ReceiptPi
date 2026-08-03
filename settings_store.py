"""Persistent runtime settings stored outside the source tree.

The JSON file lives below config.STATE_DIR, typically /var/lib/receiptpi, so the
application can run with a read-only source directory. Settings currently cover
central print rules and user-managed weather locations."""
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
}

# Protect read-modify-write operations within the Flask process.
# This lock does not coordinate separate processes; use a file lock if
# external writers are introduced in the future.
#
#
#
#
_settings_lock = threading.Lock()


# Opaque keys contain user-managed collections and are copied as-is instead
# of being merged field by field with defaults.
#
#
#
#
_OPAQUE_KEYS = {"locations"}


def _deep_merge_defaults(data, defaults):
    """Recursively add missing schema fields from the defaults.

    Opaque user-managed collections are copied without field-level merging."""
    for key, value in defaults.items():
        if key not in data:
            data[key] = json.loads(json.dumps(value))  # Defensive copy.
        elif key in _OPAQUE_KEYS:
            pass  # Preserve opaque content without field-level merging.
        elif isinstance(value, dict) and isinstance(data.get(key), dict):
            _deep_merge_defaults(data[key], value)
    return data


def _ensure_file():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(SETTINGS_FILE):
        _write(DEFAULT_SETTINGS)


def _write(data):
    """Write settings atomically using a temporary file and os.replace()."""
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
        data = json.loads(json.dumps(DEFAULT_SETTINGS))  # Defensive copy.
    return _deep_merge_defaults(data, DEFAULT_SETTINGS)


def save_settings(data):
    with _settings_lock:
        _write(data)


def update_section(section, updates):
    """Update one settings section under the process-local lock."""
    with _settings_lock:
        data = get_settings()
        data.setdefault(section, {})
        data[section].update(updates)
        _write(data)
        return data


def update_settings_transaction(mutate_fn):
    """Apply a complex in-place settings mutation under one lock."""
    with _settings_lock:
        data = get_settings()
        mutate_fn(data)
        _write(data)
        return data
