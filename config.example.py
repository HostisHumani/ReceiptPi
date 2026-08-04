"""
Zentrale Konfiguration für den ReceiptPi-Server und die Trigger-Scripts.
Platzhalter durch echte Werte ersetzen, bevor die jeweiligen Features genutzt
werden. Diese Datei NICHT ins Git-Repo committen, wenn echte Werte drinstehen
(z.B. in .gitignore aufnehmen).
"""

# --- Drucker ---------------------------------------------------------------
# Werte per `lsusb` ermitteln, siehe ANLEITUNG.md Schritt 6.
VENDOR_ID = 0x04b8
PRODUCT_ID = 0x0202

# --- Home Assistant (für Netatmo-Werte) ------------------------------------
HA_BASE_URL = "http://homeassistant.local:8123"
HA_TOKEN = "Hier HA Token"
NETATMO_INDOOR_ENTITY = "Platzhalter für netatmo innen"
NETATMO_OUTDOOR_ENTITY = "Platzhalter für netatmo außen"

# --- Wetter (DWD via Bright Sky, kein API-Key nötig) ------------------------
WEATHER_LAT = 53.90
WEATHER_LON = 10.00
WEATHER_LOCATION_NAME = "Hamburg"

# --- Fritz!Box (für WLAN-Gäste-QR) ------------------------------------------
FRITZBOX_ADDRESS = "192.168.178.1"
FRITZBOX_USER = "Platzhalter Fritzbox User"
FRITZBOX_PASSWORD = "Platzhalter Fritzbox Passwort"
WIFI_QR_AUTH_TYPE = "WPA"  # WPA deckt WPA/WPA2/WPA3-Mixed ab; "nopass" für offene Netze

# --- Flask-Session (für CSRF-Schutz der Web-UI) -----------------------------
# Generieren mit: python3 -c "import secrets; print(secrets.token_hex(32))"
# Pflichtfeld: der Server verweigert den Start, solange hier noch der
# Platzhalter oder ein leerer String steht.
SECRET_KEY = "Hier zufälligen Wert per secrets.token_hex(32) einsetzen"

# --- SSH-Direktabfragen für Systembericht (CPU/RAM/LXC/Docker/Backups) ------
# Setzt passwortlosen SSH-Zugriff vom ReceiptPi-Pi zu diesen Hosts voraus
# (siehe ANLEITUNG.md, Abschnitt 15) - dieselben Befehle wie im Termux Lab
# Commander (scripts/termux-lab-commander.sh), kein Zabbix nötig.
# Beispiel-IPs unten - durch die tatsächlichen Adressen in deinem Netz ersetzen.
SSH_PROXMOX_HOST = "192.168.1.10"
SSH_PROXMOX_USER = "root"
SSH_PINAS_HOST = "192.168.1.11"
SSH_PINAS_USER = "root"
SSH_PBS_HOST = "192.168.1.12"
SSH_PBS_USER = "root"

# --- API-Absicherung ---------------------------------------------------------
# Falls gesetzt (nicht leer), verlangen alle /print/*-Endpunkte
# (status, list, image, wifi, weather) den Header "X-Api-Token: <wert>".
# Leer lassen ("") deaktiviert den Schutz komplett (nur fürs rein interne
# Netz empfohlen, siehe ANLEITUNG.md).
API_TOKEN = ""

# --- Laufzeitdaten der Watch-Scripts -----------------------------------------
# FHS-konformer Pfad statt im Projekt-/Quellcode-Verzeichnis - muss vorher
# angelegt und dem Service-User gehören (siehe ANLEITUNG.md).
STATE_DIR = "/var/lib/receiptpi"

# --- GitHub-Star-Watch -------------------------------------------------------
GITHUB_OWNER = "YourGitHubUsername"
GITHUB_REPO = "YourRepo"
