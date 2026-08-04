"""
Module: weather report - DWD forecast (Bright Sky, no API key needed) +
Netatmo readings via Home Assistant's REST API (since Netatmo is already
set up there).

Locations come from settings_store instead of being fixed in config.py -
they can be managed at runtime via /settings/weather/locations without
restarting the service (see modules/settings/routes.py).

The printed receipt text is fully translated via i18n as well (not
just the web UI page around it) - DWD/Netatmo are kept as-is in both
languages since they're the actual data source names, not generic
labels.
"""
import json
import urllib.request
from datetime import datetime

import config
from flask import Blueprint, jsonify, render_template, request

import i18n
import settings_store
from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, require_api_token

weather_bp = Blueprint("weather", __name__)


def resolve_location(name=None):
    """Resolves a location name to (name, lat, lon). Without a name, the
    default location from settings is used. An unknown name falls back
    to the default instead of crashing."""
    weather_settings = settings_store.get_settings()["weather"]
    locations = weather_settings["locations"]
    default_name = weather_settings.get("default_location")

    chosen_name = name if (name and name in locations) else default_name
    if chosen_name not in locations:
        # last-resort fallback in case the settings file is ever empty/broken
        chosen_name = next(iter(locations)) if locations else None
    if chosen_name is None:
        return None, None, None

    loc = locations[chosen_name]
    return chosen_name, loc["lat"], loc["lon"]


def fetch_dwd_forecast(lat, lon):
    url = (
        f"https://api.brightsky.dev/weather?lat={lat}"
        f"&lon={lon}&date={datetime.now().strftime('%Y-%m-%d')}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "receiptpi-weather"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    now_hour = datetime.now().hour
    weather_list = data.get("weather", [])
    best = None
    for entry in weather_list:
        entry_hour = int(entry["timestamp"][11:13])
        if entry_hour >= now_hour:
            best = entry
            break
    if best is None and weather_list:
        best = weather_list[-1]
    return best


def fetch_ha_sensor(entity_id):
    if not entity_id or entity_id.startswith("Platzhalter"):
        return None
    url = f"{config.HA_BASE_URL}/api/states/{entity_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config.HA_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data.get("state"), data.get("attributes", {}).get("unit_of_measurement", "")


def _raw_print_weather(location_name=None):
    resolved_name, lat, lon = resolve_location(location_name)
    location_display = resolved_name or i18n.tr("receipt.weather.no_location_configured")
    lines = [i18n.tr("receipt.weather.title", location=location_display)]

    if lat is not None:
        try:
            forecast = fetch_dwd_forecast(lat, lon)
            if forecast:
                temp = forecast.get("temperature")
                cond = forecast.get("condition", "")
                wind = forecast.get("wind_speed")
                precip = forecast.get("precipitation")
                lines.append(i18n.tr("receipt.weather.dwd_line", temp=temp, cond=cond))
                if wind is not None:
                    lines.append(i18n.tr("receipt.weather.wind", value=wind))
                if precip is not None:
                    lines.append(i18n.tr("receipt.weather.precipitation", value=precip))
            else:
                lines.append(i18n.tr("receipt.weather.dwd_no_data"))
        except Exception as e:
            lines.append(i18n.tr("receipt.weather.dwd_error", error=e))
    else:
        lines.append(i18n.tr("receipt.weather.no_location_in_settings"))

    # Netatmo readings still only refer to the one physical home device,
    # regardless of the selected weather location - only makes sense for
    # "home", so it's always shown alongside.
    try:
        indoor = fetch_ha_sensor(config.NETATMO_INDOOR_ENTITY)
        if indoor:
            lines.append(i18n.tr("receipt.weather.indoor", value=indoor[0], unit=indoor[1]))
        outdoor = fetch_ha_sensor(config.NETATMO_OUTDOOR_ENTITY)
        if outdoor:
            lines.append(i18n.tr("receipt.weather.outdoor", value=outdoor[0], unit=outdoor[1]))
        if not indoor and not outdoor:
            lines.append(i18n.tr("receipt.weather.netatmo_not_configured"))
    except Exception as e:
        lines.append(i18n.tr("receipt.weather.netatmo_error", error=e))

    text = "\n".join(lines)
    _raw_print_message(None, text)


@weather_bp.route("/weather", methods=["GET"])
def weather_page():
    weather_settings = settings_store.get_settings()["weather"]
    return render_template(
        "weather.html",
        message=None,
        success=None,
        csrf_token=get_csrf_token(),
        locations=list(weather_settings["locations"].keys()),
        default_location=weather_settings.get("default_location"),
    )


@weather_bp.route("/print/weather", methods=["POST", "GET"])
@require_api_token
def print_weather():
    """
    Optional JSON body { "location": "Berlin" } or query parameter
    ?location=Berlin - without one, the default location is used.
    """
    location_name = request.args.get("location")
    if request.method == "POST" and request.is_json:
        location_name = (request.get_json(silent=True) or {}).get("location", location_name)

    ok, detail, status_code = enqueue_print(_raw_print_weather, location_name)
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@weather_bp.route("/ui/weather", methods=["POST"])
@csrf_protect
def ui_print_weather():
    location_name = request.form.get("location") or None
    ok, detail, _status_code = enqueue_print(_raw_print_weather, location_name)
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    weather_settings = settings_store.get_settings()["weather"]
    return render_template(
        "weather.html",
        message=message,
        success=ok,
        csrf_token=get_csrf_token(),
        locations=list(weather_settings["locations"].keys()),
        default_location=weather_settings.get("default_location"),
    )
