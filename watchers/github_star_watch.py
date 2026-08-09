"""
Polls the GitHub API for new stars across one or more repos and prints a
status message via the ReceiptPi server (app.py) whenever any of them
gains a star.

Runs best as a cronjob every few minutes. No open
port, no webhook needed - purely an outbound poll.
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

# ---------------------------------------------------------------------------
# Repo list now lives in settings.json (editable via the web UI, see
# modules/settings/routes.py) instead of config.py - settings_store.py
# migrates an existing config.GITHUB_REPOS (or the older single-repo
# GITHUB_OWNER/GITHUB_REPO) into settings.json once, automatically, the
# first time get_settings() runs (whether that's triggered by this
# script or by the Flask app - whichever runs first after the upgrade).
# From then on settings.json is authoritative; config.py's copies of
# these values are no longer read here.
REPOS = settings_store.get_settings()["github_watch"]["repos"]
PRINTER_URL = "http://localhost:5000/print/message"
API_TOKEN = getattr(config, "API_TOKEN", "")
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # fallback: project root
STATE_FILE = os.path.join(STATE_DIR, "star_state.json")
# ---------------------------------------------------------------------------


def get_star_count(owner, repo):
    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers={"User-Agent": "star-watch-script"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data["stargazers_count"]


def load_state():
    """Returns {"owner/repo": last_known_star_count, ...}. NOTE: the
    state file format changed with multi-repo support (used to be a
    single {"stars": N} for one repo) - an old-format file simply won't
    match any "owner/repo" key here, so every repo re-baselines silently
    on the first run after upgrading. That's a one-time, harmless reset,
    not a bug: it just means no notification fires for stars gained
    before the upgrade, only for genuinely new ones afterward."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}  # corrupted/empty file - treat as "no prior state"
    return {}


def save_state(state):
    """Atomic write (temp file + os.replace), so the file doesn't end up
    corrupted/empty if power is lost mid-write."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def print_notification(owner, repo, new_total, gained):
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-Api-Token"] = API_TOKEN
    payload = json.dumps({
        "title": "NEW GITHUB STAR",
        "text": f"{owner}/{repo}\n+{gained} star(s)\nTotal: {new_total}",
    }).encode()
    req = urllib.request.Request(PRINTER_URL, data=payload, headers=headers)
    urllib.request.urlopen(req, timeout=10)


def check_repo(owner, repo, state):
    key = f"{owner}/{repo}"
    current = get_star_count(owner, repo)
    last = state.get(key)

    if last is None:
        # First run for this repo (or corrupted/old-format state file):
        # only save the baseline, don't print.
        state[key] = current
        print(f"{key}: baseline set at {current} stars")
        return

    if current > last:
        gained = current - last
        print_notification(owner, repo, current, gained)
        state[key] = current
        print(f"{key}: new star! {last} -> {current}")
    elif current > 0 and current < last:
        # Stars got removed - do NOT lower the saved high-water mark,
        # otherwise later re-reaching the old count would be reported as
        # a "new" star again (e.g. 100 -> 99 -> 100).
        print(f"{key}: stars dropped ({last} -> {current}), baseline stays {last}")
    # current == last: nothing to do, no write needed for this repo


def main():
    state = load_state()
    for entry in REPOS:
        owner, repo = entry["owner"], entry["repo"]
        try:
            check_repo(owner, repo, state)
        except Exception as e:
            print(f"{owner}/{repo}: error - {e}")
    save_state(state)


if __name__ == "__main__":
    main()
