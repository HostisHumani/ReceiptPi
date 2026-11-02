"""
Login enforcement for the whole app, registered in app.py as the first
before_request/after_request hook.

As long as no admin account exists, nothing here changes a response:
the device behaves as it did without logins. Once one exists, each
request is classified by the decorators on its view function (see
security.py), never by its URL - the JSON settings API and the
settings pages share /settings/, and a URL prefix rule would have to
be kept in sync with every new route by hand:
  - "public" (security.public, plus Flask's own static route): open.
  - "api_token" (security.require_api_token): open here; the decorator
    itself checks the X-Api-Token header and never looks at the
    session cookie.
  - anything else, including routes added later without either
    decorator and URLs that match no route: needs a valid session.

The session cookie (COOKIE_NAME) is separate from Flask's own signed
session cookie, which keeps holding only the CSRF token. It carries a
random token whose hash identifies the row in auth.db (auth_store.py),
so ending a session server-side takes effect immediately.
"""
from urllib.parse import urlsplit

import config
from flask import current_app, g, jsonify, redirect, request, url_for

import auth_store
import i18n
from security import ACCESS_API_TOKEN, ACCESS_ATTR, ACCESS_PUBLIC

COOKIE_NAME = "receiptpi_session"

# Flask registers its static file route itself, so it can't carry the
# decorator.
_PUBLIC_ENDPOINTS = {"static"}

_MAX_NEXT_LEN = 2000


def cookie_secure():
    return bool(getattr(config, "LOGIN_COOKIE_SECURE", False))


def access_class(endpoint):
    """"public", "api_token" or None (login required) for an endpoint;
    None also for a request that matched no route."""
    if endpoint is None:
        return None
    if endpoint in _PUBLIC_ENDPOINTS:
        return ACCESS_PUBLIC
    view = current_app.view_functions.get(endpoint)
    return getattr(view, ACCESS_ATTR, None)


def load_session():
    """Validates the request's session cookie once per request and stores
    the result in g (auth_user, auth_session). Returns the session dict
    or None."""
    if "auth_session" in g:
        return g.auth_session
    session = auth_store.validate_session(request.cookies.get(COOKIE_NAME))
    g.auth_session = session
    g.auth_user = session["user"] if session else None
    return session


def safe_next(value):
    """The redirect target after a login: only a path on this site.
    Anything else - an absolute URL, a scheme-relative "//host", a
    backslash (browsers treat "/\\host" like "//host"), control
    characters (stripped by browsers, so "/\\t/host" becomes "//host") -
    falls back to "/". The auth pages themselves are no useful target."""
    if not isinstance(value, str) or not value or len(value) > _MAX_NEXT_LEN:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    if parts.path in ("/login", "/logout", "/setup"):
        return "/"
    return value


def _wants_html_page():
    """A browser navigating to a page, as opposed to fetch(), an <img>
    or a script: navigations send Sec-Fetch-Mode: navigate (current
    browsers) and always name text/html explicitly in Accept, while
    fetch() sends */* by default. A wildcard alone doesn't count."""
    if request.headers.get("Sec-Fetch-Mode") == "navigate":
        return True
    return any(mime == "text/html" and quality > 0 for mime, quality in request.accept_mimetypes)


def _referer_next():
    """The login target for a form sent without a valid session: the
    page the form was on, taken from the Referer if it points at this
    host, otherwise "/". Never the URL the form was posted to - after
    the login the browser opens the target with GET, and the form
    submission itself is dropped, not repeated."""
    try:
        parts = urlsplit(request.headers.get("Referer", ""))
    except ValueError:
        return "/"
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != request.host.lower():
        return "/"
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return safe_next(target)


def login_required_response():
    if request.method in ("GET", "HEAD") and _wants_html_page():
        target = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.login", next=target))
    # A form submitted from a page whose session has ended: 303 makes the
    # browser follow with GET. A request with X-Api-Token comes from a
    # script (a form can't set that header) and gets the JSON 401 like
    # fetch() does.
    if _wants_html_page() and "X-Api-Token" not in request.headers:
        return redirect(url_for("auth.login", next=_referer_next()), code=303)
    return jsonify({"status": "error", "error": "login_required",
                    "detail": i18n.tr("auth.error.login_required")}), 401


def enforce_login():
    """before_request hook, see the module docstring."""
    if not auth_store.admin_exists():
        return None
    if access_class(request.endpoint) in (ACCESS_PUBLIC, ACCESS_API_TOKEN):
        return None
    if load_session():
        return None
    return login_required_response()


def set_session_cookie(response, token, remember, expires):
    """remember: a persistent cookie that lasts as long as the session
    row (90 days, renewed with it). Otherwise a browser-session cookie;
    the 24 h inactivity limit for it is enforced on the server side."""
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=(expires - int(auth_store._now())) if remember else None,
        expires=expires if remember else None,
        path="/", secure=cookie_secure(), httponly=True, samesite="Lax",
    )


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/", secure=cookie_secure(), httponly=True, samesite="Lax")


def after_request(response):
    """Re-sends a remembered session's cookie whenever validate_session()
    pushed its expiry forward, so cookie and session row run out
    together. A logged-in HTML page holds personal content (lists,
    drafts, settings) and must not be stored by the browser or any
    cache in between."""
    session = g.get("auth_session")
    if not session:
        return response
    if session.get("refreshed") and session.get("remember") and not g.get("auth_cookie_handled"):
        set_session_cookie(response, request.cookies.get(COOKIE_NAME), True, session["expires"])
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response
