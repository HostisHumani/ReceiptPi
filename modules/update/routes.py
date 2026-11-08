"""
Module: update - Settings -> Updates, and the receipt of an update.

The page shows the running version, the newest full release (from the
update check, watchers/update_check_watch.py), the state of the updater
and the switch for automatic updates, and has the button that asks for
an update. The updater (deploy/update/updater.py) runs as root; this
module only writes the request file its path unit waits for and reads
the status file it writes. Nothing written here can name a version or a
path: the updater takes only the action and the account name from the
request.

An update can only be asked for, and automatic updates only switched,
while an admin account exists: without one the web UI is open to
everyone on the network. The updater checks this itself as well.

POST /print/update-result is the receipt the updater asks for after an
update. Only the updater on the device calls it, so it answers requests
from localhost only. It is the interface between the updater of one
release and the server of another - after a rollback the previous
release prints - so its fields stay as they are: result, reason, from,
to, mode, by. Answers: 200 printed, 429 held back by quiet hours or the
rate limit (the receipt then waits under pending prints), anything else
failed. The receipt is printed in German and English, like the Wi-Fi
setup receipts: an update can run at night, for whoever reads it first.
"""
import json
import os
import re
import time

from flask import Blueprint, g, jsonify, render_template, request

import auth_store
import i18n
import settings_store
import version
from modules.setup_wifi.routes import _is_local_request
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token
from text_style import clean_print_text, get_text_scale, wrap_body_text

update_bp = Blueprint("update", __name__)

# The installed updater's files; the same paths as in
# deploy/update/updater.py and deploy/systemd/.
UPDATE_DIR = "/var/lib/receiptpi-update"
UPDATER_UNIT = "/etc/systemd/system/receiptpi-update.path"
UPDATE_CONF = "/etc/receiptpi/update.conf"
APP_LINK = "/opt/receiptpi"
REQUEST_NAME = "update-request.json"
UPDATE_CHECK_NAME = "update_check.json"
MAX_STATUS_BYTES = 64 * 1024

RESULTS = ("updated", "failed", "rolled_back", "rollback_failed")
MODES = ("manual", "auto", "ssh")
LANGS = ("de", "en")
MAX_BY_CHARS = 64
_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
_REASON_RE = re.compile(r"^[a-z_]{0,32}$")
# Reasons the updater writes; each has update.reason.<key> in the
# translations. Any other one is shown as update.reason.other.
REASONS = ("request", "no_admin", "config", "layout", "version", "no_key", "download",
           "manifest", "signature", "channel", "checksum", "package", "python", "requirements",
           "manual_install", "space", "venv", "prepare", "backup", "health", "switch", "handoff",
           "interrupted", "journal", "internal", "manual")
PHASES = ("preparing", "prepare", "backup", "switch")
# The status fields the page uses; anything else in status.json is
# ignored.
STATUS_KEYS = ("state", "phase", "result", "reason", "from", "to", "mode", "by", "receipt",
               "updated_at")


# ---------------------------------------------------------------- reading the updater's state

def request_path():
    return os.path.join(settings_store.STATE_DIR, REQUEST_NAME)


def updater_available():
    """Installed from the image or with deploy/install.sh: the path unit
    exists and the code is a release behind a link. Other installations
    (a plain checkout, copied by hand) are updated by hand."""
    return os.path.isfile(UPDATER_UNIT) and os.path.islink(APP_LINK)


def test_mode():
    return os.path.exists(UPDATE_CONF)


