"""Free-form message printing module.

_raw_print_message() is also reused by modules that need a simple text receipt,
including weather, system reports, and the boot greeting."""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

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


def _raw_print_message(title, text):
    p = get_printer()
    try:
        p.set(align="center", bold=True, width=2, height=2)
        if title:
            p.text(f"{title}\n")
        p.set(align="left", bold=False, width=1, height=1)
        p.text(f"{text}\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def do_print_message(title, text):
    """Return (success, detail, HTTP status); see enqueue_print()."""
    return enqueue_print(_raw_print_message, title, text)


@message_bp.route("/message", methods=["GET"])
def message_page():
    return render_template("message.html", message=None, success=None, csrf_token=get_csrf_token())


@message_bp.route("/print/message", methods=["POST"])
@require_api_token
def print_message():
    """Accept JSON in the form {"title": "optional", "text": "message"}."""
    data, err = get_json_body()
    if err:
        return err
    title = (data.get("title") or "")[:MAX_TITLE_LEN] or None
    text = (data.get("text") or "")[:MAX_TEXT_LEN]
    ok, detail, status_code = do_print_message(title, text)
    if ok:
        return jsonify({"status": "gedruckt"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@message_bp.route("/ui/message", methods=["POST"])
@csrf_protect
def ui_print_message():
    title = request.form.get("title", "").strip()[:MAX_TITLE_LEN]
    text = request.form.get("text", "").strip()[:MAX_TEXT_LEN]
    ok, detail, _status_code = do_print_message(title, text)
    message = "Gedruckt ✓" if ok else f"Fehler: {detail}"
    return render_template("message.html", message=message, success=ok, csrf_token=get_csrf_token())
