"""
Module: login, first-time setup and the own account page.

/setup creates the one admin account and exists only until it does
(404 afterwards). It asks for a setup code, which proves physical or
administrative access to the device:
  - with an API_TOKEN in config.py, the code is that token;
  - without one, a one-time code printed on demand on the receipt
    printer. It lives only in this process's memory, is valid for
    SETUP_CODE_TTL_S, and only the most recently printed one counts.
    It never goes into the print history, the pending list, a log line
    or an error message; if the print fails, the code is discarded and
    a new one has to be requested.
The console script (scripts/manage_users.py create-admin) is the way
in when neither works.
"""
import secrets
import threading
import time

import config
from flask import Blueprint, abort, g, make_response, redirect, render_template, request, session

import auth_store
import i18n
from modules.auth import guard
from modules.auth.throttle import throttle
from print_queue import enqueue_print
from security import any_user, csrf_protect, get_csrf_token, public

auth_bp = Blueprint("auth", __name__)

SETUP_CODE_TTL_S = 15 * 60
# Without 0/O and 1/I/L, so a code read off a receipt can't be mistyped
# between look-alikes. 12 characters of 32 = 60 bits.
SETUP_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SETUP_CODE_LEN = 12

_setup_code_lock = threading.Lock()
_setup_code = None  # (normalized code, monotonic expiry) or None

# Brute-force key shared by every setup attempt: the code is one secret
# for the whole device, so attempts from many addresses add up.
_SETUP_THROTTLE_KEY = "setup"

MAX_NAME_FIELD_LEN = 64


def _now():
    return time.monotonic()


def _api_token():
    return getattr(config, "API_TOKEN", None) or ""


def _client_key():
    return "ip:" + (request.remote_addr or "unknown")


def _new_setup_code():
    return "".join(secrets.choice(SETUP_CODE_ALPHABET) for _ in range(SETUP_CODE_LEN))


def _format_setup_code(code):
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _normalize_setup_code(value):
    return "".join(c for c in (value or "").upper() if c not in " -")


def _setup_code_matches(submitted):
    """compare_digest on bytes: it raises TypeError for str arguments
    with non-ASCII characters, which a form field can carry."""
    if not isinstance(submitted, str) or not submitted:
        return False
    token = _api_token()
    if token:
        return secrets.compare_digest(submitted.strip().encode("utf-8"), str(token).encode("utf-8"))
    with _setup_code_lock:
        current = _setup_code
    if current is None or _now() >= current[1]:
        return False
    return secrets.compare_digest(_normalize_setup_code(submitted).encode("utf-8"), current[0].encode("utf-8"))


def _discard_setup_code():
    global _setup_code
    with _setup_code_lock:
        _setup_code = None


