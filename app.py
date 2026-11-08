"""
ReceiptPi - server for an Epson TM-T88V (or other ESC/POS printer) on a Pi.

Modular structure:
  - print_queue.py    central print queue (one worker thread, prevents
                       concurrent USB access) + central print rules
                       (quiet hours, rate limit, duplicate suppression)
  - printer.py         printer hardware access
  - security.py        CSRF protection, API token protection, JSON parsing
  - auth_store.py      user accounts and login sessions (SQLite in
                       STATE_DIR/auth.db); modules/auth/ has the login
                       pages and the login check for every request
  - settings_store.py   central settings (JSON in STATE_DIR)
  - i18n.py             minimal translation lookup (JSON files, no
                       Flask-Babel/gettext toolchain)
  - modules/            one Flask blueprint per printing feature
                       (lists, message, images, wifi, weather, system,
                       settings, recipes, ...)
  - watchers/            standalone cron scripts that watch something and
                       send a print job to this server when needed (do
                       NOT run inside this process)

app.py itself stays deliberately thin: create the Flask app, register
blueprints, health check and home page.

The boot greeting does NOT run here at module level - gunicorn.conf.py
(on_starting hook) starts it as a separate process (boot_greeting.py),
exactly once per service start, regardless of worker count. For a direct
`python3 app.py` run (dev mode) the __main__ block below handles it,
since that's a single, non-forking process anyway.
"""
import json
import os

import config
from flask import Flask, abort, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import history_store
import i18n
import module_catalog
import mqtt_bridge
import pending_store
import printer_status
import settings_store
import themes
import user_prefs
import version
from logos import ensure_default_logo_seeded
from modules.auth import guard as auth_guard
from modules.auth.preferences import preferences_bp
from modules.auth.routes import auth_bp
from modules.auth.users import users_bp
from modules.automation.routes import automation_bp
from modules.backup.routes import backup_bp
from modules.games.routes import games_bp
from modules.history import owners as history_owners
from modules.history.routes import history_bp
from modules.images.routes import images_bp
from modules.lists.routes import lists_bp
from modules.message.routes import message_bp
from modules.pending.routes import pending_bp
from modules.recipes.routes import recipes_bp
from modules.settings.routes import settings_bp
from modules.setup_wifi.routes import setup_wifi_bp
from modules.system.routes import system_bp
from modules.weather.routes import weather_bp
from modules.wifi.routes import wifi_bp
from print_queue import start_worker
from security import any_user, public
from static_versioning import static_url, versioned_style_css

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15MB - headroom over MAX_IMAGE_BYTES (12MB, see modules/images/routes.py) for multipart overhead
# Flask 3's own default is None (no Cache-Control max-age at all, only
# ETag/Last-Modified) - every page load re-requests style.css, every
# icon's individual SVG (see the CSS mask-image icon system in
# style.css), and the per-page JS files, each a conditional GET
# round-trip to the Pi even when nothing changed. Templates reference
# static files through static_url(), and the served style.css has its
# icon and font URLs rewritten (both in static_versioning.py), so a
# changed file gets a new ?v=<content hash> URL after a deploy and is
# fetched right away instead of waiting out this max-age. Only static
# paths without a ?v= (e.g. the icon paths inside manifest.json) can
# stay stale for up to this hour; for everything else it just lets
# navigation between pages skip the revalidation round-trip.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

# Written by watchers/update_check_watch.py (cron, every 12h) - read here
# as a plain local file, NEVER fetched from GitHub inside a request. See
# that script for why: a page render must never wait on or be able to
# fail because of GitHub being unreachable.
UPDATE_CACHE_FILE = os.path.join(settings_store.STATE_DIR, "update_check.json")


