"""
Module: dashboard / print history - read-only view over history_store
(SQLite), reachable from the burger menu. This module doesn't print
anything and doesn't write to the history table itself - it only
displays what print_queue.py already logged centrally for every other
module's print jobs (see history_store.log_job()).

Every logged-in account can open it and sees only its own jobs; admins
see all of them and can filter by owner (see owners.py). Totals and
pages are counted within the same scope as the list.
"""
from flask import Blueprint, render_template, request

import history_store
import i18n
from modules.history import owners
from security import any_user

history_bp = Blueprint("history", __name__)

PAGE_SIZE = 25

# job_type -> translation key for the label shown in the table. Reuses
# each module's own tile.*.name key instead of duplicating the label in
# a second place - kept here (not in history_store) since it's a
# presentation concern, not storage.
JOB_TYPE_LABELS = {
    "shopping": "tile.shopping.name",
    "todo": "history.job_type.todo",
    "task": "history.job_type.task",
    "message": "tile.message.name",
    "images": "tile.images.name",
    "wifi": "tile.wifi.name",
    "weather": "tile.weather.name",
    "weather_alert": "history.job_type.weather_alert",
    "setup_wifi": "history.job_type.setup_wifi",
    "system": "tile.system.name",
    "automation": "tile.automation.name",
    "games": "tile.games.name",
    "recipes": "tile.recipes.name",
    "boot": "history.job_type.boot",
    "other": "history.job_type.other",
}


@history_bp.route("/history", methods=["GET"])
@any_user
def history_page():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    viewer = owners.current_viewer()
    scope, selected = owners.scope_for(viewer, request.args.get("owner"))
    show_filter = viewer["accounts"] and viewer["admin"]
    if scope is None:
        stats, entries = {"total": 0, "by_status": {}}, []
    else:
        stats = history_store.get_stats_for(scope)
        entries = history_store.get_page(scope, limit=PAGE_SIZE, offset=offset)
    accounts = owners.accounts_by_id() if show_filter else {}
    for entry in entries:
        entry["type_label"] = i18n.tr(JOB_TYPE_LABELS.get(entry["job_type"], "history.job_type.other"))
        if show_filter:
            owner = None if entry["owner_id"] is None else (entry["owner_id"], entry["owner_created"])
            entry["owner_label"] = owners.owner_label(owner, accounts)

    total_pages = max(1, -(-stats["total"] // PAGE_SIZE))  # ceil division without importing math

    return render_template(
        "history.html",
        entries=entries,
        stats=stats,
        page=page,
        total_pages=total_pages,
        show_filter=show_filter,
        owner_options=owners.filter_options(accounts, history_store.get_owners()) if show_filter else [],
        owner_selected=selected,
    )
