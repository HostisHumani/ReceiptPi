"""
Pollt die GitHub-API auf neue Stars für ein Repo und druckt bei Zuwachs
eine Statusmeldung über den ReceiptPi-Server (app.py).

Läuft am besten per Cronjob alle paar Minuten, siehe ANLEITUNG.md.
Kein offener Port, kein Webhook nötig - reine Abfrage nach außen.
"""

import json
import os
import sys
import urllib.request

# config.py liegt im Projekt-Wurzelverzeichnis, watchers/ ist eine Ebene
# darunter - daher explizit ins sys.path aufnehmen, sonst schlägt der
# Import fehl, egal von wo aus der Cronjob das Script startet.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config

# ---------------------------------------------------------------------------
GITHUB_OWNER = config.GITHUB_OWNER
GITHUB_REPO = config.GITHUB_REPO
PRINTER_URL = "http://localhost:5000/print/message"
API_TOKEN = getattr(config, "API_TOKEN", "")
STATE_DIR = getattr(config, "STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # Fallback: Projekt-Wurzelverzeichnis, falls STATE_DIR fehlt
STATE_FILE = os.path.join(STATE_DIR, "star_state.json")
# ---------------------------------------------------------------------------


def get_star_count():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    req = urllib.request.Request(url, headers={"User-Agent": "star-watch-script"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data["stargazers_count"]


def load_last_count():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f).get("stars")
        except (json.JSONDecodeError, OSError):
            return None  # beschädigte/leere Datei - wie "kein vorheriger Stand" behandeln
    return None


def save_last_count(count):
    """Schreibt atomar (temp-Datei + os.replace), damit die Datei bei einem
    Stromausfall mitten im Schreiben nicht beschädigt/leer zurückbleibt."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump({"stars": count}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def print_notification(new_total, gained):
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-Api-Token"] = API_TOKEN
    payload = json.dumps({
        "title": "NEUER GITHUB STAR",
        "text": f"{GITHUB_OWNER}/{GITHUB_REPO}\n+{gained} Star(s)\nGesamt: {new_total}",
    }).encode()
    req = urllib.request.Request(PRINTER_URL, data=payload, headers=headers)
    urllib.request.urlopen(req, timeout=10)


def main():
    current = get_star_count()
    last = load_last_count()

    if last is None:
        # Erster Lauf (oder beschädigte State-Datei): nur Baseline speichern
        save_last_count(current)
        print(f"Baseline gesetzt: {current} Stars")
        return

    if current > last:
        gained = current - last
        print_notification(current, gained)
        save_last_count(current)
        print(f"Neuer Star! {last} -> {current}")
    elif current > 0 and current < last:
        # Stars wurden entfernt - gespeicherten Höchststand NICHT absenken,
        # sonst würde ein späteres Wiedererreichen des alten Stands erneut
        # als "neuer" Star gemeldet (z.B. 100 -> 99 -> 100).
        print(f"Stars gesunken ({last} -> {current}), Baseline bleibt {last}")
    # current == last: nichts zu tun, auch kein Schreibzugriff nötig


if __name__ == "__main__":
    main()
