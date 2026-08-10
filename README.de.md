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

*[English version](README.md)*

Flask-Server für einen ESC/POS-Thermodrucker (getestet mit Epson TM-T88V) an
einem Raspberry Pi Zero (2) W. Mobile Web-UI (Deutsch/Englisch) zum Drucken
von Listen (Einkauf, To-Do, Task-/Kanban-Karten), Statusmeldungen,
Wetterberichten, Bildern, Systemberichten sowie Offline-Spielen zum
Ausdrucken (Sudoku, Würfelblock, Tic-Tac-Toe) – die meisten Druckarten
optional mit vorangestelltem kleinen Logo – plus einem generischen
Automation-Webhook (kompatibel mit Home Assistant, Node-RED, n8n oder
jedem Tool, das einen HTTP-POST absetzen kann) und weiteren
Automations-Triggern (GitHub-Stars, Fritz!Box-Gästenetz,
Zabbix-Webhooks für PBS-Fehler).

<p align="center">
  <a href="assets/home.png">
    <img src="assets/home.png" alt="ReceiptPi Web Interface" height="320">
  </a>
</p>

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
läuft der Service stattdessen über Gunicorn mit `gunicorn.conf.py` und genau einem Worker, da die Print-Queue prozesslokal arbeitet.

Die meisten Einstellungen (Ruhezeit-Regeln, Wetter-Standorte, beobachtete
GitHub-Repos, SSH-Ziele für den Systembericht, Logos je Druckart,
UI-Sprache) werden komplett über die Settings-Seite der Web-UI verwaltet –
dafür muss `config.py` nicht angefasst werden. Nur Bootstrap-Werte
(Secret Key, API-Token, Drucker-USB-IDs, echte Zugangsdaten wie
HA-Token oder Fritz!Box-Passwort) bleiben in `config.py`, da ReceiptPi
aktuell keinen Login/Auth-Schutz für die Web-UI selbst hat.

## Struktur

```
receiptpi/
├── app.py                 Flask-App erzeugen, Blueprints registrieren
├── gunicorn.conf.py       on_starting-Hook für den Boot-Gruß
├── print_queue.py         zentrale Druck-Queue + Druckregeln-Prüfung
├── printer.py             Drucker-Hardware-Zugriff (USB)
├── security.py            CSRF-Schutz, API-Token-Schutz, JSON-Parsing
├── settings_store.py      zentrale Settings (JSON in STATE_DIR, nicht im Projektordner)
├── history_store.py       SQLite-Druckhistorie (STATE_DIR), Auto-Pruning nach 180 Tagen
├── logos.py                Logo-Auflösung je Druckart, Upload-Validierung, Seeding
├── i18n.py                 minimales Übersetzungs-Lookup (JSON-Dateien, kein Flask-Babel)
├── module_catalog.py       Registry der ein-/ausschaltbaren Home-Module (Key, Icon, URL)
├── text_style.py           Easy-Read-Textgrößen-Skalierung, gemeinsam für Druck und Web-UI
├── themes.py                Registry der wählbaren Web-UI-Farbschemata
├── config.example.py       Vorlage für config.py (lokal ausfüllen)
├── modules/
│   ├── lists/                 Einkaufslisten, To-Do-Listen, Task-/Kanban-Karten
│   ├── message/               Statusmeldungen (freier Titel + Text)
│   ├── images/                Bilder drucken
│   ├── wifi/                  Gäste-WLAN-Zettel (Text + QR-Code)
│   ├── weather/                Wetterbericht (DWD + Netatmo)
│   ├── system/                 Systembericht (Proxmox/PBS/piNAS via SSH)
│   ├── games/                  Offline-Spiele (Sudoku, Würfelblock, Tic-Tac-Toe)
│   ├── automation/            generischer Automation-Webhook
│   ├── history/                Druckhistorie-Dashboard
│   └── settings/                Settings-Seiten (Web-UI) + Settings-API
├── watchers/
│   ├── github_star_watch.py     Cron: druckt bei neuem GitHub-Star
│   ├── fritzbox_wifi_watch.py    Cron: druckt WLAN-Zettel bei aktiviertem Gästenetz
│   └── storm_warning_watch.py    Cron: druckt bei neuer aktiver Unwetterwarnung
├── assets/example-logos/    mitgelieferter Logo-Startsatz (Strichzeichnungen)
├── static/
│   ├── style.css             Design-System (Spacing/Farben/Themes, Card-/Tile-/Icon-Stile)
│   └── icons/                 lokaler Lucide-SVG-Iconsatz (siehe LICENSE in diesem Ordner)
└── templates/                gemeinsames Layout und Modul-Seiten
    ├── base.html
    ├── home.html
    ├── shopping.html
    ├── lists_*.html            Listen-Übersicht, To-Do-Seite, Task-/Kanban-Karten-Seite
    ├── message.html
    ├── images.html
    ├── wifi.html
    ├── weather.html
    ├── system.html
    ├── history.html
    ├── games_*.html            Spiele-Übersicht + eine Seite je Spiel
    └── settings_*.html        Settings-Übersicht + eine Unterseite je Bereich
```

