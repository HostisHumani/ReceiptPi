"""
Module: settings - manage print rules (quiet hours, rate limit,
duplicate suppression), weather locations, and the UI language, without
touching config.py or restarting the service.

Two access paths to the same data:
  - JSON API under /settings/api, /settings/print_rules,
    /settings/quiet_hours/rules, /settings/weather/locations - for
    scripts/automations, protected by
    X-Api-Token (unchanged from before). Error messages here stay in
    English regardless of the UI language setting - it's a machine-
    facing API, not something end users read in a browser.
  - Web UI under /settings (page) + /ui/settings/* (forms) - protected
    by CSRF token, localized via i18n.tr() to match the current UI
    language.
"""
import base64
import os
import uuid

from flask import Blueprint, jsonify, render_template, request, send_file

import i18n
import logos
import module_catalog
import secrets_crypto
import settings_store
import themes
from modules.recipes import mealie
from security import (
    MAX_ITEM_LEN,
    MAX_TEXT_LEN,
    MAX_TITLE_LEN,
    csrf_protect,
    get_csrf_token,
    get_json_body,
    require_api_token,
)

settings_bp = Blueprint("settings", __name__)

ALL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]  # 0=Monday..6=Sunday, matches datetime.weekday()


# ---------------------------------------------------------------------------
# Shared validation (used by both the JSON API and the web UI form).
# Returns translation KEYS (not literal messages) so each call site can
# render them in whichever language is appropriate - English for the
# JSON API, the current UI language for the web UI.
# ---------------------------------------------------------------------------

def validate_print_rules_updates(updates):
    """Validates a print_rules update dict (max_jobs_per_hour /
    duplicate_window_seconds only - quiet-hour rules have their own
    schema, see validate_quiet_hours_rule()). Returns None if everything
    is valid, otherwise (translation_key, format_kwargs) for the error.
    IMPORTANT: type(x) is bool instead of isinstance(x, bool) - in
    Python, bool is a subclass of int, so isinstance(True, int) would be
    True and would silently let e.g. {"max_jobs_per_hour": true} through."""
    if "max_jobs_per_hour" in updates:
        value = updates["max_jobs_per_hour"]
        if type(value) is not int or value < 1:
            return "settings.validation.max_jobs", {}

    if "duplicate_window_seconds" in updates:
        value = updates["duplicate_window_seconds"]
        if type(value) is not int or value < 0:
            return "settings.validation.duplicate_window", {}

    if "pending_retention_days" in updates:
        value = updates["pending_retention_days"]
        if type(value) is not int or value < 0:
            return "settings.validation.pending_retention", {}

    if "text_size" in updates and updates["text_size"] not in ("normal", "large"):
        return "settings.validation.text_size", {}

    return None


def validate_quiet_hours_rule(data):
    """Validates a single quiet-hours rule (label/days/start/end/
    enabled). Returns None if valid, otherwise (translation_key,
    format_kwargs). days must be a non-empty list of distinct weekday
    numbers 0(Monday)..6(Sunday) - matches datetime.weekday(), see
    print_queue._active_quiet_hours_rule()."""
    import datetime as _dt

    days = data.get("days")
    if not isinstance(days, list) or not days:
        return "settings.validation.days_missing", {}
    try:
        days_int = [int(d) for d in days]
    except (TypeError, ValueError):
        return "settings.validation.days_invalid", {}
    if any(d < 0 or d > 6 for d in days_int) or len(set(days_int)) != len(days_int):
        return "settings.validation.days_invalid", {}

    for field in ("start", "end"):
        value = data.get(field)
        if not isinstance(value, str):
            return "settings.validation.time_format", {"field": field}
        try:
            _dt.time.fromisoformat(value)
        except ValueError:
            return "settings.validation.time_format", {"field": field}

    if "enabled" in data and type(data["enabled"]) is not bool:
        return "settings.validation.enabled_bool", {}

    return None


def build_quiet_hours_rule(data):
    """Builds a stored rule dict from already-validated input (see
    validate_quiet_hours_rule()). Assigns a fresh id - rules are never
    edited in place, only added/toggled/deleted, so a new id on every
    add is fine and keeps this simple (same pattern as weather
    locations, which also have no in-place edit)."""
    return {
        "id": uuid.uuid4().hex[:12],
        "label": str(data.get("label") or "").strip()[:50],
        "enabled": bool(data.get("enabled", True)),
        "days": sorted({int(d) for d in data["days"]}),
        "start": data["start"],
        "end": data["end"],
    }


def validate_and_build_location(name, lat_raw, lon_raw):
    """Returns (name, lat, lon, error_key, error_kwargs) - error_key is
    None on success."""
    name = str(name or "").strip()[:50]
    if not name:
        return None, None, None, "settings.validation.name_missing", {}
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None, None, None, "settings.validation.latlon_numbers", {}
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None, None, "settings.validation.latlon_range", {}
    return name, lat, lon, None, {}


def validate_ssh_host_entry(host_raw, user_raw):
    """Returns (host, user, error_key, error_kwargs) - error_key is None
    on success. Both host and user empty is valid (that role simply
    isn't configured - the SSH call then fails per-section at print
    time, same as any other unreachable host, nothing special-cased
    there). A host without a user is rejected, since ssh_run() would
    otherwise be called with an empty username."""
    host = str(host_raw or "").strip()
    user = str(user_raw or "").strip()
    if host and not user:
        return None, None, "settings.validation.ssh_user_missing", {}
    return host, user, None, {}


