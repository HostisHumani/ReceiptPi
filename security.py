"""
CSRF protection for the web UI forms (/ui/*), token protection for the
JSON/automation endpoints (/print/*), robust JSON parsing, plus shared
input limits used by several modules.

Every view's access class for the login check (modules/auth/guard.py)
comes from the decorators here, not from its URL: require_api_token
marks a view as "api_token", public marks it as "public", and a view
with neither needs a login once an admin account exists.
"""
import re
import secrets as secrets_module
from functools import wraps

import config
from flask import abort, jsonify, request, session

import auth_store
import i18n

ACCESS_ATTR = "receiptpi_access"
ACCESS_PUBLIC = "public"
ACCESS_API_TOKEN = "api_token"

# Shared input limits (used by shopping, message, and others)
MAX_TITLE_LEN = 100
MAX_TEXT_LEN = 2000
MAX_ITEMS = 100
MAX_ITEM_LEN = 200

# Allowlists for a system report host's SSH target - the one check both
# the host form/API (modules/settings/) and the settings backup restore
# (modules/backup/) apply, so neither accepts a value the other refuses.
# A value starting with "-" would reach the ssh command line as an option
# (e.g. -oProxyCommand=..., which runs a local command), and whitespace
# or control characters have no place in a host or user name. Host:
# DNS name, IPv4 or IPv6 (optionally bracketed or with a %zone suffix),
# at most 253 characters like a DNS name. User: the usual POSIX login
# name characters, at most 64. fullmatch() rather than match() with "$",
# since "$" also matches right before a trailing newline.
SSH_HOST_RE = re.compile(r"[A-Za-z0-9\[][A-Za-z0-9._:%\[\]-]{0,252}")
SSH_USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")


def is_valid_ssh_host(host):
    return isinstance(host, str) and SSH_HOST_RE.fullmatch(host) is not None


def is_valid_ssh_user(user):
    return isinstance(user, str) and SSH_USER_RE.fullmatch(user) is not None


def csrf_protect(view_func):
    """Protects the /ui/* forms against cross-site POSTs from the local
    network: every rendered page carries a session-bound token in a
    hidden form field, checked against the session on submit."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        form_token = request.form.get("csrf_token")
        session_token = session.get("csrf_token")
        # Explicit check first: without it, a POST with no prior page
        # load (both values None) would pass the comparison below,
        # since None != None evaluates to False.
        if not form_token or not session_token or not secrets_module.compare_digest(form_token, session_token):
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets_module.token_hex(16)
    return session["csrf_token"]


def public(view_func):
    """Marks a view as reachable without a login (health check, static
    assets, the login page itself). The view is returned unchanged."""
    setattr(view_func, ACCESS_ATTR, ACCESS_PUBLIC)
    return view_func


def require_api_token(view_func):
    """Protects the JSON/automation endpoints with a static token
    (X-Api-Token header). The web UI (/ui/*) is unaffected - it's only
    used from a browser on the internal network. Additionally
    restricting the port to the LAN via firewall is recommended.

    Only ever the header: a login session cookie is never accepted here,
    so a page in a logged-in browser can't be used to call the API on
    the visitor's behalf.

    Without an API_TOKEN, /print/* stays open as before. The JSON
    settings API under /settings/ is the exception once an admin
    account exists: it can change every setting, and with logins in
    place an open settings API would be a way around them - so it
    answers 403 until an API_TOKEN is configured."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        token = getattr(config, "API_TOKEN", None)
        if not token and request.path.startswith("/settings/") and auth_store.admin_exists():
            return jsonify({"status": "error", "detail": i18n.tr("auth.error.api_token_required")}), 403
        # compare_digest instead of != : a plain string comparison
        # short-circuits on the first mismatched character, which
        # leaks (via response timing) how many leading characters of a
        # guess were correct. Low real-world risk on a home LAN, but
        # free to avoid.
        if token and not secrets_module.compare_digest(request.headers.get("X-Api-Token", ""), token):
            return jsonify({"status": "error", "detail": "invalid or missing X-Api-Token"}), 401
        return view_func(*args, **kwargs)
    setattr(wrapper, ACCESS_ATTR, ACCESS_API_TOKEN)
    return wrapper


def backup_access_error(action, submitted_token):
    """The one access gate for the settings backup (see
    modules/backup/routes.py). action is "download" or "restore";
    submitted_token is the API token typed into the form. Returns None
    if allowed, otherwise a translation key for the refusal.

    The web UI has no user accounts, so the API token stands in for
    "administrator" here: with API_TOKEN set, both actions require it.
    Without one, downloading stays allowed like every other page on the
    LAN, but restoring is refused - it rewrites settings, and nothing
    would tell an administrator apart from anyone else on the network.
    A later login with admin roles only has to change this function; the
    endpoints and the archive format stay the same.

    Compared as bytes: compare_digest() raises TypeError for str
    arguments containing non-ASCII characters, which a form field can
    carry."""
    token = getattr(config, "API_TOKEN", None)
    if not token:
        return "backup.error.restore_locked" if action == "restore" else None
    if not submitted_token or not secrets_module.compare_digest(
        submitted_token.encode("utf-8"), str(token).encode("utf-8")
    ):
        return "backup.error.token_invalid"
    return None


def get_json_body():
    """Parses the JSON body robustly. Returns (data, error_response) -
    on invalid/missing JSON, data is None and error_response is a ready
    (jsonify(...), 400) tuple to return directly."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"status": "error", "detail": "Invalid or missing JSON body"}), 400)
    return data, None
