"""
Printer hardware access: opens the USB connection to the ESC/POS
printer, plus a couple of low-level system helpers (local IP for the
boot greeting).
"""
import socket
import traceback

import config
from escpos.printer import Usb

VENDOR_ID = config.VENDOR_ID
PRODUCT_ID = config.PRODUCT_ID


class PrinterUnavailableError(Exception):
    """The printer could not be opened (switched off, unplugged, wrong IDs,
    USB error). Carries only the message text: no chained exception and
    no frames that still reference a pyusb device."""


def _release_frames(exc):
    """Clears the local variables of every frame in the tracebacks of exc
    and of the exceptions it was raised from or during.

    python-escpos raises its DeviceNotFoundError inside an except block
    (Usb.open()), which leaves a reference cycle frame -> exception ->
    traceback -> frame. The usb.core.Device objects referenced from those
    frames would then only be released by a later cyclic garbage
    collection, at an arbitrary point in whatever thread happens to
    trigger it. Clearing the frames releases them right here, by
    reference counting."""
    pending, seen = [exc], set()
    while pending:
        e = pending.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        # Frames that are still executing (this function's caller) are
        # skipped by clear_frames().
        traceback.clear_frames(e.__traceback__)
        pending.extend((e.__cause__, e.__context__))


def _close_quietly(p):
    try:
        p.close()
    except Exception:
        pass  # the connection is being given up anyway; the open error is what gets reported


def get_printer():
    """Opens the connection to the printer. Raises PrinterUnavailableError
    with the reason as message if it can't be opened.

    pyusb keeps one libusb context per process for as long as the
    process runs (usb.backend.libusb1._lib_object), and every device
    object refers to it. That context is never replaced or released
    here: libusb must not free a context while a device of it is still
    referenced, and a device object can outlive the call that found it.
    The context is created by the first call in a process - the print
    worker, never the Gunicorn master (see gunicorn.conf.py)."""
    p = Usb(VENDOR_ID, PRODUCT_ID, profile="TM-T88V")
    error = None
    try:
        # Force the "USA" international character set (ESC R 0). Some
        # printers - especially ones previously configured for the German
        # retail market, like ours was - default to a national ISO 646
        # variant that remaps plain ASCII punctuation to national letters:
        # "[" becomes "Ä", "]" becomes "Ü", "\\" becomes "Ö", etc. Without
        # this, printing a literal "[ ]" checkbox comes out as "Ä Ü" instead.
        # Setting USA here ensures plain ASCII always prints as written,
        # regardless of whatever locale the printer's firmware/DIP switches
        # happen to default to. This first write is also what opens the
        # USB connection (python-escpos opens lazily).
        p._raw(b"\x1b\x52\x00")
        return p
    except Exception as e:
        error = str(e) or type(e).__name__
        _release_frames(e)
    # Everything below runs outside the except block on purpose: raised
    # here, the new error has no __context__ leading back to the original
    # exception and its traceback. Its own traceback contains this frame,
    # so p is dropped as well, in case close() failed and it still holds
    # the device.
    _close_quietly(p)
    del p
    raise PrinterUnavailableError(error)


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
