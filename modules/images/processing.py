"""
Image -> 1-bit conversion shared by the print path and the preview
endpoints (see routes.py). Kept free of Flask/printer imports, so it can
be called and tested on its own.

Pipeline (same order for every dithering method):
  validate -> scale down to the print width -> flatten transparency onto
  white -> grayscale -> autocontrast -> dither.
"""
import io
import time

from PIL import Image, ImageChops, ImageOps

# High enough for real phone photos (even higher-res sensors), while
# keeping worst-case decoded memory in check on a 512MB Pi Zero 2 W: a
# fully decoded RGB image at this cap is roughly 6000*6000*3 ≈ 108MB,
# versus 400MB+ at a 12000px cap - large enough to risk taking down the
# single worker process on a genuinely huge (not even malicious, just
# unusually large) upload. Still comfortably above what any real phone
# camera produces.
MAX_IMAGE_DIMENSION = 6000
MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12MB - covers larger JPEGs/PNGs from modern phone cameras too

# 512px, not 576: python-escpos caps media.width at 512px in the
# TM-T88V profile, regardless of the actual printer - 576 causes
# "Image width is too large (576 > 512)" for real (large) photos.
PRINT_WIDTH = 512

DITHER_FLOYD_STEINBERG = "floyd-steinberg"
DITHER_ATKINSON = "atkinson"
DITHER_BAYER = "bayer"
DITHER_METHODS = (DITHER_FLOYD_STEINBERG, DITHER_ATKINSON, DITHER_BAYER)
DEFAULT_DITHER = DITHER_FLOYD_STEINBERG

# Atkinson runs as a plain Python loop (no numpy on the target), which
# costs several seconds per megapixel on a Pi Zero 2 W and holds the GIL
# of the single Gunicorn worker the whole time. Both limits keep one
# request from stalling the server: the pixel cap rejects oversized
# images up front, the time limit aborts a run that is slower than
# expected anyway (e.g. CPU contention with other processes).
ATKINSON_MAX_PIXELS = PRINT_WIDTH * 2000
ATKINSON_TIME_LIMIT_S = 15


class ImageConversionError(Exception):
    """A conversion failure with a caller-facing explanation.

    detail: English text for the JSON API (not localized, like the other
    API detail strings). ui_key/ui_params: optional translation key for
    the web UI - errors without one show `detail` there as well."""

    def __init__(self, detail, status=400, ui_key=None, **ui_params):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.ui_key = ui_key
        self.ui_params = ui_params

    def ui_text(self, tr):
        """tr: i18n.tr - passed in so this module needs no i18n/settings import."""
        if self.ui_key:
            return tr(self.ui_key, **self.ui_params)
        return self.detail


def _has_alpha(img):
    return img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)


def _flatten_on_white(img):
    """convert("L") ignores alpha and reads the RGB values underneath
    transparent pixels - often black - so a transparent background would
    print as a solid black area. Compositing onto white first makes
    transparent areas print as paper. Opaque pixels are unchanged by the
    composite, so this step doesn't change fully opaque images."""
    img = img.convert("RGBA")
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, img)


def _dither_atkinson(gray, deadline):
    """Atkinson error diffusion: threshold at 128, then 1/8 of the
    quantization error goes to each of six neighbors - (x+1, y),
    (x+2, y), (x-1, y+1), (x, y+1), (x+1, y+1), (x, y+2). The remaining
    2/8 is dropped on purpose, which is what keeps highlights and shadows
    clean instead of speckled.

    Only three rows are held as Python float lists at any time (the
    current row and the two below it that receive error), instead of the
    whole image - a full-image list would cost tens of MB on the Pi for
    tall images.

    deadline: time.monotonic() value after which the conversion is
    aborted (checked once per row - cheap, and a row is short enough for
    the limit to be honored closely)."""
    w, h = gray.size
    src = gray.tobytes()
    out = bytearray(w * h)

    def row(y):
        return [float(v) for v in src[y * w:(y + 1) * w]] if y < h else None

    cur, below, below2 = row(0), row(1), row(2)
    for y in range(h):
        if time.monotonic() > deadline:
            raise ImageConversionError(
                f"Atkinson dithering exceeded the {ATKINSON_TIME_LIMIT_S}s time limit and was aborted. "
                "Use Floyd-Steinberg or Bayer for this image.",
                504, "images.error.atkinson_timeout", seconds=ATKINSON_TIME_LIMIT_S,
            )
        base = y * w
        for x in range(w):
            old = cur[x]
            if old < 128:
                new = 0
            else:
                new = 255
                out[base + x] = 255
            err = (old - new) / 8
            if err:
                if x + 1 < w:
                    cur[x + 1] += err
                    if x + 2 < w:
                        cur[x + 2] += err
                if below is not None:
                    if x > 0:
                        below[x - 1] += err
                    below[x] += err
                    if x + 1 < w:
                        below[x + 1] += err
                    if below2 is not None:
                        below2[x] += err
        cur, below, below2 = below, below2, row(y + 3)
    return Image.frombytes("L", (w, h), bytes(out)).convert("1", dither=Image.Dither.NONE)


