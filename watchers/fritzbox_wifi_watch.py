"""Poll the Fritz!Box guest Wi-Fi state and request a receipt when enabled.

Run this script periodically through cron or a systemd timer. The shared
get_guest_wifi_status() implementation lives in the Wi-Fi module."""

import json
import os
import sys
import urllib.request

# Add the project root so cron execution can import config and modules.
#
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config
from modules.wifi.routes import get_guest_wifi_status

PRINTER_URL = "http://localhost:5000/print/wifi"
API_TOKEN = getattr(config, "API_TOKEN", "")
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # Fall back to the project root.
STATE_FILE = os.path.join(STATE_DIR, "wifi_state.json")


def load_last_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f).get("enabled")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_last_state(enabled):
    """Persist watcher state atomically."""
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
    status = get_guest_wifi_status()
    last_enabled = load_last_state()

    if last_enabled is None:
        # First run establishes the baseline without printing.
        save_last_state(status["enabled"])
        print(f"Baseline gesetzt: Gästenetz {'an' if status['enabled'] else 'aus'}")
        return

    if status["enabled"] and not last_enabled:
        print_wifi(status["ssid"], status["password"])
        print("Gästenetz aktiviert -> WLAN-Zettel gedruckt")

    save_last_state(status["enabled"])


if __name__ == "__main__":
    main()
