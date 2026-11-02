"""
Printer status without printing anything: USB presence from sysfs plus
the USB printer-class request GET_PORT_STATUS (USB Device Class
Definition for Printing Devices 1.1, section 4.2.2), sent as one raw
usbfs control transfer. Used by the MQTT status monitor and by /health
(health_snapshot()).

Deliberately free of python-escpos and pyusb:
  - print_queue.py shares USB_LOCK from here, and print_queue is
    imported (via the message/weather/wifi modules) by the cron
    watchers - a python-escpos import there would create a temp dir on
    every cron run (escpos/capabilities.py calls mkdtemp() at import
    time, even when ESCPOS_CAPABILITIES_PICKLE_DIR is set).
  - escpos.printer.Usb.open() detaches the kernel driver and resets the
    device on every call, which a status check must never do.
  - sysfs and the device node are read fresh on every check, so the
    result never depends on the state of a libusb context.

What a status check does, and what it never does:
  - reads sysfs; opens the device node; claims the printer interface
    for the duration of a single control transfer and releases it
    again. Linux usbfs only lets GET_PORT_STATUS through on a claimed
    interface (drivers/usb/core/devio.c: check_ctrlrecip() ->
    checkintf()); an explicit claim avoids the kernel's implicit claim
    and its log warning.
  - never: bulk transfers, device reset, kernel-driver detach, retries.
    An interface held by a kernel driver (usblp) or by another process
    simply yields "unknown".
  - only while USB_LOCK is free, i.e. never while this process is
    running a print job (the print worker holds the lock around every
    job).
"""
import ctypes
import errno
import fcntl
import glob
import os
import struct
import threading
import time

import config

# Held by the print worker around every job (see print_queue.py) and taken
# without blocking by read_port_status() - a status check is skipped
# rather than ever delaying or interleaving with a print job.
USB_LOCK = threading.Lock()

SYSFS_USB_DEVICES = "/sys/bus/usb/devices"
USB_CLASS_PRINTER = 0x07

# struct usbdevfs_ctrltransfer from include/uapi/linux/usbdevice_fs.h:
# u8 bRequestType, u8 bRequest, u16 wValue, u16 wIndex, u16 wLength,
# u32 timeout (ms), void *data - native alignment.
_CTRL_FMT = "@BBHHHIP"


def _ioc(direction, nr, size):
    """_IOC() from asm-generic/ioctl.h for the usbfs ioctl type 'U'."""
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


_IOC_WRITE, _IOC_READ = 1, 2
USBDEVFS_CONTROL = _ioc(_IOC_READ | _IOC_WRITE, 0, struct.calcsize(_CTRL_FMT))
USBDEVFS_CLAIMINTERFACE = _ioc(_IOC_READ, 15, struct.calcsize("I"))
USBDEVFS_RELEASEINTERFACE = _ioc(_IOC_READ, 16, struct.calcsize("I"))

# GET_PORT_STATUS: class request, device-to-host, recipient interface.
_REQ_TYPE_CLASS_INTERFACE_IN = 0xA1
_GET_PORT_STATUS = 1
_CONTROL_TIMEOUT_MS = 500

# Status byte bits (USB printer class 1.1, table 3). Bits 7-6 and 2-0
# are reserved.
PORT_PAPER_EMPTY = 0x20
PORT_SELECT = 0x10
PORT_NOT_ERROR = 0x08

# Printer states as published to MQTT/shown in the UI.
STATE_READY = "ready"
STATE_PAPER_OUT = "paper_out"
STATE_ERROR = "error"
STATE_OFFLINE = "offline"
STATE_UNKNOWN = "unknown"
STATES = (STATE_READY, STATE_PAPER_OUT, STATE_ERROR, STATE_OFFLINE, STATE_UNKNOWN)
PROBLEM_STATES = (STATE_PAPER_OUT, STATE_ERROR)


def _read_attr(path):
    with open(path, encoding="ascii") as f:
        return f.read().strip()


