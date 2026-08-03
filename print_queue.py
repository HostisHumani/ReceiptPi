"""Central print queue and print-rule enforcement.

All jobs are processed by one worker thread to serialize access to the printer.
Quiet hours, rate limiting, and duplicate suppression are applied centrally so
feature modules do not need to implement them independently."""
import hashlib
import queue
import threading
import time
from datetime import datetime
from datetime import time as dtime

import settings_store

PRINT_QUEUE = queue.Queue(maxsize=100)
_worker_started = False

# Rate-limit and deduplication state is process-local by design.
# A restart intentionally resets temporary throttling state.
#
_recent_job_times = []
_recent_job_hashes = {}

# Protect shared rule state from concurrent Gunicorn threads.
#
#
#
#
_rules_lock = threading.Lock()


def _job_fingerprint(func, args, dedupe_key=None):
    """Build a fingerprint for duplicate detection.

    Text jobs use the callable and arguments. Binary or object-based jobs should
    supply an explicit dedupe_key, such as a hash of the original image bytes."""
    key_part = dedupe_key if dedupe_key is not None else args
    raw = f"{func.__module__}.{func.__name__}:{key_part}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _in_quiet_hours(rules):
    if not rules.get("quiet_hours_enabled"):
        return False
    now = datetime.now().time()
    try:
        start = dtime.fromisoformat(rules["quiet_hours_start"])
        end = dtime.fromisoformat(rules["quiet_hours_end"])
    except (ValueError, KeyError):
        return False  # Invalid time settings should not block printing.
    if start <= end:
        return start <= now <= end
    else:
        # Quiet period crosses midnight, for example 22:00-07:00.
        return now >= start or now <= end


def check_print_rules(func, args, dedupe_key=None):
    """Apply quiet hours, rate limits, and duplicate suppression."""
    with _rules_lock:
        return _check_print_rules_locked(func, args, dedupe_key)


def _check_print_rules_locked(func, args, dedupe_key=None):
    rules = settings_store.get_settings()["print_rules"]
    now = time.time()

    if _in_quiet_hours(rules):
        return False, f"Ruhezeit aktiv ({rules['quiet_hours_start']}-{rules['quiet_hours_end']})"

    global _recent_job_times
    _recent_job_times = [t for t in _recent_job_times if now - t < 3600]
    max_per_hour = rules.get("max_jobs_per_hour", 20)
    if len(_recent_job_times) >= max_per_hour:
        return False, f"Rate-Limit erreicht (max. {max_per_hour} Druckaufträge/Stunde)"

    fingerprint = _job_fingerprint(func, args, dedupe_key)
    window = rules.get("duplicate_window_seconds", 60)
    last_seen = _recent_job_hashes.get(fingerprint)
    if last_seen and now - last_seen < window:
        return False, f"Duplikat unterdrückt (identischer Auftrag vor weniger als {window}s)"

    # Remove expired fingerprints so the dictionary cannot grow indefinitely.
    #
    # Keep recent entries for at least one hour to avoid premature cleanup.
    #
    #
    cutoff = now - max(window, 3600)
    for key in [k for k, t in _recent_job_hashes.items() if t < cutoff]:
        del _recent_job_hashes[key]

    _recent_job_times.append(now)
    _recent_job_hashes[fingerprint] = now
    return True, None


def _print_worker():
    while True:
        func, args, result, event = PRINT_QUEUE.get()
        try:
            func(*args)
            result["ok"] = True
            result["detail"] = "gedruckt"
        except Exception as e:
            result["ok"] = False
            result["detail"] = str(e)
        event.set()
        PRINT_QUEUE.task_done()


def start_worker():
    """Start the queue worker once; repeated calls are safe."""
    global _worker_started
    if not _worker_started:
        threading.Thread(target=_print_worker, daemon=True).start()
        _worker_started = True


def enqueue_print(func, *args, timeout=30, bypass_rules=False, dedupe_key=None):
    """Enqueue a job and wait for completion.

    Returns (success, detail, HTTP status). Set bypass_rules only for internal jobs
    such as health checks or the boot greeting. An explicit dedupe_key may be used
    when the arguments do not provide a stable representation.

    A 504 response only ends the HTTP wait. The queued job may still print later,
    so clients must not retry timeouts automatically."""
    if not bypass_rules:
        allowed, reason = check_print_rules(func, args, dedupe_key)
        if not allowed:
            return False, reason, 429

    result = {}
    event = threading.Event()
    try:
        PRINT_QUEUE.put((func, args, result, event), timeout=5)
    except queue.Full:
        return False, "Druck-Queue ist voll (max. 100 wartende Aufträge)", 503
    if not event.wait(timeout=timeout):
        return False, "Timeout - Druckauftrag hing zu lange in der Queue", 504
    ok = result.get("ok", False)
    detail = result.get("detail", "unbekannter Fehler")
    return ok, detail, (200 if ok else 500)


def enqueue_print_async(func, *args, bypass_rules=False, dedupe_key=None):
    """Enqueue a fire-and-forget job without blocking the caller."""
    if not bypass_rules:
        allowed, _reason = check_print_rules(func, args, dedupe_key)
        if not allowed:
            return  # Silently drop optional asynchronous jobs when the queue is full.
    try:
        PRINT_QUEUE.put((func, args, {}, threading.Event()), timeout=5)
    except queue.Full:
        pass
