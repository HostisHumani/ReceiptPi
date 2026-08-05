"""
Gunicorn-Server-Hooks. on_starting() läuft garantiert genau EINMAL im
Master-Prozess, bevor die Worker geforkt werden - unabhängig davon, wie
viele --workers konfiguriert sind. Der Boot-Gruß gehört hierhin statt in
app.py, weil app.py von JEDEM Worker-Prozess einzeln importiert wird und
dort mehrfach drucken würde, sollte --workers jemals versehentlich > 1
gesetzt werden (z.B. durch spätere Config-Änderungen).
"""
import socket

import history_store
from modules.message.routes import _raw_print_message
from printer import get_local_ip


def on_starting(server):
    """Boot-Gruß läuft VOR dem Fork der Worker-Prozesse und damit auch
    vor print_queue.start_worker() - der Druck geht hier deshalb direkt
    per _raw_print_message() raus statt über enqueue_print()/die
    zentrale Queue. Für die History wird deshalb ebenfalls direkt
    history_store.log_job() aufgerufen statt sich auf die sonst übliche
    Logging-Stelle im Worker (_print_worker in print_queue.py) zu
    verlassen."""
    hostname = socket.gethostname()
    try:
        ip = get_local_ip()
        text = f"Hostname: {hostname}\nIP: {ip}\nReceiptPi-Server gestartet"
        _raw_print_message("ONLINE", text)
    except Exception as e:
        # Boot-Gruß ist "nice to have" - ein nicht erreichbarer Drucker
        # darf den Serverstart nicht verhindern.
        server.log.warning(f"Boot-Gruß konnte nicht gedruckt werden: {e}")
        history_store.log_job("boot", hostname, "system", "error", str(e))
        return
    history_store.log_job("boot", f"{hostname} ({ip})", "system", "ok")
