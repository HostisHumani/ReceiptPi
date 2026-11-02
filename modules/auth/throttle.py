"""
Brute-force protection for the login and setup forms: failed attempts
are counted per key (an account name, a client IP, or all setup
attempts together), and from the THRESHOLD-th failure on, every key
involved is locked for a doubling wait (30 s, 1, 2, 4, 8 min, then
15 min at most).

The counters live in this process's memory only, like the print
queue's rate limit: they are lost on a service restart, and with more
than one Gunicorn worker each worker would count on its own (the
service runs exactly one, which the process-local print queue requires
anyway). Persisting them would mean a disk write per failed attempt,
which an attacker could then use to wear down the SD card.

While a key is locked, attempts are refused before any password or
code check, so a locked-out attacker doesn't cost a scrypt run per
request either. Refusals don't count as failures - otherwise the
rightful user's own retries would keep extending the wait.
"""
import threading
import time

THRESHOLD = 5
BASE_WAIT_S = 30
MAX_WAIT_S = 15 * 60
# A key's failures are forgotten once it has been quiet for this long
# after its lock ran out.
FORGET_AFTER_S = 3600
# Bounds the memory a flood of made-up account names can take up; the
# entries closest to being forgotten go first.
MAX_ENTRIES = 2000


def _now():
    return time.monotonic()


class Throttle:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}  # key -> [failures, time of last failure]

    @staticmethod
    def _wait_for(failures):
        if failures < THRESHOLD:
            return 0
        return min(MAX_WAIT_S, BASE_WAIT_S * 2 ** (failures - THRESHOLD))

    def _forget_stale_locked(self, now):
        for key in [k for k, (n, last) in self._entries.items()
                    if now - last > self._wait_for(n) + FORGET_AFTER_S]:
            del self._entries[key]

    def retry_after(self, keys):
        """Seconds until all of keys are unlocked (0 = not locked)."""
        now = _now()
        wait = 0
        with self._lock:
            for key in keys:
                entry = self._entries.get(key)
                if entry:
                    wait = max(wait, entry[1] + self._wait_for(entry[0]) - now)
        return max(0, int(wait + 0.999))

    def failure(self, keys):
        now = _now()
        with self._lock:
            if len(self._entries) >= MAX_ENTRIES:
                self._forget_stale_locked(now)
            while len(self._entries) >= MAX_ENTRIES:
                oldest = min(self._entries, key=lambda k: self._entries[k][1])
                del self._entries[oldest]
            for key in keys:
                entry = self._entries.setdefault(key, [0, now])
                entry[0] += 1
                entry[1] = now

    def success(self, keys):
        with self._lock:
            for key in keys:
                self._entries.pop(key, None)

    def reset(self):
        with self._lock:
            self._entries.clear()


throttle = Throttle()
