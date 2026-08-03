"""Image printing module.

Supported inputs:
  - POST /print/image   JSON with a Base64-encoded image
  - POST /ui/images    Multipart upload from the web interface

Both paths use process_and_enqueue_image() so validation, conversion, scaling,
and deduplication remain consistent."""
import base64
import hashlib
import io

from flask import Blueprint, jsonify, render_template, request
from PIL import Image, ImageOps

from print_queue import enqueue_print
from printer import get_printer
from security import csrf_protect, get_csrf_token, get_json_body, require_api_token

images_bp = Blueprint("images", __name__)

# Limit decoded dimensions to reduce decompression-bomb risk while still
# accepting typical high-resolution phone photos.
#
#
MAX_IMAGE_DIMENSION = 12000
MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB supports large JPEG and PNG uploads from modern phones.


def _raw_print_image(img):
    p = get_printer()
    try:
        p.image(img)
        p.cut()
    finally:
        p.close()


def process_and_enqueue_image(img_bytes):
    """Validate, convert, scale, and enqueue an image.

    Returns the same (success, detail, HTTP status) tuple as enqueue_print()."""
    if len(img_bytes) > MAX_IMAGE_BYTES:
        return False, f"Bilddatei zu groß ({len(img_bytes)} Bytes, Maximum {MAX_IMAGE_BYTES} Bytes)", 400

    # Use a hash of the original bytes because PIL object representations include
    # process-specific memory addresses and are not stable deduplication keys.
    #
    #
    dedupe_key = hashlib.sha256(img_bytes).hexdigest()

    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.verify()  # Validate the file structure without fully decoding it.
        img = Image.open(io.BytesIO(img_bytes))  # Reopen because verify() invalidates the image object.

        if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
            return False, f"Bild zu groß ({img.width}x{img.height}), Maximum {MAX_IMAGE_DIMENSION}px pro Seite", 400

        # The python-escpos TM-T88V profile limits image width to 512 pixels.
        #
        #
        #
        max_width = 512
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)))

        img = img.convert("L")
        # Apply autocontrast before monochrome conversion to preserve detail in
        # dark or low-contrast images. A small cutoff ignores extreme outliers.
        #
        #
        #
        img = ImageOps.autocontrast(img, cutoff=1)
        # Pillow uses Floyd-Steinberg dithering when converting to mode 1 without
        # an explicit threshold, which preserves gradients better than hard clipping.
        #
        #
        #
        #
        img = img.convert("1")
    except Exception as e:
        return False, f"Bild konnte nicht verarbeitet werden: {e}", 400

    return enqueue_print(_raw_print_image, img, dedupe_key=dedupe_key)


@images_bp.route("/images", methods=["GET"])
def images_page():
    return render_template("images.html", message=None, success=None, csrf_token=get_csrf_token())


@images_bp.route("/print/image", methods=["POST"])
@require_api_token
def print_image():
    """Accept JSON in the form {"image_base64": "..."}."""
    data, err = get_json_body()
    if err:
        return err
    image_b64 = data.get("image_base64")
    if not image_b64:
        return jsonify({"status": "error", "detail": "image_base64 fehlt"}), 400

    try:
        # Reject malformed Base64 instead of silently ignoring invalid characters.
        #
        img_bytes = base64.b64decode(image_b64, validate=True)
    except Exception as e:
        return jsonify({"status": "error", "detail": f"Ungültiges Base64: {e}"}), 400

    ok, detail, status_code = process_and_enqueue_image(img_bytes)
    if ok:
        return jsonify({"status": "gedruckt"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@images_bp.route("/ui/images", methods=["POST"])
@csrf_protect
def ui_print_image():
    """Handle a multipart image upload from the web interface."""
    uploaded = request.files.get("image")
    if not uploaded or uploaded.filename == "":
        message, success = "Keine Datei ausgewählt", False
    else:
        img_bytes = uploaded.read()
        ok, detail, _status_code = process_and_enqueue_image(img_bytes)
        message = "Gedruckt ✓" if ok else f"Fehler: {detail}"
        success = ok
    return render_template("images.html", message=message, success=success, csrf_token=get_csrf_token())
