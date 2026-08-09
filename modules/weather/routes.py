"""
Module: weather report - DWD forecast (Bright Sky, no API key needed) or
Open-Meteo (worldwide, also no API key) + Netatmo readings via Home
Assistant's REST API (since Netatmo is already set up there). Report
provider is a per-installation setting (weather.report_provider), see
modules/weather/alerts.py for the Open-Meteo fetch function.

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
from logos import print_logo
from modules.message.routes import _raw_print_message
from modules.weather.alerts import fetch_open_meteo_forecast
from print_queue import enqueue_print
from printer import get_printer
from security import csrf_protect, get_csrf_token, require_api_token
from text_style import get_text_scale, wrap_body_text

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


def _is_netatmo_placeholder(value):
    """Recognizes both the current English placeholder text
    (config.example.py, since 2026-08-08) and the older German one
    ("Platzhalter...") - an existing config.py is deliberately never
    overwritten by an update (see config.example.py's own comments), so
    someone still running an older config.py with the original German
    placeholder must keep being detected as "not configured" too,
    instead of suddenly being treated as a real entity ID and firing a
    request at Home Assistant with garbage."""
    return not value or value.startswith(("Placeholder", "Platzhalter"))


def fetch_ha_sensor(entity_id):
    if _is_netatmo_placeholder(entity_id):
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

    report_provider = settings_store.get_settings()["weather"].get("report_provider", "dwd")
    provider_label = "Open-Meteo" if report_provider == "open-meteo" else "DWD"

    dwd_lines = []
    if lat is not None:
        try:
            if report_provider == "open-meteo":
                forecast = fetch_open_meteo_forecast(lat, lon)
            else:
                forecast = fetch_dwd_forecast(lat, lon)
            if forecast:
                temp = forecast.get("temperature")
                wind = forecast.get("wind_speed")
                precip = forecast.get("precipitation")
                if temp is not None:
                    dwd_lines.append(i18n.tr("receipt.weather.temp", value=temp))
                if wind is not None:
                    dwd_lines.append(i18n.tr("receipt.weather.wind", value=wind))
                if precip is not None:
                    dwd_lines.append(i18n.tr("receipt.weather.precipitation", value=precip))
                if not dwd_lines:
                    dwd_lines.append(i18n.tr("receipt.weather.dwd_no_data", provider=provider_label))
            else:
                dwd_lines.append(i18n.tr("receipt.weather.dwd_no_data", provider=provider_label))
        except Exception as e:
            dwd_lines.append(i18n.tr("receipt.weather.dwd_error", provider=provider_label, error=e))
    else:
        dwd_lines.append(i18n.tr("receipt.weather.no_location_in_settings"))

    # Netatmo readings still only refer to the one physical home device,
    # regardless of the selected weather location - only makes sense for
    # "home", so it's always shown alongside (if configured at all).
    # fetch_ha_sensor() returns None for an unconfigured/placeholder
    # entity ID without hitting the network, so netatmo_lines simply
    # stays empty and the whole section gets skipped below - no one-line
    # "not configured" placeholder anymore, since not everyone has a
    # Netatmo station.
    netatmo_lines = []
    netatmo_configured = bool(
        getattr(config, "NETATMO_INDOOR_ENTITY", None) and not _is_netatmo_placeholder(config.NETATMO_INDOOR_ENTITY)
    ) or bool(
        getattr(config, "NETATMO_OUTDOOR_ENTITY", None) and not _is_netatmo_placeholder(config.NETATMO_OUTDOOR_ENTITY)
    )
    if netatmo_configured:
        try:
            indoor = fetch_ha_sensor(config.NETATMO_INDOOR_ENTITY)
            if indoor:
                netatmo_lines.append(i18n.tr("receipt.weather.indoor", value=indoor[0], unit=indoor[1]))
            outdoor = fetch_ha_sensor(config.NETATMO_OUTDOOR_ENTITY)
            if outdoor:
                netatmo_lines.append(i18n.tr("receipt.weather.outdoor", value=outdoor[0], unit=outdoor[1]))
            if not netatmo_lines:
                netatmo_lines.append(i18n.tr("receipt.weather.dwd_no_data", provider="Netatmo"))  # reused, now provider-aware
        except Exception as e:
            netatmo_lines.append(i18n.tr("receipt.weather.netatmo_error", error=e))

    scale = get_text_scale()
    p = get_printer()
    try:
        logo_printed = print_logo(p, "weather")
        if logo_printed:
            p.set(align="left", bold=False, width=1, height=1)
            p.text("\n")
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(i18n.tr("receipt.weather.title", location=location_display) + "\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
        p.text("\n")

        p.set(align="left", bold=True, width=scale.body_width, height=scale.body_height, custom_size=True)
        p.text(i18n.tr("receipt.weather.dwd_heading", provider=provider_label) + "\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
        for line in dwd_lines:
            p.text(wrap_body_text(line, scale) + "\n")
        p.text("\n")

        if netatmo_lines:
            p.set(align="left", bold=True, width=scale.body_width, height=scale.body_height, custom_size=True)
            p.text(i18n.tr("receipt.weather.netatmo_heading") + "\n")
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            for line in netatmo_lines:
                p.text(wrap_body_text(line, scale) + "\n")
            p.text("\n")

        p.set(align="center", bold=False, width=1, height=1, custom_size=True)
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


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

    resolved_name, _lat, _lon = resolve_location(location_name)
    ok, detail, status_code = enqueue_print(
        _raw_print_weather, location_name, job_type="weather", summary=resolved_name or "", source="api",
    )
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@weather_bp.route("/ui/weather", methods=["POST"])
@csrf_protect
def ui_print_weather():
    location_name = request.form.get("location") or None
    resolved_name, _lat, _lon = resolve_location(location_name)
    ok, detail, _status_code = enqueue_print(
        _raw_print_weather, location_name, job_type="weather", summary=resolved_name or "", source="ui",
    )
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


@weather_bp.route("/print/weather/alert", methods=["POST"])
@require_api_token
def print_weather_alert():
    """Only meant to be called by watchers/storm_warning_watch.py, not
    a general-purpose endpoint - still requires the same X-Api-Token as
    every other /print/* route, just not documented in the README's
    public endpoint list.

    Whether quiet hours get bypassed is decided ENTIRELY server-side
    from settings_store's weather.storm_warning.ignore_quiet_hours
    toggle - never from anything in the request body. That toggle is
    itself only reachable through the authenticated Settings UI, so
    this endpoint can't be used to bypass quiet hours for anything the
    admin hasn't already explicitly opted into.
    """
    data = request.get_json(silent=True) or {}
    headline = (data.get("headline") or "").strip()
    description = (data.get("description") or "").strip()
    if not headline:
        return jsonify({"status": "error", "detail": "'headline' must not be empty"}), 400

    ignore_quiet_hours = settings_store.get_settings()["weather"]["storm_warning"].get(
        "ignore_quiet_hours", False,
    )
    text = f"{headline}\n\n{description}".strip()
    ok, detail, status_code = enqueue_print(
        _raw_print_message, i18n.tr("receipt.weather.alert_title"), text, "weather",
        job_type="weather_alert", summary=headline[:60], source="system",
        bypass_quiet_hours=ignore_quiet_hours,
    )
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code
