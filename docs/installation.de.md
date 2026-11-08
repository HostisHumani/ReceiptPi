# Installation

*[English version](installation.md)*

Diese Anleitung installiert ReceiptPi auf einem frischen Raspberry Pi OS
Lite mit dem Installationsskript `deploy/install.sh`.

---

## Stand

Das Installationsskript ist in einem Debian-13-„trixie“-Container (arm64)
ohne laufendes systemd getestet:

- Installation, ein zweiter Lauf mit identischem Ergebnis und ein dritter
  Lauf, der die vorhandene Konfiguration behält
- Besitzer und Rechte aller installierten Dateien, und keine Konfiguration
  und kein Geheimnis in den installierten Dateien vor dem ersten Start
- das Erststart-Skript, das die Konfiguration erzeugt
- der Server, gestartet als Benutzer `receiptpi` mit der Befehlszeile aus
  `receiptpi.service`, und jeder Watcher auf dieselbe Weise

Noch nicht getestet: ein echter Raspberry Pi, das Starten der Dienste und
Timer durch systemd und der Druckerzugriff über USB mit der udev-Regel.

---

## Getestete Hardware

Die Anwendung selbst läuft auf:

| Komponente | Version |
|------------|---------|
| Einplatinenrechner | Raspberry Pi Zero 2 W |
| Betriebssystem | Raspberry Pi OS Lite, 64 Bit (Debian 13 „trixie“) |
| Thermodrucker | Epson TM-T88V (USB, ID `04b8:0e02`) |

Andere Raspberry-Pi-Modelle und Drucker sind nicht getestet.

---

## Voraussetzungen

- Raspberry Pi OS Lite **64 Bit** (trixie). `deploy/requirements.lock`
  installiert nur fertig gebaute Pakete, und die sind nur für 64 Bit
  (aarch64) geprüft.
- Netzwerkzugang: Das Skript lädt Systempakete mit apt und Python-Pakete
  von PyPI.
- Ein Benutzer mit `sudo`-Rechten. Zeitzone und WLAN-Land ändert das Skript
  nicht: Diese im Raspberry Pi Imager oder mit `sudo raspi-config` setzen.

---

## Installieren

```bash
sudo apt install git
git clone https://github.com/HostisHumani/ReceiptPi.git
cd ReceiptPi
sudo ./deploy/install.sh
```

Das Skript darf erneut laufen; eine vorhandene Konfiguration behält es.

Was es einrichtet:

| Pfad / Unit | Zweck |
|-------------|-------|
| `/opt/receiptpi` | Verweis auf das aktive Release |
| `/opt/receiptpi-releases/<version>` | Programmcode eines Releases, gehört root, für den Dienst nicht beschreibbar. Das Release vor dem aktiven bleibt erhalten. |
| `/opt/receiptpi-venvs/<key>` | Python-Umgebung mit den Versionen und Hashes aus `deploy/requirements.lock`; Releases mit derselben Lock-Datei teilen sie |
| `/etc/receiptpi/config.py` | Konfiguration (`root:receiptpi`, Modus 0640), entsteht beim ersten Start |
| `/var/lib/receiptpi` | Einstellungen, Verlauf, Konten (Modus 0750) |
| `/var/lib/receiptpi-update` | Status, Sicherungen und Downloads des Updaters (`root:receiptpi`, Modus 0750) |
| `receiptpi` | System-Benutzer ohne Login, unter dem Server und Watcher laufen |
| `receiptpi.service` | Der Server (Gunicorn, Port 5000), aktiviert |
| `receiptpi-firstboot.service` | Erzeugt die Konfiguration vor dem ersten Serverstart |
| `receiptpi-*.timer` | Die Watcher, siehe unten |
| `receiptpi-update.path`, `receiptpi-update@.service` | Der Updater (`deploy/update/updater.py`, root): installiert ein neueres, signiertes Release, wenn die Web-UI es anfordert |
| `receiptpi-update-auto.timer` | Nächtlicher Lauf des Updaters zwischen 02:00 und 03:30; er installiert nur etwas, wenn automatische Updates eingeschaltet sind |
| `receiptpi-update-recover.service` | Beim Start: schließt ein unterbrochenes Update ab oder macht es rückgängig, etwa nach einem Stromausfall |
| `/etc/udev/rules.d/99-receiptpi-printer.rules` | Druckerzugriff für die Gruppe `lp` |

Eine `config.py`, `secret.key` oder `settings.json` im Checkout wird nicht
mitkopiert. Auf einem Gerät, auf dem `/opt/receiptpi` von einer älteren
Installation noch ein normaler Ordner ist, ersetzt das Skript diesen Ordner
durch den Verweis.

---

## Erster Start

Bevor der Server zum ersten Mal startet, erzeugt
`receiptpi-firstboot.service` die Datei `/etc/receiptpi/config.py` aus
`config.example.py`:

- ein eigener zufälliger `SECRET_KEY` für jedes Gerät
- `API_TOKEN` leer
- die Beispieleinträge der Vorlage (Hosts für den Systembericht,
  beobachtete GitHub-Repos) leer

Eine vorhandene `config.py` wird nie ersetzt.

Danach die Web-UI öffnen und das Admin-Konto anlegen:

```
http://<hostname>:5000/setup
```

Bei leerem `API_TOKEN` druckt die Setup-Seite einen einmaligen
Einrichtungscode aus. Der Drucker muss für diesen Schritt also angeschlossen
und eingeschaltet sein. Bis ein Admin-Konto existiert, ist die Web-UI für
alle im Netz offen.

### Konfiguration ändern

`config.py` wird beim Start des Servers gelesen:

```bash
sudo nano /etc/receiptpi/config.py
sudo systemctl restart receiptpi
```

