"""
The setup page, served on the setup network only (10.42.0.1, port 80).

Phones check for a login page right after joining a network: every
address resolves to this device while the setup network is up (dnsmasq
setting in the image), and every request that isn't for the page itself
is redirected to it, so the phone opens the page by itself.

The page lists the networks found before the setup network went up,
takes the name of another network by hand, and the password. A form
token issued with the page ties a submission to a page this server
handed out. The page follows the phone's language, German or English;
its texts live in translations/<lang>.json next to this file.
"""
import html
import http.server
import json
import os
import secrets
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
LANGS = ("de", "en")
MAX_BODY = 4096
MAX_SSID_BYTES = 32


def load_texts():
    texts = {}
    for lang in LANGS:
        with open(os.path.join(HERE, "translations", f"{lang}.json"), encoding="utf-8") as f:
            texts[lang] = json.load(f)
    return texts


def pick_lang(accept_language):
    """German if the phone's first language is German, else English."""
    first = (accept_language or "").split(",")[0].strip().lower()
    return "de" if first.startswith("de") else "en"


def _valid_password(password):
    """WPA: 8 to 63 printable ASCII characters, or 64 hex digits."""
    if len(password) == 64:
        return all(c in "0123456789abcdefABCDEF" for c in password)
    return 8 <= len(password) <= 63 and all(" " <= c <= "~" for c in password)


def validate(form, networks):
    """Returns ((ssid, password, hidden), None) or (None, error key).
    A name typed by hand wins over the selection; it counts as a hidden
    network unless it is in the list."""
    known = {n["ssid"]: n for n in networks}
    manual = form.get("manual", "").strip()
    ssid = manual or form.get("network", "")
    password = form.get("password", "")
    if not ssid or (not manual and ssid not in known):
        return None, "error.no_network"
    if len(ssid.encode("utf-8")) > MAX_SSID_BYTES:
        return None, "error.name_long"
    # An empty password only for a network listed as open, or one typed
    # by hand (which may be open, the list can't tell).
    open_network = ssid in known and not known[ssid]["secured"]
    if (password or not (open_network or manual)) and not _valid_password(password):
        return None, "error.password"
    return (ssid, password, bool(manual) and ssid not in known), None


class Portal:
    """Serves the page in a thread until stop(). on_submit(ssid, password,
    hidden) is called once a valid form arrives."""

    def __init__(self, address, port, networks, on_submit, texts=None):
        self.address = address
        self.port = port
        self.networks = list(networks)
        self.on_submit = on_submit
        self.texts = texts or load_texts()
        self.token = secrets.token_urlsafe(16)
        with open(os.path.join(HERE, "page.html"), encoding="utf-8") as f:
            self.template = f.read()
        self._server = None
        self._thread = None

    def start(self):
        portal = self

        class Handler(_Handler):
            pass

        Handler.portal = portal
        self._server = http.server.ThreadingHTTPServer((self.address, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self):
        return f"http://{self.address}/" if self.port == 80 else f"http://{self.address}:{self.port}/"

    def t(self, lang, key, **kwargs):
        value = self.texts.get(lang, {}).get(key) or self.texts["en"].get(key, key)
        return value.format(**{k: html.escape(v) for k, v in kwargs.items()})

    def render(self, lang, title_key, body):
        return (self.template
                .replace("{lang}", lang)
                .replace("{title}", html.escape(self.t(lang, title_key)))
                .replace("{body}", body))

    def form_page(self, lang, error_key=None, selected=None):
        e = html.escape
        parts = [f"<h1>{e(self.t(lang, 'page.title'))}</h1>",
                 f"<p>{e(self.t(lang, 'page.intro'))}</p>"]
        if error_key:
            parts.append(f'<p class="error" role="alert">{e(self.t(lang, error_key))}</p>')
        parts.append('<form method="post" action="/connect">')
        parts.append(f'<input type="hidden" name="token" value="{e(self.token)}">')
        parts.append(f"<fieldset><legend>{e(self.t(lang, 'page.networks'))}</legend>")
        if self.networks:
            selected = selected if selected in {n["ssid"] for n in self.networks} else \
                self.networks[0]["ssid"]
            for n in self.networks:
                checked = " checked" if n["ssid"] == selected else ""
                badge = "" if n["secured"] else f' <span class="badge">{e(self.t(lang, "page.open"))}</span>'
                parts.append(f'<label class="net"><input type="radio" name="network" '
                             f'value="{e(n["ssid"])}"{checked}> {e(n["ssid"])}{badge}</label>')
        else:
            parts.append(f"<p>{e(self.t(lang, 'page.none_found'))}</p>")
        parts.append("</fieldset>")
        parts.append(f'<label>{e(self.t(lang, "page.manual"))}'
                     '<input type="text" name="manual" maxlength="32" autocomplete="off" '
                     'autocapitalize="none" spellcheck="false"></label>')
        parts.append(f'<label>{e(self.t(lang, "page.password"))}'
                     '<input type="password" name="password" maxlength="64" '
                     'autocomplete="current-password"></label>')
        parts.append(f'<p class="hint">{e(self.t(lang, "page.password_hint"))}</p>')
        parts.append(f'<button type="submit">{e(self.t(lang, "page.submit"))}</button>')
        parts.append("</form>")
        return self.render(lang, "page.title", "\n".join(parts))

    def connecting_page(self, lang, ssid):
        e = html.escape
        body = (f"<h1>{e(self.t(lang, 'page.connecting_title'))}</h1>"
                f"<p>{self.t(lang, 'page.connecting_text', network=ssid)}</p>")
        return self.render(lang, "page.connecting_title", body)


class _Handler(http.server.BaseHTTPRequestHandler):
    portal = None
    server_version = "ReceiptPi"
    sys_version = ""

    def log_message(self, format, *args):
        pass  # requests carry the setup password; nothing about them is logged

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self):
        self._send(302, headers={"Location": self.portal.url})

    def _page(self, status, text):
        self._send(status, text.encode("utf-8"), {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                       "form-action 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })

    def _lang(self):
        return pick_lang(self.headers.get("Accept-Language"))

    def _own_host(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host == self.portal.address

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path != "/" or not self._own_host():
            self._redirect()
            return
        self._page(200, self.portal.form_page(self._lang()))

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != "/connect" or not self._own_host():
            self._redirect()
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._send(413)
            return
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        lang = self._lang()
        if not secrets.compare_digest(form.get("token", "").encode(), self.portal.token.encode()):
            self._page(400, self.portal.form_page(lang, "error.expired"))
            return
        result, error = validate(form, self.portal.networks)
        if error:
            self._page(400, self.portal.form_page(lang, error, selected=form.get("network")))
            return
        ssid, password, hidden = result
        self._page(200, self.portal.connecting_page(lang, ssid))
        self.portal.on_submit(ssid, password, hidden)
