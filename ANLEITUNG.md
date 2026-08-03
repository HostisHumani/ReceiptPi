# ReceiptPi (ehem. Bondrucker) – Setup & Migration auf dem Pi Zero 2 W

Dieses Projekt hieß bisher "Bondrucker"/"Thermodrucker" und lief bereits
produktiv unter `~/thermodrucker` (systemd-Service `thermodrucker.service`,
Hostname `bondrucker.local`). Diese Anleitung deckt zwei Fälle ab:

- **Frisches Setup** (Abschnitte 1-10): kompletter Weg von der leeren
  SD-Karte bis zum ersten Testdruck.
- **Migration** (Abschnitt 0): falls schon ein laufender `thermodrucker`-
  Aufbau existiert (dein aktueller Stand) und auf die neue, modulare
  `receiptpi`-Struktur umgezogen werden soll.

---

## 0. Migration von der alten Bondrucker-Struktur

**Ausgangslage:** `~/thermodrucker` läuft, Service heißt
`thermodrucker.service`, `config.py` mit echten Werten liegt dort,
Cronjobs zeigen auf `~/thermodrucker/github_star_watch.py` und
`~/thermodrucker/fritzbox_wifi_watch.py`, Zustand liegt unter
`/var/lib/bondrucker`.

**Ziel:** `~/receiptpi` mit der neuen Modul-/Watcher-Struktur, Service
heißt `receiptpi.service`, Zustand liegt unter `/var/lib/receiptpi`.

### 0a. Neue Dateien auf den Pi bringen
```bash
ssh <user>@bondrucker.local "mkdir -p ~/receiptpi"
scp -r /home/adrian/lab/receiptpi/* <user>@bondrucker.local:~/receiptpi/
```

### 0b. `config.py` migrieren (NICHT einfach kopieren)
Die alte `~/thermodrucker/config.py` enthält bereits alle echten Werte
(`HA_TOKEN`, `FRITZBOX_PASSWORD`, `API_TOKEN`, SSH-Hosts, etc.). Die neue
`config.example.py` hat dieselben Feldnamen, aber `STATE_DIR` zeigt jetzt
standardmäßig auf `/var/lib/receiptpi` statt `/var/lib/bondrucker`.

```bash
ssh <user>@bondrucker.local
cp ~/thermodrucker/config.py ~/receiptpi/config.py
nano ~/receiptpi/config.py
```
Darin `STATE_DIR` manuell auf `/var/lib/receiptpi` ändern (bzw. auf den
Pfad aus Schritt 0c unten). Alle anderen Werte bleiben unverändert
gültig – nichts muss neu recherchiert werden.

### 0c. Neues STATE_DIR anlegen
```bash
sudo install -d -o <user> -g <user> -m 0750 /var/lib/receiptpi
```
Die Watch-Scripts setzen bei der Baseline (Github-Stars, WLAN-Status)
einmalig neu auf – harmlos, druckt beim ersten Lauf nach der Migration
nichts, sondern merkt sich nur den aktuellen Stand neu.

### 0d. venv + Pakete für die neue Struktur
```bash
cd ~/receiptpi
python3 -m venv ~/receiptpi-env
source ~/receiptpi-env/bin/activate
pip install -r requirements.txt
```
(Eigenes venv statt das alte `~/thermo-env` weiterzunutzen – sauberer
Schnitt, altes venv kann nach erfolgreicher Migration gelöscht werden.)

### 0e. Alten Service stoppen, neuen einrichten
```bash
sudo systemctl stop thermodrucker.service
sudo systemctl disable thermodrucker.service
```
Neuen Service anlegen wie in Abschnitt 9 beschrieben (Name:
`receiptpi.service`, `ExecStart` zeigt auf `~/receiptpi-env` und
`~/receiptpi`).

