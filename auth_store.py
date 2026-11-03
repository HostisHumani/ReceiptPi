"""
User accounts and login sessions for the web UI (SQLite in
STATE_DIR/auth.db).

A separate database file instead of a table in history.db: history.db
is world-readable (0644) and part of every routine write path, while
this file holds password hashes and session hashes and is kept at 0600.
Deleting auth.db (with the service stopped) puts the device back into
the "no admin yet" state, in which the web UI is open as before.

Passwords: werkzeug.security with its default method (scrypt). One
scrypt run needs about 32 MiB of RAM (n=32768, r=8: 128 * n * r bytes),
so all hash operations run one at a time behind _hash_semaphore - with
--threads 4, parallel logins would otherwise multiply that on a device
with very little free memory.

Sessions: the cookie only ever carries a random token; the database
stores its SHA-256 hash, so a leaked copy of auth.db cannot be turned
back into a working cookie.

No WAL journal mode on purpose: admin_exists() is called on every
request and only stats the file (see there), which relies on every
committed write changing the main file's mtime. In WAL mode commits go
to the -wal file and reach the main file only at a checkpoint.

Roles: "admin" may do everything; "user" may only use the pages marked
for all logged-in users (see modules/auth/guard.py). Every change that
could leave the device without an active admin (deleting, disabling or
demoting one) checks the remaining active admins inside the same write
transaction as the change itself, so two such requests at the same time
can't both pass the check.

Personal settings: users.prefs holds a JSON object per account (see
user_prefs.py for its keys and their validation). This module only
stores and parses it; a value that isn't a JSON object reads as {}.

The clock is the module-level _now(), so tests can replace it.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time

import config
from werkzeug.security import check_password_hash, generate_password_hash

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(STATE_DIR, "auth.db")

SCHEMA_VERSION = 2

ROLES = ("admin", "user")

# Names are stored lowercased (see normalize_name()), so the allowed
# characters only need to cover lowercase letters. The UNIQUE ... COLLATE
# NOCASE column is a second line of defence for rows written by anything
# that bypassed normalize_name().
NAME_RE = re.compile(r"[a-z0-9._-]{3,32}")
PASSWORD_MIN_LEN = 10
PASSWORD_MAX_LEN = 128

# Start passwords that an admin hands over by hand: no 0/O, 1/l/I, and
# lowercase only, so a password read off a screen or a note can't be
# mistyped between look-alikes. 16 characters of 32 = 80 bits, in groups
# of four separated by "-" (the dashes are part of the password).
INITIAL_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
INITIAL_PASSWORD_GROUPS = 4
INITIAL_PASSWORD_GROUP_LEN = 4

REMEMBER_LIFETIME_S = 90 * 24 * 3600
IDLE_TIMEOUT_S = 24 * 3600

# How stale last_seen may get before a request writes it again. A
# remembered session only needs its 90-day expiry pushed forward, so
# once a day is enough. A non-remembered session expires after 24 h
# without use, measured from the last written last_seen - with a daily
# write, a session in active use could expire minutes after its last
# request. Hourly keeps that error under an hour while still writing at
# most once per hour of active use.
TOUCH_INTERVAL_REMEMBER_S = 24 * 3600
TOUCH_INTERVAL_IDLE_S = 3600

# Expired rows are deleted lazily: on every new session, and while
# validating at most once per this interval (and only if there is
# something to delete, so a quiet database is never written).
_PURGE_INTERVAL_S = 3600

_lock = threading.Lock()
_hash_semaphore = threading.BoundedSemaphore(1)
_dummy_hash = None
_last_purge = 0.0

# (st_ino, st_mtime_ns, st_size) of auth.db at the last admin check, and
# its result. Same idea as settings_store's mtime cache: a request only
# costs a stat() as long as nobody wrote the file - also when the
# console script (another process) is what wrote it.
_admin_cache = (None, False)


class AuthError(ValueError):
    """A rejected input or state. code is a stable identifier (also used
    as the last part of a translation key by the web UI)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _now():
    return time.time()


def normalize_name(name):
    return (name or "").strip().lower()


def validate_name(name):
    """Returns the normalized name or raises AuthError("name_invalid")."""
    normalized = normalize_name(name)
    if not NAME_RE.fullmatch(normalized):
        raise AuthError("name_invalid")
    return normalized


def validate_password(password):
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LEN:
        raise AuthError("password_too_short")
    if len(password) > PASSWORD_MAX_LEN:
        raise AuthError("password_too_long")


def _check_state_dir():
    """auth.db must not live in a directory that other users can write
    to: anyone with write access to the directory can replace the file
    (and with it the admin account), whatever the file's own mode is."""
    os.makedirs(STATE_DIR, exist_ok=True)
    st = os.stat(STATE_DIR)
    if st.st_mode & stat.S_IWOTH:
        raise RuntimeError(f"{STATE_DIR} is writable by other users; refusing to use it for auth.db")