SYSTEM_REPORT_ROLES = ("proxmox", "pbs", "custom")
# Fallback display-name key per role, used when an entry's "name" is
# blank - mirrors modules.system.routes.ROLE_NAME_KEYS, kept as its own
# copy here rather than imported to avoid a cross-module dependency
# between modules/settings and modules/system for one small dict.
SYSTEM_REPORT_ROLE_NAME_KEYS = {
    "proxmox": "settings.system_report.proxmox",
    "pbs": "settings.system_report.pbs",
    "custom": "settings.system_report.custom",
}


def validate_system_report_host_entry(name_raw, role_raw, host_raw, user_raw, command_raw, password_raw=""):
    """Returns (entry_dict, error_key, error_kwargs) - error_key is None
    on success. Unlike validate_ssh_host_entry() above (used for the old
    3 pre-seeded fixed fields, where "not configured yet" was a valid
    state), a NEW entry being added here has no reason to exist without
    a host+user - so both are required. "command" is only kept for
    role="custom" (see modules/system/routes.py._report_sections_for_entry) -
    stripped for the 3 structured roles so a leftover value can't linger
    unused if the user switches an entry's role after typing one in.
    "password_raw" is optional (empty = key-auth host, unchanged
    behavior) - when given, it's encrypted here via secrets_crypto.py
    before ever being assembled into the entry dict, so the plaintext
    never gets anywhere near what update_settings_transaction() writes
    to settings.json."""
    role = str(role_raw or "").strip()
    if role not in SYSTEM_REPORT_ROLES:
        return None, "settings.validation.system_report_role_invalid", {}

    host, user, error_key, error_kwargs = validate_ssh_host_entry(host_raw, user_raw)
    if error_key:
        return None, error_key, error_kwargs
    if not host or not user:
        return None, "settings.validation.system_report_host_missing", {}

    command = str(command_raw or "").strip()[:MAX_TEXT_LEN]
    if role == "custom" and not command:
        return None, "settings.validation.system_report_command_missing", {}
    if role != "custom":
        command = ""

    password = str(password_raw or "").strip()[:MAX_ITEM_LEN]

    entry = {
        "id": uuid.uuid4().hex[:12],
        "name": str(name_raw or "").strip()[:MAX_TITLE_LEN],
        "role": role,
        "host": host,
        "user": user,
        "command": command,
        "password_encrypted": secrets_crypto.encrypt_password(password),
    }
    return entry, None, {}


def validate_github_repo(owner_raw, repo_raw):
    """Returns (owner, repo, error_key, error_kwargs) - error_key is
    None on success."""
    owner = str(owner_raw or "").strip()
    repo = str(repo_raw or "").strip()
    if not owner or not repo:
        return None, None, "settings.validation.github_repo_missing", {}
    return owner, repo, None, {}


def _render_index(message=None, success=None):
    return render_template("settings_index.html", message=message, success=success)


def _render_language(message=None, success=None):
    return render_template(
        "settings_language.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
    )


def _render_design(message=None, success=None):
    return render_template(
        "settings_design.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
    )


def _render_modules(message=None, success=None):
    return render_template(
        "settings_modules.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
    )


def _render_print_rules(message=None, success=None):
    return render_template(
        "settings_print_rules.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
    )


def _render_weather(message=None, success=None):
    return render_template(
        "settings_weather.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
    )


def _render_system_report(message=None, success=None):
    return render_template(
        "settings_system_report.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
    )


def _render_github_watch(message=None, success=None):
    return render_template(
        "settings_github_watch.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
    )


def _render_recipes(message=None, success=None):
    settings = settings_store.get_settings()
    return render_template(
        "settings_recipes.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        recipes=settings["recipes"],
        # Only a yes/no for the template - neither the token nor its
        # ciphertext is ever handed to the page.
        has_token=bool(settings["recipes"]["mealie"].get("token_encrypted")),
    )


def _render_logos(message=None, success=None):
    return render_template(
        "settings_logos.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
        module_keys=logos.MODULE_KEYS,
        has_custom_logo=logos.has_custom_logo,
        has_default_logo=logos.has_default_logo(),
    )


# ---------------------------------------------------------------------------
# Web UI: settings pages. /settings is an overview (tile grid, same pattern
# as the home page) linking to one page per settings area - kept as
# separate pages instead of one long scroll so the settings section stays
# manageable as more areas get added (logos, etc.) instead of growing into
# an ever-longer single page.
# ---------------------------------------------------------------------------

@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    return _render_index()


@settings_bp.route("/settings/language", methods=["GET"])
def settings_language_page():
    return _render_language()


@settings_bp.route("/settings/design", methods=["GET"])
def settings_design_page():
    return _render_design()


@settings_bp.route("/settings/modules", methods=["GET"])
def settings_modules_page():
    return _render_modules()


@settings_bp.route("/settings/print-rules", methods=["GET"])
def settings_print_rules_page():
    return _render_print_rules()


@settings_bp.route("/settings/weather", methods=["GET"])
def settings_weather_page():
    return _render_weather()


@settings_bp.route("/settings/system-report", methods=["GET"])
def settings_system_report_page():
    return _render_system_report()


@settings_bp.route("/settings/github-watch", methods=["GET"])
def settings_github_watch_page():
    return _render_github_watch()


@settings_bp.route("/settings/recipes", methods=["GET"])
def settings_recipes_page():
    return _render_recipes()