### 0f. Cronjobs umstellen
```bash
crontab -e
```
Alte Zeilen (`~/thermodrucker/...`) durch die neuen Pfade ersetzen (siehe
Abschnitt 11/12 unten, `~/receiptpi/watchers/...` statt
`~/thermodrucker/...`).

### 0g. Testen, dann aufräumen
Testdruck + Web-UI wie in Abschnitt 7/8 beschrieben durchführen. Erst
wenn alles läuft:
```bash
rm -rf ~/thermodrucker ~/thermo-env
sudo rm -rf /var/lib/bondrucker
```

**Hostname/Zabbix-Webhook-URLs:** `bondrucker.local` kann unverändert
bleiben (reine Namensfrage, keine Funktionsänderung nötig) oder auf
`receiptpi.local` umbenannt werden (`sudo raspi-config` → System Options
→ Hostname, danach neu booten). Falls umbenannt: alle `http://bondrucker
.local:5000/...`-URLs in Zabbix (Abschnitt 13c) und im Fritz!Box-Watcher
entsprechend anpassen.

---

## 1. OS-Image (nur bei frischer SD-Karte relevant)
Raspberry Pi OS Lite, 64-bit, Trixie/Debian 13. Der Zero 2 W hat einen
Cortex-A53 (ARMv8) und bootet damit nativ 64-bit. Im Raspberry Pi Imager:
Gerät "Raspberry Pi Zero 2 W" auswählen, dann "Raspberry Pi OS Lite
(64-bit)".

Beim Flashen in den erweiterten Einstellungen (Zahnrad-Symbol) setzen:
- Hostname: `receiptpi.local` (oder `bondrucker.local`, falls die
  bestehende Migration aus Abschnitt 0 genutzt wird)
- SSH aktivieren, Public Key hinterlegen
- WLAN-SSID + Passwort eintragen
- Nutzername/Passwort setzen

## 2. Erster Boot & Verbindung
```bash
ssh <user>@<hostname>.local
```

