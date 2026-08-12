"""
Module: "Ausstehende Aufträge" - review, manually reprint, or discard
print jobs that were blocked by quiet hours/rate limit, or that failed
with a real print error (see pending_store.py + print_queue.py's
retry_payload/block_kind for how entries end up here in the first
place).

Like modules/history/, this is not in module_catalog.MODULES - not a
toggleable/tileable print type itself, just a management view over
jobs that already happened (or rather, didn't). Always reachable via
the burger menu.

Replay handlers below deliberately call enqueue_print() directly with
the right _raw_print_* function, INSTEAD of going through each
module's do_print_list()/do_print_task() wrapper - those wrappers also
clear the CURRENT lists_store draft on success (see modules/lists/
routes.py), which would be wrong here: a pending entry is a separate,
already-past submission, unrelated to whatever the user might
currently have open/in-progress in the browser. Replaying it must
never touch a live draft.
"""
from datetime import datetime

from flask import Blueprint, render_template, request

import i18n
import pending_store
from modules.history.routes import JOB_TYPE_LABELS
from modules.images.routes import _raw_print_image
from modules.lists.routes import _raw_print_list, _raw_print_task
from modules.message.routes import _raw_print_message
from modules.system.routes import _raw_print_system_report
from modules.weather.routes import _raw_print_weather
from modules.wifi.routes import _raw_print_wifi
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token

pending_bp = Blueprint("pending", __name__)


def _replay_list(entry):
    payload = entry["payload"]
    job_type = entry["job_type"]  # "shopping" or "todo"
    return enqueue_print(
        _raw_print_list, payload["title"], payload["items"], job_type == "shopping",
        job_type=job_type, summary=entry["summary"], source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


def _replay_task(entry):
    fields = entry["payload"]
    return enqueue_print(
        _raw_print_task, fields, job_type="task", summary=entry["summary"], source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


def _replay_message(entry):
    payload = entry["payload"]
    return enqueue_print(
        _raw_print_message, payload["title"], payload["text"], payload["module"],
        job_type=entry["job_type"], summary=entry["summary"], source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


def _replay_wifi(entry):
    payload = entry["payload"]
    return enqueue_print(
        _raw_print_wifi, payload["ssid"], payload["password"], payload["auth_type"],
        job_type="wifi", summary=entry["summary"], source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


def _replay_weather(entry):
    payload = entry["payload"]
    return enqueue_print(
        _raw_print_weather, payload.get("location_name"),
        job_type="weather", summary=entry["summary"], source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


def _replay_system(_entry):
    return enqueue_print(
        _raw_print_system_report, job_type="system", summary="", source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


def _replay_images(entry):
    img = pending_store.load_pending_image(entry["id"])
    if img is None:
        # The JSON entry survived but the image file didn't (e.g. an
        # unclean shutdown between the two writes) - report a clear
        # error instead of crashing; the entry stays in the pending
        # list either way (see the caller in pending_print()), so
        # nothing is silently lost, just permanently unprintable.
        return False, "pending image file is missing", 410
    return enqueue_print(
        _raw_print_image, img,
        job_type="images", summary=entry["summary"], source="pending",
        bypass_quiet_hours=True, bypass_duplicate=True,
    )


# job_type -> replay handler. Deliberately explicit/closed rather than
# a fallback default - a job_type with no entry here simply can't be
# replayed (shouldn't happen in practice, since only the job types
# below ever get a retry_payload in the first place, see each
# module's enqueue_print() call).
REPLAY_HANDLERS = {
    "shopping": _replay_list,
    "todo": _replay_list,
    "task": _replay_task,
    "message": _replay_message,
    "automation": _replay_message,
    "weather_alert": _replay_message,
    "wifi": _replay_wifi,
    "weather": _replay_weather,
    "system": _replay_system,
    "images": _replay_images,
}


def _selected_ids():
    """IDs from the request form - either explicit `ids` (one or more,
    from the per-row/selected-rows forms) or every currently pending
    id (the "alle" forms, which submit a single hidden marker instead
    of listing every id individually)."""
    if request.form.get("scope") == "all":
        return [entry["id"] for entry in pending_store.get_all()]
    return request.form.getlist("ids")


def _labeled_entries():
    entries = pending_store.get_all()
    for entry in entries:
        entry["type_label"] = i18n.tr(JOB_TYPE_LABELS.get(entry["job_type"], "history.job_type.other"))
        entry["created_at_display"] = datetime.fromtimestamp(entry["created_at"]).strftime("%d.%m.%Y %H:%M")
    return entries


@pending_bp.route("/pending", methods=["GET"])
def pending_page():
    return render_template("pending.html", entries=_labeled_entries(), csrf_token=get_csrf_token())


@pending_bp.route("/pending/print", methods=["POST"])
@csrf_protect
def pending_print():
    ids = _selected_ids()
    entries = pending_store.get_by_ids(ids)
    printed_ids = []
    any_failed = False
    for entry in entries:
        handler = REPLAY_HANDLERS.get(entry["job_type"])
        if handler is None:
            any_failed = True
            continue
        ok, _detail, _status = handler(entry)
        if ok:
            # Only ever remove (and, for images, delete the file) AFTER
            # a confirmed successful reprint - never speculatively
            # before the result is known.
            printed_ids.append(entry["id"])
        else:
            any_failed = True
    if printed_ids:
        pending_store.remove(printed_ids)
    if not entries:
        message, success = i18n.tr("pending.print.none_selected"), False
    elif any_failed:
        message, success = i18n.tr("pending.print.partial", printed=len(printed_ids), total=len(entries)), False
    else:
        message, success = i18n.tr("pending.print.all_ok", count=len(printed_ids)), True
    return render_template("pending.html", entries=_labeled_entries(), csrf_token=get_csrf_token(),
                            message=message, success=success)


@pending_bp.route("/pending/discard", methods=["POST"])
@csrf_protect
def pending_discard():
    ids = _selected_ids()
    entries = pending_store.get_by_ids(ids)
    if entries:
        pending_store.remove([entry["id"] for entry in entries])
        message, success = i18n.tr("pending.discard.ok", count=len(entries)), True
    else:
        message, success = i18n.tr("pending.print.none_selected"), False
    return render_template("pending.html", entries=_labeled_entries(), csrf_token=get_csrf_token(),
                            message=message, success=success)
