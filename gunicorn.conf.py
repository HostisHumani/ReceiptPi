"""
Gunicorn-Server-Hooks. on_starting() läuft garantiert genau EINMAL im
Master-Prozess, bevor die Worker geforkt werden - unabhängig davon, wie
viele --workers konfiguriert sind. Der Boot-Gruß gehört hierhin statt in
app.py, weil app.py von JEDEM Worker-Prozess einzeln importiert wird und
dort mehrfach drucken würde, sollte --workers jemals versehentlich > 1
gesetzt werden (z.B. durch spätere Config-Änderungen).
"""
import socket

from modules.message.routes import _raw_print_message
from printer import get_local_ip


def on_starting(server):
    try:
        hostname = socket.gethostname()
        ip = get_local_ip()
        text = f"Hostname: {hostname}\nIP: {ip}\nReceiptPi-Server gestartet"
        _raw_print_message("ONLINE", text)
    except Exception as e:
        # Boot-Gruß ist "nice to have" - ein nicht erreichbarer Drucker
        # darf den Serverstart nicht verhindern.
        server.log.warning(f"Boot-Gruß konnte nicht gedruckt werden: {e}")