def find_printer(vendor_id=None, product_id=None, sysfs_root=SYSFS_USB_DEVICES):
    """Looks the printer up in sysfs only - no USB traffic at all.
    Returns None if no such device is attached (unplugged, switched off),
    else a dict with the usbfs device node, the printer-class interface
    number and the name of the kernel driver bound to that interface
    (None if unbound)."""
    vendor = f"{vendor_id if vendor_id is not None else config.VENDOR_ID:04x}"
    product = f"{product_id if product_id is not None else config.PRODUCT_ID:04x}"
    for dev_dir in glob.glob(os.path.join(sysfs_root, "*")):
        try:
            if _read_attr(os.path.join(dev_dir, "idVendor")) != vendor:
                continue
            if _read_attr(os.path.join(dev_dir, "idProduct")) != product:
                continue
            busnum = int(_read_attr(os.path.join(dev_dir, "busnum")))
            devnum = int(_read_attr(os.path.join(dev_dir, "devnum")))
        except (OSError, ValueError):
            continue
        name = os.path.basename(dev_dir)
        interface = None
        driver = None
        for intf_dir in sorted(glob.glob(os.path.join(sysfs_root, f"{name}:*"))):
            try:
                if int(_read_attr(os.path.join(intf_dir, "bInterfaceClass")), 16) != USB_CLASS_PRINTER:
                    continue
                interface = int(_read_attr(os.path.join(intf_dir, "bInterfaceNumber")), 16)
            except (OSError, ValueError):
                continue
            driver_link = os.path.join(intf_dir, "driver")
            driver = os.path.basename(os.readlink(driver_link)) if os.path.islink(driver_link) else None
            break
        return {
            "devnode": f"/dev/bus/usb/{busnum:03d}/{devnum:03d}",
            "interface": interface,
            "driver": driver,
        }
    return None


def _control_in(fd, request_type, request, value, index, length):
    buf = ctypes.create_string_buffer(length)
    header = struct.pack(_CTRL_FMT, request_type, request, value, index, length,
                         _CONTROL_TIMEOUT_MS, ctypes.addressof(buf))
    received = fcntl.ioctl(fd, USBDEVFS_CONTROL, bytearray(header))
    return buf.raw[:received]


def read_port_status(device):
    """One GET_PORT_STATUS on the device returned by find_printer().
    Returns {"result": ..., "raw": int or None, "detail": str}:
      "ok"            raw holds the status byte
      "busy"          a print job holds USB_LOCK, or another process
                      holds the interface (EBUSY)
      "driver_bound"  a kernel driver (usblp) owns the interface - left
                      alone, nothing is detached
      "no_interface"  no printer-class interface found in sysfs
      "io_error"      anything else (detail: errno name)"""
    if device.get("interface") is None:
        return {"result": "no_interface", "raw": None, "detail": ""}
    if device.get("driver"):
        return {"result": "driver_bound", "raw": None, "detail": device["driver"]}
    if not USB_LOCK.acquire(blocking=False):
        return {"result": "busy", "raw": None, "detail": "print job running"}
    try:
        fd = os.open(device["devnode"], os.O_RDWR | os.O_CLOEXEC)
    except OSError as e:
        USB_LOCK.release()
        return {"result": "io_error", "raw": None, "detail": errno.errorcode.get(e.errno, str(e.errno))}
    iface = struct.pack("I", device["interface"])
    try:
        try:
            fcntl.ioctl(fd, USBDEVFS_CLAIMINTERFACE, iface)
        except OSError as e:
            if e.errno == errno.EBUSY:
                return {"result": "busy", "raw": None, "detail": "interface claimed elsewhere"}
            return {"result": "io_error", "raw": None, "detail": errno.errorcode.get(e.errno, str(e.errno))}
        try:
            data = _control_in(fd, _REQ_TYPE_CLASS_INTERFACE_IN, _GET_PORT_STATUS, 0, device["interface"], 1)
        except OSError as e:
            return {"result": "io_error", "raw": None, "detail": errno.errorcode.get(e.errno, str(e.errno))}
        finally:
            try:
                fcntl.ioctl(fd, USBDEVFS_RELEASEINTERFACE, iface)
            except OSError:
                pass  # closing the descriptor below releases the claim anyway
        if len(data) != 1:
            return {"result": "io_error", "raw": None, "detail": f"{len(data)} status bytes"}
        return {"result": "ok", "raw": data[0], "detail": ""}
    finally:
        os.close(fd)
        USB_LOCK.release()


def classify_port_status(raw):
    """Maps a GET_PORT_STATUS byte to a printer state. Paper empty is
    checked first: it is the more specific of the two conditions, so it
    wins when a printer reports both. The class spec has no bit for an
    open cover - a printer can only signal it through "not error", so an
    open cover is reported as STATE_ERROR rather than guessed at."""
    if raw & PORT_PAPER_EMPTY:
        return STATE_PAPER_OUT
    if not raw & PORT_NOT_ERROR or not raw & PORT_SELECT:
        return STATE_ERROR
    return STATE_READY


