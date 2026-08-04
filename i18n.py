"""
Minimal i18n layer: loads translation strings from JSON files and looks
them up by key. Deliberately NOT using Flask-Babel/gettext (too heavy a
toolchain - .po/.mo compilation, pybabel CLI - for a handful of UI
strings in two languages). Language is a single, shared setting (this is
a single-user home appliance, not a multi-user app with per-session
locales), stored in settings_store as settings["language"].

Translation files: translations/<lang>.json in the project root for
shared/chrome strings (navigation, settings page). Individual modules
MAY ship their own modules/<name>/translations/<lang>.json - the loader
merges those in on top, so a module (including future community modules)
can add or override its own strings without touching the core files.
"""
import glob
import json
import os

_TRANSLATIONS = {}  # lang -> {key: value}
# code -> display name (shown in the language switcher). A dict instead
# of a plain list, so adding a third language later means one JSON file
# plus one entry here - no hardcoded radio buttons to update in the
# template (see templates/settings.html).
SUPPORTED_LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
}
DEFAULT_LANGUAGE = "de"


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def load_translations():
    """Collects every translations/<lang>.json file (project root plus
    each module) and rebuilds the lookup table. Called once at startup."""
    global _TRANSLATIONS
    _TRANSLATIONS = {lang: {} for lang in SUPPORTED_LANGUAGES}

    root = os.path.dirname(os.path.abspath(__file__))
    search_paths = [os.path.join(root, "translations")]
    search_paths += glob.glob(os.path.join(root, "modules", "*", "translations"))

    for folder in search_paths:
        for lang in SUPPORTED_LANGUAGES:
            file_path = os.path.join(folder, f"{lang}.json")
            if os.path.exists(file_path):
                _TRANSLATIONS[lang].update(_load_json(file_path))


def t(key, lang=None, **kwargs):
    """Translates a key into the given (or default) language. Falls back
    to German if the key is missing in the target language, and to the
    key itself if it doesn't exist anywhere - so the UI stays usable even
    with an incomplete translation instead of showing blanks or errors.

    kwargs are applied via str.format() for messages with placeholders,
    e.g. t("settings.weather_locations.saved", lang, name="Berlin")."""
    if lang is None:
        lang = DEFAULT_LANGUAGE
    value = _TRANSLATIONS.get(lang, {}).get(key)
    if value is None:
        value = _TRANSLATIONS.get(DEFAULT_LANGUAGE, {}).get(key)
    if value is None:
        value = key
    if kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError):
            return value  # malformed placeholder - better to show the raw string than crash
    return value


def current_language():
    """Reads the current UI language from settings_store. Imported
    lazily (inside the function) to avoid a hard/circular dependency at
    module load time - i18n.py itself has no reason to require
    settings_store to exist yet when it's first imported."""
    import settings_store
    return settings_store.get_settings().get("language", DEFAULT_LANGUAGE)


def tr(key, **kwargs):
    """Shorthand for t(key, current_language(), **kwargs) - for use in
    route handlers where the current language isn't already at hand
    (templates get t() bound to the current language via app.py's
    context processor instead)."""
    return t(key, current_language(), **kwargs)