@settings_bp.route("/settings/logos", methods=["GET"])
def settings_logos_page():
    return _render_logos()


@settings_bp.route("/settings/logos/file/<slot>", methods=["GET"])
def logos_file(slot):
    """Serves a stored logo image directly, so the settings page can
    show a live <img> preview of what's actually saved instead of just
    a filename. Not access-controlled beyond what the rest of the
    web UI already has (see the no-login-wall note in settings_store.py
    migration comments) - a logo image isn't sensitive."""
    if slot not in logos.MODULE_KEYS and slot != "default":
        return "", 404
    path = os.path.join(logos.LOGOS_DIR, f"{slot}.png")
    if not os.path.isfile(path):
        return "", 404
    return send_file(path, mimetype="image/png")


@settings_bp.route("/ui/settings/print_rules", methods=["POST"])
@csrf_protect
def ui_update_print_rules():
    try:
        updates = {
            "max_jobs_per_hour": int(request.form.get("max_jobs_per_hour", 20)),
            "duplicate_window_seconds": int(request.form.get("duplicate_window_seconds", 60)),
            "pending_retention_days": int(request.form.get("pending_retention_days", 0)),
            "text_size": request.form.get("text_size", "normal"),
        }
    except ValueError:
        message, success = i18n.tr("settings.print_rules.invalid_numbers"), False
    else:
        error = validate_print_rules_updates(updates)
        if error:
            key, kwargs = error
            message, success = i18n.tr(key, **kwargs), False
        else:
            settings_store.update_section("print_rules", updates)
            message, success = i18n.tr("settings.print_rules.saved"), True

    return _render_print_rules(message, success)


@settings_bp.route("/ui/settings/quiet_hours/add", methods=["POST"])
@csrf_protect
def ui_add_quiet_hours_rule():
    data = {
        "label": request.form.get("label", ""),
        "days": request.form.getlist("days"),
        "start": request.form.get("start", "22:00"),
        "end": request.form.get("end", "07:00"),
        "enabled": request.form.get("enabled") == "on",
    }
    error = validate_quiet_hours_rule(data)
    if error:
        key, kwargs = error
        message, success = i18n.tr(key, **kwargs), False
    else:
        rule = build_quiet_hours_rule(data)

        def _mutate(settings):
            settings["print_rules"].setdefault("quiet_hours_rules", [])
            settings["print_rules"]["quiet_hours_rules"].append(rule)

        settings_store.update_settings_transaction(_mutate)
        message, success = i18n.tr("settings.quiet_hours.saved"), True

    return _render_print_rules(message, success)


@settings_bp.route("/ui/settings/quiet_hours/toggle", methods=["POST"])
@csrf_protect
def ui_toggle_quiet_hours_rule():
    rule_id = request.form.get("id", "")
    found = {"ok": False}

    def _mutate(settings):
        for rule in settings["print_rules"].get("quiet_hours_rules", []):
            if rule["id"] == rule_id:
                rule["enabled"] = not rule.get("enabled", True)
                found["ok"] = True
                break

    settings_store.update_settings_transaction(_mutate)
    if found["ok"]:
        message, success = i18n.tr("settings.quiet_hours.toggled"), True
    else:
        message, success = i18n.tr("settings.quiet_hours.not_found", id=rule_id), False

    return _render_print_rules(message, success)


@settings_bp.route("/ui/settings/quiet_hours/delete", methods=["POST"])
@csrf_protect
def ui_delete_quiet_hours_rule():
    rule_id = request.form.get("id", "")
    found = {"ok": False}

    def _mutate(settings):
        rules = settings["print_rules"].get("quiet_hours_rules", [])
        remaining = [r for r in rules if r["id"] != rule_id]
        found["ok"] = len(remaining) != len(rules)
        settings["print_rules"]["quiet_hours_rules"] = remaining

    settings_store.update_settings_transaction(_mutate)
    if found["ok"]:
        message, success = i18n.tr("settings.quiet_hours.deleted"), True
    else:
        message, success = i18n.tr("settings.quiet_hours.not_found", id=rule_id), False

    return _render_print_rules(message, success)


@settings_bp.route("/ui/settings/weather/provider", methods=["POST"])
@csrf_protect
def ui_set_weather_report_provider():
    provider = request.form.get("report_provider", "")
    if provider not in ("dwd", "open-meteo"):
        return _render_weather(i18n.tr("settings.weather_provider.unsupported", provider=provider), False)

    def _mutate(settings):
        settings["weather"]["report_provider"] = provider

    settings_store.update_settings_transaction(_mutate)
    return _render_weather(i18n.tr("settings.weather_provider.updated"), True)


@settings_bp.route("/ui/settings/weather/storm-warning", methods=["POST"])
@csrf_protect
def ui_set_storm_warning():
    provider = request.form.get("provider", "dwd")
    if provider not in ("dwd", "meteoalarm", "nws"):
        provider = "dwd"

    def _mutate(settings):
        sw = settings["weather"]["storm_warning"]
        sw["enabled"] = request.form.get("enabled") == "on"
        sw["provider"] = provider
        sw["dwd_state"] = (request.form.get("dwd_state") or "").strip()
        sw["dwd_region_name"] = (request.form.get("dwd_region_name") or "").strip()
        sw["meteoalarm_country"] = (request.form.get("meteoalarm_country") or "").strip()
        sw["meteoalarm_region"] = (request.form.get("meteoalarm_region") or "").strip()
        sw["ignore_quiet_hours"] = request.form.get("ignore_quiet_hours") == "on"

    settings_store.update_settings_transaction(_mutate)
    return _render_weather(i18n.tr("settings.storm_warning.updated"), True)


