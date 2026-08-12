"""
Module: lists - shopping lists, to-do lists, and printable task/kanban
cards. Grew out of the original shopping-only module; /shopping and
/print/list keep working unchanged for backward compatibility (see
module_catalog.py and settings_store's shopping->lists module-key
migration), now living alongside /lists/todo and /lists/task under one
shared "lists" module toggle.

UI and API routes share the same underlying print/validation
functions (do_print_list/do_print_task, parse_task_fields) rather than
each re-implementing their own - see the shared section below.
"""
import textwrap
from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request

import i18n
import lists_store
from logos import print_logo
from print_queue import enqueue_print
from printer import get_printer
from security import (
    MAX_ITEM_LEN,
    MAX_ITEMS,
    MAX_TEXT_LEN,
    MAX_TITLE_LEN,
    csrf_protect,
    get_csrf_token,
    get_json_body,
    require_api_token,
)
from text_style import BODY_COLUMNS, get_text_scale

lists_bp = Blueprint("lists", __name__)

PRIORITIES = ("low", "medium", "high")

# ---------------------------------------------------------------
# Shared print primitives (used by shopping/to-do lists AND the task
# card's optional checklist section)
# ---------------------------------------------------------------


def _print_checklist(p, items):
    """Prints one checkbox item per line, word-wrapped with
    continuation lines indented under the checkbox. "( )" instead of
    "[ ]": square brackets fall in the code range some printers remap
    under a national ISO 646 character set (see printer.py); the fix
    there covers this centrally, but parentheses are unaffected either
    way."""
    scale = get_text_scale()
    checkbox_prefix = "( ) "
    # textwrap.fill's width already accounts for initial_indent/
    # subsequent_indent internally - it must NOT be reduced by the
    # prefix length here too, or lines end up narrower than intended.
    cols = max(BODY_COLUMNS.get(scale.body_width, 42), 10)
    for item in items:
        if item.strip():
            wrapped = textwrap.fill(
                item.strip(), width=cols,
                initial_indent=checkbox_prefix, subsequent_indent=" " * len(checkbox_prefix),
            )
            p.text(wrapped + "\n")


# ---------------------------------------------------------------
# Shopping list / to-do list - identical mechanism (title + checklist),
# distinguished only by job_type/logo slot/heading text.
# ---------------------------------------------------------------


