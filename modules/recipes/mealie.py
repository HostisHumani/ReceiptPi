"""
Mealie API client + pure formatting helpers for the recipes module.

Every endpoint, query parameter and response field used here was
verified against a live Mealie v3.27.0 instance (2026-09-24, raw
responses captured) before this was written - NOT taken from the docs
or the OpenAPI spec alone. The per-function comments note the quirks
that verification turned up. Don't add endpoints/fields here without
checking a real response first.

Deliberately plain urllib (same as modules/weather/), no extra HTTP
dependency on the Pi for a handful of GET requests.
"""
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

import secrets_crypto
import settings_store
from security import MAX_ITEM_LEN, MAX_ITEMS, MAX_TEXT_LEN, MAX_TITLE_LEN

# Every call here happens inside a request handler (a user is waiting
# on the page) - fail fast rather than tying up one of the 4 gunicorn
# threads for long if Mealie is down.
TIMEOUT_SECONDS = 10

# Upper bound for how many comma-separated ingredients the random
# recipe form resolves - each one is its own /api/foods request.
MAX_RANDOM_INGREDIENTS = 5

SEARCH_PAGE_SIZE = 20

# /api/foods?search= is fuzzy and NOT ordered by relevance (verified:
# searching "Salz" returned 18 foods with the exact "Salz" last) - so
# we fetch a full page and look for an exact name match ourselves (see
# resolve_food()) instead of trusting the first result.
FOOD_SEARCH_PAGE_SIZE = 50


class MealieError(Exception):
    """kind is one of: "not_configured", "unreachable", "auth",
    "not_found", "http", "invalid_response" - mapped to a translated
    message by the caller (see routes.error_message())."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------

def normalize_base_url(raw):
    """Returns the base URL without trailing slash, or None if it isn't
    a plain http(s) URL. The trailing slash matters: every API path
    below starts with "/api/...", and Mealie answers unknown paths
    (e.g. a doubled "//api") with HTTP 200 + its HTML web app instead
    of a 404 (verified), which would only surface later as a confusing
    "not JSON" error."""
    url = str(raw or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _url_key(url):
    """Comparison key for "same address": scheme and host are
    case-insensitive per RFC 3986 (urlsplit's .hostname is already
    lowercased), an explicit default port equals no port, everything else
    (path, query, userinfo) must match exactly. Raises ValueError for an
    unparseable port (e.g. ":abc", ":99999")."""
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port or _DEFAULT_PORTS.get(scheme)
    return (scheme, parsed.hostname, port, parsed.username, parsed.password,
            parsed.path, parsed.query)


def same_base_url(a, b):
    """Whether two base URLs point at the same Mealie address - the gate
    for reusing the stored token (see settings routes). Trailing slashes
    are ignored via normalize_base_url(). Anything unparseable counts as
    different, so the caller falls back to demanding a freshly entered
    token rather than sending the stored one somewhere unexpected."""
    a, b = normalize_base_url(a), normalize_base_url(b)
    if not a or not b:
        return False
    try:
        return _url_key(a) == _url_key(b)
    except ValueError:
        return False


def _same_origin(a, b):
    try:
        return _url_key(a)[:3] == _url_key(b)[:3]
    except ValueError:
        return False


def _settings():
    return settings_store.get_settings().get("recipes", {})


def is_configured():
    """Cheap check (no decryption) for templates deciding whether to
    show Mealie-dependent UI at all, e.g. the import section on
    /shopping."""
    s = _settings()
    m = s.get("mealie", {})
    return s.get("provider") == "mealie" and bool(m.get("base_url")) and bool(m.get("token_encrypted"))


def get_config():
    """Returns (base_url, token) or raises MealieError("not_configured").
    A token that no longer decrypts (secret.key replaced/restored from a
    different install) counts as not configured - same handling as
    secrets_crypto.decrypt_password()'s other callers."""
    s = _settings()
    if s.get("provider") != "mealie":
        raise MealieError("not_configured")
    m = s.get("mealie", {})
    base_url = m.get("base_url") or ""
    token = secrets_crypto.decrypt_password(m.get("token_encrypted"))
    if not base_url or not token:
        raise MealieError("not_configured")
    return base_url, token


# ---------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------

