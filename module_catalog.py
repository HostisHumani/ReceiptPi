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
    {"key": "recipes", "icon": "chef-hat", "url": "/recipes"},
]

MODULE_KEYS = {m["key"] for m in MODULES}


def active_modules(settings):
    """Effective on/off state per module key: the user's
    enabled_modules toggle AND any module-specific precondition. Used
    for everything that hides/blocks a module - app.py's before_request
    404 hook, the home page tiles and module count, and UI bits of
    other modules that link into it (the Mealie import on /shopping) -
    so they can never disagree with each other.

    Why a separate precondition and not just the toggle: "recipes" has
    a second switch of its own - the provider dropdown on
    /settings/recipes ("off"/"mealie"). With the provider "off" there's
    nothing the module could do, so it must behave exactly like a
    disabled module (no tile, 404 on its routes) instead of showing a
    tile that leads to a "not configured" page.

    The raw enabled_modules dict stays the source for the checkboxes on
    /settings/modules - showing the effective state there would make
    saving that page silently write recipes=False while the provider
    is off, so switching the provider back on later wouldn't bring the
    module back."""
    enabled = settings.get("enabled_modules", {})
    active = {m["key"]: bool(enabled.get(m["key"], True)) for m in MODULES}
    if settings.get("recipes", {}).get("provider", "off") == "off":
        active["recipes"] = False
    return active
