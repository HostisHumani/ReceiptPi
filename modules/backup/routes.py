"""
Settings backup and restore in the web UI (Settings -> Backup). The
archive format and all checks live in archive.py; this module only
handles the requests.

Flow:
  GET  /settings/backup                     page
  POST /ui/settings/backup/download         streamed .zip download
  POST /ui/settings/backup/restore/check    upload -> full check, nothing
                                            changed yet -> summary page
  POST /ui/settings/backup/restore/apply    explicit confirmation -> apply
  POST /ui/settings/backup/restore/cancel   drop the staged upload

All posts are CSRF-protected and go through security.backup_access_error()
- the single place that decides who may download or restore. There is
deliberately no JSON API for this.
"""
import logging

import config
from flask import Blueprint, Response, render_template, request, stream_with_context

import i18n
import print_queue
from security import (
    backup_access_error,
    backup_restore_locked,
    backup_token_required,
    csrf_protect,
    get_csrf_token,
)

from . import archive

backup_bp = Blueprint("backup", __name__)
logger = logging.getLogger(__name__)

# Values that only exist in config.py and therefore never come back with
# a restore. Shown after a restore as "set / not set" - never the values.
CONFIG_ONLY_VALUES = (
    "API_TOKEN", "HA_BASE_URL", "HA_TOKEN", "NETATMO_INDOOR_ENTITY", "NETATMO_OUTDOOR_ENTITY",
    "FRITZBOX_ADDRESS", "FRITZBOX_USER", "FRITZBOX_PASSWORD",
)
_PLACEHOLDER_PREFIXES = ("Placeholder", "Platzhalter", "Your ")


def _config_status():
    status = []
    for name in CONFIG_ONLY_VALUES:
        value = getattr(config, name, "")
        configured = bool(value) and not (isinstance(value, str) and value.startswith(_PLACEHOLDER_PREFIXES))
        status.append({"name": name, "configured": configured})
    return status


def _render(mode="start", message=None, success=None, **context):
    return render_template(
        "settings_backup.html",
        mode=mode,
        message=message,
        success=success,
        csrf_token=get_csrf_token(),
        token_required=backup_token_required(),
        restore_locked=backup_restore_locked(),
        max_archive_mb=archive.MAX_ARCHIVE_BYTES // (1024 * 1024),
        too_large_to_restore=archive.estimated_size() > archive.MAX_ARCHIVE_BYTES,
        **context,
    )


def _error(err):
    return i18n.tr(err.key, **err.params)


@backup_bp.route("/settings/backup", methods=["GET"])
def backup_page():
    return _render()


@backup_bp.route("/ui/settings/backup/download", methods=["POST"])
@csrf_protect
def ui_download_backup():
    denied = backup_access_error("download", request.form.get("api_token", ""))
    if denied:
        return _render(message=i18n.tr(denied), success=False)
    resp = Response(stream_with_context(archive.iter_backup_chunks()), mimetype="application/zip")
    resp.headers["Content-Disposition"] = f'attachment; filename="{archive.download_filename()}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@backup_bp.route("/ui/settings/backup/restore/check", methods=["POST"])
@csrf_protect
def ui_check_restore():
    denied = backup_access_error("restore", request.form.get("api_token", ""))
    if denied:
        return _render(message=i18n.tr(denied), success=False)
    if request.content_length and request.content_length > archive.MAX_ARCHIVE_BYTES + 64 * 1024:
        return _render(message=i18n.tr("backup.error.too_large", max_mb=archive.MAX_ARCHIVE_BYTES // (1024 * 1024)),
                       success=False)
    uploaded = request.files.get("archive")
    if not uploaded or uploaded.filename == "":
        return _render(message=i18n.tr("images.no_file_selected"), success=False)

    staging_id = None
    try:
        staging_id = archive.stage_upload(uploaded)
        contents, report = archive.preview(archive.staged_path(staging_id))
    except archive.ArchiveError as e:
        archive.discard_staged(staging_id)
        return _render(message=_error(e), success=False)
    except Exception:
        archive.discard_staged(staging_id)
        logger.exception("Could not check uploaded backup archive")
        return _render(message=i18n.tr("backup.error.not_zip"), success=False)

    if report["errors"]:
        archive.discard_staged(staging_id)
        staging_id = None
    return _render("summary", manifest=contents["manifest"], logo_slots=contents["logo_slots"],
                   report=report, staging_id=staging_id)


@backup_bp.route("/ui/settings/backup/restore/apply", methods=["POST"])
@csrf_protect
def ui_apply_restore():
    staging_id = request.form.get("staging_id", "")
    denied = backup_access_error("restore", request.form.get("api_token", ""))
    if denied:
        return _render(message=i18n.tr(denied), success=False)
    path = archive.staged_path(staging_id)
    if path is None:
        return _render(message=i18n.tr("backup.error.staging_expired"), success=False)
    if request.form.get("confirm") != "yes":
        return _render(message=i18n.tr("backup.error.not_confirmed"), success=False)
    # Queued or running print jobs read settings and logos while they
    # print, so the restore only runs while the queue is idle.
    if print_queue.PRINT_QUEUE.unfinished_tasks:
        return _render(message=i18n.tr("backup.error.queue_busy"), success=False)

    try:
        report, snapshot_name = archive.apply_restore(path)
    except archive.ArchiveError as e:
        return _render(message=_error(e), success=False)
    except Exception:
        logger.exception("Restore failed, previous state put back")
        return _render(message=i18n.tr("backup.error.apply_failed"), success=False)
    archive.discard_staged(staging_id)
    return _render("result", message=i18n.tr("backup.restore.done"), success=True,
                   report=report, snapshot_name=snapshot_name, config_status=_config_status())


@backup_bp.route("/ui/settings/backup/restore/cancel", methods=["POST"])
@csrf_protect
def ui_cancel_restore():
    archive.discard_staged(request.form.get("staging_id", ""))
    return _render(message=i18n.tr("backup.restore.cancelled"), success=True)