@settings_bp.route("/ui/settings/weather/add", methods=["POST"])
@csrf_protect
def ui_add_weather_location():
    name, lat, lon, error_key, error_kwargs = validate_and_build_location(
        request.form.get("name"), request.form.get("lat"), request.form.get("lon")
    )
    set_default = request.form.get("set_default") == "on"

    if error_key:
        message, success = i18n.tr(error_key, **error_kwargs), False
    else:
        def _mutate(settings):
            settings["weather"]["locations"][name] = {"lat": lat, "lon": lon}
            if set_default:
                settings["weather"]["default_location"] = name

        settings_store.update_settings_transaction(_mutate)
        message, success = i18n.tr("settings.weather_locations.saved", name=name), True

    return _render_weather(message, success)


@settings_bp.route("/ui/settings/weather/delete", methods=["POST"])
@csrf_protect
def ui_delete_weather_location():
    name = request.form.get("name", "")
    current = settings_store.get_settings()
    locations = current["weather"]["locations"]

    if name not in locations:
        message, success = i18n.tr("settings.weather_locations.not_found", name=name), False
    elif len(locations) == 1:
        message, success = i18n.tr("settings.weather_locations.last_cannot_delete"), False
    else:
        def _mutate(settings):
            del settings["weather"]["locations"][name]
            if settings["weather"]["default_location"] == name:
                settings["weather"]["default_location"] = next(iter(settings["weather"]["locations"]))

        settings_store.update_settings_transaction(_mutate)
        message, success = i18n.tr("settings.weather_locations.deleted", name=name), True

    return _render_weather(message, success)


@settings_bp.route("/ui/settings/weather/default", methods=["POST"])
@csrf_protect
def ui_set_default_weather_location():
    name = request.form.get("name", "")
    current = settings_store.get_settings()

    if name not in current["weather"]["locations"]:
        message, success = i18n.tr("settings.weather_locations.not_found", name=name), False
    else:
        def _mutate(settings):
            settings["weather"]["default_location"] = name

        settings_store.update_settings_transaction(_mutate)
        message, success = i18n.tr("settings.weather_locations.default_set", name=name), True

    return _render_weather(message, success)


@settings_bp.route("/ui/settings/language", methods=["POST"])
@csrf_protect
def ui_set_language():
    """Sets the UI language. A single, shared setting (not per-session) -
    this is a single-user home appliance, not a multi-user app."""
    lang = request.form.get("language", "")
    if lang not in i18n.SUPPORTED_LANGUAGES:
        # Deliberately not translated via the (about to be rejected) new
        # language - stays in whatever the CURRENT language still is.
        message, success = i18n.tr("settings.language.unsupported", lang=lang), False
    else:
        def _mutate(settings):
            settings["language"] = lang

        settings_store.update_settings_transaction(_mutate)
        # Render in the NEW language, since the change already applied.
        message, success = i18n.t("settings.language.updated", lang), True

    return _render_language(message, success)


@settings_bp.route("/ui/settings/design", methods=["POST"])
@csrf_protect
def ui_set_design():
    """Sets the UI theme. Same single-shared-setting pattern as the
    language switcher above - see themes.py for the valid keys."""
    theme = request.form.get("theme", "")
    if theme not in themes.SUPPORTED_THEMES:
        message, success = i18n.tr("settings.design.unsupported", theme=theme), False
    else:
        def _mutate(settings):
            settings["theme"] = theme

        settings_store.update_settings_transaction(_mutate)
        message, success = i18n.tr("settings.design.updated"), True

    return _render_design(message, success)


@settings_bp.route("/ui/settings/modules", methods=["POST"])
@csrf_protect
def ui_set_modules():
    """Turns individual home-page modules on/off. An unchecked box
    simply doesn't appear in the form data at all (standard HTML
    checkbox behavior) - so absence means False, not "leave
    unchanged"."""
    def _mutate(settings):
        enabled = settings.setdefault("enabled_modules", {})
        for m in module_catalog.MODULES:
            enabled[m["key"]] = request.form.get(f"enabled_{m['key']}") == "on"

    settings_store.update_settings_transaction(_mutate)
    message, success = i18n.tr("settings.modules.updated"), True
    return _render_modules(message, success)


@settings_bp.route("/ui/settings/system_report/add", methods=["POST"])
@csrf_protect
def ui_add_system_report_host():
    entry, error_key, error_kwargs = validate_system_report_host_entry(
        request.form.get("name"),
        request.form.get("role"),
        request.form.get("host"),
        request.form.get("user"),
        request.form.get("command"),
        request.form.get("password"),
    )
    if error_key:
        role = request.form.get("role", "")
        return _render_system_report(i18n.tr(error_key, role=role, **error_kwargs), False)

    def _mutate(settings):
        settings["system_report"].setdefault("hosts", [])
        settings["system_report"]["hosts"].append(entry)

    settings_store.update_settings_transaction(_mutate)
    return _render_system_report(i18n.tr("settings.system_report.saved"), True)