class _NoAuthOnCrossOriginRedirect(urllib.request.HTTPRedirectHandler):
    """urllib's default redirect handling copies every header except
    Content-Length/Content-Type onto the redirected request - the
    Authorization header included, whatever host the redirect points to.
    A Mealie (or anything posing as one) answering with a redirect to a
    different scheme/host/port would thus get the token forwarded there.
    Strip it for those; a same-origin redirect keeps it, so a Mealie
    behind a path-rewriting reverse proxy still works."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and not _same_origin(req.full_url, new_req.full_url):
            new_req.remove_header("Authorization")
        return new_req


# build_opener() swaps its default HTTPRedirectHandler for this subclass;
# everything else (proxy env vars, HTTPS handling) stays as urlopen() had it.
_OPENER = urllib.request.build_opener(_NoAuthOnCrossOriginRedirect)


def _get(base_url, token, path, params=None):
    url = base_url + path
    if params:
        # doseq=True turns {"foods": [a, b]} into foods=a&foods=b - the
        # repeated-parameter form verified to work for multi-food
        # filtering.
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"Accept": "application/json", "User-Agent": "receiptpi"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        # HTTPError must be caught before URLError (it's a subclass).
        # Verified: missing/invalid token -> 401 {"detail": "Could not
        # validate credentials"}; unknown recipe slug / shopping list
        # id -> 404.
        if e.code in (401, 403):
            raise MealieError("auth", f"HTTP {e.code}") from e
        if e.code == 404:
            raise MealieError("not_found", f"HTTP {e.code}") from e
        raise MealieError("http", f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MealieError("unreachable", str(getattr(e, "reason", e))) from e
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise MealieError("invalid_response", "response is not JSON") from e


def _get_configured(path, params=None):
    base_url, token = get_config()
    return _get(base_url, token, path, params)


def _page_items(data):
    """Mealie's paginated list shape (verified for /api/recipes,
    /api/foods, /api/households/shopping/lists): {"page", "per_page",
    "total", "total_pages", "items": [...], "next", "previous"}."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise MealieError("invalid_response", "unexpected list response shape")
    items = [i for i in data["items"] if isinstance(i, dict)]
    total = data.get("total") if isinstance(data.get("total"), int) else len(items)
    return items, total


# ---------------------------------------------------------------
# API calls
# ---------------------------------------------------------------

def check_connection(base_url, token):
    """Returns {"version": ..., "user": ...} or raises MealieError.

    Two requests, not one: /api/app/about is PUBLIC - verified to
    return HTTP 200 with no token and with a made-up token alike - so
    on its own it only proves the URL points at a Mealie server, never
    that the token works. /api/users/self requires a valid token (401
    otherwise, verified) and is what actually validates it."""
    about = _get(base_url, None, "/api/app/about")
    if not isinstance(about, dict) or not about.get("version"):
        raise MealieError("invalid_response", "not a Mealie /api/app/about response")
    user = _get(base_url, token, "/api/users/self")
    if not isinstance(user, dict):
        raise MealieError("invalid_response", "unexpected /api/users/self response")
    return {
        "version": str(about["version"]),
        "user": str(user.get("fullName") or user.get("username") or ""),
    }


def search_recipes(query):
    """Returns (items, total) - items are [{"name", "slug",
    "description"}]. Note: Mealie's search only matches recipe
    name/description, not ingredients or tags (verified) - ingredient
    lookup goes through random_recipe()'s food filter instead."""
    items, total = _page_items(_get_configured(
        "/api/recipes", {"search": query, "perPage": SEARCH_PAGE_SIZE},
    ))
    results = [
        {
            "name": str(i.get("name") or "").strip(),
            "slug": str(i.get("slug") or ""),
            "description": str(i.get("description") or "").strip(),
        }
        for i in items if i.get("slug")
    ]
    return results, total


def get_recipe(slug):
    """Full recipe object (raw dict). Raises MealieError("not_found")
    for an unknown slug (verified: HTTP 404)."""
    data = _get_configured("/api/recipes/" + urllib.parse.quote(str(slug), safe=""))
    if not isinstance(data, dict):
        raise MealieError("invalid_response", "unexpected recipe response")
    return data


def resolve_food(name):
    """Resolves a typed-in ingredient name to a Mealie food.

    Returns (food, suggestions): food is {"id", "name"} for an exact
    (case-insensitive) match on name OR pluralName, else None -
    suggestions is then a few names from the fuzzy search result for
    the user to pick from. Why exact-only: passing a NAME to
    /api/recipes?foods= crashes Mealie with HTTP 500 (verified), so a
    UUID is mandatory, and the fuzzy /api/foods search is too loose to
    just take its first hit (see FOOD_SEARCH_PAGE_SIZE)."""
    items, _total = _page_items(_get_configured(
        "/api/foods", {"search": name, "perPage": FOOD_SEARCH_PAGE_SIZE},
    ))
    wanted = name.strip().casefold()
    for food in items:
        names = (food.get("name"), food.get("pluralName"))
        if food.get("id") and any(n and str(n).strip().casefold() == wanted for n in names):
            return {"id": str(food["id"]), "name": str(food.get("name"))}, []
    suggestions = [str(f["name"]) for f in items if f.get("name")][:8]
    return None, suggestions