def _read_update_cache():
    """Missing/corrupt cache (e.g. before the watcher's first run) is
    just \"no update known\" - never an error, never something that
    should block rendering a page."""
    try:
        with open(UPDATE_CACHE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

INVALID_SECRET_KEYS = {
    "",
    "Insert a random value here via secrets.token_hex(32)",
    "Hier zufälligen Wert per secrets.token_hex(32) einsetzen",  # older config.example.py wording, kept for upgrade compatibility
}
if getattr(config, "SECRET_KEY", "") in INVALID_SECRET_KEYS:
    raise RuntimeError(
        "SECRET_KEY is not configured (still the placeholder from "
        "config.example.py). Please set a random value in config.py: "
        "python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.secret_key = config.SECRET_KEY  # needed for the web UI's CSRF token session

# X-Forwarded-* headers are only believed with a reverse proxy declared
# in config.py, and then for exactly that many proxy hops: any client
# can send these headers itself, and the client address they would set
# is what the login brute-force protection counts per IP. X-Forwarded-
# Prefix is never used, the app always runs at the root path.
_trusted_proxies = getattr(config, "TRUSTED_PROXY_COUNT", 0)
if isinstance(_trusted_proxies, bool) or not isinstance(_trusted_proxies, int) or _trusted_proxies < 0:
    raise RuntimeError("TRUSTED_PROXY_COUNT in config.py must be a whole number >= 0")
if _trusted_proxies:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_trusted_proxies, x_proto=_trusted_proxies,
                            x_host=_trusted_proxies, x_port=_trusted_proxies, x_prefix=0)

start_worker()  # start the print queue worker thread
i18n.load_translations()  # load translation JSON files once at startup
ensure_default_logo_seeded()  # one-time: bundled default logo -> STATE_DIR, see logos.py

for blueprint in (
    lists_bp, message_bp, images_bp, wifi_bp, weather_bp, system_bp, automation_bp, settings_bp,
    history_bp, pending_bp, games_bp, recipes_bp, backup_bp, auth_bp, users_bp, preferences_bp,
    setup_wifi_bp,
):
    app.register_blueprint(blueprint)

# Same place and reason as start_worker() above: app.py is imported in the
# worker after Gunicorn forks, so the MQTT threads live in the one process
# that also owns the print queue. Does nothing unless MQTT is enabled.
mqtt_bridge.start()

# Registered before _enforce_enabled_modules on purpose: Flask runs
# before_request hooks in registration order, and without a login a
# disabled module's URL should lead to the login page like any other,
# not reveal through a 404 which modules are switched off.
app.before_request(auth_guard.enforce_login)
app.after_request(auth_guard.after_request)


@app.before_request
def _enforce_enabled_modules():
    """Blocks every route (UI page, /ui/* form post, AND /print/*
    API) of a disabled module with a 404 - not just hiding its home
    page tile. request.blueprint is None for routes not in any
    blueprint (e.g. /, /health), so those are always left alone. See
    module_catalog.py for why the blueprint name and the module key
    are guaranteed to match. "Disabled" means the EFFECTIVE state from
    module_catalog.active_modules() (toggle + module-specific
    precondition, e.g. recipes needs a provider selected), not just the
    raw enabled_modules toggle."""
    bp = request.blueprint
    if bp not in module_catalog.MODULE_KEYS:
        return
    if not module_catalog.active_modules(settings_store.get_settings()).get(bp, True):
        abort(404)


@app.context_processor
def inject_i18n():
    """Makes t() and the current language/theme/glass look available in
    every template without each route having to pass them explicitly.
    All follow the logged-in account's own choice if it made one, else
    the device default (see i18n.current_language(),
    themes.current_theme(), themes.current_glass())."""
    settings = settings_store.get_settings()
    lang = i18n.current_language()
    theme = themes.current_theme()
    glass, glass_blur = themes.current_glass()
    # UI text scale follows the same Easy-Read setting used for print
    # jobs (settings.print_rules.text_size) - one switch for both,
    # rather than a separate UI-only preference.
    text_scale = settings.get("print_rules", {}).get("text_size", "normal")
    update_cache = _read_update_cache()
    latest_version = update_cache.get("latest_version")
    return {
        "t": lambda key, **kw: i18n.t(key, lang, **kw),
        "current_language": lang,
        "supported_languages": i18n.SUPPORTED_LANGUAGES,
        "current_theme": theme,
        "current_glass": glass,
        "current_glass_blur": glass_blur,
        "supported_themes": themes.SUPPORTED_THEMES,
        "current_text_scale": text_scale,
        "all_modules": module_catalog.MODULES,
        # Raw toggles - only for the checkboxes on /settings/modules.
        # Everything that shows/hides a module uses active_modules
        # instead, see module_catalog.active_modules().
        "enabled_modules": settings.get("enabled_modules", {}),
        "active_modules": module_catalog.active_modules(settings),
        "current_version": version.format_display(version.CURRENT_VERSION),
        "update_available": bool(update_cache.get("update_available")) and bool(latest_version),
        "latest_version": version.format_display(latest_version) if latest_version else None,
        "latest_release_url": update_cache.get("latest_url"),
        "github_repo_url": "https://github.com/HostisHumani/ReceiptPi",
        # Cheap: pending_store stays small (a handful of manually-
        # managed entries at most), so reading it on every request for
        # the burger-menu badge is not a concern the way the settings
        # read above already isn't.
        "pending_count": _pending_count(),
        # Cache-busting query param for /static/* URLs this app owns
        # and edits - see static_versioning.py for why.
        "static_url": static_url,
    }


def _pending_count():
    """Badge count for the menu: an admin (and everyone while no admin
    account exists) counts every pending entry, any other account only
    its own, and a visitor without a login none."""
    viewer = history_owners.current_viewer()
    if viewer["admin"]:
        return len(pending_store.get_all())
    scope = history_owners.own_scope(viewer)
    return pending_store.count(scope) if scope else 0


@app.route("/static/style.css")
@public
def style_css():
    """Serves static/style.css with its internal /static/icons/*.svg
    and /static/fonts/*.woff2 references rewritten to versioned URLs
    (see static_versioning.py) - takes over the exact /static/style.css
    path from Flask's default static-file route (Werkzeug always
    prefers a fixed-path rule like this one over the catch-all
    /static/<path:filename> route, regardless of registration order).
    Icon/font files themselves are still served unchanged by that
    default handler, just requested under a versioned URL now. Same
    Cache-Control policy as every other /static/* file
    (SEND_FILE_MAX_AGE_DEFAULT) - cache duration isn't the problem
    being solved here, a changing URL on every edit is."""
    resp = app.response_class(versioned_style_css(), mimetype="text/css")
    resp.headers["Cache-Control"] = f"public, max-age={app.config['SEND_FILE_MAX_AGE_DEFAULT']}"
    return resp


_SW_SOURCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "sw.js")
_sw_body_cache = None