Die meisten Einstellungen laufen stattdessen über die Web-UI
(Einstellungen). `config.py` enthält nur Startwerte wie die Drucker-IDs, den
API-Token und Zugangsdaten, siehe die Kommentare in der Datei.

---

## WLAN-Einrichtung (nur Image)

Ein Gerät, das mit dem ReceiptPi-Image beschrieben wurde, braucht keine
WLAN-Einstellungen im Raspberry Pi Imager; es richtet sein WLAN selbst ein.
Stand: getestet mit nachgestelltem NetworkManager, noch nicht auf einem
echten Gerät.

1. Gerät mit angeschlossenem Drucker einschalten. Findet es innerhalb von
   2 Minuten kein Netz, druckt es einen Beleg **WLAN einrichten** mit einem
   Netznamen (`ReceiptPi-…`), einem Passwort und einem QR-Code.
2. Den QR-Code mit dem Handy scannen oder sich mit Name und Passwort vom
   Beleg von Hand verbinden.
3. Die Einrichtungsseite öffnet sich von selbst. Falls nicht, im Browser
   `http://10.42.0.1` öffnen.
4. Dein WLAN wählen, sein Passwort eingeben und auf **Verbinden** tippen.
5. Das Einrichtungs-WLAN schließt sich. Das Gerät druckt **WLAN verbunden**
   mit seiner Adresse und einem QR-Code zur Web-UI. Weiter mit
   [Erster Start](#erster-start).

Gut zu wissen:

- Klappt das Verbinden nicht, öffnet sich das Einrichtungs-WLAN wieder mit
  demselben Passwort, und ein Hinweis wird gedruckt.
- Verliert das Gerät später sein WLAN für 10 Minuten (neuer Router, neues
  Passwort), öffnet sich das Einrichtungs-WLAN wieder mit einem neuen
  Passwort. Alle 10 Minuten prüft es eine Minute lang, ob das gespeicherte
  WLAN wieder da ist.
- Ein Netzwerkkabel am Gerät beendet die Einrichtung sofort.
- Ein USB-WLAN-Stick wird vor dem eingebauten WLAN verwendet.
- Die Uhrzeit: Das Gerät hat keine Uhr, die ohne Strom weiterläuft. Es holt
  die Zeit vom Zeitserver, den dein Router mitteilt, sonst von öffentlichen
  Zeitservern. Antwortet keiner davon, wird die Uhrzeit deines Handys von
  der Einrichtungsseite übernommen, damit das Datum von Anfang an stimmt.
- Mit `install.sh` installiert, öffnet das Gerät nie ein Einrichtungs-WLAN.

---

## Drucker und USB

ReceiptPi spricht den Drucker direkt über USB an (python-escpos mit dem
Profil `TM-T88V`). Das Layout geht von einem 80-mm-Drucker mit 42 Zeichen
pro Zeile (Font A) und 512 Pixel breiten Bildern aus.

Konfiguration und udev-Regel sind für den Epson TM-T88V eingerichtet
(`VENDOR_ID = 0x04b8`, `PRODUCT_ID = 0x0e02`). `lsusb` zeigt ihn als
`ID 04b8:0e02 Seiko Epson Corp. TM-T88V`.

Ein anderer Drucker (nicht getestet) braucht seine IDs in
`/etc/receiptpi/config.py` und eine udev-Regel derselben Form in einer
eigenen Datei, z. B. `/etc/udev/rules.d/99-receiptpi-local.rules`;
`99-receiptpi-printer.rules` wird bei jedem Lauf des Skripts ersetzt.

Ohne Drucker startet der Server trotzdem: Die Startbegrüßung schreibt nur
eine Warnung ins Log, und `/health` antwortet mit Status 500 und
`"state": "offline"`.

---

## Watcher

Die Skripte unter `watchers/` laufen als systemd-Timer unter dem Benutzer
`receiptpi`. Sie lesen dieselbe Konfiguration wie der Server und schicken
Druckaufträge an ihn auf `localhost:5000`; ein später gesetzter
`API_TOKEN` gilt also auch für sie.

| Timer | Skript | Intervall |
|-------|--------|-----------|
| `receiptpi-github-star.timer` | `github_star_watch.py` | alle 5 Minuten |
| `receiptpi-storm-warning.timer` | `storm_warning_watch.py` | alle 15 Minuten |
| `receiptpi-update-check.timer` | `update_check_watch.py` | alle 12 Stunden |
| `receiptpi-wifi-watch.timer` | `fritzbox_wifi_watch.py` | jede Minute, nicht aktiviert |

Ohne weitere Einrichtung finden die ersten beiden nichts zu tun (keine Repos
eingetragen, Sturmwarnung in den Einstellungen aus), und der Update-Check
merkt sich nur, ob es ein neueres Release gibt.

Der Watcher für das Gäste-WLAN ist installiert, aber nicht aktiviert. Mit
einer Fritz!Box deren Zugangsdaten in `/etc/receiptpi/config.py` eintragen
und den Timer aktivieren:

```bash
sudo systemctl enable --now receiptpi-wifi-watch.timer
```

---

## Fehlersuche

```bash
systemctl status receiptpi
journalctl -u receiptpi -n 50 --no-pager
journalctl -u receiptpi-firstboot --no-pager
systemctl list-timers 'receiptpi-*'
journalctl -u 'receiptpi-watcher@*' -n 50 --no-pager
```

- **Drucker nicht gefunden** (`/health` meldet `offline`): Prüfen, ob
  `lsusb` den Drucker mit den IDs aus `config.py` zeigt und ob sein
  Geräteknoten der Gruppe `lp` gehört (`ls -l /dev/bus/usb/*/*`).
