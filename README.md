<p align="center">
  <img src="assets/logo-header.png" alt="ReceiptPi Logo" width="700">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/Status-Alpha-orange.svg" alt="Status">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Raspberry%20Pi-C51A4A?logo=raspberrypi&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/github/last-commit/HostisHumani/ReceiptPi?label=Last%20Commit" alt="Last Commit">
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#roadmap">Roadmap</a> •
  <a href="../../issues">Issues</a>
</p>

# ReceiptPi

*[Deutsche Version / German version](README.de.md)*

Flask server for an ESC/POS thermal receipt printer (tested with an Epson
TM-T88V) on a Raspberry Pi Zero (2) W. Mobile web UI (German/English) for
printing shopping lists, status messages, weather reports, images and
system reports - each print type optionally prefixed with a small logo -
plus a generic automation webhook (compatible with Home Assistant,
Node-RED, n8n, or any tool that can do an HTTP POST) and other automation
triggers (GitHub stars, Fritz!Box guest network, Zabbix webhooks for
backup failures).

<p align="center">
  <a href="assets/home.png">
    <img src="assets/home.png" alt="ReceiptPi Web Interface" height="320">
  </a>
</p>

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# Fill config.py with real values (Home Assistant token, Netatmo entities, Fritz!Box credentials, ...)

python3 app.py
```

The server then runs on port 5000, web UI at `http://<pi-hostname>:5000`.
`python3 app.py` uses Flask's development server - for production the
service runs via Gunicorn with `gunicorn.conf.py` and exactly 1 worker, which is required because the print queue is process-local.

Most settings (quiet-hour rules, weather locations, GitHub-watched repos,
SSH targets for the system report, per-module logos, UI language) are
configured entirely through the web UI's Settings page - no need to touch
`config.py` for those. Only bootstrap-level values (secret key, API token,
printer USB IDs, real credentials such as the Home Assistant token or
Fritz!Box password) stay in `config.py`, since ReceiptPi currently has no
login/auth for the web UI itself.

## Structure

```
receiptpi/
├── app.py                 Create the Flask app, register blueprints
├── gunicorn.conf.py       on_starting hook for the boot greeting
├── print_queue.py         central print queue + print-rule checks
├── printer.py             printer hardware access (USB)
├── security.py            CSRF protection, API token protection, JSON parsing
├── settings_store.py      central settings (JSON in STATE_DIR, not in the project folder)
├── history_store.py       SQLite print history (STATE_DIR), auto-pruned after 180 days
├── logos.py                per-print-type logo resolution, upload validation, seeding
├── i18n.py                 minimal translation lookup (JSON files, no Flask-Babel)
├── config.example.py       template for config.py (fill in locally)
├── modules/
│   ├── shopping/            shopping list
│   ├── message/               status messages (free title + text)
│   ├── images/                print images
│   ├── wifi/                  guest wifi slip (text + QR code)
│   ├── weather/                weather report (DWD + Netatmo)
│   ├── system/                 system report (Proxmox/PBS/piNAS via SSH)
│   ├── automation/            generic automation webhook
│   ├── history/                print history dashboard
│   └── settings/                settings pages (web UI) + settings API
├── watchers/
│   ├── github_star_watch.py     cron: prints on a new GitHub star
│   └── fritzbox_wifi_watch.py    cron: prints a wifi slip once the guest network is enabled
├── assets/example-logos/    bundled starter logo set (outline icons)
└── templates/                shared layout and module pages
    ├── base.html
    ├── home.html
    ├── shopping.html
    ├── message.html
    ├── images.html
    ├── wifi.html
    ├── weather.html
    ├── system.html
    ├── history.html
    └── settings_*.html        settings overview + one sub-page per area
```

Each feature is implemented as its own Flask blueprint. Unhandled exceptions are isolated to the current request; modules share the same process, print queue and settings store.

## Settings pages

`/settings` is an overview page (tile grid) linking to a dedicated sub-page per area, instead of one long form:

