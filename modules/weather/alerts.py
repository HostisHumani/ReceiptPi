"""
Weather report and storm-warning providers.

Report providers (regular /print/weather content): DWD (existing, via
Bright Sky - see modules/weather/routes.py fetch_dwd_forecast) and
Open-Meteo here, since it's free, needs no API key, and covers the
whole world (combines the best available national model per location)
- picked as the international-friendly alternative to DWD, see the
report_provider weather setting.

Storm-warning providers (only used by watchers/storm_warning_watch.py):
each national weather service publishes warnings differently, so there
is no single global API - see settings_store.DEFAULT_SETTINGS["weather"]
["storm_warning"] for the provider choice.

  - DWD: implemented, verified against a realistic sample payload.
    Uses the community-standard (undocumented but widely used, e.g. by
    the official Home Assistant DWD integration) warnings.json feed,
    keyed by Bundesland+Landkreis rather than lat/lon - see
    fetch_dwd_alert().
  - NWS (USA): implemented, verified against a realistic sample
    payload. api.weather.gov works natively with lat/lon, no API key
    needed - see fetch_nws_alert().
  - MeteoAlarm (Europe): implemented. Uses the official public Atom
    feed per country at feeds.meteoalarm.org (confirmed real, free, no
    key, actively maintained - the OLDER RSS variant was sunset
    2026-01-14, do not use it). Parses CAP fields directly from the
    Atom XML (cap:areaDesc/severity/event/expires) - CORRECTED
    2026-08-07 against a real active warning on the live feed after an
    earlier version (based on 2021-2023 community reference code using
    an "awt:N level:M" image-alt pattern) turned out to be checking a
    format MeteoAlarm has since replaced; see fetch_meteoalarm_alert()'s
    docstring for what to check if this drifts again. The official
    modern MeteoAlarm EDR API (api.meteoalarm.org) was deliberately NOT
    used - it requires being a registered MeteoAlarm Member/
    Re-distributor, not just an API call, so it isn't usable for a
    self-hosted hobby project.
  - ECCC (Canada): REMOVED (2026-08-07). Was briefly added with guessed
    GeoJSON property names for api.weather.gc.ca/collections/
    weather-alerts, never verified against a live response - shipping
    code based on a guess is not acceptable here, so it was pulled out
    entirely rather than left as a dead stub. Not in the settings
    dropdown. If this comes back later, it needs a real verified
    response first, same standard as the three providers above.
"""
import json
import re
import urllib.request
from datetime import UTC, datetime


class AlertResult:
    """Return type for every fetch_*_alert() function below - keeps the
    watcher (watchers/storm_warning_watch.py) provider-agnostic, it
    never needs to know the specifics of any one provider's response
    shape.

    warning_id: stable identifier for THIS specific warning, used by
    the watcher's state file to avoid printing the same active warning
    on every 15-minute poll. None when there's no active warning.
    headline/description: short/long text for the receipt. None when
    there's no active warning.
    implemented: reserved for a future provider that's visible in the
    UI but not yet built - not currently used by any of the three
    active providers (dwd/nws/meteoalarm all fully implemented)."""

    def __init__(self, active, warning_id=None, headline=None, description=None,
                 implemented=True, error=None):
        self.active = active
        self.warning_id = warning_id
        self.headline = headline
        self.description = description
        self.implemented = implemented
        self.error = error


# ---------------------------------------------------------------------
# Report providers
# ---------------------------------------------------------------------

def fetch_open_meteo_forecast(lat, lon):
    """Returns a dict shaped like fetch_dwd_forecast()'s return value
    (temperature/wind_speed/precipitation keys) so
    modules/weather/routes.py can treat both providers identically.
    Open-Meteo's current_weather + hourly precipitation, no API key."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,wind_speed_10m,precipitation"
        "&timezone=auto"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "receiptpi-weather"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    current = data.get("current")
    if not current:
        return None
    return {
        "temperature": current.get("temperature_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "precipitation": current.get("precipitation"),
    }


# ---------------------------------------------------------------------
# Storm-warning providers
# ---------------------------------------------------------------------

def fetch_dwd_alert(state, region_name):
    """DWD's warnings.json, filtered by exact Bundesland (state) +
    Landkreis (region_name) match - same fields the official DWD
    warning app itself uses. Both values are free text the user enters
    in Settings (see templates/settings_weather.html); DWD publishes
    the canonical spelling list at:
    https://www.dwd.de/DE/leistungen/opendata/help/warnungen/cap_warncellids_csv.html

    The feed is wrapped in a JSONP callback (warnWetter.loadWarnings(...
    );) rather than being plain JSON - stripped here via the first '('
    and last ')' rather than string-replacing the exact callback name,
    since that name isn't documented/guaranteed to stay identical.

    Only considers the "warnings" section (currently active), not
    "vorabInformation" (advance/preliminary notices) - a preliminary
    notice isn't the same as an active warning, and printing on those
    too would likely mean far more, less certain prints."""
    if not state or not region_name:
        return AlertResult(active=False, error="DWD: state/region_name not configured")

    url = "https://www.dwd.de/DWD/warnungen/warnapp/json/warnings.json?jsonp=loadWarnings"
    req = urllib.request.Request(url, headers={"User-Agent": "receiptpi-weather"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    json_str = raw[raw.find("(") + 1:raw.rfind(")")]
    data = json.loads(json_str)

    matches = []
    for warncell_id, warnings in data.get("warnings", {}).items():
        for w in warnings:
            if w.get("state") == state and w.get("regionName") == region_name:
                matches.append((warncell_id, w))

    if not matches:
        return AlertResult(active=False)

    # Highest level first (4 = extreme) - if several warnings are active
    # for the same area, the receipt should lead with the worst one.
    warncell_id, worst = max(matches, key=lambda m: m[1].get("level", 0))
    return AlertResult(
        active=True,
        warning_id=f"dwd:{warncell_id}:{worst.get('start')}:{worst.get('event')}",
        headline=worst.get("headline") or worst.get("event") or "Unwetterwarnung",
        description=worst.get("description") or "",
    )


def fetch_nws_alert(lat, lon):
    """api.weather.gov, natively lat/lon-based, no API key. The NWS API
    requires a descriptive User-Agent per their usage policy (not just
    a browser-style UA string) - see
    https://www.weather.gov/documentation/services-web-api"""
    headers = {
        "User-Agent": "ReceiptPi (https://github.com/HostisHumani/ReceiptPi)",
        "Accept": "application/geo+json",
    }
    url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)

    features = data.get("features", [])
    if not features:
        return AlertResult(active=False)

    # NWS orders active alerts by severity/urgency already; take the
    # first as "the" warning for this receipt, same one-warning-per-
    # print approach as the DWD path above.
    props = features[0].get("properties", {})
    return AlertResult(
        active=True,
        warning_id=props.get("id") or f"nws:{props.get('sent')}:{props.get('event')}",
        headline=props.get("headline") or props.get("event") or "Severe Weather Alert",
        description=props.get("description") or "",
    )