def random_recipe(food_ids, require_all=False):
    """Returns one random recipe summary ({"name", "slug"}) or None if
    nothing matches. food_ids must already be UUIDs (see
    resolve_food()). Several ids are OR-combined by Mealie by default,
    requireAllFoods=true makes it AND (both verified). An empty
    food_ids list picks from all recipes. paginationSeed must change
    per call - the same seed returns the same "random" order."""
    params = {"orderBy": "random", "paginationSeed": secrets.token_hex(8), "perPage": 1}
    if food_ids:
        params["foods"] = list(food_ids)
        if require_all:
            params["requireAllFoods"] = "true"
    items, _total = _page_items(_get_configured("/api/recipes", params))
    if not items or not items[0].get("slug"):
        return None
    return {"name": str(items[0].get("name") or "").strip(), "slug": str(items[0]["slug"])}


def get_shopping_lists():
    """Returns [{"id", "name"}] of the token user's household lists."""
    items, _total = _page_items(_get_configured("/api/households/shopping/lists"))
    return [
        {"id": str(i["id"]), "name": str(i.get("name") or "").strip()}
        for i in items if i.get("id")
    ]


def get_shopping_list_items(list_id, decimal_sep="."):
    """Returns (lines, skipped_checked): ready-to-use item strings for
    the ReceiptPi shopping list, in the order Mealie returned them.

    - Checked items stay in Mealie's listItems with checked: true
      (verified) - they're skipped here, not in Mealie.
    - No sorting by "position": it's not unique (verified: several items
      share the same value after checking/adding), so the API's own
      array order is the only stable order available.
    - Read-only: nothing is ever written back to Mealie."""
    data = _get_configured(
        "/api/households/shopping/lists/" + urllib.parse.quote(str(list_id), safe=""),
    )
    if not isinstance(data, dict) or not isinstance(data.get("listItems"), list):
        raise MealieError("invalid_response", "unexpected shopping list response")
    lines = []
    skipped_checked = 0
    for item in data["listItems"]:
        if not isinstance(item, dict):
            continue
        if item.get("checked") is True:
            skipped_checked += 1
            continue
        line = format_shopping_item(item, decimal_sep)
        if line:
            lines.append(line[:MAX_ITEM_LEN])
    return lines, skipped_checked


# ---------------------------------------------------------------
# Pure formatting helpers (no I/O - unit-testable in isolation)
# ---------------------------------------------------------------

def format_quantity(quantity, decimal_sep="."):
    """250.0 -> "250", 0.5 -> "0.5" (or "0,5"), 0 / None -> "".

    0.0 means "no amount" in Mealie, not "zero": a shopping item added
    in the Mealie UI without an amount comes back with quantity 0.0
    (verified) - it must not print as "0 Hundesnacks"."""
    if type(quantity) not in (int, float) or quantity <= 0:
        return ""
    text = f"{quantity:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", decimal_sep)


def _unit_label(unit, quantity):
    """Unit display name. pluralName above 1 mirrors how Mealie's own
    display text does it ("2 Zehen", "1 ¹/₂ Cups" - verified), and the
    abbreviation only when the unit itself asks for it via
    useAbbreviation (false for every unit seen so far)."""
    if unit.get("useAbbreviation") and unit.get("abbreviation"):
        return str(unit["abbreviation"]).strip()
    if type(quantity) in (int, float) and quantity > 1 and unit.get("pluralName"):
        return str(unit["pluralName"]).strip()
    return str(unit.get("name") or "").strip()


def format_shopping_item(item, decimal_sep="."):
    """Builds one shopping list line from a Mealie listItems entry, or
    returns None if there's nothing to show.

    Deliberately NOT item["display"]: for a free-text item (food: null,
    text in "note") Mealie's display prepends a quantity nobody entered -
    "1 <note>" for an item created with just a note (verified; the API's
    default quantity is 1). Built from the structured fields instead:
    - food set:   [quantity] [unit] <food name> [(note)]
    - food null:  note text as-is"""
    note = str(item.get("note") or "").strip()
    food = item.get("food")
    name = str(food.get("name") or "").strip() if isinstance(food, dict) else ""
    if not name:
        return note or None

    quantity = item.get("quantity")
    parts = []
    amount = format_quantity(quantity, decimal_sep)
    if amount:
        parts.append(amount)
    unit = item.get("unit")
    if isinstance(unit, dict):
        label = _unit_label(unit, quantity)
        if label:
            parts.append(label)
    parts.append(name)
    line = " ".join(parts)
    # The note carries real information on a food item too (e.g.
    # "250 Gramm Käse" + note "Schweizer", verified) - kept, but set
    # apart in parentheses instead of Mealie's run-on "Käse Schweizer".
    if note:
        line += f" ({note})"
    return line


