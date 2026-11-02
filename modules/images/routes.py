"""
Module: print images. Two paths to the same result:
  - POST /print/image      JSON with base64 (for scripts/curl/automations)
  - POST /ui/images         multipart file upload (from the web UI subpage)
Both go through the same process_and_enqueue_image() function, so
validation/scaling/duplicate detection isn't maintained twice.

Previews mirror that split without printing:
  - POST /print/image/preview   JSON with base64, API token
  - POST /ui/images/preview     multipart upload, CSRF token
Both return the exact 1-bit PNG the print path would send, produced by
the same processing.image_to_1bit() call.

Note: the detail strings from processing.ImageConversionError (image
too large, unknown method, ...) stay in English on the JSON API, which
isn't localized. The web UI shows the translated variant where one
exists (see ImageConversionError.ui_text()).
"""
import base64
import hashlib
import io
import logging
import threading

from flask import Blueprint, jsonify, make_response, render_template, request

import i18n
from print_queue import enqueue_print
from printer import get_printer
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token

from .processing import DEFAULT_DITHER, DITHER_METHODS, ImageConversionError, image_to_1bit

images_bp = Blueprint("images", __name__)
logger = logging.getLogger(__name__)

# One image conversion at a time across previews AND print requests:
# Gunicorn runs a single worker, and a decoded full-size photo plus an
# Atkinson run can take tens of MB and several CPU-seconds - two in
# parallel would only slow both down and double peak memory on a 512MB
# Pi. A preview gives up quickly (the user can simply retry), a print
# request waits longer, since it was explicitly submitted.
_CONVERT_LOCK = threading.Lock()
PREVIEW_LOCK_WAIT_S = 2
PRINT_LOCK_WAIT_S = 30


def _raw_print_image(img):
    p = get_printer()
    try:
        p.image(img)
        p.cut()
    finally:
        p.close()


def _convert_exclusive(img_bytes, dither, wait_s):
    if not _CONVERT_LOCK.acquire(timeout=wait_s):
        raise ImageConversionError(
            "Another image is being processed right now, please try again in a moment", 503,
            "images.error.busy",
        )
    try:
        return image_to_1bit(img_bytes, dither)
    finally:
        _CONVERT_LOCK.release()


def _read_dither(value):
    """Missing/empty means the default method, so callers that don't
    send the field get Floyd-Steinberg. Anything else is passed on as
    given and rejected by image_to_1bit() if it isn't a known method."""
    if value is None or value == "":
        return DEFAULT_DITHER
    return value.strip().lower() if isinstance(value, str) else value


def process_and_enqueue_image(img_bytes, source="ui", dither=DEFAULT_DITHER, localized=False):
    """Shared pipeline for both upload paths: converts via
    image_to_1bit() and enqueues the print job. Returns (ok, detail,
    http_status) - the same 3-tuple convention as enqueue_print().

    localized=True returns the translated UI text for conversion errors
    that have one (web UI); the JSON API keeps the English detail."""
    try:
        img = _convert_exclusive(img_bytes, dither, PRINT_LOCK_WAIT_S)
    except ImageConversionError as e:
        return False, e.ui_text(i18n.tr) if localized else e.detail, e.status
    except Exception:
        # Full exception only to the server log (journalctl): Pillow's
        # messages carry internals like object reprs/memory addresses that
        # don't belong in an API response - the caller gets a fixed text.
        logger.exception("Could not process uploaded image (%d bytes)", len(img_bytes))
        return False, "Could not process image", 400

    # Hash of the raw image bytes as an explicit dedupe_key: the default
    # duplicate detection in print_queue is based on str(args), which
    # for image printing contains a Pillow object (including its memory
    # address) - two uploads of the SAME image would never get the same
    # fingerprint otherwise. A non-default method is part of the key, so
    # printing the same image again with a different method isn't treated
    # as a duplicate; the default method keeps the plain hash.
    dedupe_key = hashlib.sha256(img_bytes).hexdigest()
    summary = f"{img.width}x{img.height}px"
    if dither != DEFAULT_DITHER:
        dedupe_key = f"{dedupe_key}:{dither}"
        summary = f"{summary}, {dither}"

    return enqueue_print(
        _raw_print_image, img, dedupe_key=dedupe_key,
        job_type="images", summary=summary, source=source,
        retry_payload={"image": img},
    )