def fetch_meteoalarm_alert(country_slug, region_name):
    """MeteoAlarm's public Atom feed for one country, e.g.
    https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-germany -
    see https://feeds.meteoalarm.org for the full list of valid
    country_slug values (e.g. "germany", "austria", "united-kingdom",
    "czechia", "republic-of-north-macedonia" - not ISO codes, and not
    always the plain English name).

    Parsing approach (REVISED 2026-08-07): each <entry> carries its
    warning data as CAP fields directly in the XML - cap:areaDesc,
    cap:severity, cap:event, cap:expires, etc. An EARLIER version of
    this function looked for an "awt:N level:M" pattern instead (based
    on real, but apparently outdated, 2021-2023 community reference
    code) - that pattern no longer exists in the live feed, MeteoAlarm
    has since modernized the format. This version was corrected against
    field names read directly off a real active warning on the live
    feed ("Yellow Wind Warning issued for Germany - Kreis Harburg",
    2026-08-07) rather than a secondhand code reference - still worth
    double-checking if MeteoAlarm changes format again; a mismatch here
    fails soft (returns active=False) rather than crashing, but that
    also means a format change could silently miss a real warning, not
    just error out. If storm warnings stop showing up despite one
    clearly being active on meteoalarm.org, this function is the first
    place to check.

    region_name must match EXACTLY as MeteoAlarm spells it for that
    country (cap:areaDesc content) - visible on the country's page at
    meteoalarm.org, or in the raw feed content itself."""
    if not country_slug or not region_name:
        return AlertResult(active=False, error="MeteoAlarm: country/region not configured")

    url = f"https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country_slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "receiptpi-weather"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    entries = re.findall(r"<entry\b.*?</entry>", text, re.DOTALL)
    now = datetime.now(UTC)
    matches = []
    for entry in entries:
        area_match = re.search(r"<cap:areaDesc>(.*?)</cap:areaDesc>", entry, re.DOTALL)
        if not area_match or area_match.group(1).strip() != region_name:
            continue

        expires_match = re.search(r"<cap:expires>(.*?)</cap:expires>", entry)
        if expires_match:
            try:
                expires = datetime.fromisoformat(expires_match.group(1).strip())
                if expires < now:
                    continue  # this specific entry has already expired - skip it
            except ValueError:
                pass  # unparseable timestamp - err on the side of keeping the entry

        severity_match = re.search(r"<cap:severity>(.*?)</cap:severity>", entry)
        event_match = re.search(r"<cap:event>(.*?)</cap:event>", entry)
        id_match = re.search(r"<id>(.*?)</id>", entry)
        matches.append({
            "severity": severity_match.group(1).strip() if severity_match else "Unknown",
            "event": event_match.group(1).strip() if event_match else "Weather warning",
            "id": id_match.group(1).strip() if id_match else None,
        })

    if not matches:
        return AlertResult(active=False)

    # CAP standard severity order - lead with the worst if multiple
    # active warnings exist for the same region.
    SEVERITY_RANK = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1, "Unknown": 0}
    worst = max(matches, key=lambda m: SEVERITY_RANK.get(m["severity"], 0))
    warning_id = worst["id"] or f"meteoalarm:{country_slug}:{region_name}:{worst['severity']}:{worst['event']}"
    return AlertResult(
        active=True,
        warning_id=warning_id,
        headline=f"{worst['severity']} - {worst['event']}",
        description=f"{region_name} ({country_slug})",
    )


def fetch_alert(provider, weather_settings, lat, lon):
    """Single entry point for the watcher - dispatches to the right
    provider fetch function based on the "storm_warning.provider"
    setting, so watchers/storm_warning_watch.py doesn't need its own
    if/elif chain."""
    storm = weather_settings.get("storm_warning", {})
    if provider == "dwd":
        return fetch_dwd_alert(storm.get("dwd_state", ""), storm.get("dwd_region_name", ""))
    elif provider == "nws":
        return fetch_nws_alert(lat, lon)
    elif provider == "meteoalarm":
        return fetch_meteoalarm_alert(storm.get("meteoalarm_country", ""), storm.get("meteoalarm_region", ""))
    return AlertResult(active=False, error=f"unknown provider: {provider}")
