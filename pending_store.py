"""
Persists print jobs that were blocked by quiet hours/rate limit, or
that failed due to a real printer/hardware error, so they can be
reviewed and manually reprinted or discarded later (the "Ausstehende
Aufträge" page, modules/pending/routes.py) instead of the content
being lost the moment the block/error happens.

Deliberately NOT created for every block reason: duplicate-suppression
blocks are excluded on purpose (see print_queue.py's `block_kind`) -
those reflect an already-successful earlier submission of the same
content moments ago, not lost content, so queuing them would either
double-print or clutter the list with near-identical noise. Only
genuinely deferred content - quiet hours, rate limit, or a real print
failure - ends up here.

SECURITY: entries here can contain data that would be sensitive if
exposed (e.g. a wifi password in a "wifi" job's payload). Unlike
settings_store/lists_store, this file and any image files under
IMAGES_DIR are created with 0600 permissions (owner read/write only).
This module also never logs/prints a payload's full content anywhere -
only the caller-supplied `summary` string is meant to be
human-readable and shown in the UI, and callers are responsible for
keeping secrets out of THAT (same rule print_queue.py already
documents for history_store - e.g. wifi's summary is the SSID, never
the password). `payload` itself must never be rendered in
templates/pending.html or included in any log/print statement -
it exists purely for replay.
"""
import json
import os
import threading
import time
import uuid

import config
from PIL import Image

import settings_store

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
PENDING_FILE = os.path.join(STATE_DIR, "pending_jobs.json")
IMAGES_DIR = os.path.join(STATE_DIR, "pending_images")

_lock = threading.Lock()


def _read():
    try:
        with open(PENDING_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write(entries):
    """Atomic write (temp file + os.replace) with 0600 permissions -
    entries can contain sensitive payload fields, so this file must
    not be group/world-readable. chmod happens on the temp file BEFORE
    the rename, since os.replace() keeps whatever permissions the
    source file already had rather than the destination's."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = PENDING_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, PENDING_FILE)


def _image_path(entry_id):
    return os.path.join(IMAGES_DIR, f"{entry_id}.png")


def save_pending_image(entry_id, img):
    """Saves a PIL image for a pending "images" job as a small PNG
    (0600 permissions, same reasoning as _write()). The image is
    expected to already be the downscaled/dithered print-ready version
    (see modules/images/routes.py's process_and_enqueue_image(), which
    resizes to max 512px width and converts to 1-bit BEFORE this ever
    gets called) - a few KB, never the original upload."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    path = _image_path(entry_id)
    tmp_path = path + ".tmp"
    img.save(tmp_path, format="PNG")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def load_pending_image(entry_id):
    """Returns a PIL Image for a pending "images" entry, or None if
    the file is missing/unreadable. A missing/orphaned image file
    (e.g. an unclean shutdown between writing the JSON entry and the
    image file, or vice versa) must never crash the pending list or a
    replay attempt - just makes that one entry non-printable, handled
    by the caller (see modules/pending/routes.py's _replay_images)."""
    try:
        with Image.open(_image_path(entry_id)) as img:
            return img.copy()
    except (OSError, ValueError):
        return None


def _delete_pending_image(entry_id):
    """Best-effort delete - a missing file (already gone, or this
    entry was never an "images" job) is not an error, just a no-op."""
    try:
        os.remove(_image_path(entry_id))
    except OSError:
        pass


def _retention_cutoff():
    """Returns the created_at cutoff below which entries get pruned,
    or None if pending_retention_days is 0 (never auto-delete) - see
    the "Ausstehende Aufträge automatisch löschen" setting on
    /settings/print-rules."""
    days = settings_store.get_settings()["print_rules"].get("pending_retention_days", 0)
    if not days:
        return None
    return time.time() - (days * 86400)


def _prune_locked(entries):
    """Removes expired entries (and their image files, if any) in
    place. Runs under _lock, called from get_all() - see module
    docstring for why this happens on read rather than via a separate
    cron job: this store stays small (a handful of entries at most,
    manually managed), a dedicated watcher would be unjustified extra
    moving parts for that. Returns the surviving list."""
    cutoff = _retention_cutoff()
    if cutoff is None:
        return entries
    kept, expired = [], []
    for entry in entries:
        (expired if entry.get("created_at", 0) < cutoff else kept).append(entry)
    for entry in expired:
        if entry.get("job_type") == "images":
            _delete_pending_image(entry["id"])
    return kept


def get_all():
    """Returns all pending entries, newest first, after pruning any
    that are older than the configured retention setting."""
    with _lock:
        entries = _read()
        pruned = _prune_locked(entries)
        if len(pruned) != len(entries):
            _write(pruned)
        return list(reversed(pruned))


def add(job_type, summary, source, status, reason, payload):
    """Adds a new pending entry, returns its id.

    `payload` is normally a plain JSON-serializable dict. EXCEPTION:
    for job_type "images", pass {"image": <PIL Image>} instead - the
    image is written to disk here (via save_pending_image(), only at
    the moment it's actually needed, i.e. only when a job truly ends
    up pending, not on every successful print) and replaced with a
    small {"image_ref": "<id>.png"} marker before the JSON entry is
    written. This keeps the generic call sites in print_queue.py free
    of any image-specific special-casing - they just pass `payload`
    through opaquely."""
    entry_id = uuid.uuid4().hex[:12]
    if job_type == "images" and "image" in payload:
        save_pending_image(entry_id, payload["image"])
        payload = {"image_ref": f"{entry_id}.png"}
    entry = {
        "id": entry_id,
        "job_type": job_type,
        "summary": summary,
        "source": source,
        "status": status,
        "reason": reason,
        "created_at": time.time(),
        "payload": payload,
    }
    with _lock:
        entries = _read()
        entries.append(entry)
        _write(entries)
    return entry_id


def get_by_ids(ids):
    """Returns entries matching the given ids, in the same order as
    `ids` (not storage order) and with duplicates removed - callers
    iterate this to replay/discard in the order the user selected."""
    with _lock:
        by_id = {e["id"]: e for e in _read()}
    return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


def remove(ids):
    """Removes the given entries (by id) and any associated image
    files. Callers must only pass ids that are CONFIRMED handled -
    either a successful reprint (checked by the caller BEFORE calling
    this) or an explicit discard - never speculatively before knowing
    the outcome, so a job is never lost just because a reprint attempt
    was merely started."""
    ids = set(ids)
    with _lock:
        entries = _read()
        kept, removed = [], []
        for entry in entries:
            (removed if entry["id"] in ids else kept).append(entry)
        _write(kept)
    for entry in removed:
        if entry.get("job_type") == "images":
            _delete_pending_image(entry["id"])
