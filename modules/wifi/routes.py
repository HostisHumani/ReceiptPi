"""
Module: guest wifi slip - SSID/password as readable text plus a wifi QR
code, in a single print job (one printer connection instead of two).

get_guest_wifi_status() is the central place for the Fritz!Box query -
both the manual "print now" button here and
watchers/fritzbox_wifi_watch.py (polling cron job) use the same
function, instead of maintaining the query twice.
"""
import config
import qrcode
from flask import Blueprint, jsonify, render_template
from fritzconnection import FritzConnection
from fritzconnection.lib.fritzwlan import FritzGuestWLAN
from PIL import Image

import i18n
from logos import print_logo
from print_queue import enqueue_print
from printer import get_printer
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token
from text_style import get_text_scale

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
    """Escapes special characters per the WIFI QR format spec
    (\\, ;, ,, :, ") - otherwise the QR code can become invalid if the
    SSID or password contains one of these characters."""
    for char in ("\\", ";", ",", ":", '"'):
        value = value.replace(char, "\\" + char)
    return value


def _raw_print_wifi(ssid, password, auth_type="WPA"):
    qr_source = (
        f"WIFI:T:{auth_type};S:{escape_wifi_qr_value(ssid)};"
        f"P:{escape_wifi_qr_value(password)};;"
    )
    # qrcode.make() returns a qrcode.image.pil.PilImage wrapper whose
    # .width/.height are NOT pixel dimensions (.width is the QR module
    # count, e.g. 29 for a small code) - only .get_image() returns the
    # actual underlying PIL Image with correct pixel .width/.height/
    # .size. Using the wrapper's attributes directly (as an earlier
    # version of this code did) computed a wildly wrong resize ratio
    # and printed a hugely stretched, distorted QR code - found via a
    # real hardware test on 2026-08-09.
    qr_img = qrcode.make(qr_source).get_image()
    qr_target_width = 450
    if qr_img.width < qr_target_width:
        ratio = qr_target_width / qr_img.width
        qr_img = qr_img.resize((qr_target_width, int(qr_img.height * ratio)), Image.NEAREST)

    scale = get_text_scale()
    p = get_printer()
    try:
        logo_printed = print_logo(p, "wifi")
        if logo_printed:
            p.set(align="left", bold=False, width=1, height=1)
            p.text("\n")
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(i18n.tr("receipt.wifi.title") + "\n")
        p.text("\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
        p.text(i18n.tr("receipt.wifi.ssid_label", value=ssid) + "\n")
        p.text("\n")
        p.text(i18n.tr("receipt.wifi.password_label", value=password) + "\n")
        p.text("\n")
        # Always normal (1x1) size for the QR code regardless of
        # print_rules.text_size - explicit bold=False/underline=0 too,
        # not just size, since the image is unrelated to any of this
        # ESC/POS text state and shouldn't inherit it.
        p.set(align="center", bold=False, underline=0, width=1, height=1, custom_size=True)
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
    """
    Expects JSON: { "ssid": "...", "password": "...", "auth_type": "WPA" (optional) }
    """
    data, err = get_json_body()
    if err:
        return err
    ssid = str(data.get("ssid") or "")
    password = str(data.get("password") or "")[:64]
    auth_type = str(data.get("auth_type") or "WPA")
    allowed_auth_types = {"WPA", "WEP", "nopass"}
    if auth_type not in allowed_auth_types:
        return jsonify({"status": "error", "detail": "auth_type must be WPA, WEP or nopass"}), 400
    if not ssid:
        return jsonify({"status": "error", "detail": "ssid is missing"}), 400
    if len(ssid.encode("utf-8")) > 32:
        # 32 bytes is the technical wifi SSID limit - for accented/
        # multi-byte Unicode characters, byte count and character count
        # diverge.
        return jsonify({"status": "error", "detail": "ssid must be at most 32 bytes"}), 400
    ok, detail, status_code = enqueue_print(
        _raw_print_wifi, ssid, password, auth_type, job_type="wifi", summary=ssid, source="api",
        retry_payload={"ssid": ssid, "password": password, "auth_type": auth_type},
    )
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@wifi_bp.route("/ui/wifi", methods=["POST"])
@csrf_protect
def ui_print_wifi():
    """Queries the CURRENT guest network status live from the Fritz!Box
    (not the watcher's cached state) and prints immediately - regardless
    of whether the on/off status has changed since the last watcher
    run."""
    try:
        status = get_guest_wifi_status()
    except Exception as e:
        return render_template(
            "wifi.html", message=i18n.tr("wifi.unreachable_prefix") + str(e), success=False,
            csrf_token=get_csrf_token(),
        )

    if not status["enabled"]:
        message, success = i18n.tr("wifi.disabled"), False
    else:
        auth_type = getattr(config, "WIFI_QR_AUTH_TYPE", "WPA")
        ok, detail, _status_code = enqueue_print(
            _raw_print_wifi, status["ssid"], status["password"], auth_type,
            job_type="wifi", summary=status["ssid"], source="ui",
            retry_payload={"ssid": status["ssid"], "password": status["password"], "auth_type": auth_type},
        )
        message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
        success = ok

    return render_template("wifi.html", message=message, success=success, csrf_token=get_csrf_token())
