"""
Receipts of the Wi-Fi setup (setup_wifi/daemon.py):

  hotspot    the setup network with its password and a QR code to join it
  failed     the home network couldn't be joined; setup network as above
  connected  the device is on the network, with its address as text and QR

Only the setup service on the device itself calls this endpoint, so it
answers requests from localhost only. The receipts are printed in German
and English: during the setup no account and no language has been chosen
yet. They go through the central print queue like every other print, but
skip the quiet hours: a device that has lost its network can only tell
how to reach it on paper. The setup password never reaches the print
history or the pending jobs - the history entry names only the network,
and there is no retry payload.
"""
import ipaddress
import re

from flask import Blueprint, jsonify, request

import i18n
from print_queue import enqueue_print
from security import get_json_body, require_api_token
from text_style import clean_print_text, get_text_scale, wrap_body_text

setup_wifi_bp = Blueprint("setup_wifi", __name__)

KINDS = ("hotspot", "failed", "connected")
LANGS = ("de", "en")
MAX_SSID_BYTES = 32
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 63
# Where the setup service serves its page while the setup network is up.
SETUP_URL = "http://10.42.0.1/"
_URL_RE = re.compile(r"^http://(?P<host>[0-9.]{7,15}):5000/$")


def _both(key, **kwargs):
    return [i18n.t(key, lang, **kwargs) for lang in LANGS]


def _labelled(key, value):
    """ "WLAN / Wi-Fi: <value>" - one line for both languages."""
    return " / ".join(dict.fromkeys(_both(key))) + ": " + value


def _raw_print_setup_wifi(kind, setup_ssid, setup_password, network, url):
    # Imported here like in the other print modules: python-escpos is
    # only loaded once a receipt is actually printed.
    from modules.wifi.routes import qr_image, wifi_qr_source
    from printer import get_printer

    scale = get_text_scale()
    network_text = clean_print_text(network)
    if kind == "connected":
        title = " / ".join(_both("receipt.setup_wifi.connected_title"))
        lines = []
        if network:
            lines.append(_labelled("receipt.setup_wifi.network_word", network_text))
        lines += _both("receipt.setup_wifi.connected_url", url=url)
        qr = qr_image(url)
    else:
        title = " / ".join(_both("receipt.setup_wifi.title"))
        lines = _both("receipt.setup_wifi.failed", network=network_text) if kind == "failed" else []
        lines += _both("receipt.setup_wifi.step_join")
        qr = qr_image(wifi_qr_source(setup_ssid, setup_password))

    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height,
              custom_size=True)
        p.text(wrap_body_text(title, scale) + "\n\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height,
              custom_size=True)
        for line in lines:
            p.text(wrap_body_text(line, scale) + "\n\n")
        if kind != "connected":
            p.set(align="left", bold=True, width=scale.body_width, height=scale.body_height,
                  custom_size=True)
            p.text(_labelled("receipt.setup_wifi.network_word", clean_print_text(setup_ssid)) + "\n")
            p.text(_labelled("receipt.setup_wifi.password_word", clean_print_text(setup_password))
                   + "\n\n")
        # The QR code always at normal size and without any text style.
        p.set(align="center", bold=False, underline=0, width=1, height=1, custom_size=True)
        p.image(qr)
        if kind != "connected":
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height,
                  custom_size=True)
            p.text("\n")
            for line in _both("receipt.setup_wifi.step_page", url=SETUP_URL):
                p.text(wrap_body_text(line, scale) + "\n\n")
        p.cut()
    finally:
        p.close()


def _is_local_request():
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _valid_url(url):
    m = _URL_RE.match(url)
    if not m:
        return False
    try:
        ipaddress.IPv4Address(m["host"])
    except ValueError:
        return False
    return True


def _ssid_ok(value, required=True):
    size = len(value.encode("utf-8"))
    return (size > 0 or not required) and size <= MAX_SSID_BYTES


@setup_wifi_bp.route("/print/setup-wifi", methods=["POST"])
@require_api_token
def print_setup_wifi():
    """
    Expects JSON from the setup service:
      {"kind": "hotspot", "ssid": <setup network>, "password": <its password>}
      {"kind": "failed", "ssid": ..., "password": ..., "network": <home network>}
      {"kind": "connected", "network": <home network, may be "">, "url": "http://<ip>:5000/"}
    """
    if not _is_local_request():
        return jsonify({"status": "error", "detail": "only available on the device itself"}), 403
    data, err = get_json_body()
    if err:
        return err
    kind = str(data.get("kind") or "")
    if kind not in KINDS:
        return jsonify({"status": "error", "detail": "kind must be hotspot, failed or connected"}), 400
    network = str(data.get("network") or "")
    if not _ssid_ok(network, required=(kind == "failed")):
        return jsonify({"status": "error", "detail": "network must be 1 to 32 bytes"}), 400

    setup_ssid = setup_password = url = ""
    if kind == "connected":
        url = str(data.get("url") or "")
        if not _valid_url(url):
            return jsonify({"status": "error", "detail": "url must be http://<IPv4>:5000/"}), 400
    else:
        setup_ssid = str(data.get("ssid") or "")
        setup_password = str(data.get("password") or "")
        if not _ssid_ok(setup_ssid):
            return jsonify({"status": "error", "detail": "ssid must be 1 to 32 bytes"}), 400
        if not MIN_PASSWORD_LEN <= len(setup_password) <= MAX_PASSWORD_LEN:
            return jsonify({"status": "error", "detail": "password must be 8 to 63 characters"}), 400

    ok, detail, status_code = enqueue_print(
        _raw_print_setup_wifi, kind, setup_ssid, setup_password, network, url,
        job_type="setup_wifi", summary=network or setup_ssid, source="api",
        bypass_quiet_hours=True, retry_payload=None,
    )
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code
