"""
Central print queue: every print job (web UI, cron watchers, webhooks)
lands here and gets processed one at a time by a single worker thread,
so nothing collides on the USB connection.

check_print_rules() centrally checks quiet hours, rate limit and
duplicate suppression from settings_store before every job - so no
individual module has to know about or duplicate this logic.

Note on i18n: the block-reason strings below go through i18n.tr()
(current UI language), same as the web UI. This means /print/* JSON
responses for a BLOCKED job (429) follow the language setting too,
unlike other JSON error messages elsewhere which stay English - a
deliberate, small inconsistency rather than threading translation keys
through every single module's route handlers for this narrow case.
"""
import hashlib
import queue
import threading
import time
from datetime import datetime
from datetime import time as dtime

import history_store
import i18n
import pending_store
import settings_store

PRINT_QUEUE = queue.Queue(maxsize=100)
_worker_started = False

# Rate limit / duplicate detection: lives purely in process memory, so
# it doesn't survive a restart - that's intentional, a restart shouldn't
# block things indefinitely, only rein in "runaway" bursts within one
# running session.
_recent_job_times = []
_recent_job_hashes = {}

# Protects _recent_job_times/_recent_job_hashes from concurrent access
# by multiple Gunicorn threads (--threads 4) - without
# the lock, two parallel requests could both pass the same duplicate
# check before either one records its fingerprint.
_rules_lock = threading.Lock()


def _job_fingerprint(func, args, dedupe_key=None):
    """Builds the fingerprint for duplicate detection. Defaults to
    func+args, which works well for text-based jobs (status message,
    weather, ...). UNSUITABLE for images: a Pillow Image object in args
    turns into something like "<PIL.Image.Image ... at 0x7f...>" via
    str(args) - the memory address changes on every upload even for the
    exact same image, so the duplicate lock would never trigger. For
    such cases an explicit dedupe_key can be passed (e.g. a hash of the
    raw image bytes) that's used instead of args in the fingerprint."""
    key_part = dedupe_key if dedupe_key is not None else args
    raw = f"{func.__module__}.{func.__name__}:{key_part}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _rule_matches_now(rule, now_weekday, now_time, yesterday_weekday):
    """Does this single rule's day+time window cover the current
    moment? Handles the overnight case (start > end, e.g. 18:00-09:00)
    by checking two possibilities: the window started TODAY and hasn't
    ended yet, or it started YESTERDAY (on one of the rule's days) and
    hasn't ended yet - e.g. a Mon-Fri 18:00-09:00 rule still applies on
    Tuesday at 02:00 because of Monday's window, even though Tuesday
    itself doesn't have to be one of the rule's days."""
    if not rule.get("enabled", True):
        return False
    days = rule.get("days") or []
    try:
        start = dtime.fromisoformat(rule["start"])
        end = dtime.fromisoformat(rule["end"])
    except (ValueError, KeyError, TypeError):
        return False  # broken/missing time value - better not to block
    if start <= end:
        return now_weekday in days and start <= now_time <= end
    else:
        started_today = now_weekday in days and now_time >= start
        continues_from_yesterday = yesterday_weekday in days and now_time <= end
        return started_today or continues_from_yesterday


def _active_quiet_hours_rule(rules):
    """Returns the first enabled quiet-hours rule whose day+time window
    covers right now, or None if none applies. "First match wins" -
    with several overlapping rules there's only one reason to report to
    the caller, so there's no need to merge or prioritize them."""
    quiet_rules = rules.get("quiet_hours_rules") or []
    if not quiet_rules:
        return None
    now = datetime.now()
    now_weekday = now.weekday()  # 0=Monday .. 6=Sunday
    yesterday_weekday = (now_weekday - 1) % 7
    now_time = now.time()
    for rule in quiet_rules:
        if _rule_matches_now(rule, now_weekday, now_time, yesterday_weekday):
            return rule
    return None