Jede Funktion ist als eigener Flask-Blueprint umgesetzt. Nicht abgefangene Ausnahmen betreffen nur den jeweiligen Request; die Module teilen sich weiterhin Prozess, Druck-Queue und Settings-Store.

## Web-UI

Die Web-UI ist mobile-first (eine einzelne schmale Spalte, kein eigenes
Desktop-Layout) und als kleines Card-/Kachel-Design-System statt reiner
Formulare umgesetzt:

- **Home/Dashboard** (`/`) – ein Status-Streifen (Drucker-Erreichbarkeit,
  live per `/health`-Poll alle 60 Sekunden geprüft; wie viele der
  schaltbaren Module aktuell aktiv sind; Gesamt-Druckanzahl aus dem
  Verlaufs-Store), gefolgt von einem Kachel-Grid mit Links zu jedem
  aktuell aktivierten Modul.
- **Navigation** – ein Topbar-Wordmark, ein kleiner Drucker-Status-Punkt
  und ein Burger-Menü (Verlauf, Einstellungen).
- **Themes** – 5 wählbare Farbschemata (Forrest [Standard], Dark Lime,
  Frost, Butter Bean, White Purple), einstellbar unter
  `/settings/design`, angewendet über ein `data-theme`-Attribut plus
  CSS Custom Properties.
