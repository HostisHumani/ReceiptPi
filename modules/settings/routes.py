"""
Module: settings - manage print rules (quiet hours, rate limit,
duplicate suppression), weather locations, and the UI language, without
touching config.py or restarting the service.

Two access paths to the same data:
  - JSON API under /settings/api, /settings/print_rules,
    /settings/weather/locations - for scripts/automations, protected by
    X-Api-Token (unchanged from before). Error messages here stay in
    English regardless of the UI language setting - it's a machine-
    facing API, not something end users read in a browser.
  - Web UI under /settings (page) + /ui/settings/* (forms) - protected
    by CSRF token, localized via i18n.tr() to match the current UI
    language.
"""
from flask import Blueprint, jsonify, render_template, request

import i18n
import settings_store
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token

settings_bp = Blueprint("settings", __name__)


# ---------------------------------------------------------------------------
# Shared validation (used by both the JSON API and the web UI form).
# Returns translation KEYS (not literal messages) so each call site can
# render them in whichever language is appropriate - English for the
# JSON API, the current UI language for the web UI.
# ---------------------------------------------------------------------------

def validate_print_rules_updates(updates):
    """Validates a print_rules update dict. Returns None if everything is
    valid, otherwise (translation_key, format_kwargs) for the error.
    IMPORTANT: type(x) is bool instead of isinstance(x, bool) - in
    Python, bool is a subclass of int, so isinstance(True, int) would be
    True and would silently let e.g. {"max_jobs_per_hour": true} through."""
    import datetime as _dt

    if "quiet_hours_enabled" in updates and type(updates["quiet_hours_enabled"]) is not bool:
        return "settings.validation.quiet_hours_enabled_bool", {}

    for field in ("quiet_hours_start", "quiet_hours_end"):
        if field in updates:
            if not isinstance(updates[field], str):
                return "settings.validation.time_format", {"field": field}
            try:
                _dt.time.fromisoformat(updates[field])
            except ValueError:
                return "settings.validation.time_format", {"field": field}

    if "max_jobs_per_hour" in updates:
        value = updates["max_jobs_per_hour"]
        if type(value) is not int or value < 1:
            return "settings.validation.max_jobs", {}

    if "duplicate_window_seconds" in updates:
        value = updates["duplicate_window_seconds"]
        if type(value) is not int or value < 0:
            return "settings.validation.duplicate_window", {}

    return None


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


def _render_settings(message=None, success=None):
    return render_template(
        "settings.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
    )


# ---------------------------------------------------------------------------
# Web UI: settings page
# ---------------------------------------------------------------------------

@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    return _render_settings()


@settings_bp.route("/ui/settings/print_rules", methods=["POST"])
@csrf_protect
def ui_update_print_rules():
    updates = {
        "quiet_hours_enabled": request.form.get("quiet_hours_enabled") == "on",
        "quiet_hours_start": request.form.get("quiet_hours_start", "22:00"),
        "quiet_hours_end": request.form.get("quiet_hours_end", "07:00"),
    }
    try:
        updates["max_jobs_per_hour"] = int(request.form.get("max_jobs_per_hour", 20))
        updates["duplicate_window_seconds"] = int(request.form.get("duplicate_window_seconds", 60))
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

    return _render_settings(message, success)


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

    return _render_settings(message, success)


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

    return _render_settings(message, success)


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

    return _render_settings(message, success)


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

    return _render_settings(message, success)


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
    { "quiet_hours_enabled": bool, "quiet_hours_start": "HH:MM",
      "quiet_hours_end": "HH:MM", "max_jobs_per_hour": int,
      "duplicate_window_seconds": int }
    Only the fields provided are changed, the rest stay as they were.
    """
    data, err = get_json_body()
    if err:
        return err

    allowed_fields = {
        "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end",
        "max_jobs_per_hour", "duplicate_window_seconds",
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify({"status": "error", "detail": "no valid fields provided"}), 400

    error = validate_print_rules_updates(updates)
    if error:
        key, kwargs = error
        return jsonify({"status": "error", "detail": i18n.t(key, "en", **kwargs)}), 400

    result = settings_store.update_section("print_rules", updates)
    return jsonify({"status": "saved", "print_rules": result["print_rules"]}), 200


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
