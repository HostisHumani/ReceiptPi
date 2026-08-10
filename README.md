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
printing shopping lists, status messages, weather reports, images, system
reports, and offline pen-and-paper games (Sudoku, a dice score sheet,
Tic-Tac-Toe boards) - most print types optionally prefixed with a small
logo - plus a generic automation webhook (compatible with Home Assistant,
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
├── module_catalog.py       registry of the toggleable home-page modules (key, icon, URL)
├── text_style.py           Easy-Read text-size scaling, shared by print jobs and the web UI
├── themes.py                registry of the selectable web UI color themes
├── config.example.py       template for config.py (fill in locally)
├── modules/
│   ├── shopping/            shopping list
│   ├── message/               status messages (free title + text)
│   ├── images/                print images
│   ├── wifi/                  guest wifi slip (text + QR code)
│   ├── weather/                weather report (DWD + Netatmo)
│   ├── system/                 system report (Proxmox/PBS/piNAS via SSH)
│   ├── games/                  offline games (Sudoku, dice score sheet, Tic-Tac-Toe)
│   ├── automation/            generic automation webhook
│   ├── history/                print history dashboard
│   └── settings/                settings pages (web UI) + settings API
├── watchers/
│   ├── github_star_watch.py     cron: prints on a new GitHub star
│   ├── fritzbox_wifi_watch.py    cron: prints a wifi slip once the guest network is enabled
│   └── storm_warning_watch.py    cron: prints on a new active storm warning
├── assets/example-logos/    bundled starter logo set (outline icons)
├── static/
│   ├── style.css             design system (spacing/colors/themes, card/tile/icon styles)
│   └── icons/                 local Lucide SVG icon set (see LICENSE in that folder)
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
    ├── games_*.html            games overview + one page per game
    └── settings_*.html        settings overview + one sub-page per area
```

Each feature is implemented as its own Flask blueprint. Unhandled exceptions are isolated to the current request; modules share the same process, print queue and settings store.

## Web UI

The web UI is mobile-first (a single narrow column, no separate desktop
layout) and built as a small card/tile-based design system rather than
plain forms:

- **Home/dashboard** (`/`) - a status strip (printer reachability, polled
  live via `/health` every 60 seconds; how many of the toggleable modules
  are currently active; total print count from the history store),
  followed by a tile grid linking to every currently enabled module.
- **Navigation** - a topbar wordmark, a small printer-status dot, and a
  hamburger menu (History, Settings).
- **Themes** - 5 selectable color themes (Forrest [default], Dark Lime,
  Frost, Butter Bean, White Purple), picked under `/settings/design` and
  applied via a `data-theme` attribute plus CSS custom properties.
- **Icons** - a local set of Lucide SVG icons under `static/icons/` (no
  external icon font or CDN), rendered via CSS `mask-image` so every icon
  automatically follows the active theme's accent color instead of being
  hardcoded to one color. See [Third-Party Licenses](#third-party-licenses).
- **Easy-Read** - one text-size switch scales both the web UI and the
  receipt output, see [Easy-Read](#easy-read-large-text) below.

There is currently no dedicated app logo or favicon - only the topbar
wordmark (a printer icon) and the documentation banner image at the top
of this file.

## Settings pages

`/settings` is an overview page (tile grid, grouped into General /
Printing / Integrations / System) linking to a dedicated sub-page per
area, instead of one long form:

- `/settings/language` - UI language (German/English)
- `/settings/design` - web UI color theme
- `/settings/modules` - enable/disable individual home-page modules, see [Module toggles](#module-toggles)
- `/settings/print-rules` - quiet-hour rules (multiple independent rules, each with its own weekdays and time window) + rate limiting + duplicate suppression + the Easy-Read text-size switch
- `/settings/logos` - global logo toggle, default logo, and a per-print-type toggle/upload/preview (falls back to the default logo if no custom one is set)
- `/settings/weather` - weather report provider (DWD or Open-Meteo), weather locations, and an independent storm-warning provider (DWD, MeteoAlarm, or NWS) with its own enable toggle and an optional "ignore quiet hours" override
- `/settings/github-watch` - watched GitHub repositories
- `/settings/system-report` - SSH targets for the system report (Proxmox/piNAS/PBS)

## Endpoints

All `/print/*` and `/settings/*` endpoints require the `X-Api-Token: <value>`
header once `API_TOKEN` is set in `config.py` (empty = no protection).
Every print job also passes through the central print rules: quiet hours, rate limiting and duplicate suppression.
A blocked job responds with `429`.

**Printing**
- `POST /print/message` - `{ "title": "...", "text": "..." }`
- `POST /print/list` - `{ "title": "...", "items": ["..."] }`
- `POST /print/image` - multipart upload (max. 6000px per side, 12MB)
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
Freezes the actually installed versions - `requirements.txt` itself
deliberately has no version pins (no `==x.y.z`), `requirements.lock.txt`
is only a reference in case an update ever breaks something.

## Features

- Shopping lists
- Free-form messages
- Image uploads and API image printing
- Guest Wi-Fi credentials and QR codes
- Weather reports (DWD or Open-Meteo, selectable, + optional Netatmo), with an optional storm-warning watcher (DWD, MeteoAlarm, or NWS, prints only when a warning is actually active)
- System reports (Proxmox/piNAS/PBS via SSH)
- Offline games (Sudoku, a dice score sheet, Tic-Tac-Toe), see [Games](#games) below
- Print history dashboard (stats + paginated log, SQLite-backed)
- Optional per-print-type logos with a global default fallback
- Individually toggleable home-page modules, see [Module toggles](#module-toggles) below
- Web-based settings, split into per-area sub-pages and grouped by topic
- 5 selectable web UI color themes
- Easy-Read: one switch for larger text on both the receipt and the web UI, see [Easy-Read](#easy-read-large-text) below
- German/English UI, including receipt content itself (not just the UI chrome)
- USB-connected ESC/POS printer (python-escpos + pyusb, Epson TM-T88V profile)

### Games

Three offline games, printed through the same central print queue as
every other module. Unlike most modules they are UI-only - there is no
`/print/games/*` JSON API and they are not wired into the automation
webhook - and they have no logo slot.

- **Sudoku** (`/games/sudoku`) - a locally generated 9x9 puzzle with three
  difficulty levels (easy/medium/hard), with an option to also print the
  solution as a second receipt.
- **Dice score sheet** (`/games/dice`) - a generic five-dice score pad
  (upper section ones-sixes with subtotal/bonus, lower section
  three-/four-of-a-kind, full house, small/large straight, five-alike,
  chance, plus upper/lower/grand totals). One receipt per player, 1-12
  players per print.
- **Tic-Tac-Toe** (`/games/tictactoe`) - printable blank 3x3 boards to
  fill in by hand, 3/6/9 rounds per print.

### Easy-Read (large text)

A single switch under `/settings/print-rules` ("text size": Normal /
Large) scales both the receipt output and the web UI from the same
setting:

- **Receipt**: headings print at double width/double height, body text at
  double width (which halves the usable line width, so body text wraps
  more often); the footer timestamp and section separators always stay
  at normal size.
- **Web UI**: the base font size grows from 16px to 19px, and the home
  status strip and module tile grid drop from a multi-column to a
  single-column layout so nothing gets squeezed into a cramped column.

It is a plain text-size switch, not a full accessibility overhaul - there
is no separate screen-reader markup or contrast mode tied to it.

### Module toggles

`/settings/modules` lets each of the 7 catalog modules (shopping,
message, weather, images, wifi, system, games) be switched on or off
individually:

- Disabling a module removes its tile from the home page **and** blocks
  all of its routes - web UI pages, `/ui/*` form posts, and `/print/*`
  API endpoints alike - with a 404, not just hiding the tile.
- The home page's status strip shows how many of these 7 modules are
  currently active.
- History, the Settings pages themselves, and the generic automation
  webhook are outside this toggle system and stay reachable regardless.
- Two of the three watchers respect this: `fritzbox_wifi_watch.py` and
  `storm_warning_watch.py` check the `wifi`/`weather` module state and
  skip cleanly if disabled. `github_star_watch.py` does not check it and
  will simply fail its print call if the `message` module is disabled.

## Roadmap

Next up: NFC-tag-triggered printing (just a URL in the tag, no app
needed), and a recipe module (Tandoor/Mealie) with credentials
configurable via the web UI. The roadmap may change as ReceiptPi is
tested on more hardware.

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

## Third-Party Licenses

The web UI's icons are from [Lucide](https://lucide.dev) (`static/icons/`),
used under the ISC License - see [static/icons/LICENSE](static/icons/LICENSE)
for the full text.

## License

MIT - see [LICENSE](LICENSE).