def check_print_rules(func, args, dedupe_key=None, bypass_quiet_hours=False, bypass_duplicate=False):
    """Checks quiet hours, rate limit and duplicates. Returns
    (allowed: bool, reason: str or None, block_kind: str or None) -
    block_kind is one of "quiet_hours"/"rate_limit"/"duplicate" when
    blocked, else None. Runs entirely under _rules_lock, see the
    comment there. dedupe_key, see _job_fingerprint(). bypass_quiet_hours
    skips ONLY the quiet-hours check - rate limit and duplicate
    suppression still apply. bypass_duplicate skips ONLY duplicate
    suppression. Neither is exposed to any public API input; only set
    internally by callers that read the corresponding opt-in from
    settings_store themselves (bypass_quiet_hours: the storm-warning
    watcher, see modules/weather/routes.py print_storm_warning()) or
    that are replaying an already-reviewed pending job (bypass_duplicate,
    see modules/pending/routes.py)."""
    with _rules_lock:
        return _check_print_rules_locked(func, args, dedupe_key, bypass_quiet_hours, bypass_duplicate)


def _check_print_rules_locked(func, args, dedupe_key=None, bypass_quiet_hours=False, bypass_duplicate=False):
    rules = settings_store.get_settings()["print_rules"]
    now = time.time()

    if not bypass_quiet_hours:
        active_rule = _active_quiet_hours_rule(rules)
        if active_rule:
            label = active_rule.get("label")
            if label:
                reason = i18n.tr(
                    "print_queue.quiet_hours_active_labeled",
                    label=label, start=active_rule["start"], end=active_rule["end"],
                )
            else:
                reason = i18n.tr(
                    "print_queue.quiet_hours_active",
                    start=active_rule["start"], end=active_rule["end"],
                )
            return False, reason, "quiet_hours"

    global _recent_job_times
    _recent_job_times = [t for t in _recent_job_times if now - t < 3600]
    max_per_hour = rules.get("max_jobs_per_hour", 20)
    if len(_recent_job_times) >= max_per_hour:
        return False, i18n.tr("print_queue.rate_limit", max=max_per_hour), "rate_limit"

    fingerprint = _job_fingerprint(func, args, dedupe_key)
    window = rules.get("duplicate_window_seconds", 60)
    if not bypass_duplicate:
        last_seen = _recent_job_hashes.get(fingerprint)
        if last_seen and now - last_seen < window:
            return False, i18n.tr("print_queue.duplicate", window=window), "duplicate"

    # Clean out old hash entries, otherwise the dict grows unbounded over
    # months (unlike _recent_job_times above, which already gets cleaned
    # on every call). Cutoff is deliberately max(window, 3600) instead of
    # just window, so a briefly-set short window doesn't immediately
    # delete entries that were just needed for the duplicate check.
    cutoff = now - max(window, 3600)
    for key in [k for k, t in _recent_job_hashes.items() if t < cutoff]:
        del _recent_job_hashes[key]

    _recent_job_times.append(now)
    _recent_job_hashes[fingerprint] = now
    return True, None, None


def _print_worker():
    while True:
        func, args, result, event, meta = PRINT_QUEUE.get()
        try:
            func(*args)
            result["ok"] = True
            result["detail"] = "printed"
        except Exception as e:
            result["ok"] = False
            result["detail"] = str(e)
        if meta.get("log_history", True):
            history_store.log_job(
                meta.get("job_type", "other"), meta.get("summary", ""), meta.get("source", "system"),
                "ok" if result["ok"] else "error", result.get("detail", ""),
            )
        # A real print failure (printer unreachable, USB error, ...) -
        # as opposed to a rules block, which is handled in
        # enqueue_print() before the job ever reaches this worker.
        # retry_payload is None for jobs that opted out (see
        # enqueue_print's docstring) AND for every replay attempt from
        # modules/pending/routes.py itself, so a job that keeps failing
        # on retry never creates a second/duplicate pending entry.
        if not result["ok"] and meta.get("retry_payload") is not None:
            pending_store.add(
                meta.get("job_type", "other"), meta.get("summary", ""), meta.get("source", "system"),
                "error", result.get("detail", ""), meta["retry_payload"],
            )
        event.set()
        PRINT_QUEUE.task_done()


def start_worker():
    """Starts the worker thread once (calling this multiple times is
    safe, e.g. if the module gets imported repeatedly via several
    blueprints)."""
    global _worker_started
    if not _worker_started:
        threading.Thread(target=_print_worker, daemon=True).start()
        _worker_started = True