@settings_bp.route("/ui/settings/system_report/delete", methods=["POST"])
@csrf_protect
def ui_delete_system_report_host():
    host_id = request.form.get("id", "")
    removed = {"entry": None}

    def _mutate(settings):
        hosts = settings["system_report"].get("hosts", [])
        for h in hosts:
            if h.get("id") == host_id:
                removed["entry"] = h
                break
        settings["system_report"]["hosts"] = [h for h in hosts if h.get("id") != host_id]

    settings_store.update_settings_transaction(_mutate)
    if removed["entry"]:
        display_name = removed["entry"].get("name") or i18n.tr(
            SYSTEM_REPORT_ROLE_NAME_KEYS.get(removed["entry"].get("role"), "settings.system_report.proxmox")
        )
        message, success = i18n.tr("settings.system_report.deleted", name=display_name), True
    else:
        message, success = i18n.tr("settings.system_report.not_found"), False
    return _render_system_report(message, success)


RECIPE_PROVIDERS = ("off", "mealie")


@settings_bp.route("/ui/settings/recipes", methods=["POST"])
@csrf_protect
def ui_save_recipes():
    """Saves provider + Mealie URL/token. An empty token field means
    "keep the stored token" - the page never shows the token again
    (only a "saved" badge), so re-submitting the form to change just
    the URL must not wipe it. Switching the provider to "off" keeps
    the stored URL/token, so switching back doesn't mean re-entering
    them; "remove token" is its own explicit action below."""
    provider = request.form.get("provider", "off")
    if provider not in RECIPE_PROVIDERS:
        return _render_recipes(i18n.tr("settings.recipes.provider_invalid"), False)

    raw_url = request.form.get("base_url", "").strip()
    base_url = mealie.normalize_base_url(raw_url) if raw_url else ""
    if base_url is None:
        return _render_recipes(i18n.tr("settings.recipes.url_invalid"), False)
    token = request.form.get("token", "").strip()[:MAX_TEXT_LEN]

    has_stored_token = bool(settings_store.get_settings()["recipes"]["mealie"].get("token_encrypted"))
    if provider == "mealie" and (not base_url or not (token or has_stored_token)):
        return _render_recipes(i18n.tr("settings.recipes.missing_fields"), False)

    # Encrypted before the transaction, same as the system report host
    # passwords - the plaintext never gets near what's written to disk.
    token_encrypted = secrets_crypto.encrypt_password(token) if token else None

    def _mutate(settings):
        section = settings["recipes"]
        section["provider"] = provider
        section["mealie"]["base_url"] = base_url
        if token_encrypted:
            section["mealie"]["token_encrypted"] = token_encrypted

    settings_store.update_settings_transaction(_mutate)
    return _render_recipes(i18n.tr("settings.recipes.saved"), True)


@settings_bp.route("/ui/settings/recipes/test", methods=["POST"])
@csrf_protect
def ui_test_recipes_connection():
    """Called via fetch() from the settings page, so the typed-in
    (unsaved) token stays in its field instead of being lost to a page
    reload. Tests exactly what's in the form; an empty token field
    falls back to the stored one (the normal case when just re-testing
    an existing setup). Never saves anything."""
    base_url = mealie.normalize_base_url(request.form.get("base_url", ""))
    if base_url is None:
        return jsonify({"ok": False, "message": i18n.tr("settings.recipes.url_invalid")}), 200
    token = request.form.get("token", "").strip()[:MAX_TEXT_LEN]
    if not token:
        token = secrets_crypto.decrypt_password(
            settings_store.get_settings()["recipes"]["mealie"].get("token_encrypted")
        ) or ""
    if not token:
        return jsonify({"ok": False, "message": i18n.tr("settings.recipes.token_missing")}), 200
    try:
        info = mealie.check_connection(base_url, token)
    except mealie.MealieError as e:
        return jsonify({"ok": False, "message": i18n.tr(f"recipes.error.{e.kind}")}), 200
    return jsonify({
        "ok": True,
        "message": i18n.tr("settings.recipes.test_ok", version=info["version"], user=info["user"]),
    }), 200


@settings_bp.route("/ui/settings/recipes/token/delete", methods=["POST"])
@csrf_protect
def ui_delete_recipes_token():
    def _mutate(settings):
        settings["recipes"]["mealie"]["token_encrypted"] = ""

    settings_store.update_settings_transaction(_mutate)
    return _render_recipes(i18n.tr("settings.recipes.token_deleted"), True)


@settings_bp.route("/ui/settings/github_watch/add", methods=["POST"])
@csrf_protect
def ui_add_github_repo():
    owner, repo, error_key, error_kwargs = validate_github_repo(
        request.form.get("owner"), request.form.get("repo")
    )
    if error_key:
        message, success = i18n.tr(error_key, **error_kwargs), False
    else:
        def _mutate(settings):
            settings["github_watch"].setdefault("repos", [])
            already_present = any(
                r["owner"] == owner and r["repo"] == repo for r in settings["github_watch"]["repos"]
            )
            if not already_present:
                settings["github_watch"]["repos"].append({"owner": owner, "repo": repo})

        settings_store.update_settings_transaction(_mutate)
        message, success = i18n.tr("settings.github_watch.saved", owner=owner, repo=repo), True

    return _render_github_watch(message, success)


@settings_bp.route("/ui/settings/github_watch/delete", methods=["POST"])
@csrf_protect
def ui_delete_github_repo():
    owner = request.form.get("owner", "")
    repo = request.form.get("repo", "")
    found = {"ok": False}

    def _mutate(settings):
        repos = settings["github_watch"].get("repos", [])
        remaining = [r for r in repos if not (r["owner"] == owner and r["repo"] == repo)]
        found["ok"] = len(remaining) != len(repos)
        settings["github_watch"]["repos"] = remaining

    settings_store.update_settings_transaction(_mutate)
    if found["ok"]:
        message, success = i18n.tr("settings.github_watch.deleted", owner=owner, repo=repo), True
    else:
        message, success = i18n.tr("settings.github_watch.not_found", owner=owner, repo=repo), False

    return _render_github_watch(message, success)


