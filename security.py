"""
CSRF protection for the web UI forms (/ui/*), token protection for the
JSON/automation endpoints (/print/*), robust JSON parsing, plus shared
input length limits used by several modules.
"""
import secrets as secrets_module
from functools import wraps

import config
from flask import abort, jsonify, request, session

# Shared input limits (used by shopping, message, and others)
MAX_TITLE_LEN = 100
MAX_TEXT_LEN = 2000
MAX_ITEMS = 100
MAX_ITEM_LEN = 200


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


def require_api_token(view_func):
    """Protects the JSON/automation endpoints with a static token
    (X-Api-Token header). The web UI (/ui/*) is unaffected - it's only
    used from a browser on the internal network. Additionally
    restricting the port to the LAN via firewall is recommended."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        token = getattr(config, "API_TOKEN", None)
        # compare_digest instead of != : a plain string comparison
        # short-circuits on the first mismatched character, which
        # leaks (via response timing) how many leading characters of a
        # guess were correct. Low real-world risk on a home LAN, but
        # free to avoid.
        if token and not secrets_module.compare_digest(request.headers.get("X-Api-Token", ""), token):
            return jsonify({"status": "error", "detail": "invalid or missing X-Api-Token"}), 401
        return view_func(*args, **kwargs)
    return wrapper


def get_json_body():
    """Parses the JSON body robustly. Returns (data, error_response) -
    on invalid/missing JSON, data is None and error_response is a ready
    (jsonify(...), 400) tuple to return directly."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"status": "error", "detail": "Invalid or missing JSON body"}), 400)
    return data, None
