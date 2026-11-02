"""
Module: generic automation webhook - a plain HTTP POST endpoint for any
automation platform (Home Assistant, Node-RED, n8n, a cron script, ...)
to trigger a print job. Not tied to any specific platform - the module
name and history "webhook" source reflect that ("Home Assistant" is just
one possible client of this webhook, not what it fundamentally is).

Deliberately a thin wrapper around the same "plain text on receipt"
primitive as the message module (_raw_print_message), NOT a new print
primitive of its own - the caller supplies title+text itself (e.g. via
a Home Assistant template), same shape as /print/message.
Kept as its own module rather than just reusing /print/message directly
so automation traffic gets its own logo slot (module="automation", see
logos.py) and its own history source ("webhook") - makes it easy to
tell apart "I sent this from the phone" vs "an automation triggered
this" on the history dashboard, without adding a `source` field API
callers would need to know to set correctly.

No GET page / web UI form: this endpoint has no legitimate manual-print
use case of its own - see /message for that.
"""
from flask import Blueprint, jsonify

from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import (
    MAX_TEXT_LEN,
    MAX_TITLE_LEN,
    get_json_body,
    require_api_token,
)

automation_bp = Blueprint("automation", __name__)


def parse_automation_body(data):
    """Validates a {"title", "text"} body - shared by /print/automation
    and the MQTT "message" command (see mqtt_bridge.py), so both apply
    exactly the same rules. Returns ((title, text), None) or (None,
    (detail, http_status))."""
    title = data.get("title")
    text = data.get("text")
    if title is not None and not isinstance(title, str):
        return None, ("'title' must be a string", 400)
    if text is not None and not isinstance(text, str):
        return None, ("'text' must be a string", 400)
    title = (title or "")[:MAX_TITLE_LEN] or None
    text = (text or "")[:MAX_TEXT_LEN]
    if not text:
        return None, ("'text' must not be empty", 400)
    return (title, text), None


def do_print_automation(title, text, source="webhook"):
    """Returns (ok, detail, http_status), see enqueue_print()."""
    summary = title or (text[:60] + ("…" if len(text) > 60 else ""))
    return enqueue_print(
        _raw_print_message, title, text, "automation",
        job_type="automation", summary=summary, source=source,
        retry_payload={"title": title, "text": text, "module": "automation"},
    )


@automation_bp.route("/print/automation", methods=["POST"])
@require_api_token
def print_automation():
    """
    Expects JSON: { "title": "optional", "text": "message" }

    Protected by the same X-Api-Token header as every other /print/*
    endpoint (see security.require_api_token) - Home Assistant's
    rest_command integration (and equivalents in Node-RED/n8n/etc.)
    supports custom headers, so no separate webhook-specific secret
    scheme is needed.
    """
    data, err = get_json_body()
    if err:
        return err
    fields, error = parse_automation_body(data)
    if error:
        detail, status = error
        return jsonify({"status": "error", "detail": detail}), status
    ok, detail, status_code = do_print_automation(*fields)
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code
