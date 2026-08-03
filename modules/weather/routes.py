"""Weather report module using Bright Sky forecasts and optional Netatmo
sensor values retrieved through Home Assistant.

Weather locations are managed through settings_store and can be changed at
runtime without restarting ReceiptPi."""
import json
import urllib.request
from datetime import datetime

import config
from flask import Blueprint, jsonify, render_template, request

import settings_store
from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, require_api_token

weather_bp = Blueprint("weather", __name__)


def resolve_location(name=None):
    """Resolve a configured location and fall back to the default."""
    weather_settings = settings_store.get_settings()["weather"]
    locations = weather_settings["locations"]
    default_name = weather_settings.get("default_location")

    chosen_name = name if (name and name in locations) else default_name
    if chosen_name not in locations:
        # Final fallback for missing or invalid location settings.
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
    lines = [f"Wetter {resolved_name or '(kein Standort konfiguriert)'}"]

    if lat is not None:
        try:
            forecast = fetch_dwd_forecast(lat, lon)
            if forecast:
                temp = forecast.get("temperature")
                cond = forecast.get("condition", "")
                wind = forecast.get("wind_speed")
                precip = forecast.get("precipitation")
                lines.append(f"DWD: {temp}°C, {cond}")
                if wind is not None:
                    lines.append(f"Wind: {wind} km/h")
                if precip is not None:
                    lines.append(f"Niederschlag: {precip} mm")
            else:
                lines.append("DWD: keine Daten")
        except Exception as e:
            lines.append(f"DWD-Fehler: {e}")
    else:
        lines.append("Kein Standort in den Settings hinterlegt")

    # Netatmo values belong to the configured home installation and are shown
    # independently of the selected forecast location.
    #
    try:
        indoor = fetch_ha_sensor(config.NETATMO_INDOOR_ENTITY)
        if indoor:
            lines.append(f"Innen: {indoor[0]}{indoor[1]}")
        outdoor = fetch_ha_sensor(config.NETATMO_OUTDOOR_ENTITY)
        if outdoor:
            lines.append(f"Außen: {outdoor[0]}{outdoor[1]}")
        if not indoor and not outdoor:
            lines.append("Netatmo: Entity-IDs noch nicht konfiguriert")
    except Exception as e:
        lines.append(f"Netatmo-Fehler: {e}")

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
    """Print weather for an optional JSON or query-string location."""
    location_name = request.args.get("location")
    if request.method == "POST" and request.is_json:
        location_name = (request.get_json(silent=True) or {}).get("location", location_name)

    ok, detail, status_code = enqueue_print(_raw_print_weather, location_name)
    if ok:
        return jsonify({"status": "gedruckt"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@weather_bp.route("/ui/weather", methods=["POST"])
@csrf_protect
def ui_print_weather():
    location_name = request.form.get("location") or None
    ok, detail, _status_code = enqueue_print(_raw_print_weather, location_name)
    message = "Wetter gedruckt ✓" if ok else f"Fehler: {detail}"
    weather_settings = settings_store.get_settings()["weather"]
    return render_template(
        "weather.html",
        message=message,
        success=ok,
        csrf_token=get_csrf_token(),
        locations=list(weather_settings["locations"].keys()),
        default_location=weather_settings.get("default_location"),
    )
