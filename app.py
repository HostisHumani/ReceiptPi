"""
ReceiptPi - server for an Epson TM-T88V (or other ESC/POS printer) on a Pi.

Modular structure:
  - print_queue.py    central print queue (one worker thread, prevents
                       concurrent USB access) + central print rules
                       (quiet hours, rate limit, duplicate suppression)
  - printer.py         printer hardware access
  - security.py        CSRF protection, API token protection, JSON parsing
  - settings_store.py   central settings (JSON in STATE_DIR)
  - i18n.py             minimal translation lookup (JSON files, no
                       Flask-Babel/gettext toolchain)
  - modules/            one Flask blueprint per printing feature
                       (shopping, message, images, wifi, weather, system,
                       settings)
  - watchers/            standalone cron scripts that watch something and
                       send a print job to this server when needed (do
                       NOT run inside this process)

app.py itself stays deliberately thin: create the Flask app, register
blueprints, health check and home page.

The boot greeting does NOT run here at module level - it runs via
gunicorn.conf.py (on_starting hook), which is guaranteed to fire exactly
once in the master process, regardless of worker count. For a direct
`python3 app.py` run (dev mode) the __main__ block below handles it,
since that's a single, non-forking process anyway.
"""
import json
import os

import config
from flask import Flask, abort, render_template, request

import history_store
import i18n
import module_catalog
import pending_store
import settings_store
import themes
import version
from logos import ensure_default_logo_seeded
from modules.automation.routes import automation_bp
from modules.games.routes import games_bp
from modules.history.routes import history_bp
from modules.images.routes import images_bp
from modules.lists.routes import lists_bp
from modules.message.routes import message_bp
from modules.pending.routes import pending_bp
from modules.settings.routes import settings_bp
from modules.system.routes import system_bp
from modules.weather.routes import weather_bp
from modules.wifi.routes import wifi_bp
from print_queue import enqueue_print, start_worker
from printer import _raw_health_check

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15MB - headroom over MAX_IMAGE_BYTES (12MB, see modules/images/routes.py) for multipart overhead
# Flask 3's own default is None (no Cache-Control max-age at all, only
# ETag/Last-Modified) - every page load re-requests style.css, every
# icon's individual SVG (see the CSS mask-image icon system in
# style.css), and the per-page JS files, each a conditional GET
# round-trip to the Pi even when nothing changed. No cache-busting
# exists anywhere in this codebase (url_for('static', ...) and every
# hardcoded icon URL in style.css are plain, unversioned paths -
# verified, not assumed), so a UI deploy is only picked up once the
# max-age window has actually elapsed, not immediately. 1 hour keeps
# that window short enough that a deploy is visible within the same
# session for anyone actively testing it, while still skipping the
# round-trip entirely for the (far more common) case of just
# navigating between pages within that hour.
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

start_worker()  # start the print queue worker thread
i18n.load_translations()  # load translation JSON files once at startup
ensure_default_logo_seeded()  # one-time: bundled default logo -> STATE_DIR, see logos.py

for blueprint in (lists_bp, message_bp, images_bp, wifi_bp, weather_bp, system_bp, automation_bp, settings_bp, history_bp, pending_bp, games_bp):
    app.register_blueprint(blueprint)


@app.before_request
def _enforce_enabled_modules():
    """Blocks every route (UI page, /ui/* form post, AND /print/*
    API) of a disabled module with a 404 - not just hiding its home
    page tile. request.blueprint is None for routes not in any
    blueprint (e.g. /, /health), so those are always left alone. See
    module_catalog.py for why the blueprint name and the module key
    are guaranteed to match."""
    bp = request.blueprint
    if bp not in module_catalog.MODULE_KEYS:
        return
    enabled = settings_store.get_settings().get("enabled_modules", {})
    if not enabled.get(bp, True):
        abort(404)


@app.context_processor
def inject_i18n():
    """Makes t() and the current language/theme available in every
    template without each route having to pass them explicitly.
    Language and theme are single, shared settings (see settings_store)
    rather than per-session, since this is a single-user home
    appliance."""
    settings = settings_store.get_settings()
    lang = settings.get("language", i18n.DEFAULT_LANGUAGE)
    theme = settings.get("theme", themes.DEFAULT_THEME)
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
        "supported_themes": themes.SUPPORTED_THEMES,
        "current_text_scale": text_scale,
        "all_modules": module_catalog.MODULES,
        "enabled_modules": settings.get("enabled_modules", {}),
        "current_version": version.format_display(version.CURRENT_VERSION),
        "update_available": bool(update_cache.get("update_available")) and bool(latest_version),
        "latest_version": version.format_display(latest_version) if latest_version else None,
        "latest_release_url": update_cache.get("latest_url"),
        "github_repo_url": "https://github.com/HostisHumani/ReceiptPi",
        # Cheap: pending_store stays small (a handful of manually-
        # managed entries at most), so reading it on every request for
        # the burger-menu badge is not a concern the way the settings
        # read above already isn't.
        "pending_count": len(pending_store.get_all()),
    }


@app.route("/health", methods=["GET"])
def health():
    ok, detail, status_code = enqueue_print(_raw_health_check, timeout=10, bypass_rules=True, log_history=False)
    if ok:
        return {"status": "ok", "printer": "reachable"}, 200
    return {"status": "error", "detail": detail}, status_code


@app.route("/", methods=["GET"])
def index():
    enabled = settings_store.get_settings().get("enabled_modules", {})
    active_count = sum(1 for m in module_catalog.MODULES if enabled.get(m["key"], True))
    total_prints = history_store.get_stats()["total"]
    return render_template(
        "home.html",
        active_module_count=active_count,
        total_module_count=len(module_catalog.MODULES),
        total_prints=total_prints,
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