@settings_bp.route("/ui/settings/logos/toggle", methods=["POST"])
@csrf_protect
def ui_toggle_logos_global():
    def _mutate(settings):
        settings["logos"]["enabled"] = not settings["logos"].get("enabled", False)

    settings_store.update_settings_transaction(_mutate)
    return _render_logos(i18n.tr("settings.logos.saved"), True)


@settings_bp.route("/ui/settings/logos/module_toggle", methods=["POST"])
@csrf_protect
def ui_toggle_logos_module():
    module = request.form.get("module", "")
    if module not in logos.MODULE_KEYS:
        return _render_logos(i18n.tr("settings.logos.unknown_module", module=module), False)

    def _mutate(settings):
        settings["logos"].setdefault("modules", {}).setdefault(module, {"enabled": False})
        settings["logos"]["modules"][module]["enabled"] = not settings["logos"]["modules"][module].get(
            "enabled", False,
        )

    settings_store.update_settings_transaction(_mutate)
    return _render_logos(i18n.tr("settings.logos.saved"), True)


@settings_bp.route("/ui/settings/logos/upload", methods=["POST"])
@csrf_protect
def ui_upload_logo():
    slot = request.form.get("slot", "")
    if slot not in logos.MODULE_KEYS and slot != "default":
        return _render_logos(i18n.tr("settings.logos.unknown_module", module=slot), False)

    uploaded = request.files.get("logo")
    if not uploaded or uploaded.filename == "":
        return _render_logos(i18n.tr("images.no_file_selected"), False)

    ok, detail = logos.save_logo(slot, uploaded.read())
    if ok:
        return _render_logos(i18n.tr("settings.logos.upload_saved"), True)
    return _render_logos(i18n.tr("print.error_prefix") + detail, False)


@settings_bp.route("/ui/settings/logos/delete", methods=["POST"])
@csrf_protect
def ui_delete_logo():
    slot = request.form.get("slot", "")
    if slot not in logos.MODULE_KEYS and slot != "default":
        return _render_logos(i18n.tr("settings.logos.unknown_module", module=slot), False)

    if logos.delete_logo(slot):
        return _render_logos(i18n.tr("settings.logos.upload_deleted"), True)
    return _render_logos(i18n.tr("settings.logos.nothing_to_delete"), False)


# ---------------------------------------------------------------------------
# JSON API (for scripts/automations, protected by X-Api-Token)
# ---------------------------------------------------------------------------

@settings_bp.route("/settings/api", methods=["GET"])
@require_api_token
def get_all_settings():
    return jsonify(settings_store.get_settings()), 200


@settings_bp.route("/settings/print_rules", methods=["POST"])
@require_api_token
def update_print_rules():
    """
    Expects JSON with one or more of the following fields:
    { "max_jobs_per_hour": int, "duplicate_window_seconds": int, "pending_retention_days": int }
    Only the fields provided are changed, the rest stay as they were.
    Quiet-hour rules are managed separately, see
    /settings/quiet_hours/rules below - a single window doesn't fit this
    endpoint's "just update these fields" shape anymore now that there
    can be several independent rules.
    """
    data, err = get_json_body()
    if err:
        return err

    allowed_fields = {"max_jobs_per_hour", "duplicate_window_seconds", "pending_retention_days"}
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify({"status": "error", "detail": "no valid fields provided"}), 400

    error = validate_print_rules_updates(updates)
    if error:
        key, kwargs = error
        return jsonify({"status": "error", "detail": i18n.t(key, "en", **kwargs)}), 400

    result = settings_store.update_section("print_rules", updates)
    return jsonify({"status": "saved", "print_rules": result["print_rules"]}), 200


@settings_bp.route("/settings/quiet_hours/rules", methods=["GET"])
@require_api_token
def list_quiet_hours_rules():
    return jsonify(settings_store.get_settings()["print_rules"]["quiet_hours_rules"]), 200


@settings_bp.route("/settings/quiet_hours/rules", methods=["POST"])
@require_api_token
def add_quiet_hours_rule():
    """
    Expects JSON: { "label": "Wochenende" (optional), "days": [5, 6]
    (0=Monday..6=Sunday), "start": "HH:MM", "end": "HH:MM",
    "enabled": true (optional, default true) }
    """
    data, err = get_json_body()
    if err:
        return err

    error = validate_quiet_hours_rule(data)
    if error:
        key, kwargs = error
        return jsonify({"status": "error", "detail": i18n.t(key, "en", **kwargs)}), 400

    rule = build_quiet_hours_rule(data)

    def _mutate(settings):
        settings["print_rules"].setdefault("quiet_hours_rules", [])
        settings["print_rules"]["quiet_hours_rules"].append(rule)

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "saved", "quiet_hours_rules": result["print_rules"]["quiet_hours_rules"]}), 200


