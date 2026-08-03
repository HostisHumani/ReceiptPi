"""Central configuration for the ReceiptPi server and watcher scripts.
Replace placeholders before enabling the corresponding features. Never commit
a populated config.py containing credentials or private network details."""

# --- Printer ---------------------------------------------------------------
# Determine these values with lsusb; see the installation guide.
VENDOR_ID = 0x04b8
PRODUCT_ID = 0x0202

# --- Home Assistant / Netatmo ----------------------------------------------
HA_BASE_URL = "http://homeassistant.local:8123"
HA_TOKEN = "Hier HA Token"
NETATMO_INDOOR_ENTITY = "Platzhalter für netatmo innen"
NETATMO_OUTDOOR_ENTITY = "Platzhalter für netatmo außen"

# --- Weather via Bright Sky ------------------------------------------------
WEATHER_LAT = 53.30
WEATHER_LON = 9.96
WEATHER_LOCATION_NAME = "Jesteburg"

# --- Fritz!Box guest Wi-Fi -------------------------------------------------
FRITZBOX_ADDRESS = "192.168.178.1"
FRITZBOX_USER = "Platzhalter Fritzbox User"
FRITZBOX_PASSWORD = "Platzhalter Fritzbox Passwort"
WIFI_QR_AUTH_TYPE = "WPA"  # WPA covers WPA/WPA2/WPA3 mixed mode; use nopass for open networks.

# --- Flask session / CSRF --------------------------------------------------
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
# Required. ReceiptPi refuses to start while the placeholder or an empty
# value is configured.
SECRET_KEY = "Hier zufälligen Wert per secrets.token_hex(32) einsetzen"

# --- SSH targets for system reports ---------------------------------------
# Requires passwordless SSH access from ReceiptPi to each target host.
# See the installation guide for setup details.
#
# Replace the example addresses with the actual hosts in your network.
SSH_PROXMOX_HOST = "192.168.1.10"
SSH_PROXMOX_USER = "root"
SSH_PINAS_HOST = "192.168.1.11"
SSH_PINAS_USER = "root"
SSH_PBS_HOST = "192.168.1.12"
SSH_PBS_USER = "root"

# --- API protection --------------------------------------------------------
# When non-empty, all /print/* endpoints require X-Api-Token.
#
# Leaving this empty disables API authentication and is only recommended
# for isolated, trusted networks.
API_TOKEN = ""

# --- Watcher state ---------------------------------------------------------
# Store mutable state outside the source tree. Create this directory and
# assign it to the service user before starting ReceiptPi.
STATE_DIR = "/var/lib/receiptpi"

# --- GitHub star watcher ---------------------------------------------------
GITHUB_OWNER = "HostisHumani"
GITHUB_REPO = "HomeLab"