def _preview_response(img_bytes, dither, localized):
    """Converts without printing: no print queue, no print rules (rate
    limit, duplicate suppression, quiet hours) - a preview has no effect
    on later print jobs. Returns the 1-bit result as PNG, or a JSON error
    in the usual {"status": "error", "detail": ...} shape."""
    try:
        img = _convert_exclusive(img_bytes, dither, PREVIEW_LOCK_WAIT_S)
    except ImageConversionError as e:
        resp = make_response(jsonify({"status": "error", "detail": e.ui_text(i18n.tr) if localized else e.detail}),
                             e.status)
    except Exception:
        logger.exception("Could not process image for preview (%d bytes)", len(img_bytes))
        detail = i18n.tr("images.error.unreadable") if localized else "Could not process image"
        resp = make_response(jsonify({"status": "error", "detail": detail}), 400)
    else:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        resp = make_response(buf.getvalue())
        resp.headers["Content-Type"] = "image/png"
    # Never stored anywhere: every preview is computed from the current
    # upload and method. The service worker (static/sw.js) only ever
    # caches versioned GETs under /static/, so these POST responses don't
    # reach it either way.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _decode_base64_image(data):
    """Returns (img_bytes, error_response) for the JSON endpoints."""
    image_b64 = data.get("image_base64")
    if not image_b64:
        return None, (jsonify({"status": "error", "detail": "image_base64 is missing"}), 400)
    try:
        # validate=True aborts immediately on invalid base64 characters,
        # instead of silently ignoring them (Python's default behavior).
        return base64.b64decode(image_b64, validate=True), None
    except Exception:
        # Details only to the server log, same as process_and_enqueue_image().
        logger.exception("Invalid base64 in image request")
        return None, (jsonify({"status": "error", "detail": "Invalid base64"}), 400)


def _render_page(message=None, success=None, dither=DEFAULT_DITHER):
    return render_template(
        "images.html", message=message, success=success, csrf_token=get_csrf_token(),
        dither_methods=DITHER_METHODS, selected_dither=dither if dither in DITHER_METHODS else DEFAULT_DITHER,
    )


@images_bp.route("/images", methods=["GET"])
def images_page():
    return _render_page()


@images_bp.route("/print/image", methods=["POST"])
@require_api_token
def print_image():
    """
    Expects JSON: { "image_base64": "...", "dither": "floyd-steinberg" }
    "dither" is optional: floyd-steinberg (default), atkinson or bayer.
    """
    data, err = get_json_body()
    if err:
        return err
    img_bytes, err = _decode_base64_image(data)
    if err:
        return err

    ok, detail, status_code = process_and_enqueue_image(img_bytes, source="api", dither=_read_dither(data.get("dither")))
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@images_bp.route("/print/image/preview", methods=["POST"])
@require_api_token
def preview_image_api():
    """
    Same JSON body as /print/image. Returns the 1-bit print image as PNG
    without printing anything.
    """
    data, err = get_json_body()
    if err:
        return err
    img_bytes, err = _decode_base64_image(data)
    if err:
        return err
    return _preview_response(img_bytes, _read_dither(data.get("dither")), localized=False)


@images_bp.route("/ui/images/preview", methods=["POST"])
@csrf_protect
def ui_preview_image():
    """Preview for the web UI subpage (static/image-preview.js): same
    multipart fields as /ui/images, answers with a PNG or a JSON error."""
    uploaded = request.files.get("image")
    if not uploaded or uploaded.filename == "":
        resp = make_response(jsonify({"status": "error", "detail": i18n.tr("images.no_file_selected")}), 400)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return _preview_response(uploaded.read(), _read_dither(request.form.get("dither")), localized=True)


@images_bp.route("/ui/images", methods=["POST"])
@csrf_protect
def ui_print_image():
    """File upload from the web UI subpage (multipart/form-data), no
    base64 detour needed - the browser sends the raw bytes directly."""
    dither = _read_dither(request.form.get("dither"))
    uploaded = request.files.get("image")
    if not uploaded or uploaded.filename == "":
        message, success = i18n.tr("images.no_file_selected"), False
    else:
        img_bytes = uploaded.read()
        ok, detail, _status_code = process_and_enqueue_image(img_bytes, dither=dither, localized=True)
        message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
        success = ok
    return _render_page(message, success, dither)