def _ensure_file():
    """Creates auth.db as an empty file with mode 0600 before SQLite
    opens it - sqlite3.connect() would create it with the process umask
    (usually 0644). SQLite gives its journal file the same mode as the
    database file. An existing file with a wider mode is narrowed."""
    _check_state_dir()
    fd = os.open(DB_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _connect():
    _ensure_file()
    conn = sqlite3.connect(DB_FILE, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    """PRAGMA user_version is the schema version. Each step migrates one
    version up, in place and inside one write transaction, on the first
    connection; a new database runs through the same steps, so a new and
    a migrated auth.db end up with the same schema.

    Version 2 adds three columns to users:
      - must_change_password: set for a start password made by an admin
        (new account or reset); the guard then lets the account do
        nothing but change it.
      - disabled: a locked account can't log in and has no sessions.
      - last_login: when the account last logged in (NULL = never), for
        the user list. Written together with the new session row, so a
        login costs no extra write.
    ALTER TABLE ... ADD COLUMN only extends the table definition: existing
    rows, password hashes and sessions stay as they are and read the new
    columns' defaults."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    pw_hash TEXT NOT NULL,
                    prefs TEXT NOT NULL DEFAULT '{}',
                    created INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    expires INTEGER NOT NULL,
                    remember INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires)")
            conn.execute("PRAGMA user_version = 1")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
            conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE users ADD COLUMN last_login INTEGER")
            conn.execute("PRAGMA user_version = 2")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def hash_password(password):
    validate_password(password)
    with _hash_semaphore:
        return generate_password_hash(password)


def _check_hash(pw_hash, password):
    with _hash_semaphore:
        return check_password_hash(pw_hash, password)


def _get_dummy_hash():
    """A hash of a random throwaway password, made with the same method
    and parameters as real hashes, so checking an unknown name costs the
    same scrypt run as checking a wrong password."""
    global _dummy_hash
    if _dummy_hash is None:
        with _hash_semaphore:
            if _dummy_hash is None:
                _dummy_hash = generate_password_hash(secrets.token_urlsafe(32))
    return _dummy_hash


def generate_initial_password():
    groups = (
        "".join(secrets.choice(INITIAL_PASSWORD_ALPHABET) for _ in range(INITIAL_PASSWORD_GROUP_LEN))
        for _ in range(INITIAL_PASSWORD_GROUPS)
    )
    return "-".join(groups)


def _user_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"], "name": row["name"], "role": row["role"], "created": row["created"],
        "must_change_password": bool(row["must_change_password"]), "disabled": bool(row["disabled"]),
        "last_login": row["last_login"],
    }


def _parse_prefs(raw):
    """users.prefs as a dict. Anything else in the column (broken JSON, a
    list, NULL from a row written by hand) reads as no personal settings,
    so a damaged value can never keep an account from loading a page."""
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def admin_exists():
    """True once at least one admin account exists. Never creates
    auth.db: on a device without it, this is a single failing stat()."""
    global _admin_cache
    try:
        st = os.stat(DB_FILE)
    except FileNotFoundError:
        _admin_cache = (None, False)
        return False
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    if _admin_cache[0] == key:
        return _admin_cache[1]
    conn = _connect()
    try:
        result = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone() is not None
    finally:
        conn.close()
    _admin_cache = (key, result)
    return result


def create_user(name, password, role="user", only_if_no_admin=False, must_change_password=False):
    """Creates an account and returns it as a dict. only_if_no_admin
    makes the first-admin setup race-free: the admin check and the
    insert run in one write transaction, so of two concurrent setups
    (two requests, or the web UI and the console script) exactly one
    succeeds and the other gets AuthError("admin_exists")."""
    if role not in ROLES:
        raise AuthError("role_invalid")
    normalized = validate_name(name)
    pw_hash = hash_password(password)
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if only_if_no_admin and conn.execute(
                    "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
                ).fetchone():
                    raise AuthError("admin_exists")
                if conn.execute("SELECT 1 FROM users WHERE name = ?", (normalized,)).fetchone():
                    raise AuthError("name_taken")
                cur = conn.execute(
                    "INSERT INTO users (name, role, pw_hash, prefs, created, must_change_password) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (normalized, role, pw_hash, json.dumps({}), int(_now()), 1 if must_change_password else 0),
                )
                user_id = cur.lastrowid
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return _user_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        finally:
            conn.close()


def create_user_with_initial_password(name, role):
    """An account created by an admin: returns (user, password) with a
    generated start password that has to be changed at the first login.
    The password is returned once and stored only as a hash."""
    password = generate_initial_password()
    user = create_user(name, password, role=role, must_change_password=True)
    return user, password


def get_user(user_id):
    conn = _connect()
    try:
        return _user_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    finally:
        conn.close()


def get_user_by_name(name):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE name = ?", (normalize_name(name),)).fetchone()
        return _user_dict(row)
    finally:
        conn.close()


def list_users():
    """Every account with its number of unexpired sessions."""
    now = int(_now())
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT u.*, (SELECT COUNT(*) FROM sessions s
                         WHERE s.user_id = u.id AND s.expires > ?) AS active_sessions
            FROM users u ORDER BY u.id
            """,
            (now,),
        ).fetchall()
        return [dict(_user_dict(r), active_sessions=r["active_sessions"]) for r in rows]
    finally:
        conn.close()


def verify_login(name, password):
    """Returns the user dict if name and password match, else None. An
    unknown or malformed name is checked against the dummy hash, so the
    answer takes as long as for a wrong password. Passwords outside the
    allowed length never reach scrypt, but still cost the same time. A
    locked account gets None like a wrong password."""
    row = None
    normalized = normalize_name(name)
    if NAME_RE.fullmatch(normalized) and os.path.exists(DB_FILE):
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE name = ?", (normalized,)).fetchone()
        finally:
            conn.close()
    plausible = isinstance(password, str) and len(password) <= PASSWORD_MAX_LEN
    candidate = password if plausible else ""
    pw_hash = row["pw_hash"] if row is not None else _get_dummy_hash()
    ok = _check_hash(pw_hash, candidate)
    # A locked account is refused only after the full hash check, so its
    # answer takes as long as (and looks the same as) a wrong password.
    if row is None or not plausible or not ok or row["disabled"]:
        return None
    return _user_dict(row)


def verify_password(user_id, password):
    """Checks the current password of an existing account (password
    change in the web UI)."""
    conn = _connect()
    try:
        row = conn.execute("SELECT pw_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    plausible = isinstance(password, str) and len(password) <= PASSWORD_MAX_LEN
    pw_hash = row["pw_hash"] if row is not None else _get_dummy_hash()
    ok = _check_hash(pw_hash, password if plausible else "")
    return row is not None and plausible and ok


def set_password(user_id, password, revoke_sessions=True, keep_token_hash=None, must_change_password=False):
    """Replaces the password hash. With revoke_sessions, every session of
    the account ends, except keep_token_hash (the session that changed
    its own password stays logged in). must_change_password is stored
    with it: False for a password the account holder chose (own change,
    console script), True for a start password made by an admin."""
    pw_hash = hash_password(password)
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "UPDATE users SET pw_hash = ?, must_change_password = ? WHERE id = ?",
                    (pw_hash, 1 if must_change_password else 0, user_id),
                )
                if cur.rowcount != 1:
                    raise AuthError("user_not_found")
                if revoke_sessions:
                    conn.execute(
                        "DELETE FROM sessions WHERE user_id = ? AND token_hash IS NOT ?",
                        (user_id, keep_token_hash),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def reset_to_initial_password(user_id):
    """An admin's password reset: a new start password that has to be
    changed at the next login; every session of the account ends.
    Returns the password (once; only its hash is stored)."""
    password = generate_initial_password()
    set_password(user_id, password, revoke_sessions=True, must_change_password=True)
    return password


def _other_active_admins(conn, user_id):
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?", (user_id,)
    ).fetchone()[0]


def _change_user_locked(user_id, change):
    """Runs change(conn, row) for an existing account inside one write
    transaction. Raises AuthError("user_not_found") for an unknown id."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                if row is None:
                    raise AuthError("user_not_found")
                change(conn, row)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def _refuse_if_last_admin(conn, row):
    """The account in row is about to stop being an active admin."""
    if row["role"] == "admin" and not row["disabled"] and _other_active_admins(conn, row["id"]) == 0:
        raise AuthError("last_admin")


def set_role(user_id, role):
    if role not in ROLES:
        raise AuthError("role_invalid")

    def change(conn, row):
        if row["role"] == role:
            return
        if role != "admin":
            _refuse_if_last_admin(conn, row)
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

    _change_user_locked(user_id, change)


def set_disabled(user_id, disabled):
    """Locks or unlocks an account. Locking ends all of its sessions in
    the same transaction."""
    def change(conn, row):
        if disabled:
            _refuse_if_last_admin(conn, row)
            conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        else:
            conn.execute("UPDATE users SET disabled = 0 WHERE id = ?", (user_id,))

    _change_user_locked(user_id, change)


def delete_user(user_id):
    """Deletes an account together with all of its sessions."""
    def change(conn, row):
        _refuse_if_last_admin(conn, row)
        # Explicit, although the foreign key cascades too: the cascade
        # only works on connections with PRAGMA foreign_keys = ON.
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    _change_user_locked(user_id, change)


def get_prefs(user_id, created=None):
    """The stored personal settings of an account as a dict, or None if
    no account has this id - or, with created given, none with this id
    and this creation time (an owner pair from the print history or the
    pending list, see history_store.py)."""
    if not os.path.exists(DB_FILE):
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT prefs, created FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None or (created is not None and row["created"] != created):
        return None
    return _parse_prefs(row["prefs"])


def update_prefs(user_id, change):
    """Replaces an account's personal settings with change(current) in
    one write transaction and returns the new dict. Nothing is written if
    the result equals what is stored: every write changes auth.db's mtime
    and with it admin_exists()'s cache key. Raises
    AuthError("user_not_found") for an unknown id."""
    result = {}

    def apply(conn, row):
        current = _parse_prefs(row["prefs"])
        updated = change(dict(current))
        if not isinstance(updated, dict):
            raise TypeError("prefs must be a dict")
        result["prefs"] = updated
        if updated != current:
            conn.execute("UPDATE users SET prefs = ? WHERE id = ?",
                         (json.dumps(updated, sort_keys=True, separators=(",", ":")), user_id))

    _change_user_locked(user_id, apply)
    return result["prefs"]


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expiry(now, remember):
    return now + (REMEMBER_LIFETIME_S if remember else IDLE_TIMEOUT_S)


def _purge_expired_locked(conn, now):
    global _last_purge
    _last_purge = now
    if conn.execute("SELECT 1 FROM sessions WHERE expires <= ? LIMIT 1", (now,)).fetchone():
        conn.execute("DELETE FROM sessions WHERE expires <= ?", (now,))


def create_session(user_id, remember):
    """Creates a session and returns (token, expires). The token goes
    into the cookie and is not stored anywhere on this side. Also records
    the login time of the account, in the same transaction."""
    token = secrets.token_urlsafe(32)
    now = int(_now())
    expires = _expiry(now, remember)
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _purge_expired_locked(conn, now)
                conn.execute(
                    "INSERT INTO sessions (token_hash, user_id, created, last_seen, expires, remember) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (hash_token(token), user_id, now, now, expires, 1 if remember else 0),
                )
                conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    return token, expires


def validate_session(token):
    """Returns {"user": ..., "token_hash", "remember", "expires",
    "refreshed"} for a valid token, else None. "user" also carries the
    account's personal settings ("prefs"), read in the same query. refreshed is True when
    this call wrote a new last_seen/expires - the caller then re-sends a
    persistent cookie with the new lifetime. An expired session found
    here is deleted.

    The account's role and flags are read from users on every call, never
    from the cookie, so a role change, a lock or a deletion takes effect
    with the next request. A locked account's sessions are deleted when
    it is locked; the disabled check here only covers a row written by
    something else."""
    if not token or not isinstance(token, str) or len(token) > 128:
        return None
    if not os.path.exists(DB_FILE):
        return None
    token_hash = hash_token(token)
    now = int(_now())
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT s.*, u.name, u.role, u.must_change_password, u.disabled, u.prefs "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None or row["disabled"]:
            return None
        remember = bool(row["remember"])
        if row["expires"] <= now:
            with _lock:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        expires = row["expires"]
        interval = TOUCH_INTERVAL_REMEMBER_S if remember else TOUCH_INTERVAL_IDLE_S
        refreshed = False
        if now - row["last_seen"] >= interval:
            expires = _expiry(now, remember)
            with _lock:
                conn.execute(
                    "UPDATE sessions SET last_seen = ?, expires = ? WHERE token_hash = ?",
                    (now, expires, token_hash),
                )
                if now - _last_purge >= _PURGE_INTERVAL_S:
                    _purge_expired_locked(conn, now)
            refreshed = True
        elif now - _last_purge >= _PURGE_INTERVAL_S:
            with _lock:
                _purge_expired_locked(conn, now)
        return {
            "user": {"id": row["user_id"], "name": row["name"], "role": row["role"],
                     "must_change_password": bool(row["must_change_password"]),
                     "prefs": _parse_prefs(row["prefs"])},
            "token_hash": token_hash,
            "remember": remember,
            "expires": expires,
            "refreshed": refreshed,
        }
    finally:
        conn.close()


def revoke_session(token_hash):
    if not os.path.exists(DB_FILE):
        return 0
    with _lock:
        conn = _connect()
        try:
            return conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,)).rowcount
        finally:
            conn.close()


def revoke_user_sessions(user_id, except_token_hash=None):
    """Ends every session of the account, optionally except one. Returns
    the number of ended sessions."""
    if not os.path.exists(DB_FILE):
        return 0
    with _lock:
        conn = _connect()
        try:
            return conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash IS NOT ?",
                (user_id, except_token_hash),
            ).rowcount
        finally:
            conn.close()
