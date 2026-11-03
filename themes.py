"""
Central theme registry: which theme keys exist and what they're called
in the UI. The actual colors live in static/style.css as
[data-theme="<key>"] blocks - this module only needs to know the valid
keys and their display names, same split as i18n.py (translation
lookup vs. SUPPORTED_LANGUAGES).

The theme of a page is the logged-in account's own choice if it made
one (user_prefs.py), otherwise the device default settings["theme"] -
see current_theme().
"""

# key -> display name shown in the settings dropdown. Dict instead of a
# plain list, same reasoning as i18n.SUPPORTED_LANGUAGES: adding a
# theme later means one CSS block plus one entry here, no hardcoded
# radio buttons to update in the template.
SUPPORTED_THEMES = {
    "warm": "Warm",
    "forrest": "Forrest",
    "dark-lime": "Dark Lime",
    "frost": "Frost",
    "butter-bean": "Butter Bean",
    "white-purple": "White Purple",
}

# key -> one representative color for the small swatch next to each
# theme in the theme pickers (device default and personal settings).
SWATCHES = {
    "warm": "#c2410c",
    "forrest": "#2fae66",
    "dark-lime": "#b6ff2e",
    "frost": "#81a1c1",
    "butter-bean": "#013e37",
    "white-purple": "#460b6a",
}

# "Warm" is the new default (2026 redesign) - the only theme that also
# follows the system/browser light-dark preference automatically, see
# its [data-theme="warm"] block in static/style.css. Existing
# installations keep whatever theme they already have saved in
# settings.json; this only affects brand-new installs (see
# settings_store.DEFAULT_SETTINGS, which has its own literal copy of
# this default for the same reason its other defaults are literals).
DEFAULT_THEME = "warm"


def current_theme():
    """The theme for the current request: the account's own choice,
    else the device default. Same resolution and the same lazy imports
    as i18n.current_language()."""
    import user_prefs
    theme = user_prefs.current().get(user_prefs.THEME)
    if theme is not None:
        return theme
    import settings_store
    return settings_store.get_settings().get("theme", DEFAULT_THEME)


def current_glass():
    """(glass, blur) for the current request, each "on" or "off" - the
    values base.html puts into data-glass/data-blur. Resolved per switch
    like current_theme(): the account's own choice, else the device
    default (settings["glass"]). Blur is reported as "off" while glass
    is off, so the stylesheet never has to check both attributes for a
    blurred surface that isn't glass."""
    import settings_store
    import user_prefs
    prefs = user_prefs.current()
    device = settings_store.get_settings().get("glass", {})

    def _resolve(pref_key, device_key):
        value = prefs.get(pref_key)
        if value is not None:
            return value == "on"
        return bool(device.get(device_key, True))

    glass = _resolve(user_prefs.GLASS, "enabled")
    blur = glass and _resolve(user_prefs.GLASS_BLUR, "blur")
    return ("on" if glass else "off"), ("on" if blur else "off")