def read_status():
    """The updater's status.json, only its known fields as short strings;
    {} when there is none or it can't be read."""
    try:
        with open(os.path.join(UPDATE_DIR, "status.json"), "rb") as f:
            data = json.loads(f.read(MAX_STATUS_BYTES + 1)[:MAX_STATUS_BYTES].decode("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data[k])[:200] for k in STATUS_KEYS if isinstance(data.get(k), str)}


def latest_release():
    """(latest version, update available) from the update check's cache."""
    try:
        with open(os.path.join(settings_store.STATE_DIR, UPDATE_CHECK_NAME)) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return "", False
    if not isinstance(cache, dict):
        return "", False
    latest = cache.get("latest_version")
    latest = version.format_display(latest) if isinstance(latest, str) and latest else ""
    return latest, bool(cache.get("update_available")) and bool(latest)


def reason_text(reason, lang=None):
    key = f"update.reason.{reason}" if reason in REASONS else "update.reason.other"
    return i18n.t(key, lang) if lang else i18n.tr(key)


def _display(raw):
    return version.format_display(raw) if raw else "?"


def _result_key(prefix, result, reason, to_version):
    if result == "rolled_back" and reason == "manual":
        return f"{prefix}.rolled_back_ssh"
    if result == "failed" and not to_version:
        return f"{prefix}.failed_unknown"
    return f"{prefix}.{result}"


def describe(status, pending):
    """What the page says about the updater: {"busy": bool, "lines":
    [translated lines]}. busy while a request waits or an update runs."""
    running = status.get("state") == "running"
    if pending and not running:
        return {"busy": True, "lines": [i18n.tr("update.status.requested")]}
    if running:
        phase = status.get("phase", "")
        phase_text = i18n.tr(f"update.phase.{phase}") if phase in PHASES else i18n.tr("update.phase.preparing")
        return {"busy": True, "lines": [i18n.tr("update.status.running", phase=phase_text)]}
    result = status.get("result", "")
    if not result:
        return {"busy": False, "lines": [i18n.tr("update.status.never")]}
    reason = status.get("reason", "")
    lines = []
    if result in RESULTS or result in ("up_to_date", "not_available"):
        key = _result_key("update.result", result, reason, status.get("to"))
        lines.append(i18n.tr(key, from_version=_display(status.get("from")),
                             to_version=_display(status.get("to"))))
    if result in ("failed", "rolled_back", "rollback_failed") and reason and reason != "manual":
        lines.append(i18n.tr("update.status.reason", reason=reason_text(reason)))
    when = status.get("updated_at", "")
    if when:
        lines.append(i18n.tr("update.status.finished", time=when[:16].replace("T", " ")))
    if status.get("mode") == "auto":
        lines.append(i18n.tr("update.status.auto"))
    elif status.get("by"):
        lines.append(i18n.tr("update.status.by", name=status["by"]))
    if status.get("receipt") == "held":
        lines.append(i18n.tr("update.status.receipt_held"))
    return {"busy": False, "lines": lines}


# ---------------------------------------------------------------- the page

def _render(message=None, success=None):
    status = read_status()
    info = describe(status, os.path.exists(request_path()))
    latest, newer = latest_release()
    return render_template(
        "settings_updates.html",
        message=message, success=success,
        csrf_token=get_csrf_token(),
        installed_version=version.format_display(version.CURRENT_VERSION),
        latest_version=latest, update_available=newer,
        available=updater_available(), test_mode=test_mode(),
        admin_present=auth_store.admin_exists(),
        auto=bool(settings_store.get_settings().get("updates", {}).get("auto")),
        status_lines=info["lines"], busy=info["busy"],
    )


@update_bp.route("/settings/updates", methods=["GET"])
def updates_page():
    return _render()


@update_bp.route("/settings/updates/status", methods=["GET"])
def updates_status():
    """For the page's script, which asks every few seconds while an
    update is requested or running."""
    info = describe(read_status(), os.path.exists(request_path()))
    resp = jsonify({"status": "ok", "busy": info["busy"], "lines": info["lines"]})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _write_request(by):
    """The request file in one rename, so the path unit never sees half
    of it."""
    path = request_path()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "w") as f:
        json.dump({"action": "install", "by": by}, f)
    os.replace(tmp, path)


@update_bp.route("/ui/settings/updates/install", methods=["POST"])
@csrf_protect
def ui_install():
    if not updater_available():
        return _render(message=i18n.tr("update.error.not_available"), success=False)
    if not auth_store.admin_exists():
        return _render(message=i18n.tr("update.error.no_admin"), success=False)
    if request.form.get("confirm") != "yes":
        return _render(message=i18n.tr("update.error.not_confirmed"), success=False)
    if os.path.exists(request_path()) or read_status().get("state") == "running":
        return _render(message=i18n.tr("update.error.busy"), success=False)
    user = g.get("auth_user")
    try:
        _write_request(user["name"] if user else "")
    except OSError:
        return _render(message=i18n.tr("update.error.request_failed"), success=False)
    return _render(message=i18n.tr("update.requested"), success=True)


@update_bp.route("/ui/settings/updates/auto", methods=["POST"])
@csrf_protect
def ui_auto():
    if not auth_store.admin_exists():
        return _render(message=i18n.tr("update.error.no_admin"), success=False)
    enabled = request.form.get("auto") == "on"
    settings_store.update_section("updates", {"auto": enabled})
    return _render(message=i18n.tr("update.auto.saved_on" if enabled else "update.auto.saved_off"),
                   success=True)


# ---------------------------------------------------------------- the receipt

def _both(key, **kwargs):
    return [i18n.t(key, lang, **kwargs) for lang in LANGS]


def _labelled(key, value):
    """ "Zeit / Time: <value>" - one line for both languages."""
    return " / ".join(dict.fromkeys(_both(key))) + ": " + value


def _raw_print_update_result(payload):
    # Imported here like in the other print modules: python-escpos is
    # only loaded once a receipt is actually printed.
    from printer import get_printer

    scale = get_text_scale()
    result, reason = payload["result"], payload["reason"]
    title = " / ".join(dict.fromkeys(_both("receipt.update.title")))
    lines = _both(_result_key("receipt.update", result, reason, payload["to"]),
                  from_version=_display(payload["from"]), to_version=_display(payload["to"]))
    if result != "updated" and reason and reason != "manual":
        lines += [i18n.t("receipt.update.reason", lang, reason=reason_text(reason, lang))
                  for lang in LANGS]
    meta = [_labelled("receipt.update.time_word", time.strftime("%Y-%m-%d %H:%M"))]
    if payload["mode"] == "auto":
        meta.append(" / ".join(dict.fromkeys(_both("receipt.update.auto"))))
    elif payload["by"]:
        meta.append(_labelled("receipt.update.by_word", clean_print_text(payload["by"])))

    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height,
              custom_size=True)
        p.text(wrap_body_text(title, scale) + "\n\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height,
              custom_size=True)
        for line in lines:
            p.text(wrap_body_text(line, scale) + "\n\n")
        for line in meta:
            p.text(wrap_body_text(line, scale) + "\n")
        p.text("\n")
        p.cut()
    finally:
        p.close()