# One /health result is shared by every caller for this long: the web UI
# polls /health every 60 s from each open tab, and external monitors may
# poll it too; a single sysfs scan plus at most one GET_PORT_STATUS per
# window answers all of them.
HEALTH_CACHE_S = 10

_health_lock = threading.Lock()
_health_cache = None  # (time.monotonic() of the check, snapshot)


def health_snapshot(now=None):
    """Printer state for /health: {"present": bool, "state": one of
    STATES}. Uses find_printer() and read_port_status() only, so it never
    queues a print job and never opens the printer through python-escpos
    (whose open() resets the device). "present" means the printer is
    attached and switched on; the state may still be "unknown" (kernel
    driver bound, print job running, I/O error).

    Cached for HEALTH_CACHE_S. Concurrent callers wait on the lock for
    the one running the check instead of checking in parallel."""
    global _health_cache
    with _health_lock:
        now = time.monotonic() if now is None else now
        if _health_cache is not None and now - _health_cache[0] < HEALTH_CACHE_S:
            return _health_cache[1]
        snapshot = _check_health()
        _health_cache = (now, snapshot)
        return snapshot


def _check_health():
    device = find_printer()
    if device is None:
        return {"present": False, "state": STATE_OFFLINE}
    result = read_port_status(device)
    if result["result"] == "ok":
        return {"present": True, "state": classify_port_status(result["raw"])}
    return {"present": True, "state": STATE_UNKNOWN}


class PrinterStatusMonitor:
    """Turns periodic sysfs/port-status samples into one debounced
    printer state. Pure logic around two injectable probes, so it runs
    the same in the service and in tests.

    tick(now) is meant to run every PRESENCE_INTERVAL_S: it always
    checks presence (sysfs only) and sends GET_PORT_STATUS at most every
    PORT_INTERVAL_S - or right away when force_port is set (after a
    print job) or when the device just reappeared.

    "offline" is only reported once the device has been missing for
    OFFLINE_AFTER_S, so a short USB dropout doesn't flip the state. A
    returning device is checked immediately. Results that say nothing
    about the printer itself (a print job holds the lock) keep the last
    state; a kernel driver owning the interface or repeated I/O errors
    give "unknown", which is never reported as a problem."""

    PRESENCE_INTERVAL_S = 5
    PORT_INTERVAL_S = 15
    OFFLINE_AFTER_S = 10
    IO_ERRORS_BEFORE_UNKNOWN = 2

    def __init__(self, find=None, read_port=None):
        self._find = find or find_printer
        self._read_port = read_port or read_port_status
        self.state = STATE_UNKNOWN
        self.raw = None
        self.detail = ""
        self._absent_since = None
        self._last_port_check = None
        self._io_errors = 0
        self._seen = False

    def tick(self, now, force_port=False):
        """Returns True if the state changed."""
        before = (self.state, self.raw)
        device = self._find()
        if device is None:
            if self._absent_since is None:
                self._absent_since = now
            if now - self._absent_since >= self.OFFLINE_AFTER_S or not self._seen:
                self._set(STATE_OFFLINE, None, "")
            self._seen = True
            return (self.state, self.raw) != before

        reappeared = self._absent_since is not None
        self._absent_since = None
        self._seen = True
        due = self._last_port_check is None or now - self._last_port_check >= self.PORT_INTERVAL_S
        if force_port or reappeared or due:
            self._last_port_check = now
            result = self._read_port(device)
            kind = result["result"]
            if kind == "ok":
                self._io_errors = 0
                self._set(classify_port_status(result["raw"]), result["raw"], "")
            elif kind == "busy":
                if self.state == STATE_OFFLINE:
                    self._set(STATE_UNKNOWN, None, result["detail"])
            elif kind == "io_error":
                self._io_errors += 1
                if self._io_errors >= self.IO_ERRORS_BEFORE_UNKNOWN or self.state == STATE_OFFLINE:
                    self._set(STATE_UNKNOWN, None, result["detail"])
            else:
                self._set(STATE_UNKNOWN, None, result["detail"])
        return (self.state, self.raw) != before

    def _set(self, state, raw, detail):
        self.state = state
        self.raw = raw
        self.detail = detail
