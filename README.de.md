<p align="center">
  <img src="assets/logo-header.png" alt="ReceiptPi Logo" width="700">
</p>

# ReceiptPi

*[English version](README.md)*

Flask-Server für einen ESC/POS-Thermodrucker (getestet mit Epson TM-T88V) an
einem Raspberry Pi Zero (2) W. Mobile Web-UI zum Drucken von Einkaufszetteln,
Statusmeldungen, Wetterberichten und Systemberichten, plus optionale
Automations-Trigger (GitHub-Stars, Fritz!Box-Gästenetz, Zabbix-Webhooks für
PBS-Fehler).

Ausführliches Setup (inkl. Migration von einer bestehenden
"Bondrucker"/"Thermodrucker"-Installation): siehe [ANLEITUNG.md](ANLEITUNG.md).

## Schnellstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# config.py mit echten Werten füllen (HA-Token, Netatmo-Entities, Fritz!Box-Zugang, ...)

python3 app.py
```

Server läuft dann auf Port 5000, Web-UI unter `http://<pi-hostname>:5000`.
`python3 app.py` nutzt den Flask-Entwicklungsserver – für den Dauerbetrieb
läuft der Service stattdessen über Gunicorn mit `gunicorn.conf.py` und genau
1 Worker (siehe ANLEITUNG.md Schritt 9, wichtig wegen der prozessinternen
Print-Queue).

## Struktur

```
receiptpi/
├── app.py               Flask-App erzeugen, Blueprints registrieren
├── gunicorn.conf.py      on_starting-Hook für den Boot-Gruß
├── print_queue.py        zentrale Druck-Queue + Druckregeln-Prüfung
├── printer.py            Drucker-Hardware-Zugriff (USB)
├── security.py           CSRF-Schutz, API-Token-Schutz, JSON-Parsing
├── settings_store.py      zentrale Settings (JSON in STATE_DIR)
├── config.example.py      Vorlage für config.py (lokal ausfüllen)
├── modules/
│   ├── shopping/          Einkaufszettel
│   ├── message/            Statusmeldungen (freier Titel + Text)
│   ├── images/             Bilder drucken
│   ├── wifi/               Gäste-WLAN-Zettel (Text + QR-Code)
│   ├── weather/            Wetterbericht (DWD + Netatmo)
│   ├── system/              Systembericht (Proxmox/PBS/piNAS via SSH)
│   └── settings/            Settings-Seite (Web-UI) + Settings-API (Druckregeln, Wetter-Standorte)
├── watchers/
│   ├── github_star_watch.py     Cron: druckt bei neuem GitHub-Star
│   └── fritzbox_wifi_watch.py    Cron: druckt WLAN-Zettel bei aktiviertem Gästenetz
└── templates/             gemeinsames Layout und Modul-Seiten
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

Jede Funktion ist als eigener Flask-Blueprint umgesetzt. Nicht abgefangene Ausnahmen betreffen nur den jeweiligen Request; die Module teilen sich weiterhin Prozess, Druck-Queue und Settings-Store.

## Endpunkte

Alle `/print/*`- und `/settings/*`-Endpunkte verlangen den Header
`X-Api-Token: <wert>`, sobald `API_TOKEN` in `config.py` gesetzt ist (leer =
kein Schutz, siehe ANLEITUNG.md Schritt 6). Vor jedem Druckauftrag greifen
zusätzlich die zentralen Druckregeln (Ruhezeiten, Rate-Limit,
Duplikat-Sperre – siehe ANLEITUNG.md Schritt 16); ein blockierter Auftrag
antwortet mit `429`.

- `POST /print/message` – `{ "title": "...", "text": "..." }`
- `POST /print/list` – `{ "title": "...", "items": ["..."] }`
- `POST /print/image` – `{ "image_base64": "..." }` (max. 12000px pro Seite, 12MB)
- `POST /print/wifi` – `{ "ssid": "...", "password": "...", "auth_type": "WPA" }`
- `POST /print/weather` – optional `{ "location": "Berlin" }`, sonst Standard-Standort
- `POST /print/system` – kein Body nötig
- `GET /settings/api` – aktuelle Druckregeln + Wetter-Standorte (JSON)
- `POST /settings/print_rules` – Ruhezeiten/Rate-Limit/Duplikat-Fenster ändern
- `GET|POST /settings/weather/locations`, `DELETE /settings/weather/locations/<name>`
- `GET /health` – Drucker-Erreichbarkeit prüfen (kein Token nötig, läuft aber über dieselbe Queue)

## Nach der Installation

```bash
pip freeze > requirements.lock.txt
```
Friert die tatsächlich installierten Versionen ein – `requirements.txt`
selbst bleibt bewusst unversioniert, `requirements.lock.txt` dient nur als
Referenz, falls ein Update mal etwas kaputt macht.

## Aktuelle Module

- Einkaufslisten
- Freie Textmeldungen
- Bild-Upload und Bilddruck per API
- Gäste-WLAN-Zugangsdaten und QR-Codes
- Wetterberichte
- Systemberichte
- Webbasierte Einstellungen

## Roadmap

Geplant sind unter anderem die mehrsprachige Oberfläche, PWA-Paketierung, zusätzliche Module und eine weiter vereinfachte Installation. Die Roadmap kann sich mit weiteren Hardwaretests ändern.

## Mitwirken

Issues und Pull Requests sind willkommen. Neue Module sollen Flask-Blueprints verwenden, Druckaufträge ausschließlich über die zentrale Queue einreichen und weder direkt auf den Drucker noch auf Settings-Dateien schreiben.

## Trademark Notice

Raspberry Pi is a trademark of the Raspberry Pi Foundation. This project is
not affiliated with, endorsed by, or sponsored by the Raspberry Pi
Foundation.

Epson and TM-T88V are trademarks of Seiko Epson Corporation. This project is
not affiliated with, endorsed by, or sponsored by Epson.

ReceiptPi is an independent, community-built project designed to run on
Raspberry Pi hardware and to be compatible with Epson ESC/POS thermal
printers.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