@settings_bp.route("/settings/quiet_hours/rules/<rule_id>/toggle", methods=["POST"])
@require_api_token
def toggle_quiet_hours_rule(rule_id):
    found = {"ok": False}

    def _mutate(settings):
        for rule in settings["print_rules"].get("quiet_hours_rules", []):
            if rule["id"] == rule_id:
                rule["enabled"] = not rule.get("enabled", True)
                found["ok"] = True
                break

    result = settings_store.update_settings_transaction(_mutate)
    if not found["ok"]:
        return jsonify({"status": "error", "detail": f"Rule '{rule_id}' not found"}), 404
    return jsonify({"status": "saved", "quiet_hours_rules": result["print_rules"]["quiet_hours_rules"]}), 200


@settings_bp.route("/settings/quiet_hours/rules/<rule_id>", methods=["DELETE"])
@require_api_token
def delete_quiet_hours_rule(rule_id):
    found = {"ok": False}

    def _mutate(settings):
        rules = settings["print_rules"].get("quiet_hours_rules", [])
        remaining = [r for r in rules if r["id"] != rule_id]
        found["ok"] = len(remaining) != len(rules)
        settings["print_rules"]["quiet_hours_rules"] = remaining

    result = settings_store.update_settings_transaction(_mutate)
    if not found["ok"]:
        return jsonify({"status": "error", "detail": f"Rule '{rule_id}' not found"}), 404
    return jsonify({"status": "deleted", "quiet_hours_rules": result["print_rules"]["quiet_hours_rules"]}), 200


@settings_bp.route("/settings/weather/locations", methods=["GET"])
@require_api_token
def list_weather_locations():
    return jsonify(settings_store.get_settings()["weather"]), 200


@settings_bp.route("/settings/weather/locations", methods=["POST"])
@require_api_token
def add_weather_location():
    """
    Expects JSON: { "name": "Berlin", "lat": 52.52, "lon": 13.40,
                     "set_default": false (optional) }
    """
    data, err = get_json_body()
    if err:
        return err

    name, lat, lon, error_key, error_kwargs = validate_and_build_location(
        data.get("name"), data.get("lat"), data.get("lon")
    )
    if error_key:
        return jsonify({"status": "error", "detail": i18n.t(error_key, "en", **error_kwargs)}), 400

    def _mutate(settings):
        settings["weather"]["locations"][name] = {"lat": lat, "lon": lon}
        if data.get("set_default"):
            settings["weather"]["default_location"] = name

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "saved", "weather": result["weather"]}), 200


@settings_bp.route("/settings/weather/locations/<name>", methods=["DELETE"])
@require_api_token
def delete_weather_location(name):
    current = settings_store.get_settings()
    locations = current["weather"]["locations"]
    if name not in locations:
        return jsonify({"status": "error", "detail": f"Location '{name}' not found"}), 404
    if len(locations) == 1:
        return jsonify({"status": "error", "detail": "the last remaining location can't be deleted"}), 400

    def _mutate(settings):
        del settings["weather"]["locations"][name]
        if settings["weather"]["default_location"] == name:
            settings["weather"]["default_location"] = next(iter(settings["weather"]["locations"]))

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "deleted", "weather": result["weather"]}), 200


def _redact_system_report(system_report):
    """Returns a copy of the system_report dict safe to hand back over
    the JSON API: drops the Fernet-encrypted "password_encrypted" blob
    (see secrets_crypto.py) in favor of a plain "has_password" flag, so
    an X-Api-Token holder never receives the ciphertext at all - only
    whether a password is set. The ciphertext is useless without the
    Pi's own secret.key anyway, but there's no reason to hand it out
    over the network at all when a boolean says everything a caller
    needs to know."""
    redacted = dict(system_report)
    redacted["hosts"] = [
        {**{k: v for k, v in h.items() if k != "password_encrypted"},
         "has_password": bool(h.get("password_encrypted"))}
        for h in system_report.get("hosts", [])
    ]
    return redacted


@settings_bp.route("/settings/system_report", methods=["GET"])
@require_api_token
def get_system_report_settings():
    return jsonify(_redact_system_report(settings_store.get_settings()["system_report"])), 200


@settings_bp.route("/settings/system_report", methods=["POST"])
@require_api_token
def add_system_report_host():
    """
    Expects JSON: { "name": "...", "role": "proxmox"|"pbs"|"custom",
                     "host": "...", "user": "...", "command": "..." (only
                     used/required for role="custom"), "password": "..."
                     (optional - omit/empty for key-auth, see
                     secrets_crypto.py for how it's stored) }
    Adds one new host to the list - same shape/semantics as the web UI's
    "Server hinzufügen" form (see ui_add_system_report_host above). This
    used to PATCH one of 3 fixed role keys instead; now that hosts are a
    free-form list, "add one entry" is the operation that maps onto a
    single JSON object the same way add_weather_location() does.
    """
    data, err = get_json_body()
    if err:
        return err

    entry, error_key, error_kwargs = validate_system_report_host_entry(
        data.get("name"), data.get("role"), data.get("host"), data.get("user"), data.get("command"),
        data.get("password"),
    )
    if error_key:
        return jsonify({"status": "error", "detail": i18n.t(error_key, "en", role=data.get("role", ""), **error_kwargs)}), 400

    def _mutate(settings):
        settings["system_report"].setdefault("hosts", [])
        settings["system_report"]["hosts"].append(entry)

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "saved", "system_report": _redact_system_report(result["system_report"])}), 200


