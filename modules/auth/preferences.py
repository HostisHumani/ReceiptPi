"""
Module: personal settings of the logged-in account (burger menu ->
"My settings"), open to every account (see user_prefs.py for what is
stored and how a missing value falls back to the device default).

Each choice offers "device default" as its first option, which stores
nothing: the account then follows the device setting, also when an
admin changes it later. The device defaults themselves stay on the
admin settings pages (/settings/language, /settings/design).

/preferences/home arranges the home page tiles: show or hide each one
and move it up or down within its group (module_catalog.HOME_GROUPS).
Plain form buttons instead of drag and drop, so it works the same with
a finger, a mouse or a keyboard and needs no script. Every button press
saves the whole form. Only tiles the account currently sees on the
device are listed; the stored order and visibility of the others (a
module switched off by an admin, an admin-only module) stay as they
are.

The page exists only once an admin account exists: before that there
are no accounts to store anything for. An account that still has to
replace its start password never gets here - the login check sends it
to its account page first (modules/auth/guard.py).
"""
from flask import Blueprint, abort, g, make_response, render_template, request

import auth_store
import i18n
import module_catalog
import settings_store
import themes
import user_prefs
from modules.auth import guard
from security import any_user, csrf_protect, get_csrf_token

preferences_bp = Blueprint("preferences", __name__)

# Form field name -> prefs key.
_FIELDS = {
    "ui_lang": user_prefs.UI_LANG,
    "theme": user_prefs.THEME,
    "receipt_lang": user_prefs.RECEIPT_LANG,
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


def _listed_tiles(tiles):
    """{group: [module dicts]} for the tiles page: every tile the account
    could see on the home page (active, allowed to open), hidden ones
    included, in the account's order."""
    active = module_catalog.active_modules(settings_store.get_settings())
    return {
        group: [module_catalog.MODULES_BY_KEY[key] for key in module_catalog.group_order(group, tiles)
                if active.get(key, True) and guard.can_open(module_catalog.MODULES_BY_KEY[key]["url"])]
        for group in module_catalog.HOME_GROUPS
    }


def _render_tiles(status=200, message=None, success=None):
    tiles = user_prefs.current().get(user_prefs.TILES, {})
    response = make_response(render_template(
        "preferences_home.html",
        listed_tiles=_listed_tiles(tiles),
        hidden=set(tiles.get("hidden", [])),
        csrf_token=get_csrf_token(),
        message=message, success=success,
    ), status)
    response.headers["Cache-Control"] = "no-store"
    return response


def _apply_move(order, move):
    """Swaps the module named in move ("up:<key>" or "down:<key>") with
    its neighbour in order (a list of keys). A move at the edge, or for
    a key not in order, changes nothing."""
    direction, _sep, key = move.partition(":")
    if key not in order or direction not in ("up", "down"):
        return order
    i = order.index(key)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]
    return order


def _merge_order(full, listed):
    """full: every key of a group in the stored order; listed: the keys
    that were on the page, in their new order. The listed keys take the
    places the listed keys had before, so tiles that weren't on the page
    keep their positions."""
    slots = iter(listed)
    return [next(slots) if key in listed else key for key in full]


@preferences_bp.route("/preferences/home", methods=["GET"])
@any_user
def tiles_page():
    _require_account()
    return _render_tiles()


@preferences_bp.route("/ui/preferences/home", methods=["POST"])
@any_user
@csrf_protect
def ui_save_tiles():
    """The form posts the listed tiles of each group in their shown order
    (order_<group>), the ticked ones (show), and either a move
    ("up:<key>"/"down:<key>"), "save" or "reset". A key in the wrong
    group, twice, or unknown is refused with 400 and nothing is
    stored."""
    _require_account()
    current = user_prefs.current().get(user_prefs.TILES, {})
    if request.form.get("action") == "reset":
        new_tiles = {}
    else:
        shown = set(request.form.getlist("show"))
        if not shown <= module_catalog.MODULE_KEYS:
            return _render_tiles(400, i18n.tr("auth.prefs.invalid"), False)
        listed_keys = set()
        order = {}
        for group, members in module_catalog.HOME_GROUPS.items():
            posted = request.form.getlist(f"order_{group}")
            if len(set(posted)) != len(posted) or any(key not in members for key in posted):
                return _render_tiles(400, i18n.tr("auth.prefs.invalid"), False)
            listed_keys.update(posted)
            posted = _apply_move(posted, request.form.get("move", ""))
            merged = _merge_order(module_catalog.group_order(group, current), posted)
            # A group in the default order needs no entry: until the
            # account reorders it, it follows the catalog order.
            if merged != module_catalog.group_order(group):
                order[group] = merged
        hidden = (set(current.get("hidden", [])) - listed_keys) | (listed_keys - shown)
        new_tiles = module_catalog.sanitize_tiles({"order": order, "hidden": sorted(hidden)})
    try:
        prefs = user_prefs.update(g.auth_user["id"], {user_prefs.TILES: new_tiles or None})
    except auth_store.AuthError:
        abort(404)
    g.auth_user = dict(g.auth_user, prefs=prefs)
    return _render_tiles(message=i18n.tr("auth.prefs.saved"), success=True)
