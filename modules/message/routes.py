"""
Module: status messages (free title + text). Used to be called "status",
which could be confused with the server status module (now "system") -
"message" is clearer: just a text message on the receipt.

_raw_print_message is also used by other modules (weather, system, boot
greeting) as the shared "plain text on receipt" primitive.
"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

import i18n
from logos import print_logo
from print_queue import enqueue_print
from printer import get_printer
from security import (
    MAX_TEXT_LEN,
    MAX_TITLE_LEN,
    csrf_protect,
    get_csrf_token,
    get_json_body,
    require_api_token,
)

message_bp = Blueprint("message", __name__)


def _raw_print_message(title, text, module="message"):
    """module: which logo-settings entry to use (see logos.py). Defaults
    to "message" for the direct /message route - the system report
    (modules/system/routes.py) reuses this exact function as its "plain
    text on receipt" primitive but passes module="system", so it prints
    its own configured logo instead of the message module's."""
    p = get_printer()
    try:
        logo_printed = print_logo(p, module)
        if logo_printed:
            p.set(align="left", bold=False, width=1, height=1)
            p.text("\n")
        p.set(align="center", bold=True, width=1, height=2, custom_size=True)
        if title:
            p.text(f"{title}\n")
            p.text("\n")
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text(f"{text}\n")
        p.text("\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def do_print_message(title, text, source="ui"):
    """Returns (ok, detail, http_status), see enqueue_print(). summary
    for the history dashboard: the title if there is one, otherwise the
    start of the text itself."""
    summary = title or (text[:60] + ("…" if len(text) > 60 else ""))
    return enqueue_print(_raw_print_message, title, text, job_type="message", summary=summary, source=source)


@message_bp.route("/message", methods=["GET"])
def message_page():
    return render_template("message.html", message=None, success=None, csrf_token=get_csrf_token())


@message_bp.route("/print/message", methods=["POST"])
@require_api_token
def print_message():
    """
    Expects JSON: { "title": "optional", "text": "message" }
    """
    data, err = get_json_body()
    if err:
        return err
    title = (data.get("title") or "")[:MAX_TITLE_LEN] or None
    text = (data.get("text") or "")[:MAX_TEXT_LEN]
    ok, detail, status_code = do_print_message(title, text, source="api")
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@message_bp.route("/ui/message", methods=["POST"])
@csrf_protect
def ui_print_message():
    title = request.form.get("title", "").strip()[:MAX_TITLE_LEN]
    text = request.form.get("text", "").strip()[:MAX_TEXT_LEN]
    ok, detail, _status_code = do_print_message(title, text)
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template("message.html", message=message, success=ok, csrf_token=get_csrf_token())
