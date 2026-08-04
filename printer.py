"""
Drucker-Hardware-Zugriff: USB-Verbindung zum ESC/POS-Drucker öffnen, plus
ein paar systemnahe Helfer (lokale IP fürs Boot-Gruß-Modul).
"""
import socket

import config
from escpos.printer import Usb

VENDOR_ID = config.VENDOR_ID
PRODUCT_ID = config.PRODUCT_ID


def get_printer():
    """Öffnet die Verbindung zum Drucker. Wirft eine Exception, wenn er
    nicht erreichbar ist (z.B. nicht angeschlossen oder falsche IDs)."""
    return Usb(VENDOR_ID, PRODUCT_ID, profile="TM-T88V")


def _raw_health_check():
    p = get_printer()
    p.close()


def get_local_ip():
    """Ermittelt die lokale IP ohne tatsächliche Verbindung nach außen
    (verbindet nur, um die passende Interface-IP vom Betriebssystem zu
    erfragen - es wird kein Paket wirklich verschickt)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unbekannt"