def _bayer_matrix(size):
    """Standard ordered-dither index matrix, built recursively:
    M(2n) = [[4*M(n) + 0, 4*M(n) + 2], [4*M(n) + 3, 4*M(n) + 1]]."""
    m = [[0]]
    offsets = [[0, 2], [3, 1]]
    while len(m) < size:
        n = len(m)
        m = [[4 * m[y % n][x % n] + offsets[y // n][x // n] for x in range(2 * n)] for y in range(2 * n)]
    return m


# Per-position threshold on the 0-255 scale: (index + 0.5) * 256 / 64,
# i.e. the centers of 64 equal brightness bands.
_BAYER_THRESHOLDS = [[4 * v + 2 for v in row] for row in _bayer_matrix(8)]


def _dither_bayer(gray):
    """Bayer 8x8 ordered dithering: a pixel turns white if it is brighter
    than the threshold at its (x % 8, y % 8) matrix position.

    Done with Pillow's C operations instead of a per-pixel Python loop:
    a threshold image is tiled to the full size, subtract() clamps at 0,
    so the difference is > 0 exactly where pixel > threshold."""
    w, h = gray.size
    rows = [bytes(_BAYER_THRESHOLDS[y][x & 7] for x in range(w)) for y in range(8)]
    thresholds = Image.frombytes("L", (w, h), b"".join(rows[y & 7] for y in range(h)))
    diff = ImageChops.subtract(gray, thresholds)
    return diff.point(lambda v: 255 if v > 0 else 0).convert("1", dither=Image.Dither.NONE)


def image_to_1bit(img_bytes, dither=DEFAULT_DITHER):
    """Validates, scales and converts raw image file bytes into the
    1-bit image that goes to the printer. Used unchanged by both the print
    path and the preview, so a preview always shows exactly what would be
    printed.

    Raises ImageConversionError for anything the caller should report
    (too large, unknown method, Atkinson limits). Other exceptions
    (corrupt/unsupported files) propagate - callers log them and answer
    with a fixed text, since Pillow's messages carry internals."""
    if dither not in DITHER_METHODS:
        raise ImageConversionError(
            f"Unknown dither method {dither!r}, allowed: {', '.join(DITHER_METHODS)}",
            400, "images.error.unknown_dither",
        )
    if len(img_bytes) > MAX_IMAGE_BYTES:
        raise ImageConversionError(
            f"Image file too large ({len(img_bytes)} bytes, maximum {MAX_IMAGE_BYTES} bytes)", 400,
        )

    img = Image.open(io.BytesIO(img_bytes))
    img.verify()  # checks the structure without fully decoding
    img = Image.open(io.BytesIO(img_bytes))  # reopen after verify()

    if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
        raise ImageConversionError(
            f"Image too large ({img.width}x{img.height}), maximum {MAX_IMAGE_DIMENSION}px per side", 400,
        )

    target_size = img.size
    if img.width > PRINT_WIDTH:
        ratio = PRINT_WIDTH / img.width
        target_size = (PRINT_WIDTH, int(img.height * ratio))

    # Checked against the header size before decoding/scaling, so an
    # oversized Atkinson request fails right away instead of after the
    # expensive steps.
    if dither == DITHER_ATKINSON and target_size[0] * target_size[1] > ATKINSON_MAX_PIXELS:
        raise ImageConversionError(
            f"Image too large for Atkinson dithering ({target_size[0]}x{target_size[1]}px after scaling, "
            f"maximum {ATKINSON_MAX_PIXELS} pixels = {PRINT_WIDTH}x{ATKINSON_MAX_PIXELS // PRINT_WIDTH}). "
            "Use Floyd-Steinberg or Bayer for this image.",
            400, "images.error.atkinson_too_large",
            width=target_size[0], height=target_size[1],
            max_width=PRINT_WIDTH, max_height=ATKINSON_MAX_PIXELS // PRINT_WIDTH,
        )

    # Scaling happens before any per-pixel work, so dithering cost
    # depends on the print size, not on the upload's resolution.
    if target_size != img.size:
        img = img.resize(target_size)

    if _has_alpha(img):
        img = _flatten_on_white(img)

    img = img.convert("L")
    # Auto-contrast BEFORE dithering, for every method: stretches the
    # brightness range so generally darker/low-contrast photos don't end
    # up completely black. cutoff=1 clips the most extreme 1% at both
    # ends, so a few very bright/dark outliers (e.g. a small highlight)
    # don't skew the contrast.
    img = ImageOps.autocontrast(img, cutoff=1)

    if dither == DITHER_ATKINSON:
        return _dither_atkinson(img, time.monotonic() + ATKINSON_TIME_LIMIT_S)
    if dither == DITHER_BAYER:
        return _dither_bayer(img)
    # convert("1") without an explicit threshold uses Pillow's
    # Floyd-Steinberg dithering (default behavior) instead of a hard
    # black/white cutoff - photos with grayscale/gradients look much more
    # natural this way instead of turning into large black blobs.
    return img.convert("1")