@settings_bp.route("/settings/system_report/<host_id>", methods=["DELETE"])
@require_api_token
def delete_system_report_host(host_id):
    current = settings_store.get_settings()["system_report"]["hosts"]
    if not any(h.get("id") == host_id for h in current):
        return jsonify({"status": "error", "detail": "host not found"}), 404

    def _mutate(settings):
        settings["system_report"]["hosts"] = [
            h for h in settings["system_report"]["hosts"] if h.get("id") != host_id
        ]

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "deleted", "system_report": _redact_system_report(result["system_report"])}), 200


@settings_bp.route("/settings/github_watch/repos", methods=["GET"])
@require_api_token
def list_github_repos():
    return jsonify(settings_store.get_settings()["github_watch"]["repos"]), 200


@settings_bp.route("/settings/github_watch/repos", methods=["POST"])
@require_api_token
def add_github_repo():
    """Expects JSON: { "owner": "HostisHumani", "repo": "ReceiptPi" }"""
    data, err = get_json_body()
    if err:
        return err

    owner, repo, error_key, error_kwargs = validate_github_repo(data.get("owner"), data.get("repo"))
    if error_key:
        return jsonify({"status": "error", "detail": i18n.t(error_key, "en", **error_kwargs)}), 400

    def _mutate(settings):
        settings["github_watch"].setdefault("repos", [])
        already_present = any(
            r["owner"] == owner and r["repo"] == repo for r in settings["github_watch"]["repos"]
        )
        if not already_present:
            settings["github_watch"]["repos"].append({"owner": owner, "repo": repo})

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "saved", "repos": result["github_watch"]["repos"]}), 200


@settings_bp.route("/settings/github_watch/repos/<owner>/<repo>", methods=["DELETE"])
@require_api_token
def delete_github_repo(owner, repo):
    found = {"ok": False}

    def _mutate(settings):
        repos = settings["github_watch"].get("repos", [])
        remaining = [r for r in repos if not (r["owner"] == owner and r["repo"] == repo)]
        found["ok"] = len(remaining) != len(repos)
        settings["github_watch"]["repos"] = remaining

    result = settings_store.update_settings_transaction(_mutate)
    if not found["ok"]:
        return jsonify({"status": "error", "detail": f"Repo '{owner}/{repo}' not found"}), 404
    return jsonify({"status": "deleted", "repos": result["github_watch"]["repos"]}), 200


@settings_bp.route("/settings/logos/config", methods=["GET"])
@require_api_token
def get_logos_settings():
    result = dict(settings_store.get_settings()["logos"])
    result["has_default_logo"] = logos.has_default_logo()
    result["custom_logos"] = {key: logos.has_custom_logo(key) for key in logos.MODULE_KEYS}
    return jsonify(result), 200


@settings_bp.route("/settings/logos/config", methods=["POST"])
@require_api_token
def update_logos_settings():
    """
    Expects JSON with one or more fields:
    { "enabled": bool, "modules": { "shopping": {"enabled": bool}, ... } }
    Only the fields/modules provided are changed, the rest keep their
    current value. Logo image uploads are a separate endpoint, see
    /settings/logos/upload/<slot> below.
    """
    data, err = get_json_body()
    if err:
        return err

    if "enabled" in data and type(data["enabled"]) is not bool:
        return jsonify({"status": "error", "detail": "enabled must be true or false"}), 400

    modules_update = data.get("modules", {})
    if modules_update:
        if not isinstance(modules_update, dict) or not set(modules_update).issubset(logos.MODULE_KEYS):
            return jsonify({"status": "error", "detail": f"modules keys must be one of {logos.MODULE_KEYS}"}), 400
        for module_settings in modules_update.values():
            if not isinstance(module_settings, dict) or type(module_settings.get("enabled")) is not bool:
                return jsonify({"status": "error", "detail": "each module needs {\"enabled\": bool}"}), 400

    def _mutate(settings):
        if "enabled" in data:
            settings["logos"]["enabled"] = data["enabled"]
        for module, module_settings in modules_update.items():
            settings["logos"].setdefault("modules", {}).setdefault(module, {"enabled": False})
            settings["logos"]["modules"][module]["enabled"] = module_settings["enabled"]

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "saved", "logos": result["logos"]}), 200


@settings_bp.route("/settings/logos/upload/<slot>", methods=["POST"])
@require_api_token
def upload_logo(slot):
    """Expects JSON: { "logo_base64": "..." }. slot is one of the
    module keys, or "default" for the shared fallback logo."""
    if slot not in logos.MODULE_KEYS and slot != "default":
        return jsonify({"status": "error", "detail": f"slot must be one of {logos.MODULE_KEYS} or 'default'"}), 400

    data, err = get_json_body()
    if err:
        return err
    logo_b64 = data.get("logo_base64")
    if not logo_b64:
        return jsonify({"status": "error", "detail": "logo_base64 is missing"}), 400

    try:
        file_bytes = base64.b64decode(logo_b64, validate=True)
    except Exception as e:
        return jsonify({"status": "error", "detail": f"Invalid base64: {e}"}), 400

    ok, detail = logos.save_logo(slot, file_bytes)
    if ok:
        return jsonify({"status": "saved"}), 200
    return jsonify({"status": "error", "detail": detail}), 400


@settings_bp.route("/settings/logos/upload/<slot>", methods=["DELETE"])
@require_api_token
def delete_logo_api(slot):
    if slot not in logos.MODULE_KEYS and slot != "default":
        return jsonify({"status": "error", "detail": f"slot must be one of {logos.MODULE_KEYS} or 'default'"}), 400
    if logos.delete_logo(slot):
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "error", "detail": f"No logo stored for '{slot}'"}), 404