def recipe_to_printable(recipe):
    """Extracts what gets printed (and stored as the pending-reprint
    payload) from a full recipe object. Text is kept exactly as Mealie
    sent it here - the ASCII fraction mapping happens only at print
    time (see to_ascii_fractions()), so the browser preview still shows
    the original characters.

    totalTime is free text in Mealie ("45" on one recipe, "38 Minuten"
    on another - verified) - passed through unchanged, never parsed."""
    ingredients = []
    for ing in recipe.get("recipeIngredient") or []:
        if isinstance(ing, dict):
            text = str(ing.get("display") or "").strip()
            if text:
                ingredients.append(text[:MAX_ITEM_LEN])
    steps = []
    for step in recipe.get("recipeInstructions") or []:
        if isinstance(step, dict):
            text = str(step.get("text") or "").strip()
            if text:
                steps.append(text[:MAX_TEXT_LEN])
    total_time = recipe.get("totalTime")
    return {
        "slug": str(recipe.get("slug") or ""),
        # Mealie keeps trailing whitespace in names ("Granola ", verified)
        "title": str(recipe.get("name") or "").strip()[:MAX_TITLE_LEN],
        "total_time": str(total_time).strip() if total_time else "",
        "ingredients": ingredients[:MAX_ITEMS],
        "steps": steps[:MAX_ITEMS],
    }


# Verified by a real test print on 2026-09-24 (09:40, recipe "Granola",
# whose ingredients contain "¹/₂" twice - print_history job "recipes",
# status ok, output checked on paper): with this mapping, fractions
# print correctly as plain "1/2" on the TM-T88V.
#
# NOT tested: whether the printer could print the original Unicode
# characters natively through python-escpos's code page handling - that
# test print ran WITH the mapping active. Keep the mapping as the known-
# good path; only drop it after an explicit unmapped test print.
#
# Two forms exist: Mealie itself does NOT use the precomposed "½"-style
# characters in its display text but superscript digit + ASCII "/" +
# subscript digit, e.g. "¹/₂" (U+00B9, "/", U+2082) and "²/₅" (verified
# in real recipe data). The precomposed vulgar fractions are mapped too,
# since recipe step text is free text copied from anywhere.
_VULGAR_FRACTIONS = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
    "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5", "⅙": "1/6",
    "⅚": "5/6", "⅐": "1/7", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8",
    "⅞": "7/8", "⅑": "1/9", "⅒": "1/10",
}
_SUPERSCRIPT_DIGITS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789", strict=True))
_SUBSCRIPT_DIGITS = dict(zip("₀₁₂₃₄₅₆₇₈₉", "0123456789", strict=True))
_FRACTION_SLASH = "⁄"  # U+2044, the "proper" typographic fraction slash


def to_ascii_fractions(text):
    """"1 ¹/₂ Cups" -> "1 1/2 Cups", "1½ TL" -> "1 1/2 TL".

    A space is inserted when a fraction directly follows a plain digit,
    otherwise "1½" would turn into "11/2" and read as eleven halves.
    Side effect: a superscript digit outside a fraction (e.g. "m²")
    becomes a plain digit ("m2") - acceptable for a receipt."""
    out = []
    prev_plain_digit = False  # was the previous output char an original ASCII digit?
    in_superscript = False
    for ch in text:
        if ch in _VULGAR_FRACTIONS:
            if prev_plain_digit:
                out.append(" ")
            out.append(_VULGAR_FRACTIONS[ch])
            prev_plain_digit = False
            in_superscript = False
        elif ch in _SUPERSCRIPT_DIGITS:
            if prev_plain_digit and not in_superscript:
                out.append(" ")
            out.append(_SUPERSCRIPT_DIGITS[ch])
            prev_plain_digit = False
            in_superscript = True
        elif ch in _SUBSCRIPT_DIGITS:
            out.append(_SUBSCRIPT_DIGITS[ch])
            prev_plain_digit = False
            in_superscript = False
        elif ch == _FRACTION_SLASH:
            out.append("/")
            prev_plain_digit = False
            in_superscript = False
        else:
            out.append(ch)
            prev_plain_digit = ch.isascii() and ch.isdigit()
            in_superscript = False
    return "".join(out)
