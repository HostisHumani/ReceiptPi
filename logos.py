"""
Central logo printing: prints a small header logo before a receipt's
main content, if enabled - shared by every print module instead of each
one reimplementing its own image handling.

Three-tier resolution per module (see print_logo() below):
  1. logos.enabled must be on (global master switch)
  2. logos.modules.<module>.enabled must be on for this specific module
  3. Logo file: a custom upload for this module if one exists
     (LOGOS_DIR/<module>.png), otherwise the shared default
     (LOGOS_DIR/default.png) - lets one logo serve every enabled module
     without uploading it N times, per-module override still possible.

Logo files live in STATE_DIR/logos/ (uploaded via the settings UI, not
part of the project directory - same reasoning as settings.json, see
settings_store.py: survives deploys/updates, writable under
ProtectSystem=strict).

Deliberately capped to LOGO_MAX_WIDTH px - meant as a small, unobtrusive
header mark, not a dominant visual element eating up receipt paper.
"""
import io
import os
import shutil

from PIL import Image, ImageOps

import settings_store

LOGOS_DIR = os.path.join(settings_store.STATE_DIR, "logos")

# Bundled example logos shipped with the project (assets/example-logos/)
# - used to seed a working default.png on first run, see
# ensure_default_logo_seeded() below. Project root = one level up from
# this file (logos.py lives at the project root itself).
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DEFAULT_LOGO = os.path.join(PROJECT_ROOT, "assets", "example-logos", "default.png")

# ~30% of the printable width (see printer.py / modules/images/routes.py
# for why 512px, not 576px, is the real cap) - small header mark, not a
# dominant visual element. This is intentionally NOT user-configurable
# (see settings_logos.html) - keeps the feature simple and prevents an
# oversized upload from dominating every single receipt.
LOGO_MAX_WIDTH = 150

# Uploads themselves can be modest photos/logos before the print-time
# resize kicks in - much smaller ceiling than the general image-print
# feature (MAX_IMAGE_BYTES=12MB there), since a header logo has no
# reason to be a full-resolution photo.
MAX_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_UPLOAD_DIMENSION = 4000

# The print types that can carry a logo. Image printing is deliberately
# excluded - the printed content there already IS an image, stacking a
# logo on top of it would look odd.
MODULE_KEYS = ("shopping", "message", "wifi", "weather", "system", "automation")


def has_custom_logo(module_key):
    return os.path.isfile(os.path.join(LOGOS_DIR, f"{module_key}.png"))


def has_default_logo():
    return os.path.isfile(os.path.join(LOGOS_DIR, "default.png"))


def ensure_default_logo_seeded():
    """One-time convenience for a good out-of-the-box experience: if no
    default logo has ever been set up, copy the bundled example
    (assets/example-logos/default.png) into STATE_DIR/logos/ so a
    community user who enables logos gets something sensible right away
    instead of an empty "no logo set" state - not everyone has their
    own logo file ready to go.

    Uses a marker file as the trigger (not "does default.png exist"),
    so a user who deliberately deletes the default logo via the
    settings UI doesn't have it silently reappear on the next restart -
    same reasoning as the migrated_from_config flags in
    settings_store.py. IMPORTANT: the marker is only written once
    something has actually settled the question (a copy succeeded, or
    a default.png was already present for any reason) - NOT just
    because this function ran. Otherwise a restart that happens to race
    with an in-progress deploy (bundled asset not synced yet) would
    permanently record "seeded" despite never having copied anything,
    silently leaving the user without a default logo forever. If the
    bundled asset genuinely isn't there yet, this simply retries on the
    next start instead of giving up.

    Never raises - a missing bundled asset or a read-only filesystem
    shouldn't block server startup."""
    marker = os.path.join(LOGOS_DIR, ".default_seeded")
    try:
        if os.path.isfile(marker):
            return
        os.makedirs(LOGOS_DIR, exist_ok=True)
        default_path = os.path.join(LOGOS_DIR, "default.png")
        if os.path.isfile(default_path):
            # Already has one - either a previous successful seed or
            # the user's own upload. Nothing to do, but remember so we
            # don't need to check again on every future start.
            with open(marker, "w") as f:
                f.write("")
            return
        if os.path.isfile(BUNDLED_DEFAULT_LOGO):
            shutil.copyfile(BUNDLED_DEFAULT_LOGO, default_path)
            with open(marker, "w") as f:
                f.write("")
        # else: bundled asset isn't available (yet) - deliberately do
        # NOT write the marker, so this retries on the next start
        # instead of permanently giving up.
    except Exception:
        pass


