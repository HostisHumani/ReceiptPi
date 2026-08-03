"""Runtime settings module for print rules and weather locations.

The browser interface uses CSRF-protected forms. The JSON API is intended for
scripts and automation and is protected by the configured API token."""
from flask import Blueprint, jsonify, render_template, request

import settings_store
from security import require_api_token, csrf_protect, get_csrf_token, get_json_body

settings_bp = Blueprint("settings", __name__)


# ---------------------------------------------------------------------------
# Shared validation for the JSON API and web forms.
# ---------------------------------------------------------------------------

def validate_print_rules_updates(updates):
    """Validate a partial print_rules update.

    Returns None when valid, otherwise an error message. Exact type checks are used
    because bool is a subclass of int in Python."""
    import datetime as _dt

    if "quiet_hours_enabled" in updates and type(updates["quiet_hours_enabled"]) is not bool:
        return "quiet_hours_enabled muss true oder false sein"

    for field in ("quiet_hours_start", "quiet_hours_end"):
        if field in updates:
            if not isinstance(updates[field], str):
                return f"{field} muss HH:MM sein"
            try:
                _dt.time.fromisoformat(updates[field])
            except ValueError:
                return f"{field} muss HH:MM sein"

    if "max_jobs_per_hour" in updates:
        value = updates["max_jobs_per_hour"]
        if type(value) is not int or value < 1:
            return "max_jobs_per_hour muss eine positive Ganzzahl sein"

    if "duplicate_window_seconds" in updates:
        value = updates["duplicate_window_seconds"]
        if type(value) is not int or value < 0:
            return "duplicate_window_seconds muss eine Ganzzahl >= 0 sein"

    return None


def validate_and_build_location(name, lat_raw, lon_raw):
    """Return (name, latitude, longitude, error)."""
    name = str(name or "").strip()[:50]
    if not name:
        return None, None, None, "name fehlt"
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None, None, None, "lat/lon müssen Zahlen sein"
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None, None, "lat muss -90..90, lon -180..180 sein"
    return name, lat, lon, None


# ---------------------------------------------------------------------------
# Web settings interface.
# ---------------------------------------------------------------------------

@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    return render_template(
        "settings.html",
        message=None, success=None,
        csrf_token=get_csrf_token(),
        settings=settings_store.get_settings(),
    )


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
        message, success = "Rate-Limit und Duplikat-Fenster müssen Zahlen sein", False
    else:
        error = validate_print_rules_updates(updates)
        if error:
            message, success = error, False
        else:
            settings_store.update_section("print_rules", updates)
            message, success = "Druckregeln gespeichert ✓", True

    return render_template(
        "settings.html", message=message, success=success,
        csrf_token=get_csrf_token(), settings=settings_store.get_settings(),
    )


@settings_bp.route("/ui/settings/weather/add", methods=["POST"])
@csrf_protect
def ui_add_weather_location():
    name, lat, lon, error = validate_and_build_location(
        request.form.get("name"), request.form.get("lat"), request.form.get("lon")
    )
    set_default = request.form.get("set_default") == "on"

    if error:
        message, success = error, False
    else:
        def _mutate(settings):
            settings["weather"]["locations"][name] = {"lat": lat, "lon": lon}
            if set_default:
                settings["weather"]["default_location"] = name

        settings_store.update_settings_transaction(_mutate)
        message, success = f"Standort '{name}' gespeichert ✓", True

    return render_template(
        "settings.html", message=message, success=success,
        csrf_token=get_csrf_token(), settings=settings_store.get_settings(),
    )


@settings_bp.route("/ui/settings/weather/delete", methods=["POST"])
@csrf_protect
def ui_delete_weather_location():
    name = request.form.get("name", "")
    current = settings_store.get_settings()
    locations = current["weather"]["locations"]

    if name not in locations:
        message, success = f"Standort '{name}' nicht gefunden", False
    elif len(locations) == 1:
        message, success = "Letzter verbleibender Standort kann nicht gelöscht werden", False
    else:
        def _mutate(settings):
            del settings["weather"]["locations"][name]
            if settings["weather"]["default_location"] == name:
                settings["weather"]["default_location"] = next(iter(settings["weather"]["locations"]))

        settings_store.update_settings_transaction(_mutate)
        message, success = f"Standort '{name}' gelöscht ✓", True

    return render_template(
        "settings.html", message=message, success=success,
        csrf_token=get_csrf_token(), settings=settings_store.get_settings(),
    )


@settings_bp.route("/ui/settings/weather/default", methods=["POST"])
@csrf_protect
def ui_set_default_weather_location():
    name = request.form.get("name", "")
    current = settings_store.get_settings()

    if name not in current["weather"]["locations"]:
        message, success = f"Standort '{name}' nicht gefunden", False
    else:
        def _mutate(settings):
            settings["weather"]["default_location"] = name

        settings_store.update_settings_transaction(_mutate)
        message, success = f"'{name}' ist jetzt Standard-Standort ✓", True

    return render_template(
        "settings.html", message=message, success=success,
        csrf_token=get_csrf_token(), settings=settings_store.get_settings(),
    )


# ---------------------------------------------------------------------------
# JSON API for scripts and automation.
# ---------------------------------------------------------------------------

@settings_bp.route("/settings/api", methods=["GET"])
@require_api_token
def get_all_settings():
    return jsonify(settings_store.get_settings()), 200


@settings_bp.route("/settings/print_rules", methods=["POST"])
@require_api_token
def update_print_rules():
    """Update the supplied print rule fields and preserve all others."""
    data, err = get_json_body()
    if err:
        return err

    allowed_fields = {
        "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end",
        "max_jobs_per_hour", "duplicate_window_seconds",
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify({"status": "error", "detail": "keine gültigen Felder übergeben"}), 400

    error = validate_print_rules_updates(updates)
    if error:
        return jsonify({"status": "error", "detail": error}), 400

    result = settings_store.update_section("print_rules", updates)
    return jsonify({"status": "gespeichert", "print_rules": result["print_rules"]}), 200


@settings_bp.route("/settings/weather/locations", methods=["GET"])
@require_api_token
def list_weather_locations():
    return jsonify(settings_store.get_settings()["weather"]), 200


@settings_bp.route("/settings/weather/locations", methods=["POST"])
@require_api_token
def add_weather_location():
    """Add a weather location from JSON input."""
    data, err = get_json_body()
    if err:
        return err

    name, lat, lon, error = validate_and_build_location(
        data.get("name"), data.get("lat"), data.get("lon")
    )
    if error:
        return jsonify({"status": "error", "detail": error}), 400

    def _mutate(settings):
        settings["weather"]["locations"][name] = {"lat": lat, "lon": lon}
        if data.get("set_default"):
            settings["weather"]["default_location"] = name

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "gespeichert", "weather": result["weather"]}), 200


@settings_bp.route("/settings/weather/locations/<name>", methods=["DELETE"])
@require_api_token
def delete_weather_location(name):
    current = settings_store.get_settings()
    locations = current["weather"]["locations"]
    if name not in locations:
        return jsonify({"status": "error", "detail": f"Standort '{name}' nicht gefunden"}), 404
    if len(locations) == 1:
        return jsonify({"status": "error", "detail": "letzter verbleibender Standort kann nicht gelöscht werden"}), 400

    def _mutate(settings):
        del settings["weather"]["locations"][name]
        if settings["weather"]["default_location"] == name:
            settings["weather"]["default_location"] = next(iter(settings["weather"]["locations"]))

    result = settings_store.update_settings_transaction(_mutate)
    return jsonify({"status": "gelöscht", "weather": result["weather"]}), 200
