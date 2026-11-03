"""
Module: user management in the web UI (Settings -> Users), admin only
like every route here without an access decorator (see
modules/auth/guard.py).

An admin creates an account with a name and a role; the server makes a
start password (auth_store.generate_initial_password), shows it exactly
once in the response to that POST and keeps only its hash. The account
has to replace it at its first login. A password reset works the same
way and also ends all sessions of the account.

The page exists only once an admin account exists: before that there
is no login, and creating accounts here would be a way around the
setup code on /setup.

The own account can't be demoted, locked, reset or deleted here - the
account page covers the own password, and another admin covers the
rest - so an admin can't lock themselves out by a misclick. Losing the
last active admin is refused by auth_store itself.
"""
from datetime import datetime

from flask import Blueprint, abort, g, make_response, render_template, request

import auth_store
import i18n
from security import csrf_protect, get_csrf_token

users_bp = Blueprint("users", __name__)

MAX_NAME_FIELD_LEN = 64


def _require_accounts():
    if not auth_store.admin_exists() or not g.get("auth_user"):
        abort(404)


def _target(user_id):
    """The account a form refers to, or 404."""
    user = auth_store.get_user(user_id)
    if user is None:
        abort(404)
    return user


def _display_time(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else None


def _render(status=200, message=None, success=None, **context):
    """Never stored by the browser or a cache: the page can carry a start
    password, and always carries a CSRF token."""
    users = auth_store.list_users()
    for user in users:
        user["last_login_display"] = _display_time(user["last_login"])
        user["is_self"] = user["id"] == g.auth_user["id"]
    context.setdefault("name_value", "")
    context.setdefault("role_value", "user")
    response = make_response(render_template(
        "settings_users.html", users=users, roles=auth_store.ROLES, csrf_token=get_csrf_token(),
        message=message, success=success, **context), status)
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(code, status=400, **context):
    return _render(status, i18n.tr(f"auth.error.{code}"), False, **context)


def _refuse_self(user):
    return user["id"] == g.auth_user["id"]


@users_bp.route("/settings/users", methods=["GET"])
def users_page():
    _require_accounts()
    return _render()


@users_bp.route("/ui/settings/users/create", methods=["POST"])
@csrf_protect
def ui_create_user():
    _require_accounts()
    name_value = request.form.get("name", "")[:MAX_NAME_FIELD_LEN]
    role = request.form.get("role", "")
    try:
        user, password = auth_store.create_user_with_initial_password(name_value, role)
    except auth_store.AuthError as e:
        return _error(e.code, 409 if e.code == "name_taken" else 400,
                      name_value=name_value, role_value=role if role in auth_store.ROLES else "user")
    return _render(message=i18n.tr("auth.users.created", name=user["name"]), success=True,
                   initial_password={"name": user["name"], "password": password})


@users_bp.route("/ui/settings/users/<int:user_id>/role", methods=["POST"])
@csrf_protect
def ui_set_role(user_id):
    _require_accounts()
    user = _target(user_id)
    if _refuse_self(user):
        return _error("own_account", 409)
    try:
        auth_store.set_role(user_id, request.form.get("role", ""))
    except auth_store.AuthError as e:
        if e.code == "user_not_found":
            abort(404)
        return _error(e.code, 409 if e.code == "last_admin" else 400)
    return _render(message=i18n.tr("auth.users.role_changed", name=user["name"]), success=True)


def _set_disabled(user_id, disabled):
    _require_accounts()
    user = _target(user_id)
    if _refuse_self(user):
        return _error("own_account", 409)
    try:
        auth_store.set_disabled(user_id, disabled)
    except auth_store.AuthError as e:
        if e.code == "user_not_found":
            abort(404)
        return _error(e.code, 409)
    key = "auth.users.locked" if disabled else "auth.users.unlocked"
    return _render(message=i18n.tr(key, name=user["name"]), success=True)


@users_bp.route("/ui/settings/users/<int:user_id>/lock", methods=["POST"])
@csrf_protect
def ui_lock_user(user_id):
    return _set_disabled(user_id, True)


@users_bp.route("/ui/settings/users/<int:user_id>/unlock", methods=["POST"])
@csrf_protect
def ui_unlock_user(user_id):
    return _set_disabled(user_id, False)


@users_bp.route("/ui/settings/users/<int:user_id>/reset-password", methods=["POST"])
@csrf_protect
def ui_reset_password(user_id):
    _require_accounts()
    user = _target(user_id)
    if _refuse_self(user):
        return _error("own_account", 409)
    try:
        password = auth_store.reset_to_initial_password(user_id)
    except auth_store.AuthError as e:
        if e.code == "user_not_found":
            abort(404)
        raise
    return _render(message=i18n.tr("auth.users.password_reset", name=user["name"]), success=True,
                   initial_password={"name": user["name"], "password": password})


@users_bp.route("/ui/settings/users/<int:user_id>/delete", methods=["POST"])
@csrf_protect
def ui_delete_user(user_id):
    """Without confirm=yes this only shows the confirmation; the actual
    deletion is a second POST from that page."""
    _require_accounts()
    user = _target(user_id)
    if _refuse_self(user):
        return _error("own_account", 409)
    if request.form.get("confirm") != "yes":
        return _render(confirm_delete=user)
    try:
        auth_store.delete_user(user_id)
    except auth_store.AuthError as e:
        if e.code == "user_not_found":
            abort(404)
        return _error(e.code, 409)
    return _render(message=i18n.tr("auth.users.deleted", name=user["name"]), success=True)
