"""
Single source of truth for the toggleable print-type modules shown as
tiles on the home page. Used by:
  - templates/home.html (which tiles to render, and in which order)
  - templates/settings_modules.html (the on/off checkbox list)
  - app.py's before_request hook (which blueprint names are subject to
    the enabled_modules check)

Adding a future module here is the only place that needs touching for
it to show up on the home page AND be toggleable - closes the
long-standing "registry for the menu" backlog item.

Each entry's "key" MUST match the corresponding Blueprint's name
(Blueprint("<key>", __name__) in modules/<key>/routes.py) - the
before_request enforcement in app.py relies on that match.

"icon" is a Lucide icon name (kebab-case, no ".svg") - resolved to a
reusable CSS class ".icon-<name>" (see static/style.css's icon-mask
block + static/icons/<name>.svg), NOT an emoji since the 09.08.2026
redesign.
"""

MODULES = [
    {"key": "lists", "icon": "list-checks", "url": "/lists"},
    {"key": "message", "icon": "message-square", "url": "/message"},
    {"key": "weather", "icon": "cloud-sun", "url": "/weather"},
    {"key": "images", "icon": "image", "url": "/images"},
    {"key": "wifi", "icon": "wifi", "url": "/wifi"},
    {"key": "system", "icon": "server", "url": "/system"},
    {"key": "games", "icon": "gamepad-2", "url": "/games"},
]

MODULE_KEYS = {m["key"] for m in MODULES}
