"""
Module: shopping list / lists - title + one item per line, prints a list
with checkboxes.
"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

import i18n
from logos import print_logo
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
        logo_printed = print_logo(p, "shopping")
        if logo_printed:
            p.set(align="left", bold=False, width=1, height=1)
            p.text("\n")
        p.set(align="center", bold=True, width=1, height=2, custom_size=True)
        p.text(f"{title}\n")
        p.text("\n")
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text("-" * 32 + "\n")
        # ESC 3 n: slightly wider line spacing than the printer's default
        # (~30 dots), just for the item list - makes a long list easier
        # to read without wasting much extra paper. Reset back to the
        # default afterward so it doesn't affect anything printed after
        # the list (footer date, subsequent jobs, etc.).
        p._raw(bytes([0x1B, 0x33, 38]))
        for item in items:
            if item.strip():
                # "( )" instead of "[ ]": square brackets fall in the
                # code range that some printers remap under a national
                # ISO 646 character set (e.g. "[" -> "Ä", "]" -> "Ü" on
                # the German variant - see the comment in printer.py for
                # the actual fix). Parentheses aren't affected by any of
                # the common national variants, so they print correctly
                # as a checkbox even if that fix somehow doesn't apply.
                p.text(f"( ) {item.strip()}\n")
        p._raw(bytes([0x1B, 0x32]))  # ESC 2: back to default line spacing
        p.text("-" * 32 + "\n")
        p.text("\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def do_print_list(title, items, source="ui"):
    """Returns (ok, detail, http_status), see enqueue_print()."""
    summary = f"{title} ({len(items)})"
    ok, detail, status_code = enqueue_print(
        _raw_print_list, title, items, job_type="shopping", summary=summary, source=source,
    )
    if ok:
        detail = f"{len(items)} items printed"
    return ok, detail, status_code


@shopping_bp.route("/shopping", methods=["GET"])
def shopping_page():
    return render_template(
        "shopping.html", message=None, success=None, csrf_token=get_csrf_token(),
        default_title=i18n.tr("shopping.title_default"),
    )


@shopping_bp.route("/print/list", methods=["POST"])
@require_api_token
def print_list():
    """
    Expects JSON: { "title": "Shopping list", "items": ["Milk", "Bread", ...] }
    """
    data, err = get_json_body()
    if err:
        return err
    title = (data.get("title") or "List")[:MAX_TITLE_LEN]
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        return jsonify({"status": "error", "detail": "items must be a list"}), 400
    items = [str(item)[:MAX_ITEM_LEN] for item in raw_items[:MAX_ITEMS]]
    ok, detail, status_code = do_print_list(title, items, source="api")
    if ok:
        return jsonify({"status": "printed", "detail": detail}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@shopping_bp.route("/ui/list", methods=["POST"])
@csrf_protect
def ui_print_list():
    default_title = i18n.tr("shopping.title_default")
    title = (request.form.get("title", default_title).strip() or default_title)[:MAX_TITLE_LEN]
    raw_items = request.form.get("items", "")
    items = [line.strip()[:MAX_ITEM_LEN] for line in raw_items.splitlines() if line.strip()][:MAX_ITEMS]
    ok, detail, _status_code = do_print_list(title, items)
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template(
        "shopping.html", message=message, success=ok, csrf_token=get_csrf_token(),
        default_title=default_title,
    )
