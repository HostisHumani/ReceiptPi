"""Printer hardware access and small system-level helpers."""
import socket

from escpos.printer import Usb

import config

VENDOR_ID = config.VENDOR_ID
PRODUCT_ID = config.PRODUCT_ID


def get_printer():
    """Open the configured ESC/POS USB printer connection."""
    return Usb(VENDOR_ID, PRODUCT_ID, profile="TM-T88V")


def _raw_health_check():
    p = get_printer()
    p.close()


def get_local_ip():
    """Determine the local interface address without sending application data."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unbekannt"
