"""
Central configuration for the ReceiptPi server and the trigger scripts.
Replace the placeholders with real values before using the respective
features. Do NOT commit this file to Git with real values in it (add it
to .gitignore).
"""

# --- Printer -----------------------------------------------------------
# Determine via `lsusb`.
VENDOR_ID = 0x04b8
PRODUCT_ID = 0x0202

# --- Home Assistant (for Netatmo values) --------------------------------
HA_BASE_URL = "http://homeassistant.local:8123"
HA_TOKEN = "Your HA token here"
NETATMO_INDOOR_ENTITY = "Placeholder for netatmo indoor"
NETATMO_OUTDOOR_ENTITY = "Placeholder for netatmo outdoor"

# --- Weather (DWD via Bright Sky, no API key needed) ---------------------
WEATHER_LAT = 52.52
WEATHER_LON = 13.40
WEATHER_LOCATION_NAME = "Berlin"

# --- Fritz!Box (for guest wifi QR) ---------------------------------------
FRITZBOX_ADDRESS = "192.168.178.1"
FRITZBOX_USER = "Placeholder Fritzbox user"
FRITZBOX_PASSWORD = "Placeholder Fritzbox password"
WIFI_QR_AUTH_TYPE = "WPA"  # WPA covers WPA/WPA2/WPA3-mixed; "nopass" for open networks

# --- Flask session (for the web UI's CSRF protection) ---------------------
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
# Required: the server refuses to start as long as this is still the
# placeholder or an empty string.
SECRET_KEY = "Insert a random value here via secrets.token_hex(32)"

# --- Direct SSH queries for the system report (CPU/RAM/LXC/Docker/backups) --
# Requires passwordless SSH access from the ReceiptPi Pi to these hosts -
# the same commands as in the Termux Lab Commander
# (scripts/termux-lab-commander.sh), no Zabbix needed.
# Example IPs below - replace with the actual addresses on your network.
# NOTE: these values are only used ONCE, as a starting point copied into
# settings.json on the very first start (see settings_store.py). After
# that, the web UI (Settings -> System Report) is the actual place to
# manage them - changes here in config.py no longer have any effect.
SSH_PROXMOX_HOST = "192.168.1.10"
SSH_PROXMOX_USER = "root"
SSH_PINAS_HOST = "192.168.1.11"
SSH_PINAS_USER = "root"
SSH_PBS_HOST = "192.168.1.12"
SSH_PBS_USER = "root"

# --- API protection -------------------------------------------------------
# If set (non-empty), all /print/* endpoints (status, list, image, wifi,
# weather, system, automation) require the header "X-Api-Token: <value>".
# Leave empty ("") to disable the protection entirely (recommended only
# for a purely internal network).
API_TOKEN = ""

# --- Runtime data for the watch scripts -----------------------------------
# FHS-compliant path instead of inside the project/source directory -
# must be created beforehand and owned by the service user.
STATE_DIR = "/var/lib/receiptpi"

# --- GitHub star watch -----------------------------------------------------
# List of repos to watch for new stars - each entry gets its own baseline
# and its own print notification. Add as many as you like.
# NOTE: also only used ONCE, as an initial starting point copied into
# settings.json (Settings -> GitHub Watch) - see the note at the SSH
# settings above.
GITHUB_REPOS = [
    {"owner": "your-github-username", "repo": "your-repo"},
]
