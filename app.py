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
import config
from flask import Flask, render_template

import i18n
import settings_store
import themes
from logos import ensure_default_logo_seeded
from modules.automation.routes import automation_bp
from modules.history.routes import history_bp
from modules.images.routes import images_bp
from modules.message.routes import message_bp
from modules.settings.routes import settings_bp
from modules.shopping.routes import shopping_bp
from modules.system.routes import system_bp
from modules.weather.routes import weather_bp
from modules.wifi.routes import wifi_bp
from print_queue import enqueue_print, start_worker
from printer import _raw_health_check

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15MB - headroom over MAX_IMAGE_BYTES (12MB, see modules/images/routes.py) for multipart overhead

INVALID_SECRET_KEYS = {
    "",
    "Hier zufälligen Wert per secrets.token_hex(32) einsetzen",
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

for blueprint in (shopping_bp, message_bp, images_bp, wifi_bp, weather_bp, system_bp, automation_bp, settings_bp, history_bp):
    app.register_blueprint(blueprint)


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
    return {
        "t": lambda key, **kw: i18n.t(key, lang, **kw),
        "current_language": lang,
        "supported_languages": i18n.SUPPORTED_LANGUAGES,
        "current_theme": theme,
        "supported_themes": themes.SUPPORTED_THEMES,
    }


@app.route("/health", methods=["GET"])
def health():
    ok, detail, status_code = enqueue_print(_raw_health_check, timeout=10, bypass_rules=True, log_history=False)
    if ok:
        return {"status": "ok", "printer": "reachable"}, 200
    return {"status": "error", "detail": detail}, status_code


@app.route("/", methods=["GET"])
def index():
    return render_template("home.html")


if __name__ == "__main__":
    # Only for local test runs (`python3 app.py`). In production the
    # server runs via Gunicorn with gunicorn.conf.py (see ANLEITUNG.md).
    import socket

    import history_store
    from modules.message.routes import _raw_print_message
    from printer import get_local_ip

    hostname = socket.gethostname()
    try:
        ip = get_local_ip()
        text = f"Hostname: {hostname}\nIP: {ip}\nReceiptPi server started (dev mode)"
        _raw_print_message("ONLINE", text)
    except Exception:
        pass  # the boot greeting is "nice to have", not a reason to block the dev server
    else:
        history_store.log_job("boot", f"{hostname} ({ip})", "system", "ok")

    app.run(host="0.0.0.0", port=5000)
