"""Poll a GitHub repository for new stars and print an event receipt.

Run this script periodically through cron or a systemd timer. No inbound webhook
or publicly reachable endpoint is required."""

import json
import os
import sys
import urllib.request

# Add the project root so the watcher can import config regardless of cwd.
#
#
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config

# ---------------------------------------------------------------------------
GITHUB_OWNER = config.GITHUB_OWNER
GITHUB_REPO = config.GITHUB_REPO
PRINTER_URL = "http://localhost:5000/print/message"
API_TOKEN = getattr(config, "API_TOKEN", "")
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # Fall back to the project root when STATE_DIR is unavailable.
STATE_FILE = os.path.join(STATE_DIR, "star_state.json")
# ---------------------------------------------------------------------------


def get_star_count():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    req = urllib.request.Request(url, headers={"User-Agent": "star-watch-script"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data["stargazers_count"]


def load_last_count():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f).get("stars")
        except (json.JSONDecodeError, OSError):
            return None  # Treat an empty or damaged state file as an uninitialized baseline.
    return None


def save_last_count(count):
    """Persist the highest observed star count atomically."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump({"stars": count}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def print_notification(new_total, gained):
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-Api-Token"] = API_TOKEN
    payload = json.dumps({
        "title": "NEUER GITHUB STAR",
        "text": f"{GITHUB_OWNER}/{GITHUB_REPO}\n+{gained} Star(s)\nGesamt: {new_total}",
    }).encode()
    req = urllib.request.Request(PRINTER_URL, data=payload, headers=headers)
    urllib.request.urlopen(req, timeout=10)


def main():
    current = get_star_count()
    last = load_last_count()

    if last is None:
        # First run establishes the baseline without printing.
        save_last_count(current)
        print(f"Baseline gesetzt: {current} Stars")
        return

    if current > last:
        gained = current - last
        print_notification(current, gained)
        save_last_count(current)
        print(f"Neuer Star! {last} -> {current}")
    elif current > 0 and current < last:
        # Keep the highest observed count so removed and restored stars do not trigger
        # duplicate notifications.
        #
        print(f"Stars gesunken ({last} -> {current}), Baseline bleibt {last}")
    # No change; avoid an unnecessary write.


if __name__ == "__main__":
    main()