def enqueue_print(func, *args, timeout=30, bypass_rules=False, bypass_quiet_hours=False,
                   bypass_duplicate=False, dedupe_key=None, job_type="other", summary="", source="ui",
                   log_history=True, retry_payload=None):
    """Enqueues a print job and waits for it to finish. Returns
    (ok: bool, detail: str, http_status: int).

    bypass_rules=True skips quiet hours/rate limit/duplicate suppression
    entirely - only meant for internal, non-user-triggered actions like
    the health check or the boot greeting, NOT for regular print
    functions.

    bypass_quiet_hours=True skips ONLY the quiet-hours check (rate
    limit/duplicate suppression still apply) - for the opt-in "ignore
    quiet hours" storm-warning setting. Has no effect if bypass_rules is
    already True. Not meant to be settable from any public request
    body/query param; only ever set by a caller that has already read
    the corresponding toggle from settings_store itself.

    bypass_duplicate=True skips ONLY duplicate suppression (quiet
    hours/rate limit still apply unless separately bypassed) - used
    when replaying a job from the pending list (see
    modules/pending/routes.py): the user has already reviewed and
    explicitly chosen to reprint this exact content, so treating it as
    an unwanted duplicate of itself would be wrong. Same
    not-from-request-input rule as bypass_quiet_hours.

    dedupe_key: optional explicit key for duplicate suppression instead
    of relying on str(args) - important e.g. for image printing, where
    args contains a Pillow object (see _job_fingerprint()).

    job_type/summary/source/log_history: fed to history_store for the
    print-history dashboard (see modules/history/routes.py). job_type is
    a short stable id ("shopping", "wifi", ...), summary a short
    human-readable detail (title, SSID, location, ...) - NEVER put
    secrets in summary. source is where the request came from ("ui",
    "api", "system"). Set log_history=False for internal, non-user-
    facing jobs that shouldn't clutter the dashboard (e.g. the /health
    check, which runs every 60s from every open browser tab).

    retry_payload: optional JSON-serializable dict (or, for job_type
    "images" specifically, {"image": <PIL Image>} - see
    pending_store.add()) capturing enough of this job's own arguments
    to reconstruct and reprint it later. Only pass this for job types
    where that's genuinely sensible (see each caller for the reasoning
    - e.g. wifi/shopping/message are fine, a raw image upload isn't
    JSON-safe on its own so goes through pending_store's special
    handling instead). If given, and the job is blocked by quiet hours
    or rate limit (NOT duplicate suppression - see check_print_rules's
    block_kind), or fails with a real print error, it is saved to the
    pending list (modules/pending/routes.py) for later manual review.
    NEVER pass this when replaying an already-pending job - a failed
    retry attempt must not create ANOTHER pending entry for the same
    content, or a persistently-failing job would pile up duplicates
    indefinitely.

    Status codes: 429 quiet hours/rate limit/duplicate, 503 queue full,
    504 timeout, 500 print error, 200 success.

    Note on 504: the job stays in the queue and may still get printed by
    the worker once its turn comes - the timeout only ends the waiting
    on the HTTP side, not the job itself. Clients shouldn't automatically
    retry on a 504, or the same receipt could end up printed twice."""
    if not bypass_rules:
        allowed, reason, block_kind = check_print_rules(func, args, dedupe_key, bypass_quiet_hours, bypass_duplicate)
        if not allowed:
            if log_history:
                history_store.log_job(job_type, summary, source, "blocked", reason)
            # Only quiet-hours/rate-limit blocks are genuinely deferred
            # content - a duplicate block means this exact content was
            # already accepted moments ago under the original
            # submission, so there's nothing here worth recovering.
            if retry_payload is not None and block_kind in ("quiet_hours", "rate_limit"):
                pending_store.add(job_type, summary, source, "blocked", reason, retry_payload)
            return False, reason, 429

    result = {}
    event = threading.Event()
    meta = {
        "job_type": job_type, "summary": summary, "source": source, "log_history": log_history,
        "retry_payload": retry_payload,
    }
    try:
        PRINT_QUEUE.put((func, args, result, event, meta), timeout=5)
    except queue.Full:
        return False, i18n.tr("print_queue.full"), 503
    if not event.wait(timeout=timeout):
        return False, i18n.tr("print_queue.timeout"), 504
    ok = result.get("ok", False)
    detail = result.get("detail", i18n.tr("print_queue.unknown_error"))
    return ok, detail, (200 if ok else 500)