def validate_result(data):
    """(payload, None) or (None, error detail) for the receipt request."""
    result = data.get("result")
    if result not in RESULTS:
        return None, "result must be updated, failed, rolled_back or rollback_failed"
    mode = data.get("mode")
    if mode not in MODES:
        return None, "mode must be manual, auto or ssh"
    payload = {"result": result, "mode": mode}
    reason = data.get("reason", "")
    if not isinstance(reason, str) or not _REASON_RE.match(reason):
        return None, "reason must be a short key"
    payload["reason"] = reason
    for key in ("from", "to"):
        value = data.get(key, "")
        if not isinstance(value, str) or (value and not _VERSION_RE.match(value)):
            return None, f"{key} must be MAJOR.MINOR.PATCH or empty"
        payload[key] = value
    by = data.get("by", "")
    if not isinstance(by, str):
        return None, "by must be text"
    payload["by"] = clean_print_text(by)[:MAX_BY_CHARS]
    return payload, None


@update_bp.route("/print/update-result", methods=["POST"])
@require_api_token
def print_update_result():
    """
    Expects JSON from the updater:
      {"result": "updated"|"failed"|"rolled_back"|"rollback_failed",
       "reason": <key or "">, "from": <version or "">, "to": <version or "">,
       "mode": "manual"|"auto"|"ssh", "by": <account name or "">}
    """
    if not _is_local_request():
        return jsonify({"status": "error", "detail": "only available on the device itself"}), 403
    data, err = get_json_body()
    if err:
        return err
    payload, problem = validate_result(data)
    if problem:
        return jsonify({"status": "error", "detail": problem}), 400
    # A button press is answered at once; the nightly run keeps to the
    # quiet hours, and its receipt waits under pending prints. Every
    # receipt is its own event, never a duplicate of an earlier one.
    ok, detail, status_code = enqueue_print(
        _raw_print_update_result, payload,
        job_type="update", summary=f"{payload['from'] or '?'} -> {payload['to'] or '?'}",
        source="api", bypass_quiet_hours=payload["mode"] != "auto", bypass_duplicate=True,
        retry_payload=payload,
    )
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code