def save_logo(slot, file_bytes):
    """Validates and stores an uploaded logo. slot is one of
    MODULE_KEYS (a per-module custom logo) or "default" (the shared
    fallback). Always stored as PNG regardless of the uploaded format,
    so print_logo() only ever has to deal with one file type. Returns
    (ok, detail) - detail is an error message on failure, empty on
    success."""
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return False, f"Logo file too large ({len(file_bytes)} bytes, maximum {MAX_UPLOAD_BYTES} bytes)"

    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()
        img = Image.open(io.BytesIO(file_bytes))  # reopen after verify()
        if img.width > MAX_UPLOAD_DIMENSION or img.height > MAX_UPLOAD_DIMENSION:
            return False, f"Logo too large ({img.width}x{img.height}), maximum {MAX_UPLOAD_DIMENSION}px per side"
        img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
    except Exception as e:
        return False, f"Could not process logo image: {e}"

    os.makedirs(LOGOS_DIR, exist_ok=True)
    img.save(os.path.join(LOGOS_DIR, f"{slot}.png"), format="PNG")
    return True, ""


def delete_logo(slot):
    """Removes a stored logo (custom or default). Returns True if a
    file was actually there and got removed, False if there was
    nothing to delete."""
    path = os.path.join(LOGOS_DIR, f"{slot}.png")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def _resolve_logo_path(module_key):
    custom = os.path.join(LOGOS_DIR, f"{module_key}.png")
    if os.path.isfile(custom):
        return custom
    default = os.path.join(LOGOS_DIR, "default.png")
    if os.path.isfile(default):
        return default
    return None


def print_logo(p, module_key):
    """Prints the configured logo for this module onto an already-open
    printer connection `p`, if logos are enabled globally AND for this
    module AND a logo file (custom or default) actually exists. A no-op
    otherwise - callers don't need to check anything themselves, just
    call this first thing after opening the printer.

    Returns True if a logo was actually printed, False otherwise (logos
    disabled, no file, or any error) - callers use this to decide
    whether to add the blank line that separates the logo from the
    heading (see modules/message/routes.py etc.). Only meaningful for
    that layout decision, never for control flow that could break the
    print job itself.

    Never raises: a missing/unreadable/corrupt logo file must not break
    the actual print job it's attached to - worst case, the receipt
    just prints without its logo."""
    try:
        logos_settings = settings_store.get_settings().get("logos", {})
        if not logos_settings.get("enabled"):
            return False
        if not logos_settings.get("modules", {}).get(module_key, {}).get("enabled"):
            return False

        logo_path = _resolve_logo_path(module_key)
        if not logo_path:
            return False

        img = Image.open(logo_path)
        # If the logo has transparency, composite it onto white FIRST -
        # otherwise convert("L") ignores alpha entirely and reads the
        # (often black) RGB values underneath the transparent pixels,
        # turning the whole logo into a solid black rectangle instead of
        # a white background with the icon on it.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, img)
        img = img.convert("L")
        if img.width > LOGO_MAX_WIDTH:
            ratio = LOGO_MAX_WIDTH / img.width
            img = img.resize((LOGO_MAX_WIDTH, max(1, int(img.height * ratio))))
        # Same autocontrast + Floyd-Steinberg dithering as the regular
        # image-print pipeline (see modules/images/routes.py) - keeps
        # logos with soft edges/gradients from turning into black blobs.
        img = ImageOps.autocontrast(img, cutoff=1)
        img = img.convert("1")

        p.set(align="center")
        p.image(img)
        return True
    except Exception:
        return False