def _raw_print_setup_code(code):
    """Prints the setup code. No logo and no Easy-Read scaling: it's a
    short technical notice, read once. printer is imported here, not at
    module level, for the same escpos temp-dir reason as in
    modules/message/routes.py."""
    from printer import get_printer

    p = get_printer()
    try:
        p.set(align="center", bold=True, width=1, height=2, custom_size=True)
        p.text(i18n.tr("auth.receipt.setup_title") + "\n\n")
        p.set(align="center", bold=False, width=1, height=1, custom_size=True)
        p.text(i18n.tr("auth.receipt.setup_intro") + "\n\n")
        p.set(align="center", bold=True, width=2, height=2, custom_size=True)
        p.text(_format_setup_code(code) + "\n\n")
        p.set(align="center", bold=False, width=1, height=1, custom_size=True)
        p.text(i18n.tr("auth.receipt.setup_validity", minutes=SETUP_CODE_TTL_S // 60) + "\n")
        p.cut()
    finally:
        p.close()


def _print_setup_code():
    """Prints a fresh code and makes it the valid one only if the print
    succeeded. Returns (ok, detail). A random dedupe_key keeps the code
    out of the print queue's duplicate fingerprint and lets a second
    request print a second code; log_history=False and no retry_payload
    keep it out of history and pending. detail is passed on only if it
    can't contain the code."""
    global _setup_code
    code = _new_setup_code()
    _discard_setup_code()
    ok, detail, _status = enqueue_print(
        _raw_print_setup_code, code, job_type="setup", summary="", source="system",
        log_history=False, dedupe_key=secrets.token_hex(16),
    )
    if not ok:
        detail = detail or ""
        if code in detail or _format_setup_code(code) in detail:
            detail = ""
        return False, detail
    with _setup_code_lock:
        _setup_code = (code, _now() + SETUP_CODE_TTL_S)
    return True, ""


def _start_session(response, user, remember):
    """Logs user in on this response: a fresh session (any session the
    browser still had is ended first) and a fresh CSRF token, so nothing
    an attacker could have planted before the login stays valid after
    it."""
    old = request.cookies.get(guard.COOKIE_NAME)
    if old:
        auth_store.revoke_session(auth_store.hash_token(old))
    token, expires = auth_store.create_session(user["id"], remember)
    session.pop("csrf_token", None)
    get_csrf_token()
    guard.set_session_cookie(response, token, remember, expires)
    g.auth_cookie_handled = True


def _render(template, status=200, **context):
    """Auth pages are never stored by the browser (they carry a CSRF
    token and, on /account, the account name)."""
    response = make_response(render_template(
        template, csrf_token=get_csrf_token(), password_min=auth_store.PASSWORD_MIN_LEN,
        password_max=auth_store.PASSWORD_MAX_LEN, **context), status)
    response.headers["Cache-Control"] = "no-store"
    return response


def _locked_out_page(template, retry_after, **context):
    response = _render(template, 429, message=i18n.tr("auth.error.too_many_attempts"), success=False,
                       **context)
    response.headers["Retry-After"] = str(retry_after)
    return response


# -- setup ---------------------------------------------------------------------

def _setup_context(**extra):
    context = {"uses_api_token": bool(_api_token()), "code_ttl_minutes": SETUP_CODE_TTL_S // 60,
               "name_value": ""}
    context.update(extra)
    return context


@auth_bp.route("/setup", methods=["GET"])
@public
def setup_page():
    if auth_store.admin_exists():
        abort(404)
    return _render("auth_setup.html", **_setup_context(message=None, success=None))


@auth_bp.route("/setup", methods=["POST"])
@public
@csrf_protect
def setup_submit():
    if auth_store.admin_exists():
        abort(404)
    if request.form.get("action") == "print_code":
        if _api_token():
            abort(400)
        ok, detail = _print_setup_code()
        if ok:
            message = i18n.tr("auth.setup.code_printed", minutes=SETUP_CODE_TTL_S // 60)
        else:
            message = i18n.tr("auth.setup.print_failed")
            if detail:
                message += " " + i18n.tr("auth.setup.print_failed_reason", reason=detail)
        return _render("auth_setup.html", 200 if ok else 503,
                       **_setup_context(message=message, success=ok))

    name_value = request.form.get("name", "")[:MAX_NAME_FIELD_LEN]
    keys = [_SETUP_THROTTLE_KEY, _client_key()]
    retry_after = throttle.retry_after(keys)
    if retry_after:
        return _locked_out_page("auth_setup.html", retry_after, **_setup_context(name_value=name_value))
    if not _setup_code_matches(request.form.get("code", "")):
        throttle.failure(keys)
        return _render("auth_setup.html", 403, **_setup_context(
            message=i18n.tr("auth.error.setup_code_invalid"), success=False, name_value=name_value))

    password = request.form.get("password", "")
    error = None
    try:
        name = auth_store.validate_name(name_value)
        auth_store.validate_password(password)
        if password != request.form.get("password_repeat", ""):
            raise auth_store.AuthError("password_mismatch")
        user = auth_store.create_user(name, password, role="admin", only_if_no_admin=True)
    except auth_store.AuthError as e:
        if e.code == "admin_exists":
            abort(404)
        error = e.code
    if error:
        return _render("auth_setup.html", 400, **_setup_context(
            message=i18n.tr(f"auth.error.{error}", min=auth_store.PASSWORD_MIN_LEN,
                            max=auth_store.PASSWORD_MAX_LEN),
            success=False, name_value=name_value))

    throttle.success(keys)
    _discard_setup_code()
    response = redirect("/")
    _start_session(response, user, remember=False)
    return response


# -- login / logout -----------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
@public
def login():
    next_target = guard.safe_next(request.args.get("next"))
    if not auth_store.admin_exists():
        return redirect(next_target)
    if guard.load_session():
        return redirect(next_target)
    return _render("auth_login.html", next_target=next_target, name_value="", message=None, success=None)


@auth_bp.route("/login", methods=["POST"])
@public
@csrf_protect
def login_submit():
    next_target = guard.safe_next(request.form.get("next"))
    if not auth_store.admin_exists():
        return redirect(next_target)
    name_value = request.form.get("name", "")[:MAX_NAME_FIELD_LEN]
    password = request.form.get("password", "")
    remember = request.form.get("remember") == "yes"
    keys = ["name:" + auth_store.normalize_name(name_value), _client_key()]
    context = {"next_target": next_target, "name_value": name_value}

    retry_after = throttle.retry_after(keys)
    if retry_after:
        return _locked_out_page("auth_login.html", retry_after, **context)
    user = auth_store.verify_login(name_value, password)
    if user is None:
        throttle.failure(keys)
        return _render("auth_login.html", 401, message=i18n.tr("auth.error.invalid_credentials"),
                       success=False, **context)
    throttle.success(keys)
    response = redirect(next_target)
    _start_session(response, user, remember)
    return response


@auth_bp.route("/logout", methods=["POST"])
@public
@csrf_protect
def logout():
    """Public, so a stale tab whose session already ended still lands on
    the login page instead of a 401. CSRF-protected, so no other site
    can log the browser out."""
    token = request.cookies.get(guard.COOKIE_NAME)
    if token:
        auth_store.revoke_session(auth_store.hash_token(token))
    session.pop("csrf_token", None)
    response = redirect("/login")
    guard.clear_session_cookie(response)
    g.auth_cookie_handled = True
    return response


# -- own account ----------------------------------------------------------------------

def _account_page(status=200, message=None, success=None):
    return _render("auth_account.html", status, message=message, success=success,
                   account=g.auth_user, must_change=g.auth_user["must_change_password"])


@auth_bp.route("/account", methods=["GET"])
@any_user
def account_page():
    # Without an admin account there is no login, so no own account
    # either; the guard lets every request through in that state.
    if not g.get("auth_user"):
        abort(404)
    return _account_page()


@auth_bp.route("/account", methods=["POST"])
@any_user
@csrf_protect
def account_submit():
    if not g.get("auth_user"):
        abort(404)
    user = g.auth_user
    current_hash = g.auth_session["token_hash"]
    action = request.form.get("action")

    # The guard only lets change_password through while the account still
    # has a start password.
    if action == "logout_others":
        count = auth_store.revoke_user_sessions(user["id"], except_token_hash=current_hash)
        return _account_page(message=i18n.tr("auth.account.others_logged_out", count=count), success=True)

    if action != "change_password":
        abort(400)
    keys = ["name:" + user["name"], _client_key()]
    retry_after = throttle.retry_after(keys)
    if retry_after:
        response = _account_page(429, i18n.tr("auth.error.too_many_attempts"), False)
        response.headers["Retry-After"] = str(retry_after)
        return response
    if not auth_store.verify_password(user["id"], request.form.get("current_password", "")):
        throttle.failure(keys)
        return _account_page(403, i18n.tr("auth.error.current_password_wrong"), False)
    throttle.success(keys)
    new_password = request.form.get("new_password", "")
    try:
        auth_store.validate_password(new_password)
        if new_password != request.form.get("new_password_repeat", ""):
            raise auth_store.AuthError("password_mismatch")
        # A start password has to be replaced, not confirmed.
        if new_password == request.form.get("current_password", ""):
            raise auth_store.AuthError("password_unchanged")
        # Every other session ends with the old password: whoever might
        # have known it is logged out along with it.
        auth_store.set_password(user["id"], new_password, revoke_sessions=True,
                                keep_token_hash=current_hash)
    except auth_store.AuthError as e:
        return _account_page(400, i18n.tr(f"auth.error.{e.code}", min=auth_store.PASSWORD_MIN_LEN,
                                          max=auth_store.PASSWORD_MAX_LEN), False)
    # The page rendered below should already show the account without
    # the start password notice and with the full menu.
    g.auth_user = dict(user, must_change_password=False)
    return _account_page(message=i18n.tr("auth.account.password_changed"), success=True)


@auth_bp.app_context_processor
def inject_auth():
    """Login state for base.html (menu entries, setup banner) and the
    settings page (missing API_TOKEN warning). auth_is_admin decides the
    admin entries in the menu; auth_can_open(path) hides links to pages
    the account may not open. menu_csrf_token is a
    function so the CSRF token (and with it Flask's session cookie) is
    only created on pages that actually render the logout button."""
    admin_exists = auth_store.admin_exists()
    user = g.get("auth_user")
    return {
        "auth_user": user,
        "auth_is_admin": guard.is_admin(),
        "auth_must_change": bool(user and user["must_change_password"]),
        "auth_can_open": guard.can_open,
        "auth_admin_exists": admin_exists,
        "auth_setup_needed": not admin_exists,
        "auth_api_token_missing": admin_exists and not _api_token(),
        "menu_csrf_token": get_csrf_token,
    }