- **Icons** – ein lokaler Satz von Lucide-SVG-Icons unter `static/icons/`
  (keine externe Icon-Schriftart oder CDN), gerendert per CSS
  `mask-image`, sodass jedes Icon automatisch die Akzentfarbe des
  aktiven Themes annimmt, statt fest auf eine Farbe codiert zu sein.
  Siehe [Third-Party Licenses](#third-party-licenses).
- **Easy-Read** – ein Textgrößen-Schalter skaliert sowohl Web-UI als auch
  Bon-Ausdruck, siehe [Easy-Read](#easy-read-große-schrift) unten.

Ein eigenes App-Logo oder Favicon gibt es aktuell nicht – nur das
Topbar-Wordmark (ein Drucker-Icon) und das Banner-Bild oben in dieser
Datei.

## Settings-Seiten

`/settings` ist eine Übersichtsseite (Kachel-Grid, gruppiert in
Allgemein / Drucken / Integrationen / System), die auf eine eigene
Unterseite pro Bereich verlinkt – statt eines langen Formulars:

- `/settings/language` – UI-Sprache (Deutsch/Englisch)
- `/settings/design` – Farbschema der Web-UI
- `/settings/modules` – einzelne Home-Module ein-/ausblenden, siehe [Modul-Schalter](#modul-schalter)
- `/settings/print-rules` – Ruhezeit-Regeln (mehrere unabhängige Regeln, je mit eigenen Wochentagen und Zeitfenster) + Rate-Limit + Duplikat-Sperre + der Easy-Read-Textgrößen-Schalter
- `/settings/logos` – globaler Logo-Schalter, Standard-Logo, sowie je Druckart eigener Schalter/Upload/Vorschau (fällt auf das Standard-Logo zurück, wenn kein eigenes gesetzt ist)
- `/settings/weather` – Wetterbericht-Anbieter (DWD oder Open-Meteo), Wetter-Standorte, sowie ein unabhängiger Unwetterwarnungs-Anbieter (DWD, MeteoAlarm oder NWS) mit eigenem Aktiv-Schalter und optionalem "Ruhezeiten ignorieren"
- `/settings/github-watch` – beobachtete GitHub-Repos
- `/settings/system-report` – SSH-Ziele für den Systembericht (Proxmox/piNAS/PBS)

## Endpunkte

Alle `/print/*`- und `/settings/*`-Endpunkte verlangen den Header
`X-Api-Token: <wert>`, sobald `API_TOKEN` in `config.py` gesetzt ist (leer = kein Schutz).
Vor jedem Druckauftrag greifen zusätzlich die zentralen Druckregeln: Ruhezeiten, Rate-Limit und Duplikat-Sperre.
Ein blockierter Auftrag antwortet mit `429`.

**Drucken**
- `POST /print/message` – `{ "title": "...", "text": "..." }`
- `POST /print/list` – `{ "title": "...", "items": ["..."] }` – Einkaufsliste, unverändert seit vor dem Lists-Modul, weiterhin voll abwärtskompatibel
- `POST /print/todo` – `{ "title": "...", "items": ["..."] }` – To-Do-Liste, gleiche Form wie `/print/list`
- `POST /print/task` – `{ "title": "...", "description": "optional", "items": ["optionale Checkliste"], "priority": "low"|"medium"|"high", "due_date": "YYYY-MM-DD" }` – druckbare Task-/Kanban-Karte; nur `title` ist Pflicht
- `POST /print/image` – Multipart-Upload (max. 6000px pro Seite, 12MB)
- `POST /print/wifi` – `{ "ssid": "...", "password": "...", "auth_type": "WPA" }`
- `POST /print/weather` – optional `{ "location": "Berlin" }`, sonst Standard-Standort
- `POST /print/system` – kein Body nötig
- `POST /print/automation` – `{ "title": "optional", "text": "..." }` – generischer Automation-Webhook (kompatibel mit Home Assistant, Node-RED, n8n oder jedem Tool, das HTTP-POST beherrscht)
- `GET /health` – Drucker-Erreichbarkeit prüfen (kein Token nötig, läuft aber über dieselbe Queue; nicht Teil der Druckhistorie)

**Settings-API**
- `GET /settings/api` – aktuelle Druckregeln + Wetter-Standorte (JSON)
- `POST /settings/print_rules` – Rate-Limit/Duplikat-Fenster ändern
- `GET|POST /settings/quiet_hours/rules`, `POST /settings/quiet_hours/rules/<id>/toggle`, `DELETE /settings/quiet_hours/rules/<id>`
- `GET|POST /settings/weather/locations`, `DELETE /settings/weather/locations/<name>`
- `GET|POST /settings/system_report` – SSH-Ziele für den Systembericht
- `GET|POST /settings/github_watch/repos`, `DELETE /settings/github_watch/repos/<owner>/<repo>`
- `GET|POST /settings/logos/config` – globale/pro Modul Logo-Schalter
- `POST /settings/logos/upload/<slot>`, `DELETE /settings/logos/upload/<slot>` – Logo-Bild hochladen/löschen (base64), `slot` ist `default` oder ein Modul-Key

## Nach der Installation

```bash
pip freeze > requirements.lock.txt
```
Friert die tatsächlich installierten Versionen ein – `requirements.txt`
selbst enthält bewusst keine Versionsnummern (kein `==x.y.z`),
`requirements.lock.txt` dient nur als Referenz, falls ein Update mal
etwas kaputt macht.

## Features

- Listen: Einkaufslisten, To-Do-Listen und druckbare Task-/Kanban-Karten, siehe [Listen](#listen) unten
- Freie Textmeldungen
- Bild-Upload und Bilddruck per API
- Gäste-WLAN-Zugangsdaten und QR-Codes
- Wetterberichte (DWD oder Open-Meteo wählbar, + optional Netatmo), mit optionalem Unwetterwarnungs-Watcher (DWD, MeteoAlarm oder NWS, druckt nur bei tatsächlich aktiver Warnung)
- Systemberichte (Proxmox/piNAS/PBS via SSH)
- Offline-Spiele (Sudoku, Würfelblock, Tic-Tac-Toe), siehe [Spiele](#spiele) unten
- Druckhistorie-Dashboard (Statistik + paginierte Liste, SQLite-basiert)
- Optionale Logos je Druckart mit globalem Standard-Fallback
- Einzeln ein-/ausschaltbare Home-Module, siehe [Modul-Schalter](#modul-schalter) unten
- Webbasierte Einstellungen, aufgeteilt in Unterseiten je Bereich und nach Thema gruppiert
- 5 wählbare Farbschemata für die Web-UI
- Easy-Read: ein Schalter für größere Schrift auf Bon und Web-UI zugleich, siehe [Easy-Read](#easy-read-große-schrift) unten
- Deutsch/Englisch-UI, inklusive des eigentlichen Bon-Inhalts (nicht nur der UI drumherum)
- USB-angeschlossener ESC/POS-Drucker (python-escpos + pyusb, Epson-TM-T88V-Profil)

### Listen

Aus dem ursprünglichen Einkaufslisten-Modul hervorgegangen; alle drei
Listentypen teilen sich dieselbe Druck-Queue, Historie, Rate-Limit und
Ruhezeiten wie jedes andere Modul.

- **Einkaufsliste** (`/shopping`, auch erreichbar unter `/lists/shopping`)
  – eine einfache Checkliste. Der API-Endpunkt `/print/list` ist
  unverändert seit vor dem Lists-Modul, weiterhin voll abwärtskompatibel
  für bestehende Integrationen. Einziger Listentyp mit Logo-Slot (nutzt
  den bestehenden Einkaufs-Logo-Slot) – To-Do-Listen und Task-Karten
  zeigen nie ein Logo.
- **To-Do-Liste** (`/lists/todo`) – derselbe Checklisten-Mechanismus wie
  die Einkaufsliste, separat gedruckt und beschriftet. JSON-API
  `POST /print/todo`.
- **Task-/Kanban-Karte** (`/lists/task`) – ein Titel plus optionaler
  Beschreibung, optionaler Checkliste, optionaler Priorität
  (niedrig/mittel/hoch) und optionalem Fälligkeitsdatum; es werden nur
  tatsächlich gesetzte Felder gedruckt, keine leeren Platzhalter auf dem
  Bon. JSON-API `POST /print/task` – geeignet für einen einfachen
  `curl`-Aufruf, einen Home-Assistant-`rest_command`, oder jede andere
  Automation, die HTTP-POST beherrscht, mit derselben `X-Api-Token`-Auth,
  Druck-Queue, Ruhezeiten, Rate-Limit und Historie wie jeder andere
  `/print/*`-Endpunkt.

### Spiele

Drei Offline-Spiele, die über dieselbe zentrale Druck-Queue laufen wie
jedes andere Modul. Anders als die meisten Module sind sie reine
UI-Funktionen – es gibt keine `/print/games/*`-JSON-API, sie sind nicht
an den Automation-Webhook angebunden, und es gibt für sie keinen
Logo-Slot.

- **Sudoku** (`/games/sudoku`) – ein lokal generiertes 9x9-Rätsel mit drei
  Schwierigkeitsgraden (leicht/mittel/schwer), optional mit einem
  zweiten Beleg für die vollständige Lösung.
- **Würfelblock** (`/games/dice`) – ein generischer Punkteblock für
  Würfelspiele mit 5 Würfeln (oberer Bereich Einser–Sechser mit
  Zwischensumme/Bonus, unterer Bereich 3er-/4er-Pasch, Full House,
  kleine/große Straße, 5 Gleiche, Chance, plus Gesamtsummen oben/unten/
  gesamt). Ein Beleg pro Spieler, 1–12 Spieler pro Druck.
- **Tic-Tac-Toe** (`/games/tictactoe`) – leere 3x3-Spielfelder zum
  handschriftlichen Ausfüllen, 3/6/9 Runden pro Druck.

### Easy-Read (große Schrift)

Ein einzelner Schalter unter `/settings/print-rules` ("Textgröße":
Normal / Groß) skaliert sowohl den Bon-Ausdruck als auch die Web-UI aus
derselben Einstellung heraus:

- **Bon**: Überschriften werden in doppelter Breite/Höhe gedruckt,
  Fließtext in doppelter Breite (was die nutzbare Zeilenbreite halbiert,
  Fließtext bricht dadurch öfter um); Datum/Uhrzeit am Ende und
  Trennlinien bleiben immer normal groß.
- **Web-UI**: die Basis-Schriftgröße wächst von 16px auf 19px, und der
  Status-Streifen sowie das Kachel-Grid der Module fallen von mehreren
  Spalten auf eine einzelne Spalte, damit nichts gequetscht wird.

Es handelt sich um einen reinen Textgrößen-Schalter, keine vollständige
Accessibility-Überarbeitung – es gibt kein separates
Screen-Reader-Markup oder einen eigenen Kontrastmodus dazu.

### Modul-Schalter

Unter `/settings/modules` lässt sich jedes der 7 Katalog-Module
(Listen, Nachricht, Wetter, Bild, Gäste-WLAN, System, Spiele) einzeln
ein- oder ausschalten:

- Ein deaktiviertes Modul verschwindet nicht nur von der Startseite,
  sondern **alle** seine Routen – Web-UI-Seiten, `/ui/*`-Formular-Posts
  und `/print/*`-API-Endpunkte gleichermaßen – antworten mit 404.
- Der Status-Streifen der Startseite zeigt an, wie viele dieser 7 Module
  aktuell aktiv sind.
- Verlauf, die Settings-Seiten selbst und der generische
  Automation-Webhook liegen außerhalb dieses Schalter-Systems und
  bleiben immer erreichbar.
- Zwei der drei Watcher berücksichtigen das: `fritzbox_wifi_watch.py`
  und `storm_warning_watch.py` prüfen den Status des `wifi`- bzw.
  `weather`-Moduls und überspringen sauber, wenn es deaktiviert ist.
  `github_star_watch.py` prüft das nicht und scheitert einfach an
  seinem Druckaufruf, wenn das `message`-Modul deaktiviert ist.

## Roadmap

Als Nächstes geplant: NFC-Tag-ausgelöster Druck (nur eine URL im Tag,
keine App nötig), sowie ein Rezepte-Modul (Tandoor/Mealie) mit über die
Web-UI konfigurierbaren Zugangsdaten. Die Roadmap kann sich mit
weiteren Hardwaretests ändern.

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

## Third-Party Licenses

Die Icons der Web-UI stammen von [Lucide](https://lucide.dev)
(`static/icons/`) und werden unter der ISC License verwendet – der
vollständige Lizenztext steht in
[static/icons/LICENSE](static/icons/LICENSE).

## Lizenz

MIT – siehe [LICENSE](LICENSE).
