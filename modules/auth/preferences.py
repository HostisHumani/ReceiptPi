"""
Module: personal settings of the logged-in account (burger menu ->
"My settings"), open to every account (see user_prefs.py for what is
stored and how a missing value falls back to the device default).

Each choice offers "device default" as its first option, which stores
nothing: the account then follows the device setting, also when an
admin changes it later. The device defaults themselves stay on the
admin settings pages (/settings/language, /settings/design).

The page exists only once an admin account exists: before that there
are no accounts to store anything for. An account that still has to
replace its start password never gets here - the login check sends it
to its account page first (modules/auth/guard.py).
"""
from flask import Blueprint, abort, g, make_response, render_template, request

import auth_store
import i18n
import settings_store
import themes
import user_prefs
from security import any_user, csrf_protect, get_csrf_token

preferences_bp = Blueprint("preferences", __name__)

# Form field name -> prefs key.
_FIELDS = {
    "ui_lang": user_prefs.UI_LANG,
    "theme": user_prefs.THEME,
}


def _require_account():
    if not auth_store.admin_exists() or not g.get("auth_user"):
        abort(404)


def _render(status=200, message=None, success=None):
    settings = settings_store.get_settings()
    response = make_response(render_template(
        "preferences.html",
        prefs=user_prefs.current(),
        device_language=settings.get("language", i18n.DEFAULT_LANGUAGE),
        device_theme=settings.get("theme", themes.DEFAULT_THEME),
        theme_swatches=themes.SWATCHES,
        csrf_token=get_csrf_token(),
        message=message, success=success,
    ), status)
    response.headers["Cache-Control"] = "no-store"
    return response


@preferences_bp.route("/preferences", methods=["GET"])
@any_user
def preferences_page():
    _require_account()
    return _render()


@preferences_bp.route("/ui/preferences", methods=["POST"])
@any_user
@csrf_protect
def ui_save_preferences():
    """Saves every choice of the form at once; a field missing from the
    form leaves its stored value as it is. An empty value is "device
    default"."""
    _require_account()
    changes = {}
    for field, key in _FIELDS.items():
        if field not in request.form:
            continue
        value = request.form.get(field, "")
        if not user_prefs.valid_choice(key, value):
            return _render(400, i18n.tr("auth.prefs.invalid"), False)
        changes[key] = value
    try:
        prefs = user_prefs.update(g.auth_user["id"], changes)
    except auth_store.AuthError:
        abort(404)
    # The page below is already rendered with the new choices.
    g.auth_user = dict(g.auth_user, prefs=prefs)
    return _render(message=i18n.tr("auth.prefs.saved"), success=True)
