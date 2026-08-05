"""
Module: dashboard / print history - read-only view over history_store
(SQLite), reachable from the burger menu. This module doesn't print
anything and doesn't write to the history table itself - it only
displays what print_queue.py already logged centrally for every other
module's print jobs (see history_store.log_job()).
"""
from flask import Blueprint, render_template, request

import history_store
import i18n

history_bp = Blueprint("history", __name__)

PAGE_SIZE = 25

# job_type -> translation key for the label shown in the table. Reuses
# each module's own tile.*.name key instead of duplicating the label in
# a second place - kept here (not in history_store) since it's a
# presentation concern, not storage.
JOB_TYPE_LABELS = {
    "shopping": "tile.shopping.name",
    "message": "tile.message.name",
    "images": "tile.images.name",
    "wifi": "tile.wifi.name",
    "weather": "tile.weather.name",
    "system": "tile.system.name",
    "boot": "history.job_type.boot",
    "other": "history.job_type.other",
}


@history_bp.route("/history", methods=["GET"])
def history_page():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    stats = history_store.get_stats()
    entries = history_store.get_recent(limit=PAGE_SIZE, offset=offset)
    for entry in entries:
        entry["type_label"] = i18n.tr(JOB_TYPE_LABELS.get(entry["job_type"], "history.job_type.other"))

    total_pages = max(1, -(-stats["total"] // PAGE_SIZE))  # ceil division without importing math

    return render_template(
        "history.html",
        entries=entries,
        stats=stats,
        page=page,
        total_pages=total_pages,
    )
