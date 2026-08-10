"""
Polls the configured storm-warning provider (see settings_store
weather.storm_warning) and prints a message via the ReceiptPi server
whenever a NEW active warning appears - same "poll every few minutes via
cron, print through the normal HTTP API" pattern as
github_star_watch.py/fritzbox_wifi_watch.py.

Runs as a cronjob every 15 minutes. No open port, no
webhook needed - purely an outbound poll.

Reads the provider choice AND the "ignore quiet hours" opt-in live from
settings_store on every run (not cached at import time), so a Settings
change takes effect on the very next poll without a service restart -
same reasoning as github_star_watch.py's REPOS.
"""

import json
import os
import sys
import urllib.request

# config.py and settings_store.py live in the project root, watchers/ is
# one level below - add it to sys.path explicitly, otherwise the import
# fails regardless of where the cronjob starts the script from.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config

import settings_store
from modules.weather.alerts import fetch_alert
from modules.weather.routes import resolve_location

PRINTER_URL = "http://localhost:5000/print/weather/alert"
API_TOKEN = getattr(config, "API_TOKEN", "")
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STATE_FILE = os.path.join(STATE_DIR, "storm_warning_state.json")


def load_state():
    """Returns the warning_id of the last warning that was already
    printed (or None). Only ever holds ONE id, not a history - a
    provider switch or a new warning after the old one clears simply
    overwrites it, no cleanup logic needed."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f).get("last_warning_id")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_state(warning_id):
    """Atomic write (temp file + os.replace), same reasoning as the
    other watchers' state files."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump({"last_warning_id": warning_id}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def print_warning(headline, description):
    """Posts to the dedicated /print/weather/alert endpoint (NOT
    /print/message) - that endpoint decides server-side, from
    settings_store's ignore_quiet_hours toggle, whether to bypass quiet
    hours. This script deliberately does NOT send any such flag itself
    - see modules/weather/routes.py print_weather_alert()'s docstring
    for why that's a server-side-only decision."""
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-Api-Token"] = API_TOKEN
    payload = json.dumps({"headline": headline, "description": description}).encode()
    req = urllib.request.Request(PRINTER_URL, data=payload, headers=headers)
    urllib.request.urlopen(req, timeout=10)


def main():
    settings = settings_store.get_settings()
    if not settings.get("enabled_modules", {}).get("weather", True):
        print("weather module disabled, skipping")
        return

    weather_settings = settings["weather"]
    storm = weather_settings.get("storm_warning", {})

    if not storm.get("enabled"):
        print("storm warning disabled in settings, skipping")
        return

    provider = storm.get("provider", "dwd")
    _, lat, lon = resolve_location()  # default location's coordinates

    try:
        result = fetch_alert(provider, weather_settings, lat, lon)
    except Exception as e:
        print(f"{provider}: fetch error - {e}")
        return

    if not result.implemented:
        print(f"{provider}: not implemented yet, skipping")
        return
    if result.error:
        print(f"{provider}: {result.error}")
        return
    if not result.active:
        print(f"{provider}: no active warning")
        return

    last_id = load_state()
    if result.warning_id == last_id:
        print(f"{provider}: warning already printed ({result.warning_id})")
        return

    try:
        print_warning(result.headline, result.description)
        save_state(result.warning_id)
        print(f"{provider}: new warning printed - {result.headline}")
    except Exception as e:
        print(f"{provider}: print failed - {e}")


if __name__ == "__main__":
    main()
