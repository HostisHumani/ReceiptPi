"""Demo instance of ReceiptPi for documentation screenshots.

Usage: python -I -B demo_server.py <WORKDIR> <REPO> <LANG> <PORT>

Starts the app from <REPO> with an empty STATE_DIR under <WORKDIR>, a fake
printer and a printer status that always reports "ready". Nothing touches a
real device or the real state. Demo data only (made-up names, documentation
IP addresses).

GET /__demo/seed fills in accounts and demo data; until then the instance is
in its "no admin yet" state, for the setup screenshots.
"""
import json
import os
import secrets
import shutil
import sys
import threading
import time

W, REPO, LANG, PORT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
shutil.rmtree(W, ignore_errors=True)
cfg, state = os.path.join(W, "cfg"), os.path.join(W, "state")
os.makedirs(cfg)
os.makedirs(state, mode=0o750)
with open(os.path.join(REPO, "config.example.py"), encoding="utf-8") as f:
    src = f.read()
src += f"\nSTATE_DIR = {state!r}\nSECRET_KEY = {secrets.token_hex(32)!r}\nAPI_TOKEN = ''\n"
with open(os.path.join(cfg, "config.py"), "w") as f:
    f.write(src)
os.environ["ESCPOS_CAPABILITIES_PICKLE_DIR"] = os.path.join(W, "cache")
os.makedirs(os.path.join(W, "cache"))
sys.path[:0] = [cfg, REPO]

import printer
import printer_status


class FakePrinter:
    def __getattr__(self, name):
        return lambda *a, **k: None


printer.get_printer = lambda: FakePrinter()
printer_status.health_snapshot = lambda now=None: {"present": True, "state": "ready"}

import settings_store

settings_store.update_settings_transaction(lambda s: s.update(language=LANG, theme="warm"))

import app as app_module
import auth_store
import history_store
import lists_store
import pending_store
import user_prefs
import version
from modules.update import routes as update_routes

app = app_module.app
DE = LANG == "de"

# The updater's files in the demo directory: installed like from the
# image, no update run yet, and the update check found the running
# version as the latest release (so no footer notice on every page).
update_routes.UPDATE_DIR = os.path.join(W, "update")
update_routes.UPDATER_UNIT = os.path.join(W, "receiptpi-update.path")
update_routes.UPDATE_CONF = os.path.join(W, "update.conf")
update_routes.APP_LINK = os.path.join(W, "receiptpi")
os.makedirs(update_routes.UPDATE_DIR)
open(update_routes.UPDATER_UNIT, "w").close()
os.makedirs(os.path.join(W, "release"))
os.symlink("release", update_routes.APP_LINK)
_latest = version.format_display(version.CURRENT_VERSION)
with open(os.path.join(state, "update_check.json"), "w") as f:
    json.dump({"latest_version": _latest, "update_available": False,
               "latest_url": f"https://github.com/HostisHumani/ReceiptPi/releases/tag/{_latest}"}, f)

T = {
    "shopping_title": "Einkaufszettel" if DE else "Shopping list",
    "shopping_items": "Milch\nBrot\nÄpfel\nKaffee\nKäse" if DE else "Milk\nBread\nApples\nCoffee\nCheese",
}


def seed():
    admin = auth_store.create_user("alex", "demo-password-1", role="admin", only_if_no_admin=True)
    sam = auth_store.create_user("sam", "demo-password-2", role="user")
    own_a = (admin["id"], admin["created"])
    own_s = (sam["id"], sam["created"])

    def mutate(s):
        s["weather"]["locations"] = {
            ("Zuhause" if DE else "Home"): {"lat": 52.52, "lon": 13.40},
            ("Ferienhaus" if DE else "Cottage"): {"lat": 54.32, "lon": 10.13},
        }
        s["weather"]["default_location"] = "Zuhause" if DE else "Home"
        s["print_rules"].update(max_jobs_per_hour=20, duplicate_window_seconds=60, pending_retention_days=0)
        s["print_rules"]["quiet_hours_rules"] = [
            {"id": "night", "label": "Nacht" if DE else "Night", "enabled": True,
             "days": [0, 1, 2, 3, 4, 5, 6], "start": "22:00", "end": "07:00"},
            {"id": "weekend", "label": "Wochenende" if DE else "Weekend", "enabled": False,
             "days": [5, 6], "start": "07:00", "end": "09:00"},
        ]
        s["github_watch"]["repos"] = [{"owner": "octocat", "repo": "hello-world"}]
        s["system_report"]["hosts"] = [
            {"id": "pve", "name": "Proxmox", "role": "proxmox", "host": "192.0.2.10", "user": "monitor"},
            {"id": "nas", "name": "NAS", "role": "custom", "host": "192.0.2.20", "user": "monitor",
             "command": "uptime && df -h"},
        ]
        s["logos"]["enabled"] = True
        s["logos"]["modules"]["shopping"]["enabled"] = True

    settings_store.update_settings_transaction(mutate)

    jobs = [
        ("shopping", T["shopping_title"] + " (5)", "ui", "ok", "", own_a),
        ("todo", "Wochenende (4)" if DE else "Weekend (4)", "ui", "ok", "", own_s),
        ("message", "Erinnerung" if DE else "Reminder", "ui", "ok", "", own_a),
        ("weather", "Zuhause" if DE else "Home", "api", "ok", "", None),
        ("games", "Sudoku - Mittel" if DE else "Sudoku - Medium", "ui", "ok", "", own_s),
        ("message", "Paket" if DE else "Parcel", "webhook", "blocked",
         "Ruhezeit aktiv: Nacht (22:00-07:00)" if DE else "Quiet hours active: Night (22:00-07:00)", None),
        ("task", "Garagenregal reparieren" if DE else "Fix garage shelf", "ui", "error",
         "Drucker nicht erreichbar" if DE else "Printer unreachable", own_a),
    ]
    for job_type, summary, source, status, detail, owner in jobs:
        history_store.log_job(job_type, summary, source, status, detail, owner=owner)
        time.sleep(0.05)

    pending_store.add("message", "Paket" if DE else "Parcel", "webhook", "blocked",
                      "Ruhezeit aktiv: Nacht (22:00-07:00)" if DE else "Quiet hours active: Night (22:00-07:00)",
                      {"title": "Paket" if DE else "Parcel",
                       "text": "Ist angekommen." if DE else "Has arrived.", "module": "automation"},
                      owner=None)
    pending_store.add("task", "Garagenregal reparieren" if DE else "Fix garage shelf", "ui", "error",
                      "Drucker nicht erreichbar" if DE else "Printer unreachable",
                      {"title": "Garagenregal reparieren" if DE else "Fix garage shelf", "description": "",
                       "items": [], "priority": None, "due_date": None},
                      owner=own_a)

    lists_store.save_draft("shopping", {"title": T["shopping_title"], "items": T["shopping_items"]}, owner=own_a)
    user_prefs.update(admin["id"], {})
    return "ok"


@app.route("/__demo/seed")
def _demo_seed():
    return seed()


from werkzeug.serving import make_server

server = make_server("127.0.0.1", PORT, app, threaded=True)
with open(os.path.join(W, "server.pid"), "w") as f:
    f.write(str(os.getpid()))
print(f"demo server on {PORT}", flush=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.shutdown()
