"""
Cache-busting for static assets. SEND_FILE_MAX_AGE_DEFAULT (app.py) is
deliberately left at 1h - the problem this solves isn't cache
duration, it's that /static/style.css (and friends) never changed URL
between deploys, so a browser that already had it cached was fully
entitled to keep serving the stale copy for up to that hour after a
redeploy, regardless of what's actually on the server. Every URL this
module hands out includes a content-hash query param instead, so a
changed file always gets a new URL.

Hashes are computed ONCE per process and cached in memory (same
"read once, never re-read" pattern as settings_store's mtime cache) -
this app always restarts via systemd after a deploy (see CLAUDE.md's
deploy steps), so a fresh process means fresh hashes; there's no
per-request disk I/O to worry about on the Pi.

style.css itself references icons (.icon-<name> mask-image rules) and
fonts (@font-face) by a plain /static/... path. CSS is not a Jinja
template, so {{ static_url(...) }} can't be used there directly -
versioned_style_css() below rewrites those references once at startup
instead, and app.py serves the result from a dedicated route that
takes over the exact /static/style.css path from Flask's normal
static handler (icon/font files themselves are still served
byte-for-byte unchanged by that normal handler, just under a
versioned URL).
"""
import hashlib
import os
import re

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_hash_cache = {}  # relative path under static/ -> short content hash
_css_body_cache = None  # style.css, once processed (icon/font URLs versioned)


def _file_hash(relative_path):
    """Short, non-cryptographic content fingerprint (MD5 is plenty for
    change detection, this is not a security context) for a file under
    static/. Falls back to "0" if the file is missing - a stale/absent
    asset shouldn't be a reason to fail rendering the page."""
    if relative_path not in _hash_cache:
        try:
            with open(os.path.join(_STATIC_DIR, relative_path), "rb") as f:
                _hash_cache[relative_path] = hashlib.md5(f.read()).hexdigest()[:10]
        except OSError:
            _hash_cache[relative_path] = "0"
    return _hash_cache[relative_path]


def static_url(relative_path):
    """Jinja global (see app.py's inject_i18n): url_for('static', ...)
    plus a ?v=<content-hash> query param. Use this instead of plain
    url_for('static', filename=...) for any static file this app owns
    and edits (style.css, draft-autosave.js, ...) - not needed for
    third-party assets that never change post-install."""
    from flask import url_for
    return f"{url_for('static', filename=relative_path)}?v={_file_hash(relative_path)}"


_ASSET_URL_RE = re.compile(r'(/static/(?:icons|fonts)/[A-Za-z0-9_.-]+\.(?:svg|woff2))')


def versioned_style_css():
    """static/style.css's content with every /static/icons/*.svg and
    /static/fonts/*.woff2 reference it contains rewritten to its own
    versioned URL, computed once and cached for the life of the
    process (see module docstring)."""
    global _css_body_cache
    if _css_body_cache is None:
        with open(os.path.join(_STATIC_DIR, "style.css"), encoding="utf-8") as f:
            raw = f.read()

        def _versioned(match):
            path = match.group(1)
            relative = path[len("/static/"):]
            return f"{path}?v={_file_hash(relative)}"

        _css_body_cache = _ASSET_URL_RE.sub(_versioned, raw)
        _file_hash("style.css")  # also warm style.css's own hash for static_url()
    return _css_body_cache
