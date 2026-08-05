"""
Module: print images. Two paths to the same result:
  - POST /print/image      JSON with base64 (for scripts/curl/automations)
  - POST /ui/images         multipart file upload (from the web UI subpage)
Both go through the same process_and_enqueue_image() function, so
validation/scaling/duplicate detection isn't maintained twice.

Note: the detail strings returned by process_and_enqueue_image() (image
too large, invalid file, ...) stay in English regardless of the UI
language setting - they're shared with the JSON API, which isn't
localized. Only the surrounding "Error: ..." wrapper text on the web UI
page respects the language switch (see ui_print_image below).
"""
import base64
import hashlib
import io

from flask import Blueprint, jsonify, render_template, request
from PIL import Image, ImageOps

import i18n
from print_queue import enqueue_print
from printer import get_printer
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token

images_bp = Blueprint("images", __name__)

# High enough for real, uncompressed phone photos (even newer high-res
# cameras), but still bounded enough to catch a deliberate decompression
# bomb (tiny file, huge pixel dimensions in the header).
MAX_IMAGE_DIMENSION = 12000
MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12MB - covers larger JPEGs/PNGs from modern phone cameras too


def _raw_print_image(img):
    p = get_printer()
    try:
        p.image(img)
        p.cut()
    finally:
        p.close()


def process_and_enqueue_image(img_bytes, source="ui"):
    """Shared pipeline for both upload paths: validates, scales,
    converts, and enqueues the print job. Returns (ok, detail,
    http_status) - the same 3-tuple convention as enqueue_print()."""
    if len(img_bytes) > MAX_IMAGE_BYTES:
        return False, f"Image file too large ({len(img_bytes)} bytes, maximum {MAX_IMAGE_BYTES} bytes)", 400

    # Hash of the raw image bytes as an explicit dedupe_key: the default
    # duplicate detection in print_queue is based on str(args), which
    # for image printing contains a Pillow object (including its memory
    # address) - two uploads of the SAME image would never have gotten
    # the same fingerprint otherwise.
    dedupe_key = hashlib.sha256(img_bytes).hexdigest()

    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.verify()  # checks the structure without fully decoding
        img = Image.open(io.BytesIO(img_bytes))  # reopen after verify()

        if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
            return False, f"Image too large ({img.width}x{img.height}), maximum {MAX_IMAGE_DIMENSION}px per side", 400

        # 512px, not 576: python-escpos caps media.width at 512px in the
        # TM-T88V profile, regardless of the actual printer - 576 caused
        # "Image width is too large (576 > 512)" for real (large) photos.
        max_width = 512
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)))

        img = img.convert("L")
        # Auto-contrast BEFORE the conversion: stretches the brightness
        # range so generally darker/low-contrast photos don't end up
        # completely black. cutoff=1 clips the most extreme 1% at both
        # ends, so a few very bright/dark outliers (e.g. a small
        # highlight) don't skew the contrast.
        img = ImageOps.autocontrast(img, cutoff=1)
        # convert("1") without an explicit threshold uses Pillow's
        # Floyd-Steinberg dithering (default behavior) instead of a hard
        # black/white cutoff - photos with grayscale/gradients look much
        # more natural this way instead of turning into large black
        # blobs. For very high-contrast motifs (comics, plain text
        # screenshots) this makes barely any visible difference.
        img = img.convert("1")
    except Exception as e:
        return False, f"Could not process image: {e}", 400

    return enqueue_print(
        _raw_print_image, img, dedupe_key=dedupe_key,
        job_type="images", summary=f"{img.width}x{img.height}px", source=source,
    )


@images_bp.route("/images", methods=["GET"])
def images_page():
    return render_template("images.html", message=None, success=None, csrf_token=get_csrf_token())


@images_bp.route("/print/image", methods=["POST"])
@require_api_token
def print_image():
    """
    Expects JSON: { "image_base64": "..." }
    """
    data, err = get_json_body()
    if err:
        return err
    image_b64 = data.get("image_base64")
    if not image_b64:
        return jsonify({"status": "error", "detail": "image_base64 is missing"}), 400

    try:
        # validate=True aborts immediately on invalid base64 characters,
        # instead of silently ignoring them (Python's default behavior).
        img_bytes = base64.b64decode(image_b64, validate=True)
    except Exception as e:
        return jsonify({"status": "error", "detail": f"Invalid base64: {e}"}), 400

    ok, detail, status_code = process_and_enqueue_image(img_bytes, source="api")
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@images_bp.route("/ui/images", methods=["POST"])
@csrf_protect
def ui_print_image():
    """File upload from the web UI subpage (multipart/form-data), no
    base64 detour needed - the browser sends the raw bytes directly."""
    uploaded = request.files.get("image")
    if not uploaded or uploaded.filename == "":
        message, success = i18n.tr("images.no_file_selected"), False
    else:
        img_bytes = uploaded.read()
        ok, detail, _status_code = process_and_enqueue_image(img_bytes)
        message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
        success = ok
    return render_template("images.html", message=message, success=success, csrf_token=get_csrf_token())
