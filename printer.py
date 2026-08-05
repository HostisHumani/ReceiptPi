"""
Printer hardware access: opens the USB connection to the ESC/POS
printer, plus a couple of low-level system helpers (local IP for the
boot greeting module).
"""
import socket

import config
import usb.backend.libusb1
from escpos.exceptions import DeviceNotFoundError
from escpos.printer import Usb

VENDOR_ID = config.VENDOR_ID
PRODUCT_ID = config.PRODUCT_ID


def _open_usb_printer():
    p = Usb(VENDOR_ID, PRODUCT_ID, profile="TM-T88V")
    # Force the "USA" international character set (ESC R 0). Some
    # printers - especially ones previously configured for the German
    # retail market, like ours was - default to a national ISO 646
    # variant that remaps plain ASCII punctuation to national letters:
    # "[" becomes "Ä", "]" becomes "Ü", "\\" becomes "Ö", etc. Without
    # this, printing a literal "[ ]" checkbox comes out as "Ä Ü" instead.
    # Setting USA here ensures plain ASCII always prints as written,
    # regardless of whatever locale the printer's firmware/DIP switches
    # happen to default to.
    p._raw(b"\x1b\x52\x00")
    return p


def get_printer():
    """Opens the connection to the printer. Raises an exception if it's
    unreachable (e.g. not plugged in or wrong IDs).

    Retries once with a freshly created libusb backend on failure.
    pyusb caches its libusb1 backend as a process-wide singleton
    (usb.backend.libusb1._lib_object) for as long as the process runs.
    On this Pi's dwc_otg USB controller, that cached context reliably
    stops seeing the printer after a physical unplug/replug - a brand
    new Python process finds it instantly, but the long-running
    gunicorn worker doesn't, until something forces a fresh context.
    Clearing the cached backend and retrying reproduces that "fresh
    process" behavior without requiring a service restart."""
    try:
        return _open_usb_printer()
    except DeviceNotFoundError:
        usb.backend.libusb1._lib_object = None
        return _open_usb_printer()


def _raw_health_check():
    p = get_printer()
    p.close()


def get_local_ip():
    """Determines the local IP without actually connecting anywhere
    (only connects to ask the OS which interface IP would be used - no
    packet is actually sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"
