"""
Central print-history persistence: every print job (successful, failed,
or blocked by print rules) gets logged here via print_queue.py, so the
dashboard (modules/history/) can show a history without any individual
printing module having to know about storage.

SQLite instead of settings_store's JSON file: this is an append-heavy,
growing log rather than a small settings blob - rewriting an entire
JSON file on every single print job would get slower as history grows,
where SQLite handles that naturally.

Lives in config.STATE_DIR, same reasoning as settings_store.py: app.py
runs under systemd with ProtectSystem=strict and no ReadWritePaths for
the source directory.
"""
import os
import sqlite3
import threading
from datetime import datetime, timedelta

import config

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(STATE_DIR, "history.db")

# Entries older than this get pruned automatically on every write (see
# _prune_locked) - a home appliance running for years shouldn't grow an
# unbounded SQLite file just from routine boot greetings and daily use.
RETENTION_DAYS = 180

# Protects concurrent writes within THIS process (multiple Gunicorn
# threads), same limitation as settings_store._settings_lock: a
# separately cron-started watcher script is a different process and
# wouldn't know about this lock. Watchers don't write history directly
# though - they only trigger it indirectly via this server's HTTP API
# (print_queue.enqueue_print), so this is sufficient today.
_lock = threading.Lock()


def _connect():
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS print_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            job_type TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_print_history_timestamp ON print_history(timestamp)")


def log_job(job_type, summary, source, status, detail=""):
    """Records one print job.
    job_type: short stable id ("shopping", "message", "images", "wifi",
      "weather", "system", "boot", "other") - what kind of job it was.
    summary: short human-readable detail (title, SSID, location, ...) -
      NEVER put secrets here (e.g. wifi passwords).
    source: where the job came from ("ui", "api", "system").
    status: "ok", "error" or "blocked" (rejected by print rules).
    detail: error/block reason, empty on success.

    Best-effort: swallows its own errors, since a logging failure must
    never be the reason an actual print job fails or blocks the print
    worker.
    """
    try:
        with _lock:
            conn = _connect()
            try:
                _ensure_schema(conn)
                conn.execute(
                    "INSERT INTO print_history (timestamp, job_type, summary, source, status, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        job_type, (summary or "")[:200], source, status, (detail or "")[:500],
                    ),
                )
                _prune_locked(conn)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def _prune_locked(conn):
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM print_history WHERE timestamp < ?", (cutoff,))


def get_recent(limit=25, offset=0):
    """Returns the most recent entries (newest first) as a list of dicts."""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                "SELECT id, timestamp, job_type, summary, source, status, detail "
                "FROM print_history ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_stats():
    """Returns {"total": int, "by_status": {status: count}, "by_type":
    {job_type: count}} for the dashboard summary. All-time counts - no
    date filtering needed since RETENTION_DAYS already bounds how far
    back the table goes."""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            total = conn.execute("SELECT COUNT(*) FROM print_history").fetchone()[0]
            by_status = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) as count FROM print_history GROUP BY status"
                ).fetchall()
            }
            by_type = {
                row["job_type"]: row["count"]
                for row in conn.execute(
                    "SELECT job_type, COUNT(*) as count FROM print_history GROUP BY job_type ORDER BY count DESC"
                ).fetchall()
            }
            return {"total": total, "by_status": by_status, "by_type": by_type}
        finally:
            conn.close()
