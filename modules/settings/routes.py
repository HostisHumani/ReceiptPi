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
import uuid

from flask import Blueprint, jsonify, render_template, request

import i18n
import settings_store
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token

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
    try:
        updates = {
            "max_jobs_per_hour": int(request.form.get("max_jobs_per_hour", 20)),
            "duplicate_window_seconds": int(request.form.get("duplicate_window_seconds", 60)),
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

    return _render_settings(message, success)


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

    return _render_settings(message, success)


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

    return _render_settings(message, success)


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
    { "max_jobs_per_hour": int, "duplicate_window_seconds": int }
    Only the fields provided are changed, the rest stay as they were.
    Quiet-hour rules are managed separately, see
    /settings/quiet_hours/rules below - a single window doesn't fit this
    endpoint's "just update these fields" shape anymore now that there
    can be several independent rules.
    """
    data, err = get_json_body()
    if err:
        return err

    allowed_fields = {"max_jobs_per_hour", "duplicate_window_seconds"}
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
