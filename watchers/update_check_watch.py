"""
Polls the GitHub Releases API for HostisHumani/ReceiptPi and caches
whether a newer version is available, so the web UI footer (see
app.py) never has to call GitHub itself on a page render - it only
ever reads the local cache file written here.

Runs best as a cronjob every 12 hours - purely an outbound poll, same
pattern as github_star_watch.py. On ANY failure (network, timeout,
malformed response, a release tag that doesn't match ReceiptPi's
version scheme) this script logs and exits WITHOUT touching the cache
file - a GitHub outage must never take down or mislead the footer, it
just keeps showing the last known-good state until the next run
succeeds. Same for the common case where the check succeeds but the
result is identical to what's already cached (most runs, since a new
release is rare) - the write is skipped there too, since 2 runs/day
would otherwise mean ~730 SD card writes a year for a footer value
that almost never actually changes.

Deliberately uses GET /repos/{owner}/{repo}/releases (the list), NOT
/releases/latest - GitHub's own docs confirm /releases/latest returns
only "the most recent non-prerelease, non-draft release", which would
silently skip every ReceiptPi alpha release.
"""
import json
import os
import sys
import urllib.error
import urllib.request

# config.py and version.py live in the project root, watchers/ is one
# level below - add it to sys.path explicitly, same as the other
# watchers in this directory.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config

import version

OWNER = "HostisHumani"
REPO = "ReceiptPi"
RELEASES_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases"
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CACHE_FILE = os.path.join(STATE_DIR, "update_check.json")

# The real GitHub API always returns html_url as
# "https://github.com/{owner}/{repo}/releases/{tag}" (verified against
# GitHub's own REST API docs) - checked defensively before ever caching
# a clickable link, so a malformed/unexpected response can never end up
# sending someone to an arbitrary URL from the footer.
_EXPECTED_HTML_URL_PREFIX = f"https://github.com/{OWNER}/{REPO}/releases/"


def is_valid_release_url(url):
    return isinstance(url, str) and url.startswith(_EXPECTED_HTML_URL_PREFIX)


def fetch_latest_release():
    """Returns the newest published (non-draft) release dict - may be
    a prerelease, that's intentional, see module docstring - or None
    if the repo has no published releases at all yet."""
    req = urllib.request.Request(RELEASES_URL, headers={
        "User-Agent": "receiptpi-update-check",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        releases = json.load(resp)
    published = [r for r in releases if not r.get("draft")]
    if not published:
        return None
    # Belt-and-braces: GitHub already returns releases newest-first,
    # but sort explicitly by created_at rather than relying on
    # undocumented ordering.
    published.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return published[0]


def save_cache(data):
    """Atomic write (temp file + os.replace), same as the other
    watchers' state files, so the cache never ends up truncated/empty
    if power is lost mid-write."""
    tmp_path = CACHE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, CACHE_FILE)


def load_cache():
    """Returns the currently cached dict, or {} if there isn't one yet
    or it's unreadable - used only to check whether a fresh result
    actually differs before writing, see main()."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    try:
        release = fetch_latest_release()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"update check failed, cache left untouched: {e}")
        return

    if release is None:
        print("no published releases found yet, cache left untouched")
        return

    tag = release.get("tag_name", "")
    comparison = version.compare_versions(version.CURRENT_VERSION, tag)
    if comparison is None:
        # Tag doesn't match ReceiptPi's known version scheme - do NOT
        # guess whether it counts as newer, just leave the cache as it
        # was (see module docstring).
        print(f"release tag '{tag}' doesn't match the expected version scheme, cache left untouched")
        return

    html_url = release.get("html_url", "")
    if not is_valid_release_url(html_url):
        # Never cache/show a clickable "update available" link unless
        # it demonstrably points at a release of THIS repo - an
        # unexpected/malformed URL must never reach the footer, even if
        # the version comparison itself looked fine.
        print(f"release html_url '{html_url}' doesn't look like a HostisHumani/ReceiptPi release, cache left untouched")
        return

    new_cache = {
        "latest_version": tag,
        "latest_url": html_url,
        "update_available": comparison < 0,
    }
    if new_cache == load_cache():
        # Identical to what's already cached (the common case - most
        # 12h checks find no new release) - skip the write entirely.
        # Runs every 12h forever, so writing unconditionally would mean
        # an SD card write every cycle even across months where nothing
        # ever changes - avoidable SD card wear on a Pi.
        print(f"update check ok, unchanged: current={version.CURRENT_VERSION} latest={tag}, cache left untouched")
        return

    save_cache(new_cache)
    print(f"update check ok: current={version.CURRENT_VERSION} latest={tag} update_available={comparison < 0}")


if __name__ == "__main__":
    main()
