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

Every row has an owner: the account that triggered the job, stored as
the pair (owner_id, owner_created) - the account's id and its creation
time in auth.db. Both are NULL for jobs without a logged-in account
(API token, webhook, MQTT, watchers, boot greeting, no admin set up),
which belong to "System". The pair instead of the id alone: account ids
start at 1 again when auth.db is deleted, and a new account must not
inherit an old account's rows just because it got the same id.

Reading by owner always goes through a viewer scope (see _scope_sql);
get_recent() and get_stats() stay unfiltered for device-wide status
(MQTT).
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import config

import auth_store

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(STATE_DIR, "history.db")

# Entries older than this get pruned automatically on every write (see
# _prune_locked) - a home appliance running for years shouldn't grow an
# unbounded SQLite file just from routine boot greetings and daily use.
RETENTION_DAYS = 180

SCHEMA_VERSION = 1

# Protects concurrent writes within THIS process (multiple Gunicorn
# threads), same limitation as settings_store._settings_lock: a
# separately cron-started watcher script is a different process and
# wouldn't know about this lock. Watchers don't write history directly
# though - they only trigger it indirectly via this server's HTTP API
# (print_queue.enqueue_print), so this is sufficient today.
_lock = threading.Lock()

# Whether _ensure_schema_once() has already run once in this process - the
# schema virtually never changes after the table exists, so re-running
# "CREATE TABLE/INDEX IF NOT EXISTS" on every single log_job()/
# get_recent()/get_stats() call (i.e. on every print job and every
# dashboard view) is pure overhead. Safe as a plain module-level flag
# for the same reason as settings_store's cache: the project
# deliberately runs with exactly one Gunicorn worker.
_schema_ready = False

# Throttles how often _prune_locked() actually runs its DELETE query.
# RETENTION_DAYS=180 means pruning is only ever relevant once in a very
# long while - running it on every single print job is wasted work.
_last_prune = 0
_PRUNE_INTERVAL_SECONDS = 3600  # once per hour is plenty for a 180-day retention window


def _connect():
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema_once(conn):
    """Runs the schema-creation statements and the migration only the
    first time this is called in the process's lifetime (see
    _schema_ready above)."""
    global _schema_ready
    if _schema_ready:
        return
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
    conn.commit()
    _migrate()
    _schema_ready = True


def _first_admin():
    """(id, created) of the admin account with the lowest id, or None.
    Checks admin_exists() first, which never creates auth.db."""
    if not auth_store.admin_exists():
        return None
    admins = [u for u in auth_store.list_users() if u["role"] == "admin"]
    if not admins:
        return None
    first = min(admins, key=lambda u: u["id"])
    return first["id"], first["created"]


def _migrate():
    """PRAGMA user_version is the schema version. Version 1 adds the
    owner columns and fills them for existing rows: jobs from the web UI
    (source "ui", and their reprints, "pending") were made before
    accounts could be told apart, and go to the admin with the lowest
    id; everything else stays System. Without an admin at that moment
    every row stays System, and the version is set all the same.

    The version is checked again inside the write transaction: the boot
    greeting runs in its own process (gunicorn.conf.py) and can get here
    at the same moment as the server's worker, and exactly one of them
    may migrate. ALTER TABLE only extends the table definition, so code
    that doesn't know the columns keeps reading and writing the table."""
    conn = sqlite3.connect(DB_FILE, timeout=10, isolation_level=None)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                conn.execute("ALTER TABLE print_history ADD COLUMN owner_id INTEGER")
                conn.execute("ALTER TABLE print_history ADD COLUMN owner_created INTEGER")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_print_history_owner ON print_history(owner_id, id)"
                )
                admin = _first_admin()
                if admin is not None:
                    conn.execute(
                        "UPDATE print_history SET owner_id = ?, owner_created = ? "
                        "WHERE source IN ('ui', 'pending')",
                        admin,
                    )
                conn.execute("PRAGMA user_version = 1")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _owner_columns(owner):
    """(owner_id, owner_created) for an owner pair, or (None, None) for
    System."""
    if owner is None:
        return None, None
    owner_id, owner_created = owner
    return int(owner_id), int(owner_created)


