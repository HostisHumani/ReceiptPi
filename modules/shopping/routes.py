"""Shopping-list module for titled lists with printable checkboxes."""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from print_queue import enqueue_print
from printer import get_printer
from security import (
    MAX_ITEM_LEN,
    MAX_ITEMS,
    MAX_TITLE_LEN,
    csrf_protect,
    get_csrf_token,
    get_json_body,
    require_api_token,
)

shopping_bp = Blueprint("shopping", __name__)


def _raw_print_list(title, items):
    p = get_printer()
    try:
        p.set(align="center", bold=True, width=2, height=2)
        p.text(f"{title}\n")
        p.set(align="left", bold=False, width=1, height=1)
        p.text("-" * 32 + "\n")
        for item in items:
            if item.strip():
                p.text(f"[ ] {item.strip()}\n")
        p.text("-" * 32 + "\n")
        p.set(align="center")
        p.text(f"{datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        p.cut()
    finally:
        p.close()


def do_print_list(title, items):
    """Return (success, detail, HTTP status); see enqueue_print()."""
    ok, detail, status_code = enqueue_print(_raw_print_list, title, items)
    if ok:
        detail = f"{len(items)} Punkte gedruckt"
    return ok, detail, status_code


@shopping_bp.route("/shopping", methods=["GET"])
def shopping_page():
    return render_template("shopping.html", message=None, success=None, csrf_token=get_csrf_token())


@shopping_bp.route("/print/list", methods=["POST"])
@require_api_token
def print_list():
    """Accept JSON in the form {"title": "Shopping list", "items": [...]}."""
    data, err = get_json_body()
    if err:
        return err
    title = (data.get("title") or "Liste")[:MAX_TITLE_LEN]
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        return jsonify({"status": "error", "detail": "items muss eine Liste sein"}), 400
    items = [str(item)[:MAX_ITEM_LEN] for item in raw_items[:MAX_ITEMS]]
    ok, detail, status_code = do_print_list(title, items)
    if ok:
        return jsonify({"status": "gedruckt", "detail": detail}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@shopping_bp.route("/ui/list", methods=["POST"])
@csrf_protect
def ui_print_list():
    title = (request.form.get("title", "Liste").strip() or "Liste")[:MAX_TITLE_LEN]
    raw_items = request.form.get("items", "")
    items = [line.strip()[:MAX_ITEM_LEN] for line in raw_items.splitlines() if line.strip()][:MAX_ITEMS]
    ok, detail, _status_code = do_print_list(title, items)
    message = "Gedruckt ✓" if ok else f"Fehler: {detail}"
    return render_template("shopping.html", message=message, success=ok, csrf_token=get_csrf_token())
