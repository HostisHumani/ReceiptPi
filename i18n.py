"""
Minimal i18n layer: loads translation strings from JSON files and looks
them up by key. Deliberately NOT using Flask-Babel/gettext (too heavy a
toolchain - .po/.mo compilation, pybabel CLI - for a handful of UI
strings in two languages).

The language of a lookup without an explicit lang comes from
current_language(): while the print worker runs a job, the receipt
language of that job (see job_language()); in a request, the logged-in
account's own UI language if it chose one (user_prefs.py); otherwise the
device default settings["language"] from settings_store.

Translation files: translations/<lang>.json in the project root for
shared/chrome strings (navigation, settings page). Individual modules
MAY ship their own modules/<name>/translations/<lang>.json - the loader
merges those in on top, so a module (including future community modules)
can add or override its own strings without touching the core files.
"""
import contextvars
import glob
import json
import os
from contextlib import contextmanager

_TRANSLATIONS = {}  # lang -> {key: value}
# code -> display name (shown in the language switcher). A dict instead
# of a plain list, so adding a third language later means one JSON file
# plus one entry here - no hardcoded radio buttons to update in the
# template (see templates/settings_language.html).
SUPPORTED_LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
}
DEFAULT_LANGUAGE = "de"

# Receipt language of the print job the current thread is running, None
# outside a job or for a job without one. A ContextVar rather than a
# plain global, so only the print worker's own context ever sees it.
_job_language = contextvars.ContextVar("receipt_language", default=None)


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


@contextmanager
def job_language(lang):
    """Makes lang the language of every lookup without an explicit lang
    inside the block - the print worker wraps each job in it, so the
    receipt texts of all _raw_print_* functions follow the job's receipt
    language without each of them taking a parameter. None (or an
    unsupported value) leaves the normal resolution in place. The
    previous value is restored however the block ends, so a job that
    raises can't pass its language on to the next one."""
    token = _job_language.set(lang if lang in SUPPORTED_LANGUAGES else None)
    try:
        yield
    finally:
        _job_language.reset(token)


def current_language():
    """The language for the current context: the receipt language of the
    print job being run (job_language()), else the UI language chosen by
    the account behind the current request, else the device default
    from settings_store. Without a job or a request (the MQTT thread,
    watchers) and on pages that load no session (login, API token
    routes, no admin account yet) that is always the device default.
    settings_store and user_prefs are imported here rather than at
    module level: both import i18n themselves."""
    lang = _job_language.get()
    if lang is not None:
        return lang
    import user_prefs
    lang = user_prefs.current().get(user_prefs.UI_LANG)
    if lang is not None:
        return lang
    import settings_store
    return settings_store.get_settings().get("language", DEFAULT_LANGUAGE)


def tr(key, **kwargs):
    """Shorthand for t(key, current_language(), **kwargs) - for use in
    route handlers where the current language isn't already at hand
    (templates get t() bound to the current language via app.py's
    context processor instead)."""
    return t(key, current_language(), **kwargs)