def _scope_sql(viewer):
    """WHERE clause and parameters for a viewer scope:
      ("all",)                  every row
      ("system",)               rows without an owner
      ("owner", id, created)    rows of exactly this account
      ("owner_id", id)          rows with this account id, whatever its
                                creation time (rows of deleted accounts)
    Anything else raises ValueError, so a missing or malformed scope
    can never fall back to showing everything."""
    kind = viewer[0] if isinstance(viewer, tuple) and viewer else None
    if kind == "all" and len(viewer) == 1:
        return "1 = 1", ()
    if kind == "system" and len(viewer) == 1:
        return "owner_id IS NULL", ()
    if kind == "owner" and len(viewer) == 3:
        return "owner_id = ? AND owner_created = ?", (int(viewer[1]), int(viewer[2]))
    if kind == "owner_id" and len(viewer) == 2:
        return "owner_id = ?", (int(viewer[1]),)
    raise ValueError("invalid viewer scope")


def log_job(job_type, summary, source, status, detail="", owner=None):
    """Records one print job.
    job_type: short stable id ("shopping", "message", "images", "wifi",
      "weather", "system", "boot", "other") - what kind of job it was.
    summary: short human-readable detail (title, SSID, location, ...) -
      NEVER put secrets here (e.g. wifi passwords).
    source: where the job came from ("ui", "api", "system").
    status: "ok", "error" or "blocked" (rejected by print rules).
    detail: error/block reason, empty on success.
    owner: (account id, account created) of the account that triggered
      the job, or None for System.

    Best-effort: swallows its own errors, since a logging failure must
    never be the reason an actual print job fails or blocks the print
    worker.
    """
    try:
        with _lock:
            conn = _connect()
            try:
                _ensure_schema_once(conn)
                conn.execute(
                    "INSERT INTO print_history "
                    "(timestamp, job_type, summary, source, status, detail, owner_id, owner_created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        job_type, (summary or "")[:200], source, status, (detail or "")[:500],
                        *_owner_columns(owner),
                    ),
                )
                _prune_if_due(conn)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def _prune_if_due(conn):
    """Runs the retention DELETE at most once per _PRUNE_INTERVAL_SECONDS
    - with a 180-day retention window, pruning has nothing to do on
    almost every call, so gating it behind a timer avoids running a
    DELETE query (even an indexed, cheap one) on every single print job
    for no benefit."""
    global _last_prune
    now = time.time()
    if now - _last_prune < _PRUNE_INTERVAL_SECONDS:
        return
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM print_history WHERE timestamp < ?", (cutoff,))
    _last_prune = now


def get_recent(limit=25, offset=0):
    """Returns the most recent entries (newest first) as a list of dicts."""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema_once(conn)
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
            _ensure_schema_once(conn)
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


def get_page(viewer, limit=25, offset=0):
    """The entries visible in viewer's scope (see _scope_sql), newest
    first, each with its owner pair."""
    where, params = _scope_sql(viewer)
    with _lock:
        conn = _connect()
        try:
            _ensure_schema_once(conn)
            rows = conn.execute(
                "SELECT id, timestamp, job_type, summary, source, status, detail, owner_id, owner_created "
                f"FROM print_history WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_stats_for(viewer):
    """Like get_stats(), limited to viewer's scope: {"total": int,
    "by_status": {status: count}}."""
    where, params = _scope_sql(viewer)
    with _lock:
        conn = _connect()
        try:
            _ensure_schema_once(conn)
            by_status = {
                row["status"]: row["count"]
                for row in conn.execute(
                    f"SELECT status, COUNT(*) AS count FROM print_history WHERE {where} GROUP BY status",
                    params,
                ).fetchall()
            }
            return {"total": sum(by_status.values()), "by_status": by_status}
        finally:
            conn.close()


def count_for(viewer):
    """Number of entries in viewer's scope."""
    where, params = _scope_sql(viewer)
    with _lock:
        conn = _connect()
        try:
            _ensure_schema_once(conn)
            return conn.execute(f"SELECT COUNT(*) FROM print_history WHERE {where}", params).fetchone()[0]
        finally:
            conn.close()


def get_owners():
    """Every distinct owner pair that has rows (System excluded), for the
    admin's owner filter."""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema_once(conn)
            rows = conn.execute(
                "SELECT DISTINCT owner_id, owner_created FROM print_history WHERE owner_id IS NOT NULL"
            ).fetchall()
            return [(r["owner_id"], r["owner_created"]) for r in rows]
        finally:
            conn.close()
