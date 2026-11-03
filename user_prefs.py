"""
Personal settings of a web UI account, kept as a JSON object in the
users.prefs column of auth.db (auth_store.get_prefs/update_prefs):

  ui_lang       language of the web UI (a key of i18n.SUPPORTED_LANGUAGES)
  theme         UI theme (a key of themes.SUPPORTED_THEMES)
  receipt_lang  language of the receipts the account prints

A missing key means "device default": the global setting in
settings.json applies, as it does for everything without an account -
the login page, API token and MQTT requests, watchers, the boot
greeting, and the whole device while no admin account exists.

Values are checked when they are written and again when they are read,
so a row written by hand or by another version (an unknown theme, a
removed language) falls back to the device default instead of breaking
a page. Unknown keys are dropped on the next write.

The settings live in auth.db and not in settings.json: they belong to
an account and disappear with it, and they are not part of a settings
backup archive (modules/backup/archive.py exports settings.json only).
"""
import auth_store
import i18n
import themes

UI_LANG = "ui_lang"
THEME = "theme"
RECEIPT_LANG = "receipt_lang"

_CHOICES = {
    UI_LANG: i18n.SUPPORTED_LANGUAGES,
    THEME: themes.SUPPORTED_THEMES,
    RECEIPT_LANG: i18n.SUPPORTED_LANGUAGES,
}


def sanitize(prefs):
    """Only the known keys with a valid value; everything else is left
    out (= device default)."""
    if not isinstance(prefs, dict):
        return {}
    clean = {}
    for key, choices in _CHOICES.items():
        value = prefs.get(key)
        if isinstance(value, str) and value in choices:
            clean[key] = value
    return clean


def valid_choice(key, value):
    """Whether value may be stored under key. None and "" stand for
    "device default" and are always valid."""
    return value in (None, "") or (isinstance(value, str) and value in _CHOICES[key])


def update(user_id, changes):
    """Applies {key: value} to an account's settings; None or "" removes
    the key, so the device default applies again. Raises ValueError for
    an unknown key or a value that is not one of its choices, and
    auth_store.AuthError("user_not_found") for an unknown account.
    Returns the new, sanitized settings."""
    for key, value in changes.items():
        if key not in _CHOICES or not valid_choice(key, value):
            raise ValueError(key)

    def change(current):
        new = sanitize(current)
        for key, value in changes.items():
            if value in (None, ""):
                new.pop(key, None)
            else:
                new[key] = value
        return new

    return sanitize(auth_store.update_prefs(user_id, change))


def for_owner(owner):
    """The sanitized settings of the account behind an owner pair
    (account id, account created) from the print queue, the history or
    the pending list. {} for System (None) and for a deleted account."""
    if owner is None:
        return {}
    prefs = auth_store.get_prefs(owner[0], owner[1])
    return sanitize(prefs) if prefs is not None else {}


def receipt_language(owner):
    """The receipt language chosen by the owner of a print job, or None
    when the device default applies."""
    return for_owner(owner).get(RECEIPT_LANG)
