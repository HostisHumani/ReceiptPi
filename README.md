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
TM-T88V) on a Raspberry Pi Zero (2) W. Mobile web UI for printing shopping
lists, status messages, weather reports and system reports, plus optional
automation triggers (GitHub stars, Fritz!Box guest network, Zabbix webhooks
for backup failures).

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

## Structure

```
receiptpi/
├── app.py               Create the Flask app, register blueprints
├── gunicorn.conf.py      on_starting hook for the boot greeting
├── print_queue.py        central print queue + print-rule checks
├── printer.py            printer hardware access (USB)
├── security.py           CSRF protection, API token protection, JSON parsing
├── settings_store.py      central settings (JSON in STATE_DIR)
├── config.example.py      template for config.py (fill in locally)
├── modules/
│   ├── shopping/          shopping list
│   ├── message/            status messages (free title + text)
│   ├── images/             print images
│   ├── wifi/               guest wifi slip (text + QR code)
│   ├── weather/            weather report (DWD + Netatmo)
│   ├── system/              system report (Proxmox/PBS/piNAS via SSH)
│   └── settings/            settings page (web UI) + settings API (print rules, weather locations)
├── watchers/
│   ├── github_star_watch.py     cron: prints on a new GitHub star
│   └── fritzbox_wifi_watch.py    cron: prints a wifi slip once the guest network is enabled
└── templates/             shared layout and module pages
    ├── base.html
    ├── home.html
    ├── shopping.html
    ├── message.html
    ├── images.html
    ├── wifi.html
    ├── weather.html
    ├── system.html
    └── settings.html
```

Each feature is implemented as its own Flask blueprint. Unhandled exceptions are isolated to the current request; modules share the same process, print queue and settings store.

## Endpoints

All `/print/*` and `/settings/*` endpoints require the `X-Api-Token: <value>`
header once `API_TOKEN` is set in `config.py` (empty = no protection). 
Every print job also passes through the central print rules: quiet hours, rate limiting and duplicate suppression. 
A blocked job responds with `429`.

- `POST /print/message` - `{ "title": "...", "text": "..." }`
- `POST /print/list` - `{ "title": "...", "items": ["..."] }`
- `POST /print/image` - `{ "image_base64": "..." }` (max. 12000px per side, 12MB)
- `POST /print/wifi` - `{ "ssid": "...", "password": "...", "auth_type": "WPA" }`
- `POST /print/weather` - optional `{ "location": "Berlin" }`, otherwise the default location
- `POST /print/system` - no body needed
- `GET /settings/api` - current print rules + weather locations (JSON)
- `POST /settings/print_rules` - change quiet hours/rate limit/duplicate window
- `GET|POST /settings/weather/locations`, `DELETE /settings/weather/locations/<name>`
- `GET /health` - check printer reachability (no token needed, but runs through the same queue)

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
- Weather reports
- System reports
- Web-based settings

## Roadmap

Planned work includes the multilingual UI, PWA packaging, additional modules and improved installation automation. The roadmap may change as ReceiptPi is tested on more hardware.

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
