"""
Symmetric encryption for SSH passwords stored in settings.json (system
report host entries that use password auth instead of an SSH key - see
modules/system/routes.py). Uses Fernet (AES-128-CBC + HMAC-SHA256,
authenticated - a tampered/corrupted token fails to decrypt instead of
silently returning garbage) from the `cryptography` package.

The key lives in its own file under STATE_DIR (next to settings.json,
outside the project directory and therefore outside the git repo, see
CLAUDE.md's deployment section), created on first use with chmod 600 -
never in settings.json itself, so a leaked settings.json backup alone
never exposes any password, only ciphertext.
"""
import os

import config
from cryptography.fernet import Fernet, InvalidToken

STATE_DIR = getattr(config, "STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
KEY_FILE = os.path.join(STATE_DIR, "secret.key")

_fernet = None


def _load_or_create_key():
    """Returns the process-wide Fernet instance, creating and persisting
    a new key on first-ever use. The file is opened with mode 0o600 from
    the O_CREAT call itself (not written first and chmod'd after) so
    there's no window where a fresh key file is briefly world/group-
    readable."""
    global _fernet
    if _fernet is not None:
        return _fernet

    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        fd = os.open(KEY_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    # Defensive: re-assert 0o600 even on an existing file, in case it
    # ever arrived some other way (manual copy, restore from a backup
    # tool that doesn't preserve permissions, ...).
    os.chmod(KEY_FILE, 0o600)
    _fernet = Fernet(key)
    return _fernet


def encrypt_password(plain):
    """Returns the encrypted token as a str (safe for JSON), or "" for
    an empty/None input - callers store "" to mean "no password set"
    (key-auth host)."""
    if not plain:
        return ""
    return _load_or_create_key().encrypt(plain.encode()).decode()


def decrypt_password(token):
    """Returns the plaintext password, or None if there's nothing to
    decrypt or the token doesn't decrypt (wrong/rotated key, corrupted
    value) - callers treat None the same as "no password configured"
    rather than crashing the whole report."""
    if not token:
        return None
    try:
        return _load_or_create_key().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
