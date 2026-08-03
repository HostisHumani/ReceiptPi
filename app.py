"""ReceiptPi server for Epson TM-T88V and other ESC/POS printers.

Architecture:
  - print_queue.py      Central print queue and print rules
  - printer.py          Printer hardware access
  - security.py         CSRF protection, API token validation, JSON parsing
  - settings_store.py   Runtime settings stored as JSON in STATE_DIR
  - modules/            One Flask blueprint per feature
  - watchers/           Standalone polling scripts executed outside this process

This module intentionally stays small: it creates the Flask application,
registers blueprints, exposes the health check, and serves the home page.

The boot greeting is triggered by Gunicorn's on_starting hook so it runs once
in the master process. For local development, the __main__ block performs the
same action before starting Flask."""
import config
from flask import Flask, render_template

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
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15 MB leaves room for multipart overhead above the 12 MB image limit.

INVALID_SECRET_KEYS = {
    "",
    "Hier zufälligen Wert per secrets.token_hex(32) einsetzen",
}
if getattr(config, "SECRET_KEY", "") in INVALID_SECRET_KEYS:
    raise RuntimeError(
        "SECRET_KEY ist nicht konfiguriert (noch der Platzhalter aus "
        "config.example.py). Bitte in config.py einen zufälligen Wert "
        "eintragen: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.secret_key = config.SECRET_KEY  # Required for CSRF-protected web sessions.

start_worker()  # Start the single print-queue worker.

for blueprint in (shopping_bp, message_bp, images_bp, wifi_bp, weather_bp, system_bp, settings_bp):
    app.register_blueprint(blueprint)


@app.route("/health", methods=["GET"])
def health():
    ok, detail, status_code = enqueue_print(_raw_health_check, timeout=10, bypass_rules=True)
    if ok:
        return {"status": "ok", "printer": "erreichbar"}, 200
    return {"status": "error", "detail": detail}, status_code


@app.route("/", methods=["GET"])
def index():
    return render_template("home.html")


if __name__ == "__main__":
    # Local development only. Production uses Gunicorn with gunicorn.conf.py.
    #
    import socket

    from modules.message.routes import _raw_print_message
    from printer import get_local_ip

    try:
        hostname = socket.gethostname()
        ip = get_local_ip()
        text = f"Hostname: {hostname}\nIP: {ip}\nReceiptPi-Server gestartet (Dev-Modus)"
        _raw_print_message("ONLINE", text)
    except Exception:
        pass  # A failed boot greeting must not prevent the development server from starting.

    app.run(host="0.0.0.0", port=5000)
