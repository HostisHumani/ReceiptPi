"""Shared web security and request parsing helpers."""
from functools import wraps

from flask import request, session, abort, jsonify
import secrets as secrets_module

import config

# Shared input limits used by multiple modules.
MAX_TITLE_LEN = 100
MAX_TEXT_LEN = 2000
MAX_ITEMS = 100
MAX_ITEM_LEN = 200


def csrf_protect(view_func):
    """Protect web forms with a session-bound CSRF token."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        form_token = request.form.get("csrf_token")
        session_token = session.get("csrf_token")
        # Reject missing tokens explicitly; otherwise None could compare equal to None.
        #
        #
        if not form_token or not session_token or not secrets_module.compare_digest(form_token, session_token):
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets_module.token_hex(16)
    return session["csrf_token"]


def require_api_token(view_func):
    """Protect automation endpoints with the configured API token."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        token = getattr(config, "API_TOKEN", None)
        if token and request.headers.get("X-Api-Token") != token:
            return jsonify({"status": "error", "detail": "ungültiges oder fehlendes X-Api-Token"}), 401
        return view_func(*args, **kwargs)
    return wrapper


def get_json_body():
    """Parse a JSON object and return (data, error response)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"status": "error", "detail": "Ungültiger oder fehlender JSON-Body"}), 400)
    return data, None
