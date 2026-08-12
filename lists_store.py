"""
Persists in-progress list/task-card drafts (title, items, etc.) so they
survive navigating away or closing the tab before printing - see
modules/lists/routes.py's /ui/*/draft endpoints, called by
static/draft-autosave.js a short while after the user stops typing.

Lives in STATE_DIR next to settings.json, same reasoning as there
(ProtectSystem=strict under systemd, see settings_store.py's module
docstring). Deliberately its own small file rather than another
settings_store.py section: drafts are disposable scratch state, not
configuration - mixing them into settings.json would mean every
settings backup/restore drags along today's half-typed shopping list.
"""
import json
import os
import threading
import time

import config

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
DRAFTS_FILE = os.path.join(STATE_DIR, "lists_drafts.json")

KINDS = ("shopping", "todo", "task")

# Reserved key for the per-kind "last successful print cleared this
# draft at time T" marker (see save_draft()'s `loaded_at` and
# clear_draft() below) - not a valid `kind`, so it can never collide
# with a real draft entry.
_CLEARED_AT_KEY = "_cleared_at"

_lock = threading.Lock()


def now():
    """Current server time, as a token to pass into save_draft() as
    `loaded_at` (see there). A thin wrapper only so routes.py has one
    obvious place to get this from, rather than importing `time`
    itself just for this."""
    return time.time()


def _read():
    try:
        with open(DRAFTS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data):
    """Atomic write (temp file + os.replace), same pattern as
    settings_store.py/the watchers' state files."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = DRAFTS_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, DRAFTS_FILE)


def get_draft(kind):
    """Returns the raw saved field values for `kind` (dict of str->str),
    or {} if nothing was ever saved. Values are exactly what the form
    fields contained - never parsed/validated, so a half-typed due date
    or similar must survive a reload without crashing anything."""
    if kind not in KINDS:
        return {}
    with _lock:
        return _read().get(kind, {})


def save_draft(kind, fields, loaded_at=None):
    """Overwrites the stored draft for `kind` with `fields` (dict of
    str->str). Called on every autosave tick, including an
    intentionally emptied form (the "discard" button saves the emptied
    state) - an all-empty draft behaves the same as no draft on the
    next page load, so no separate delete path is needed for that
    case.

    `loaded_at` should be the now() token from when the page (or the
    last re-render after a print attempt) was generated - pass it
    straight through from the form's hidden draft_loaded_at field. If
    clear_draft(kind) has run more recently than that (i.e. a print
    succeeded after this page was rendered), the save is stale and is
    silently dropped instead of resurrecting a draft for a list that
    was just printed - this is what closes the autosave/print race:
    an autosave request already in flight when a print completes must
    never be allowed to write the draft back afterwards. Pass None to
    skip this check entirely (not currently used anywhere - every
    caller in this codebase has a page render to get a token from)."""
    if kind not in KINDS:
        return
    with _lock:
        data = _read()
        if loaded_at is not None:
            cleared_at = data.get(_CLEARED_AT_KEY, {}).get(kind, 0)
            if loaded_at < cleared_at:
                return
        data[kind] = {k: str(v) for k, v in fields.items()}
        _write(data)


def clear_draft(kind):
    """Removes the stored draft for `kind` (if any) and records the
    current time as this kind's clear cutoff - called after a
    successful print (UI or API/automation). The cutoff is what
    save_draft() checks a `loaded_at` token against, so it must be
    recorded unconditionally on every clear, even if no draft existed
    at this moment: an autosave for an older page render could still
    be in flight and arrive after this call returns."""
    if kind not in KINDS:
        return
    with _lock:
        data = _read()
        data.pop(kind, None)
        data.setdefault(_CLEARED_AT_KEY, {})[kind] = time.time()
        _write(data)