@app.route("/sw.js", methods=["GET"])
@public
def service_worker():
    """Serves static/sw.js at the site root: a service worker can only
    control URLs at or below the path it's served from, so the copy
    Flask's static handler would serve at /static/sw.js could never
    control "/" itself. The version placeholder is filled in from the
    VERSION file, which ties the worker's cache name to the release -
    a version bump changes these bytes, the browser installs the new
    worker, and its activate handler drops the old caches. Read once
    per process (same as static_versioning.py); a deploy always
    restarts the service.

    no-cache: the browser must revalidate this script on every update
    check - a worker stuck on an HTTP-cached old copy would keep an
    old cache name alive."""
    global _sw_body_cache
    if _sw_body_cache is None:
        with open(_SW_SOURCE_FILE, encoding="utf-8") as f:
            _sw_body_cache = f.read().replace("__RECEIPTPI_VERSION__", version.CURRENT_VERSION)
    resp = app.response_class(_sw_body_cache, mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/health", methods=["GET"])
@public
def health():
    """Printer reachability without a print job and without opening the
    printer (see printer_status.health_snapshot()). "Reachable" means
    attached and switched on, so paper out, an open cover or an
    unreadable status still answer 200 - "state"/"problem" carry those
    details (same meaning as in the MQTT state). Only a printer that is
    not on the USB bus at all is an error (500)."""
    snapshot = printer_status.health_snapshot()
    state = snapshot["state"]
    problem = state in printer_status.PROBLEM_STATES
    if snapshot["present"]:
        return {"status": "ok", "printer": "reachable", "state": state, "problem": problem}, 200
    return {"status": "error", "detail": i18n.tr("health.printer_offline"), "state": state,
            "problem": problem}, 500


@app.route("/", methods=["GET"])
@any_user
def index():
    active = module_catalog.active_modules(settings_store.get_settings())
    active_count = sum(1 for m in module_catalog.MODULES if active[m["key"]])
    # Every account sees its own count, admins included; the device
    # total is on /history. Without accounts it is the device total.
    viewer = history_owners.current_viewer()
    scope = history_owners.own_scope(viewer)
    total_prints = history_store.count_for(scope) if scope else 0
    return render_template(
        "home.html",
        home_tiles=module_catalog.home_tiles(active, user_prefs.current().get(user_prefs.TILES)),
        active_module_count=active_count,
        total_module_count=len(module_catalog.MODULES),
        total_prints=total_prints,
        own_prints=viewer["accounts"],
    )


if __name__ == "__main__":
    # Only for local test runs (`python3 app.py`). In production the
    # server runs via Gunicorn with gunicorn.conf.py.
    import socket

    from modules.message.routes import _raw_print_message
    from printer import get_local_ip

    hostname = socket.gethostname()
    try:
        ip = get_local_ip()
        text = i18n.tr("receipt.boot.body", hostname=hostname, ip=ip) + " (dev mode)"
        _raw_print_message(i18n.tr("receipt.boot.title"), text, use_text_scale=False)
    except Exception:
        pass  # the boot greeting is "nice to have", not a reason to block the dev server
    else:
        history_store.log_job("boot", f"{hostname} ({ip})", "system", "ok")

    app.run(host="0.0.0.0", port=5000)