- `/settings/language` - UI language (German/English)
- `/settings/print-rules` - quiet-hour rules (multiple independent rules, each with its own weekdays and time window) + rate limiting + duplicate suppression
- `/settings/weather` - weather locations
- `/settings/system-report` - SSH targets for the system report (Proxmox/piNAS/PBS)
- `/settings/github-watch` - watched GitHub repositories
- `/settings/logos` - global logo toggle, default logo, and a per-print-type toggle/upload/preview (falls back to the default logo if no custom one is set)
- `/settings/design` - UI color theme (5 built-in palettes: Forrest, Dark Lime, Frost, Butter Bean, White Purple)

## Endpoints

All `/print/*` and `/settings/*` endpoints require the `X-Api-Token: <value>`
header once `API_TOKEN` is set in `config.py` (empty = no protection).
Every print job also passes through the central print rules: quiet hours, rate limiting and duplicate suppression.
A blocked job responds with `429`.

**Printing**
- `POST /print/message` - `{ "title": "...", "text": "..." }`
- `POST /print/list` - `{ "title": "...", "items": ["..."] }`
- `POST /print/image` - multipart upload (max. 12000px per side, 12MB)
- `POST /print/wifi` - `{ "ssid": "...", "password": "...", "auth_type": "WPA" }`
- `POST /print/weather` - optional `{ "location": "Berlin" }`, otherwise the default location
- `POST /print/system` - no body needed
- `POST /print/automation` - `{ "title": "optional", "text": "..." }` - generic automation webhook (compatible with Home Assistant, Node-RED, n8n, or any tool that can do an HTTP POST)
- `GET /health` - check printer reachability (no token needed, but runs through the same queue; excluded from print history)

**Settings API**
- `GET /settings/api` - current print rules + weather locations (JSON)
- `POST /settings/print_rules` - change rate limit/duplicate window
- `GET|POST /settings/quiet_hours/rules`, `POST /settings/quiet_hours/rules/<id>/toggle`, `DELETE /settings/quiet_hours/rules/<id>`
- `GET|POST /settings/weather/locations`, `DELETE /settings/weather/locations/<name>`
- `GET|POST /settings/system_report` - SSH targets for the system report
- `GET|POST /settings/github_watch/repos`, `DELETE /settings/github_watch/repos/<owner>/<repo>`
- `GET|POST /settings/logos/config` - global/per-module logo toggles
- `POST /settings/logos/upload/<slot>`, `DELETE /settings/logos/upload/<slot>` - logo image upload/removal (base64), `slot` is `default` or a module key

## After installation

```bash
pip freeze > requirements.lock.txt
```
Freezes the actually installed versions - `requirements.txt` itself is
deliberately left unpinned, `requirements.lock.txt` is only a reference in
case an update ever breaks something.

## Current modules

- Shopping lists
- Free-form messages
- Image uploads and API image printing
- Guest Wi-Fi credentials and QR codes
- Weather reports (DWD + optional Netatmo)
- System reports (Proxmox/piNAS/PBS via SSH)
- Print history dashboard (stats + paginated log, SQLite-backed)
- Optional per-print-type logos with a global default fallback
- Web-based settings, split into per-area sub-pages
- 5 built-in UI color themes (switchable in Settings, no restart needed)
- German/English UI, including receipt content itself (not just the UI chrome)

## Roadmap

Next up: a font-size switcher (small/medium/large) for both the UI and
receipts, NFC-tag-triggered printing (just a URL in the tag, no app
needed), and a recipe module (Tandoor/Mealie) with credentials
configurable via the web UI. The roadmap may change as
ReceiptPi is tested on more hardware.

## Contributing

Issues and pull requests are welcome. New modules should use Flask Blueprints, submit print jobs through the central queue and avoid direct writes to the printer or settings files.

## Trademark Notice

Raspberry Pi is a trademark of the Raspberry Pi Foundation. This project is
not affiliated with, endorsed by, or sponsored by the Raspberry Pi
Foundation.

Epson and TM-T88V are trademarks of Seiko Epson Corporation. This project is
not affiliated with, endorsed by, or sponsored by Epson.

ReceiptPi is an independent, community-built project designed to run on
Raspberry Pi hardware and to be compatible with Epson ESC/POS thermal
printers.

## License

MIT - see [LICENSE](LICENSE).
