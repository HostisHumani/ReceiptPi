"""
CSRF-Schutz für die Web-UI-Formulare (/ui/*), Token-Schutz für die
JSON-/Automations-Endpunkte (/print/*), robustes JSON-Parsing, sowie
gemeinsame Eingabe-Längenlimits, die von mehreren Modulen genutzt werden.
"""
import secrets as secrets_module
from functools import wraps

import config
from flask import abort, jsonify, request, session

# Gemeinsame Eingabe-Limits (u.a. von shopping und status genutzt)
MAX_TITLE_LEN = 100
MAX_TEXT_LEN = 2000
MAX_ITEMS = 100
MAX_ITEM_LEN = 200


def csrf_protect(view_func):
    """Schützt die /ui/*-Formulare gegen Cross-Site-POSTs aus dem lokalen
    Netz: jede ausgelieferte Seite trägt ein Session-gebundenes Token im
    versteckten Formularfeld, das beim Absenden mit der Session
    verglichen wird."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        form_token = request.form.get("csrf_token")
        session_token = session.get("csrf_token")
        # Explizite Prüfung zuerst: ohne sie würde ein POST ganz ohne
        # vorherigen Seitenaufruf (beide Werte None) den Vergleich
        # bestehen, da None != None zu False auswertet.
        if not form_token or not session_token or not secrets_module.compare_digest(form_token, session_token):
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets_module.token_hex(16)
    return session["csrf_token"]


def require_api_token(view_func):
    """Schützt die JSON-/Automations-Endpunkte mit einem statischen Token
    (Header X-Api-Token). Die Web-UI (/ui/*) bleibt davon unberührt, da
    sie nur im internen Netz per Browser genutzt wird - siehe ANLEITUNG.md
    zur Empfehlung, den Port zusätzlich per Firewall auf das LAN zu
    beschränken."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        token = getattr(config, "API_TOKEN", None)
        if token and request.headers.get("X-Api-Token") != token:
            return jsonify({"status": "error", "detail": "ungültiges oder fehlendes X-Api-Token"}), 401
        return view_func(*args, **kwargs)
    return wrapper


def get_json_body():
    """Parst den JSON-Body robust. Gibt (data, error_response) zurück -
    bei ungültigem/fehlendem JSON ist data None und error_response ein
    fertiges (jsonify(...), 400)-Tupel zum direkten Zurückgeben."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"status": "error", "detail": "Ungültiger oder fehlender JSON-Body"}), 400)
    return data, None
