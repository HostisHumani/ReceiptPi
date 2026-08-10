"""
Polls the Fritz!Box for the guest network status. When the guest network
gets enabled, this script sends SSID + password to the ReceiptPi server,
which produces a combined printout from it (readable text + wifi QR
code).

Runs as a cronjob every 1-2 minutes.

Requires: pip install fritzconnection

The actual Fritz!Box query (get_guest_wifi_status) lives centrally in
modules/wifi/routes.py - the manual "print now" button in the web UI
uses the same function instead of maintaining the query twice.
"""

import json
import os
import sys
import urllib.request

# config.py and modules/ live in the project root, watchers/ is one
# level below - add it to sys.path explicitly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config

import settings_store
from modules.wifi.routes import get_guest_wifi_status

PRINTER_URL = "http://localhost:5000/print/wifi"
API_TOKEN = getattr(config, "API_TOKEN", "")
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # fallback: project root
STATE_FILE = os.path.join(STATE_DIR, "wifi_state.json")

# Exact strings from config.example.py - if config.py still has these,
# the Fritz!Box integration was never actually set up (e.g. no Fritz!Box
# in this household at all). Unlike FRITZBOX_ADDRESS, whose default
# (192.168.178.1) is a real, legitimately-used factory address and can't
# double as a "not configured" signal, user/password have no valid
# real-world value that collides with the placeholder text.
_PLACEHOLDER_USER = "Placeholder Fritzbox user"
_PLACEHOLDER_PASSWORD = "Placeholder Fritzbox password"


def _fritzbox_configured():
    user = getattr(config, "FRITZBOX_USER", "")
    password = getattr(config, "FRITZBOX_PASSWORD", "")
    return user not in ("", _PLACEHOLDER_USER) and password not in ("", _PLACEHOLDER_PASSWORD)


def load_last_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f).get("enabled")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_last_state(enabled):
    """Atomic write (temp file + os.replace), so the file doesn't end up
    corrupted if power is lost mid-write."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump({"enabled": enabled}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def print_wifi(ssid, password):
    auth_type = getattr(config, "WIFI_QR_AUTH_TYPE", "WPA")
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-Api-Token"] = API_TOKEN
    payload = json.dumps({
        "ssid": ssid,
        "password": password,
        "auth_type": auth_type,
    }).encode()
    req = urllib.request.Request(PRINTER_URL, data=payload, headers=headers)
    urllib.request.urlopen(req, timeout=15)


def main():
    if not settings_store.get_settings().get("enabled_modules", {}).get("wifi", True):
        print("wifi module disabled, skipping")
        return
    if not _fritzbox_configured():
        # Was previously attempted unconditionally on every run (every
        # 1-2 minutes per this script's cron interval) - anyone without
        # a Fritz!Box, or who just hasn't filled in config.py yet, got a
        # failed connection attempt every single cycle, forever. Bail
        # out cheaply and clearly instead.
        print("Fritz!Box credentials not configured (still placeholder), skipping")
        return

    status = get_guest_wifi_status()
    last_enabled = load_last_state()

    if last_enabled is None:
        # First run: only save the baseline, don't print anything.
        save_last_state(status["enabled"])
        print(f"Baseline set: guest network {'on' if status['enabled'] else 'off'}")
        return

    if status["enabled"] and not last_enabled:
        print_wifi(status["ssid"], status["password"])
        print("Guest network enabled -> wifi slip printed")

    save_last_state(status["enabled"])


if __name__ == "__main__":
    main()