## 3. System vorbereiten
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-pip python3-venv
```

## 4. Projektdateien auf den Pi bringen
Projektdateien liegen lokal unter `/home/adrian/lab/receiptpi`. Von dort
per `scp` auf den Pi kopieren:
```bash
ssh <user>@<hostname>.local "mkdir -p ~/receiptpi"
scp -r /home/adrian/lab/receiptpi/* <user>@<hostname>.local:~/receiptpi/
```

## 4b. Verzeichnis für Laufzeitdaten anlegen
Die Watch-Scripts laufen als Cronjobs, nicht innerhalb des Gunicorn-
Service (siehe Schritt 9) – die dortige Sandbox-Härtung betrifft sie
also nicht. `settings_store.py` (zentrale Druckregeln/Wetter-Standorte)
läuft dagegen INNERHALB des Service und braucht denselben Ordner
ebenfalls, da `app.py` selbst unter `ProtectSystem=strict` sonst nicht
hineinschreiben dürfte. FHS-konformer Ort für beides: `/var/lib/receiptpi`.
```bash
sudo install -d -o <user> -g <user> -m 0750 /var/lib/receiptpi
```

## 5. Python-Umgebung + Pakete
```bash
cd ~/receiptpi
python3 -m venv ~/receiptpi-env
source ~/receiptpi-env/bin/activate
pip install -r requirements.txt
```

## 6. Config-Werte eintragen
```bash
cp config.example.py config.py
nano config.py
```
Alle Werte eintragen, die die Automationen brauchen:
- `HA_TOKEN` – Long-Lived Access Token aus Home Assistant: Profil (unten
  links, eigener Name) → ganz unten "Long-Lived Access Tokens" → "Token
  erstellen"
- `NETATMO_INDOOR_ENTITY` / `NETATMO_OUTDOOR_ENTITY` – Entity-IDs aus HA:
  Entwicklerwerkzeuge → Zustände → nach "netatmo" filtern
- `FRITZBOX_USER` / `FRITZBOX_PASSWORD` – Zugangsdaten eines Fritz!Box-
  Nutzerkontos (Fritz!Box-Oberfläche → System → Fritz!Box-Benutzer),
  Berechtigung nur "FRITZ!Box-Einstellungen"
- `GITHUB_OWNER` / `GITHUB_REPO` – das zu beobachtende Repo
- `SSH_PROXMOX_HOST/USER`, `SSH_PINAS_HOST/USER`, `SSH_PBS_HOST/USER` –
  für den Systembericht (Abschnitt 15), Werte sind schon vorbelegt
- `SECRET_KEY` – zufälliger Wert für die Flask-Session (CSRF-Schutz):
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
  Server verweigert den Start, solange hier der Platzhalter steht.
- `API_TOKEN` – optional, schützt `/print/*`-Endpunkte per
  `X-Api-Token`-Header. Zusätzlich empfiehlt sich, Port 5000 in der
  Firewall nur aus dem internen Netz erreichbar zu machen.
- `STATE_DIR` – Standard `/var/lib/receiptpi`, aus Schritt 4b

## 7. Drucker anschließen und Vendor/Product-ID ermitteln
```bash
lsusb
```
Zeigt eine Zeile wie:
```
Bus 001 Device 004: ID 04b8:0202 Seiko Epson Corp. ...
```
`04b8` = Vendor-ID, `0202` = Product-ID. Beide Werte in `config.py`
eintragen (`VENDOR_ID`, `PRODUCT_ID`).

Testdruck (Dev-Modus, druckt beim Start automatisch einen Boot-Gruß):
```bash
source ~/receiptpi-env/bin/activate
python3 app.py
```
In einem zweiten Terminal (Token-Header nur nötig, falls `API_TOKEN`
gesetzt ist):
```bash
curl -X POST http://<hostname>.local:5000/print/message \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: <dein-API_TOKEN>" \
  -d '{"text": "Testdruck"}'
```

## 8. Nutzung: Web-UI
`http://<hostname>.local:5000` im Browser öffnen, als Homescreen-
Lesezeichen speichern. Die Startseite ist eine reine Modulübersicht
(Emerald-Ink/Champagne-Farbschema, `static/style.css`), jedes Modul hat
seine eigene Unterseite mit dem passenden Formular:

- **🛒 Einkauf** (`/shopping`): Titel (vorausgefüllt) + ein Eintrag pro
  Zeile, "Drucken" → Liste mit `[ ]`-Ankreuzkästchen pro Zeile
- **📝 Nachricht** (`/message`): optionaler Titel + freier Text
- **🌤️ Wetter** (`/weather`): Standort-Dropdown (aus den Settings, siehe
  Abschnitt 16) + Button
- **🖼️ Bild** (`/images`): Datei-Upload (PNG/JPG, max. 1MB) - läuft direkt
  als Multipart-Formular, ohne Base64-Umweg über den Browser
- **🖥️ System** (`/system`): Button, kein Eingabefeld nötig

Gleiche Funktionen auch per JSON:
```bash
curl -X POST http://<hostname>.local:5000/print/list \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: <dein-API_TOKEN>" \
  -d '{"title": "Einkaufszettel", "items": ["Milch", "Brot"]}'
```

Alle Druckaufträge (Web-UI, Cron-Trigger, Zabbix-Webhook) laufen über
eine gemeinsame Queue (`print_queue.py`), ein Worker-Thread arbeitet sie
nacheinander ab. Vor jedem Auftrag werden zusätzlich die zentralen
Druckregeln geprüft (Ruhezeiten, Rate-Limit, Duplikat-Sperre – siehe
Abschnitt 16).

**Beim nächsten Deploy neu hinzugekommen:** `static/style.css`,
`templates/base.html` sowie je eine Unterseite pro Modul
(`templates/{shopping,message,weather,images,system}.html`) - das alte
`templates/index.html` gibt's nicht mehr, wurde durch `templates/home.html`
(Kachel-Startseite) ersetzt.

## 9. systemd-Service (Gunicorn statt Flask-Entwicklungsserver)
Der eingebaute Flask-Server (`app.run(...)`) ist nicht für den
Dauerbetrieb gedacht. Produktiv läuft der Server über Gunicorn mit
`gunicorn.conf.py` (enthält den `on_starting`-Hook für den Boot-Gruß,
läuft dadurch garantiert nur einmal, unabhängig von der Worker-Anzahl) –
**wichtig: genau 1 Worker**, da die Print-Queue nur innerhalb eines
einzelnen Prozesses funktioniert. `--no-control-socket` deaktiviert
Gunicorns Control-Socket-Feature (seit Version ~26), das sonst wegen
`ProtectHome=read-only` einen harmlosen Fehler im Log erzeugen würde.

```bash
sudo nano /etc/systemd/system/receiptpi.service
```
```ini
[Unit]
Description=ReceiptPi Flask Server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/home/<user>/receiptpi-env/bin/gunicorn -c gunicorn.conf.py --no-control-socket --workers 1 --threads 4 --bind 0.0.0.0:5000 app:app
WorkingDirectory=/home/<user>/receiptpi
Restart=on-failure
RestartSec=5
User=<user>
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/receiptpi

[Install]
WantedBy=multi-user.target
```
`ReadWritePaths=/var/lib/receiptpi` ist jetzt nötig (anders als beim
alten Bondrucker-Service): `settings_store.py` schreibt zur Laufzeit
Druckregeln/Wetter-Standorte dorthin. Das Quellcode-Verzeichnis selbst
bleibt weiterhin unter `ProtectSystem=strict` schreibgeschützt.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now receiptpi.service
sudo systemctl status receiptpi.service --no-pager
```
Bei jedem Start druckt der Server automatisch einen "ONLINE"-Boot-Gruß
mit Hostname und lokaler IP (über `gunicorn.conf.py`, nicht `app.py`).

## 10. USB-Zugriff ohne root
```bash
sudo nano /etc/udev/rules.d/99-escpos.rules
```
Vendor/Product-ID aus Schritt 7 einsetzen. Zugriff über die Gruppe `lp`
statt `MODE="0666"`:
```
SUBSYSTEM=="usb", ATTR{idVendor}=="04b8", ATTR{idProduct}=="0202", GROUP="lp", MODE="0660"
```
```bash
sudo usermod -aG lp <user>
sudo udevadm control --reload-rules
sudo udevadm trigger
```
Danach einmal neu anmelden (oder rebooten), damit die
Gruppenmitgliedschaft aktiv wird.

## 11. GitHub-Star-Polling
`watchers/github_star_watch.py` nutzt `GITHUB_OWNER`/`GITHUB_REPO` aus
`config.py` und postet an `/print/message`. Cronjob:
```bash
crontab -e
```
```
*/5 * * * * /home/<user>/receiptpi-env/bin/python3 /home/<user>/receiptpi/watchers/github_star_watch.py 2>&1 | logger -t receiptpi_github
```
Logs: `journalctl -t receiptpi_github`. Erster Lauf setzt die Baseline
(druckt nichts), ab dem zweiten Lauf druckt jeder neue Star.

## 12. WLAN-Gästenetz-QR
`watchers/fritzbox_wifi_watch.py` prüft den Gästenetz-Status per Cronjob
und postet SSID+Passwort an `/print/wifi`. Zusätzlich gibt's eine
**📶 Gäste-WLAN**-Kachel in der Web-UI (`/wifi`) mit einem "Jetzt
drucken"-Button, der live bei der Fritz!Box abfragt (nicht nur den
zwischengespeicherten Watcher-Stand) - für den Fall, dass der Zettel
sofort oder erneut gebraucht wird. Beide Wege nutzen dieselbe
`get_guest_wifi_status()`-Funktion aus `modules/wifi/routes.py`, die
Abfrage-Logik existiert also nur einmal.

Cronjob:
```
*/1 * * * * /home/<user>/receiptpi-env/bin/python3 /home/<user>/receiptpi/watchers/fritzbox_wifi_watch.py 2>&1 | logger -t receiptpi_wifi
```
Logs: `journalctl -t receiptpi_wifi`. Erster Lauf setzt die Baseline.

## 13. PBS-Backup-Fehler über Zabbix
Vier Teile: ein Item, das den Backup-Status liefert, ein Trigger darauf,
ein Webhook-Media-Type, der ReceiptPi anspricht, und eine Action, die
beides verbindet.

### 13a. Item auf dem PBS-Host anlegen
```bash
sudo apt install -y jq
sudo nano /usr/local/bin/check_pbs_backup.sh
```
Inhalt:
```bash
#!/bin/bash
# Gibt die Anzahl fehlgeschlagener Backups unter den 20 zuletzt
# gelaufenen Backup-Tasks aus (nach Startzeit sortiert, neueste zuerst).
FAILED=$(proxmox-backup-manager task list --output-format json 2>/dev/null | \
  jq '[.[] | select(.worker_type=="backup" and .status != null)]
      | sort_by(.starttime) | reverse | .[:20]
      | map(select(.status != "OK")) | length')
echo "${FAILED:-0}"
```
```bash
sudo chmod +x /usr/local/bin/check_pbs_backup.sh
sudo nano /etc/zabbix/zabbix_agent2.d/pbs_backup.conf
```
Inhalt: `UserParameter=pbs.backup.failed,/usr/local/bin/check_pbs_backup.sh`
```bash
sudo systemctl restart zabbix-agent2
zabbix_agent2 -t pbs.backup.failed   # Test, 0 bei allem grün
```

### 13b. Item + Trigger im Zabbix-Frontend
**Data collection → Hosts → (dein PBS-Host) → Items → Create item:**
- Name: `PBS Backup Status`, Key: `pbs.backup.failed`
- Type: `Zabbix agent`, Type of information: `Numeric (unsigned)`
- Update interval: `1h`

**Triggers → Create trigger:**
- Name: `PBS Backup fehlgeschlagen`, Severity: `High`
- Expression: `last(/<dein-PBS-Hostname-in-Zabbix>/pbs.backup.failed)>0`

### 13c. Webhook-Media-Type anlegen
**Administration → Media types → Create media type:**
- Name: `ReceiptPi`, Type: `Webhook`
- Parameters:
  - `URL` = `http://<hostname>.local:5000/print/message`
  - `Subject` = `{ALERT.SUBJECT}`
  - `Message` = `{ALERT.MESSAGE}`
  - `API_TOKEN` = derselbe Wert wie `API_TOKEN` in `config.py` (leer
    lassen, falls kein Token gesetzt ist)
- Script:
```javascript
try {
    var params = JSON.parse(value),
        req = new HttpRequest(),
        resp;

    req.addHeader('Content-Type: application/json');
    if (params.API_TOKEN) {
        req.addHeader('X-Api-Token: ' + params.API_TOKEN);
    }
    resp = req.post(params.URL, JSON.stringify({
        title: params.Subject,
        text: params.Message
    }));

    if (req.getStatus() < 200 || req.getStatus() >= 300) {
        throw 'ReceiptPi antwortete mit Status ' + req.getStatus();
    }
    return 'OK';
} catch (error) {
    Zabbix.log(4, 'ReceiptPi Webhook Fehler: ' + error);
    throw 'ReceiptPi Webhook fehlgeschlagen: ' + error;
}
```

### 13d. Media-Type deinem Zabbix-User zuweisen
**Administration → Users → (dein Benutzer) → Media → Add:**
- Type: `ReceiptPi`, Send to: beliebiger Platzhaltertext
- Severity: mindestens `High`

### 13e. Action anlegen
**Configuration → Actions → Trigger actions → Create action:**
- Name: `PBS Backup Fehler an ReceiptPi`
- Conditions: `Event name` `contains` `PBS Backup fehlgeschlagen`
- Operations → Send message → Send only to: `ReceiptPi`
  - Subject: `PBS BACKUP FEHLER`
  - Body: `{TRIGGER.NAME}: {HOST.NAME}`

Status auf `Enabled` setzen. Bei `pbs.backup.failed>0` feuert der
Trigger, die Action schickt über den Webhook, ReceiptPi druckt.

## 14. Wetterbericht
Über den Button in Abschnitt 8 nutzbar, sobald `HA_TOKEN` und die beiden
Netatmo-Entity-IDs in `config.py` eingetragen sind. Standorte kommen
NICHT mehr fest aus `config.py`, sondern aus den Settings (Abschnitt 16)
– lassen sich zur Laufzeit hinzufügen, ohne den Service neu zu starten.

## 15. Systembericht (Mac Mini, PBS, piNAS)
Nutzt dieselben SSH-Befehle wie der Termux Lab Commander
(`scripts/termux-lab-commander.sh` im HomeLab-Repo), komplett ohne
Zabbix.

### 15a. SSH-Zugriff vom ReceiptPi-Pi zu Mac Mini, piNAS und PBS
```bash
ssh-keygen -t ed25519 -C "receiptpi-serverstatus" -N "" -f ~/.ssh/id_ed25519
ssh-copy-id root@192.168.1.10   # Mac Mini (Proxmox)
ssh-copy-id root@192.168.1.11   # piNAS
ssh-copy-id root@192.168.1.12   # PBS
```
(IPs anpassen, falls deine Hosts andere Adressen haben.)

Testen:
```bash
ssh root@192.168.1.10 "pct list"
ssh root@192.168.1.11 "docker ps"
ssh root@192.168.1.12 "proxmox-backup-manager task list --all"
```

### 15b. In `config.py` prüfen
`SSH_PROXMOX_HOST/USER`, `SSH_PINAS_HOST/USER`, `SSH_PBS_HOST/USER` auf
die tatsächlichen IPs in deinem Netz setzen (Beispielwerte in
`config.example.py`: 192.168.1.10/.11/.12, jeweils root).

**Was gedruckt wird:** CPU-Temp/-Last + RAM (Proxmox), LXC-/VM-Liste,
NVMe-Speicherplatz (piNAS), laufende Docker-Container, letzte 5
PBS-Backup-Tasks, verfügbare Updates auf PVE/OMV/PBS (bei ≤10 Paketen
einzeln aufgelistet, sonst nur die Anzahl – `UPDATE_LIST_THRESHOLD` in
`modules/system/routes.py`).

## 16. Druckregeln & Wetter-Standorte (Settings)
Zentral über `settings_store.py` (JSON-Datei in `STATE_DIR`), geprüft
vor JEDEM Druckauftrag – nicht pro Modul einzeln. Zwei Zugriffswege auf
dieselben Daten:

- **Web-UI** (`/settings`, ⚙️-Kachel auf der Startseite): Formulare für
  Ruhezeiten/Rate-Limit/Duplikat-Fenster sowie Wetter-Standorte
  hinzufügen/löschen/als Standard setzen - kein Token nötig, per
  CSRF-Token geschützt wie die anderen Formulare.
- **JSON-API** (für Scripts/Automationen), per `X-Api-Token` geschützt:

**Druckregeln lesen:**
```bash
curl http://<hostname>.local:5000/settings/api -H "X-Api-Token: <token>"
```

**Ruhezeiten aktivieren:**
```bash
curl -X POST http://<hostname>.local:5000/settings/print_rules \
  -H "Content-Type: application/json" -H "X-Api-Token: <token>" \
  -d '{"quiet_hours_enabled": true, "quiet_hours_start": "22:00", "quiet_hours_end": "07:00"}'
```
Während der Ruhezeit antworten `/print/*`-Endpunkte mit `429` statt zu
drucken. Rate-Limit (`max_jobs_per_hour`) und Duplikat-Sperre
(`duplicate_window_seconds`) laufen unabhängig davon immer mit.

**Wetter-Standort hinzufügen:**
```bash
curl -X POST http://<hostname>.local:5000/settings/weather/locations \
  -H "Content-Type: application/json" -H "X-Api-Token: <token>" \
  -d '{"name": "Berlin", "lat": 52.52, "lon": 13.40, "set_default": false}'
```
Beim Drucken gezielt abrufen: `POST /print/weather` mit Body
`{"location": "Berlin"}`, oder ohne Angabe wird der `default_location`
genutzt.

Health-Check und Boot-Gruß umgehen alle Druckregeln bewusst
(`bypass_rules=True` in `print_queue.enqueue_print()`), damit sie nicht
selbst durch Ruhezeit/Duplikat-Sperre blockiert werden.

---

## Anhang: Git + GitHub für dieses Projekt selbst
Aktuell per `scp` deployed (Schritt 4). Für später, falls das Projekt
doch noch versioniert/veröffentlicht werden soll:

```bash
git config --global user.name "Dein Name"
git config --global user.email "deine@email.de"
ssh-keygen -t ed25519 -C "receiptpi"
cat ~/.ssh/id_ed25519.pub
```
Ausgabe auf GitHub unter Settings → SSH and GPG keys → New SSH key
einfügen. Test: `ssh -T git@github.com`

Neues, leeres Repo auf github.com anlegen (ohne README/.gitignore),
dann von `/home/adrian/lab/receiptpi`:
```bash
cd /home/adrian/lab/receiptpi
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin git@github.com:<dein-github-name>/receiptpi.git
git push -u origin main
```
Auf dem Pi ersetzt `git clone`/`git pull` danach den `scp`-Schritt.

## Anhang: systemd-Timer statt Cron (optionale Alternative)
Cron (Abschnitte 11+12) funktioniert und ist der einfachere Einstieg.
Sauberer wäre langfristig, die beiden Watch-Scripts als systemd-Service +
Timer laufen zu lassen: `StateDirectory=` legt `/var/lib/receiptpi`
automatisch mit den richtigen Rechten an.

**GitHub-Star-Watch:**
```bash
sudo nano /etc/systemd/system/receiptpi-github.service
```
```ini
[Unit]
Description=ReceiptPi GitHub-Star-Watch

[Service]
Type=oneshot
ExecStart=/home/<user>/receiptpi-env/bin/python3 /home/<user>/receiptpi/watchers/github_star_watch.py
User=<user>
StateDirectory=receiptpi
StateDirectoryMode=0750
```
```bash
sudo nano /etc/systemd/system/receiptpi-github.timer
```
```ini
[Unit]
Description=ReceiptPi GitHub-Star-Watch alle 5 Minuten

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

**WLAN-Gästenetz-Watch** (gleiches Schema, 1-Minuten-Takt):
```bash
sudo nano /etc/systemd/system/receiptpi-wifi.service
```
```ini
[Unit]
Description=ReceiptPi WLAN-Gästenetz-Watch

[Service]
Type=oneshot
ExecStart=/home/<user>/receiptpi-env/bin/python3 /home/<user>/receiptpi/watchers/fritzbox_wifi_watch.py
User=<user>
StateDirectory=receiptpi
StateDirectoryMode=0750
```
```bash
sudo nano /etc/systemd/system/receiptpi-wifi.timer
```
```ini
[Unit]
Description=ReceiptPi WLAN-Gästenetz-Watch jede Minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
```

**Aktivieren:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now receiptpi-github.timer
sudo systemctl enable --now receiptpi-wifi.timer
```
Falls diese Variante genutzt wird: die beiden `crontab -e`-Zeilen aus
Abschnitt 11/12 weglassen, sonst laufen Cron und Timer parallel und
drucken doppelt.