def _raw_print_list(title, items, with_logo):
    """with_logo: only True for actual shopping-list prints (reuses the
    existing "shopping" logo slot in logos.py, unchanged) - to-do
    prints go through this same shared function but never show a logo,
    since there's no dedicated to-do/lists logo slot (yet)."""
    scale = get_text_scale()
    p = get_printer()
    try:
        if with_logo:
            logo_printed = print_logo(p, "shopping")
            if logo_printed:
                p.set(align="left", bold=False, width=1, height=1)
                p.text("\n")
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(f"{title}\n")
        p.text("\n")
        # Separator stays at normal size on purpose, even in Easy-Read
        # mode - it's a plain divider line, not content to read.
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text("-" * 32 + "\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
        # ESC 3 n: slightly wider line spacing than the printer's default
        # for the item list only, reset right after.
        p._raw(bytes([0x1B, 0x33, 38]))
        _print_checklist(p, items)
        p._raw(bytes([0x1B, 0x32]))  # ESC 2: back to default line spacing
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text("-" * 32 + "\n")
        p.text("\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def _parse_draft_loaded_at():
    """Parses the hidden draft_loaded_at field a draft-save request
    carries (see templates/{shopping,lists_todo,lists_task}.html and
    lists_store.save_draft()'s `loaded_at` docstring) - a missing or
    malformed value is treated as "no token" (save proceeds without
    the staleness check) rather than rejected outright, since this is
    only a best-effort race guard, not a security boundary."""
    raw = request.form.get("draft_loaded_at")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def do_print_list(title, items, job_type="shopping", source="ui"):
    """Returns (ok, detail, http_status), see enqueue_print(). job_type
    distinguishes shopping ("shopping", unchanged from before this
    module existed) from to-do ("todo") prints in the history log -
    the print routine itself is otherwise identical for both, except
    that only shopping shows a logo (see _raw_print_list). job_type
    doubles as the lists_store draft "kind" (the two happen to use the
    exact same strings) - a successful print clears the matching
    draft, whether it came from the UI form or a direct /print/list
    /print/todo API call, so a list that just got printed some other
    way doesn't linger pre-filled in the browser."""
    summary = f"{title} ({len(items)})"
    ok, detail, status_code = enqueue_print(
        _raw_print_list, title, items, job_type == "shopping",
        job_type=job_type, summary=summary, source=source,
        retry_payload={"title": title, "items": items},
    )
    if ok:
        detail = f"{len(items)} items printed"
        lists_store.clear_draft(job_type)
    return ok, detail, status_code


# ---------------------------------------------------------------
# Task / kanban card - title + optional description + optional
# checklist + optional priority + optional due date. Priority/due date
# lines are only ever written when actually set - no empty
# placeholders on the receipt.
# ---------------------------------------------------------------


def parse_task_fields(data):
    """Validates and normalizes task-card fields - the single source
    of truth shared by the UI form handler and the JSON API, so the
    two paths can never validate or print differently. `data` is a
    plain dict; the UI and API handlers each normalize their own input
    shape (form fields vs. JSON body) into this shape before calling in.

    Returns (fields, None) on success, or (None, (detail, http_status))
    on a validation error. Empty optional fields are fine; a
    non-empty but invalid priority/due_date is a hard error rather
    than being silently dropped.
    """
    title = (data.get("title") or "").strip()[:MAX_TITLE_LEN]
    if not title:
        return None, ("title is required", 400)

    description = (data.get("description") or "").strip()[:MAX_TEXT_LEN]

    raw_items = data.get("items") or []
    if not isinstance(raw_items, list):
        return None, ("items must be a list", 400)
    items = [str(item)[:MAX_ITEM_LEN] for item in raw_items[:MAX_ITEMS] if str(item).strip()]

    priority = (data.get("priority") or "").strip().lower()
    if priority and priority not in PRIORITIES:
        return None, (f"priority must be one of: {', '.join(PRIORITIES)}", 400)

    due_date = (data.get("due_date") or "").strip()
    if due_date:
        try:
            date.fromisoformat(due_date)
        except ValueError:
            return None, ("due_date must be in YYYY-MM-DD format", 400)

    return {
        "title": title,
        "description": description,
        "items": items,
        "priority": priority or None,
        "due_date": due_date or None,
    }, None


def _raw_print_task(fields):
    """No logo - there's no dedicated task/lists logo slot (yet), and
    reusing the shopping slot here would show a shopping-specific logo
    on an unrelated task card."""
    scale = get_text_scale()
    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(f"{fields['title']}\n")
        p.text("\n")
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)

        if fields["description"]:
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            cols = BODY_COLUMNS.get(scale.body_width, 42)
            p.text(textwrap.fill(fields["description"], width=cols) + "\n")
            p.text("\n")
            p.set(align="left", bold=False, width=1, height=1, custom_size=True)

        if fields["items"]:
            # Own visual section, set off from the title/description by
            # the blank line above - same checkbox styling as
            # shopping/to-do (_print_checklist), different placement.
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            p._raw(bytes([0x1B, 0x33, 38]))
            _print_checklist(p, fields["items"])
            p._raw(bytes([0x1B, 0x32]))
            p.text("\n")
            p.set(align="left", bold=False, width=1, height=1, custom_size=True)

        if fields["priority"] or fields["due_date"]:
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            if fields["priority"]:
                p.text(f"{i18n.tr('lists.task.priority_label')}: {i18n.tr('priority.' + fields['priority'])}\n")
            if fields["due_date"]:
                due = date.fromisoformat(fields["due_date"]).strftime("%d.%m.%Y")
                p.text(f"{i18n.tr('lists.task.due_label')}: {due}\n")
            p.text("\n")
            # Reset to normal size only now, right before the footer -
            # not earlier, so priority/due follow Easy-Read scaling
            # like every other body text on the card.
            p.set(align="left", bold=False, width=1, height=1, custom_size=True)

        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def do_print_task(fields, source="ui"):
    """Returns (ok, detail, http_status), see enqueue_print()."""
    ok, detail, status_code = enqueue_print(
        _raw_print_task, fields, job_type="task", summary=fields["title"], source=source,
        retry_payload=dict(fields),
    )
    if ok:
        detail = "task card printed"
        lists_store.clear_draft("task")
    return ok, detail, status_code


# ---------------------------------------------------------------
# Routes
# ---------------------------------------------------------------


@lists_bp.route("/lists", methods=["GET"])
def lists_index():
    return render_template("lists_index.html")


@lists_bp.route("/shopping", methods=["GET"])
@lists_bp.route("/lists/shopping", methods=["GET"])
def shopping_page():
    default_title = i18n.tr("shopping.title_default")
    draft = lists_store.get_draft("shopping")
    return render_template(
        "shopping.html", message=None, success=None, csrf_token=get_csrf_token(),
        default_title=default_title,
        draft_title=draft.get("title", default_title),
        draft_items=draft.get("items", ""),
        draft_loaded_at=lists_store.now(),
    )


@lists_bp.route("/print/list", methods=["POST"])
@require_api_token
def print_list():
    """Expects JSON: { "title": "Shopping list", "items": ["Milk", "Bread", ...] }
    Unchanged since before the lists module existed - kept fully
    backward compatible for existing integrations."""
    data, err = get_json_body()
    if err:
        return err
    title = (data.get("title") or "List")[:MAX_TITLE_LEN]
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        return jsonify({"status": "error", "detail": "items must be a list"}), 400
    items = [str(item)[:MAX_ITEM_LEN] for item in raw_items[:MAX_ITEMS]]
    ok, detail, status_code = do_print_list(title, items, job_type="shopping", source="api")
    if ok:
        return jsonify({"status": "printed", "detail": detail}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@lists_bp.route("/ui/list", methods=["POST"])
@csrf_protect
def ui_print_list():
    default_title = i18n.tr("shopping.title_default")
    title = (request.form.get("title", default_title).strip() or default_title)[:MAX_TITLE_LEN]
    raw_items = request.form.get("items", "")
    items = [line.strip()[:MAX_ITEM_LEN] for line in raw_items.splitlines() if line.strip()][:MAX_ITEMS]
    ok, detail, _status_code = do_print_list(title, items, job_type="shopping")
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template(
        "shopping.html", message=message, success=ok, csrf_token=get_csrf_token(),
        default_title=default_title,
        # On success the draft was just cleared - show a blank form for
        # the next list. On failure (rate limit, quiet hours, ...) echo
        # back exactly what was submitted, matching the untouched draft
        # still on disk, instead of silently losing it like before.
        draft_title=default_title if ok else title,
        draft_items="" if ok else raw_items,
        # Fresh token either way - on success this must be newer than
        # the clear_draft() that just ran (do_print_list stores its
        # own now() *inside* clear_draft, strictly before this line
        # executes), so typing again on this same re-rendered page
        # autosaves normally instead of being rejected as stale.
        draft_loaded_at=lists_store.now(),
    )


@lists_bp.route("/ui/list/draft", methods=["POST"])
@csrf_protect
def save_list_draft():
    """Called by static/draft-autosave.js a short while after typing
    stops - saves only, never prints. No response body needed."""
    lists_store.save_draft("shopping", {
        "title": request.form.get("title", ""),
        "items": request.form.get("items", ""),
    }, loaded_at=_parse_draft_loaded_at())
    return ("", 204)


@lists_bp.route("/lists/todo", methods=["GET"])
def todo_page():
    default_title = i18n.tr("lists.todo.title_default")
    draft = lists_store.get_draft("todo")
    return render_template(
        "lists_todo.html", message=None, success=None, csrf_token=get_csrf_token(),
        default_title=default_title,
        draft_title=draft.get("title", default_title),
        draft_items=draft.get("items", ""),
        draft_loaded_at=lists_store.now(),
    )


@lists_bp.route("/print/todo", methods=["POST"])
@require_api_token
def print_todo():
    """Expects JSON: { "title": "To-Do", "items": ["Task 1", ...] }"""
    data, err = get_json_body()
    if err:
        return err
    title = (data.get("title") or i18n.tr("lists.todo.title_default"))[:MAX_TITLE_LEN]
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        return jsonify({"status": "error", "detail": "items must be a list"}), 400
    items = [str(item)[:MAX_ITEM_LEN] for item in raw_items[:MAX_ITEMS]]
    ok, detail, status_code = do_print_list(title, items, job_type="todo", source="api")
    if ok:
        return jsonify({"status": "printed", "detail": detail}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@lists_bp.route("/ui/todo", methods=["POST"])
@csrf_protect
def ui_print_todo():
    default_title = i18n.tr("lists.todo.title_default")
    title = (request.form.get("title", default_title).strip() or default_title)[:MAX_TITLE_LEN]
    raw_items = request.form.get("items", "")
    items = [line.strip()[:MAX_ITEM_LEN] for line in raw_items.splitlines() if line.strip()][:MAX_ITEMS]
    ok, detail, _status_code = do_print_list(title, items, job_type="todo")
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template(
        "lists_todo.html", message=message, success=ok, csrf_token=get_csrf_token(),
        default_title=default_title,
        draft_title=default_title if ok else title,
        draft_items="" if ok else raw_items,
        draft_loaded_at=lists_store.now(),
    )


@lists_bp.route("/ui/todo/draft", methods=["POST"])
@csrf_protect
def save_todo_draft():
    lists_store.save_draft("todo", {
        "title": request.form.get("title", ""),
        "items": request.form.get("items", ""),
    }, loaded_at=_parse_draft_loaded_at())
    return ("", 204)


@lists_bp.route("/lists/task", methods=["GET"])
def task_page():
    draft = lists_store.get_draft("task")
    return render_template(
        "lists_task.html", message=None, success=None, csrf_token=get_csrf_token(),
        draft_title=draft.get("title", ""),
        draft_description=draft.get("description", ""),
        draft_items=draft.get("items", ""),
        draft_priority=draft.get("priority", ""),
        draft_due_date=draft.get("due_date", ""),
        draft_loaded_at=lists_store.now(),
    )


@lists_bp.route("/print/task", methods=["POST"])
@require_api_token
def print_task():
    """Expects JSON: { "title": "...", "description": "...", "items":
    [...], "priority": "low"|"medium"|"high", "due_date": "YYYY-MM-DD" }
    - all fields besides title are optional. Suitable for a Home
    Assistant rest_command (or any plain REST/cURL caller): same
    X-Api-Token auth, print queue, quiet hours, rate limit and history
    logging as every other /print/* endpoint - no Home-Assistant-
    specific code involved."""
    data, err = get_json_body()
    if err:
        return err
    fields, error = parse_task_fields(data)
    if error:
        detail, status = error
        return jsonify({"status": "error", "detail": detail}), status
    ok, detail, status_code = do_print_task(fields, source="api")
    if ok:
        return jsonify({"status": "printed", "detail": detail}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@lists_bp.route("/ui/task", methods=["POST"])
@csrf_protect
def ui_print_task():
    form_data = {
        "title": request.form.get("title", ""),
        "description": request.form.get("description", ""),
        "items": [line.strip() for line in request.form.get("items", "").splitlines() if line.strip()],
        "priority": request.form.get("priority", ""),
        "due_date": request.form.get("due_date", ""),
    }
    fields, error = parse_task_fields(form_data)
    if error:
        detail, _status = error
        message = i18n.tr("print.error_prefix") + detail
        # Validation error - draft on disk is untouched, echo back
        # exactly what was submitted rather than losing it.
        return render_template(
            "lists_task.html", message=message, success=False, csrf_token=get_csrf_token(),
            draft_title=request.form.get("title", ""),
            draft_description=request.form.get("description", ""),
            draft_items=request.form.get("items", ""),
            draft_priority=request.form.get("priority", ""),
            draft_due_date=request.form.get("due_date", ""),
            draft_loaded_at=lists_store.now(),
        )
    ok, detail, _status_code = do_print_task(fields, source="ui")
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template(
        "lists_task.html", message=message, success=ok, csrf_token=get_csrf_token(),
        # On success the draft was just cleared - blank form for the
        # next card. On failure (rate limit/quiet hours) echo back what
        # was submitted, matching the untouched draft still on disk.
        draft_title="" if ok else request.form.get("title", ""),
        draft_description="" if ok else request.form.get("description", ""),
        draft_items="" if ok else request.form.get("items", ""),
        draft_priority="" if ok else request.form.get("priority", ""),
        draft_due_date="" if ok else request.form.get("due_date", ""),
        draft_loaded_at=lists_store.now(),
    )


@lists_bp.route("/ui/task/draft", methods=["POST"])
@csrf_protect
def save_task_draft():
    lists_store.save_draft("task", {
        "title": request.form.get("title", ""),
        "description": request.form.get("description", ""),
        "items": request.form.get("items", ""),
        "priority": request.form.get("priority", ""),
        "due_date": request.form.get("due_date", ""),
    }, loaded_at=_parse_draft_loaded_at())
    return ("", 204)
