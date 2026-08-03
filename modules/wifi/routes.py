"""Guest Wi-Fi receipt module.

Prints the SSID and password together with a Wi-Fi QR code in one queued job.
The Fritz!Box status lookup is shared with the polling watcher."""
import qrcode
from flask import Blueprint, jsonify, render_template, request
from fritzconnection import FritzConnection
from fritzconnection.lib.fritzwlan import FritzGuestWLAN
from PIL import Image

import config
from printer import get_printer
from print_queue import enqueue_print
from security import require_api_token, csrf_protect, get_csrf_token, get_json_body

wifi_bp = Blueprint("wifi", __name__)


def get_guest_wifi_status():
    fc = FritzConnection(
        address=config.FRITZBOX_ADDRESS,
        user=config.FRITZBOX_USER,
        password=config.FRITZBOX_PASSWORD,
    )
    guest_wlan = FritzGuestWLAN(fc=fc)
    return {
        "enabled": guest_wlan.is_enabled,
        "ssid": guest_wlan.ssid,
        "password": guest_wlan.get_password(),
    }


def escape_wifi_qr_value(value):
    """Escape reserved characters used by the Wi-Fi QR format."""
    for char in ("\\", ";", ",", ":", '"'):
        value = value.replace(char, "\\" + char)
    return value


def _raw_print_wifi(ssid, password, auth_type="WPA"):
    qr_source = (
        f"WIFI:T:{auth_type};S:{escape_wifi_qr_value(ssid)};"
        f"P:{escape_wifi_qr_value(password)};;"
    )
    qr_img = qrcode.make(qr_source)
    qr_target_width = 450
    if qr_img.width < qr_target_width:
        ratio = qr_target_width / qr_img.width
        qr_img = qr_img.resize((qr_target_width, int(qr_img.height * ratio)), Image.NEAREST)

    p = get_printer()
    try:
        p.set(align="center", bold=True, width=2, height=2)
        p.text("GAESTE-WLAN\n")
        p.set(align="left", bold=False, width=1, height=1)
        p.text(f"SSID: {ssid}\n")
        p.text(f"Passwort: {password}\n")
        p.set(align="center")
        p.image(qr_img)
        p.cut()
    finally:
        p.close()


@wifi_bp.route("/wifi", methods=["GET"])
def wifi_page():
    return render_template("wifi.html", message=None, success=None, csrf_token=get_csrf_token())


@wifi_bp.route("/print/wifi", methods=["POST"])
@require_api_token
def print_wifi():
    """Accept SSID, password, and an optional authentication type as JSON."""
    data, err = get_json_body()
    if err:
        return err
    ssid = str(data.get("ssid") or "")
    password = str(data.get("password") or "")[:64]
    auth_type = str(data.get("auth_type") or "WPA")
    allowed_auth_types = {"WPA", "WEP", "nopass"}
    if auth_type not in allowed_auth_types:
        return jsonify({"status": "error", "detail": "auth_type muss WPA, WEP oder nopass sein"}), 400
    if not ssid:
        return jsonify({"status": "error", "detail": "ssid fehlt"}), 400
    if len(ssid.encode("utf-8")) > 32:
        # Wi-Fi SSIDs are limited to 32 bytes, not 32 Unicode characters.
        #
        return jsonify({"status": "error", "detail": "ssid darf maximal 32 Bytes lang sein"}), 400
    ok, detail, status_code = enqueue_print(_raw_print_wifi, ssid, password, auth_type)
    if ok:
        return jsonify({"status": "gedruckt"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@wifi_bp.route("/ui/wifi", methods=["POST"])
@csrf_protect
def ui_print_wifi():
    """Read the current Fritz!Box guest Wi-Fi state and print it immediately."""
    try:
        status = get_guest_wifi_status()
    except Exception as e:
        return render_template(
            "wifi.html", message=f"Fritz!Box nicht erreichbar: {e}", success=False,
            csrf_token=get_csrf_token(),
        )

    if not status["enabled"]:
        message, success = "Gästenetz ist aktuell deaktiviert - nichts zu drucken", False
    else:
        auth_type = getattr(config, "WIFI_QR_AUTH_TYPE", "WPA")
        ok, detail, _status_code = enqueue_print(_raw_print_wifi, status["ssid"], status["password"], auth_type)
        message = "Gedruckt ✓" if ok else f"Fehler: {detail}"
        success = ok

    return render_template("wifi.html", message=message, success=success, csrf_token=get_csrf_token())
