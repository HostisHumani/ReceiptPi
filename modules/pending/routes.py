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

Every logged-in account can open this page, but only ever sees and acts
on its own entries; admins see all of them and can filter by owner (see
modules/history/owners.py). Each id from a form is looked up through
the viewer's scope, so a foreign or made-up id is simply not found. A
reprint belongs to the owner of the pending entry, whoever presses the
button. Only an admin's reprint skips quiet hours; another account's
reprint waits for them like any new job, so the pending list is no way
around them.
"""
from datetime import datetime

from flask import Blueprint, render_template, request

import i18n
import pending_store
from modules.history import owners
from modules.history.routes import JOB_TYPE_LABELS
from modules.images.routes import _raw_print_image
from modules.lists.routes import _raw_print_list, _raw_print_task
from modules.message.routes import _raw_print_message
from modules.recipes.routes import _raw_print_recipe
from modules.system.routes import _raw_print_system_report
from modules.update.routes import _raw_print_update_result
from modules.weather.routes import _raw_print_weather
from modules.wifi.routes import _raw_print_wifi
from print_queue import enqueue_print
from security import any_user, csrf_protect, get_csrf_token

pending_bp = Blueprint("pending", __name__)


def _replay_options(entry, admin):
    """enqueue_print() options shared by every reprint: the new job
    belongs to the entry's owner, duplicate suppression is skipped (the
    content was reviewed and chosen again), quiet hours only for an
    admin."""
    return {"source": "pending", "bypass_quiet_hours": admin, "bypass_duplicate": True,
            "owner": pending_store.entry_owner(entry)}


def _replay_list(entry, admin):
    payload = entry["payload"]
    job_type = entry["job_type"]  # "shopping" or "todo"
    return enqueue_print(
        _raw_print_list, payload["title"], payload["items"], job_type == "shopping",
        job_type=job_type, summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_task(entry, admin):
    fields = entry["payload"]
    return enqueue_print(
        _raw_print_task, fields, job_type="task", summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_message(entry, admin):
    payload = entry["payload"]
    return enqueue_print(
        _raw_print_message, payload["title"], payload["text"], payload["module"],
        job_type=entry["job_type"], summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_wifi(entry, admin):
    payload = entry["payload"]
    return enqueue_print(
        _raw_print_wifi, payload["ssid"], payload["password"], payload["auth_type"],
        job_type="wifi", summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_weather(entry, admin):
    payload = entry["payload"]
    return enqueue_print(
        _raw_print_weather, payload.get("location_name"),
        job_type="weather", summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_system(entry, admin):
    return enqueue_print(
        _raw_print_system_report, job_type="system", summary="", **_replay_options(entry, admin),
    )


def _replay_images(entry, admin):
    img = pending_store.load_pending_image(entry["id"])
    if img is None:
        # The JSON entry survived but the image file didn't (e.g. an
        # unclean shutdown between the two writes) - report a clear
        # error instead of crashing; the entry stays in the pending
        # list either way (see the caller in pending_print()), so
        # nothing is silently lost, just permanently unprintable.
        return False, "pending image file is missing", 410
    # An entry without a caption prints the image alone.
    return enqueue_print(
        _raw_print_image, img, (entry.get("payload") or {}).get("caption"),
        job_type="images", summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_update(entry, admin):
    # The update receipt the nightly run held back during quiet hours;
    # the payload was checked when the updater sent it.
    return enqueue_print(
        _raw_print_update_result, entry["payload"],
        job_type="update", summary=entry["summary"], **_replay_options(entry, admin),
    )


def _replay_recipe(entry, admin):
    # The payload is the full printable recipe (see
    # mealie.recipe_to_printable()) - replaying never needs Mealie to
    # be reachable, and prints the recipe as it was when first sent.
    return enqueue_print(
        _raw_print_recipe, entry["payload"],
        job_type="recipes", summary=entry["summary"], **_replay_options(entry, admin),
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
    "recipes": _replay_recipe,
    "update": _replay_update,
}


def _labeled_entries(viewer, scope):
    if scope is None:
        return []
    entries = pending_store.get_for(scope)
    show_owner = viewer["accounts"] and viewer["admin"]
    accounts = owners.accounts_by_id() if show_owner else {}
    for entry in entries:
        entry["type_label"] = i18n.tr(JOB_TYPE_LABELS.get(entry["job_type"], "history.job_type.other"))
        entry["created_at_display"] = datetime.fromtimestamp(entry["created_at"]).strftime("%d.%m.%Y %H:%M")
        if show_owner:
            entry["owner_label"] = owners.owner_label(pending_store.entry_owner(entry), accounts)
    return entries


def _render(viewer, scope, selected, message=None, success=None):
    show_filter = viewer["accounts"] and viewer["admin"]
    options = []
    if show_filter:
        accounts = owners.accounts_by_id()
        pending_owners = {pending_store.entry_owner(e) for e in pending_store.get_for(("all",))} - {None}
        options = owners.filter_options(accounts, pending_owners)
    return render_template("pending.html", entries=_labeled_entries(viewer, scope), csrf_token=get_csrf_token(),
                           show_filter=show_filter, show_owner=show_filter, owner_options=options,
                           owner_selected=selected, message=message, success=success)


def _selected_entries(scope):
    """The entries a form refers to, always within scope: either the
    explicit `ids` (one or more, from the per-row/selected-rows forms)
    or every entry of the current view (the "alle" forms, which submit
    a single hidden marker instead of listing every id)."""
    if scope is None:
        return []
    if request.form.get("scope") == "all":
        return pending_store.get_for(scope)
    return pending_store.get_by_ids(request.form.getlist("ids"), scope)


@pending_bp.route("/pending", methods=["GET"])
@any_user
def pending_page():
    viewer = owners.current_viewer()
    scope, selected = owners.scope_for(viewer, request.args.get("owner"))
    return _render(viewer, scope, selected)


@pending_bp.route("/pending/print", methods=["POST"])
@any_user
@csrf_protect
def pending_print():
    viewer = owners.current_viewer()
    scope, selected = owners.scope_for(viewer, request.form.get("owner"))
    entries = _selected_entries(scope)
    printed_ids = []
    any_failed = False
    for entry in entries:
        handler = REPLAY_HANDLERS.get(entry["job_type"])
        if handler is None:
            any_failed = True
            continue
        ok, _detail, _status = handler(entry, viewer["admin"])
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
    return _render(viewer, scope, selected, message, success)


@pending_bp.route("/pending/discard", methods=["POST"])
@any_user
@csrf_protect
def pending_discard():
    viewer = owners.current_viewer()
    scope, selected = owners.scope_for(viewer, request.form.get("owner"))
    entries = _selected_entries(scope)
    if entries:
        pending_store.remove([entry["id"] for entry in entries])
        message, success = i18n.tr("pending.discard.ok", count=len(entries)), True
    else:
        message, success = i18n.tr("pending.print.none_selected"), False
    return _render(viewer, scope, selected, message, success)
