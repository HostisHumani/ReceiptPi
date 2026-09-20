"""
Central theme registry: which theme keys exist and what they're called
in the UI. The actual colors live in static/style.css as
[data-theme="<key>"] blocks - this module only needs to know the valid
keys and their display names, same split as i18n.py (translation
lookup vs. SUPPORTED_LANGUAGES).

Theme is a single, shared setting (settings_store["theme"]), not
per-session - this is a single-user home appliance, same reasoning as
the language setting.
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

# "Warm" is the new default (2026 redesign) - the only theme that also
# follows the system/browser light-dark preference automatically, see
# its [data-theme="warm"] block in static/style.css. Existing
# installations keep whatever theme they already have saved in
# settings.json; this only affects brand-new installs (see
# settings_store.DEFAULT_SETTINGS, which has its own literal copy of
# this default for the same reason its other defaults are literals).
DEFAULT_THEME = "warm"
