"""
Wi-Fi setup for a device without a network.

Watches NetworkManager. If the device has no network 2 minutes after the
start, or loses it for 10 minutes later on, it opens its own Wi-Fi
"ReceiptPi-XXXX" with a fresh password and has the server print a
receipt with the name, the password and a QR code to join it. The setup
page on that network (portal.py) takes the home network and its
password; the device then joins it and prints its new address. If that
fails, the setup network comes back and a note is printed.

While the setup network is up, the device can't be on another Wi-Fi at
the same time (most adapters have one radio). So every 10 minutes
without activity on the page it closes the setup network for a minute
and lets NetworkManager try the saved networks - after a router outage
the device finds its way back by itself. A cable that is plugged in ends
the setup at once.

Runs as root from receiptpi-setup-wifi.service and only on devices with
/etc/receiptpi/setup-wifi, which the image creates: a device whose
network is set up some other way must never open a network of its own.
Uses only the standard library and the system Python, and writes
nothing to disk; the log goes to the journal and never contains a
password.
"""
import argparse
import ast
import json
import os
import queue
import secrets
import signal
import sys
import time
import urllib.error
import urllib.request

import nm
from portal import Portal, load_texts

MARKER = "/etc/receiptpi/setup-wifi"
CONFIG = "/etc/receiptpi/config.py"
PRINT_URL = "http://127.0.0.1:5000/print/setup-wifi"
# Without 0/o, 1/l/i, so the password can be typed off the receipt.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PASSWORD_LEN = 12
# Time the phone needs to receive the "connecting" page before the setup
# network goes away under it.
PAGE_GRACE_S = 2
# A form submitted less than this long ago postpones the retry of the
# saved networks, so nobody gets cut off while typing.
ACTIVITY_QUIET_S = 120


def log(message):
    print(message, flush=True)


def new_password():
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LEN))


def api_token(path=CONFIG):
    """API_TOKEN from the device configuration, "" if there is none. Read
    with ast instead of importing the file, so nothing in it runs here."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "API_TOKEN"):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return ""
            return value if isinstance(value, str) else ""
    return ""


class ReceiptPrinter:
    """Sends the receipts to the server's print API on localhost."""

    def __init__(self, url=PRINT_URL, config=CONFIG, attempts=3, pause=10, sleep=time.sleep):
        self.url = url
        self.config = config
        self.attempts = attempts
        self.pause = pause
        self.sleep = sleep

    def send(self, payload):
        headers = {"Content-Type": "application/json"}
        token = api_token(self.config)
        if token:
            headers["X-Api-Token"] = token
        data = json.dumps(payload).encode("utf-8")
        for attempt in range(1, self.attempts + 1):
            try:
                request = urllib.request.Request(self.url, data=data, headers=headers)
                with urllib.request.urlopen(request, timeout=60):
                    log(f"receipt printed: {payload['kind']}")
                    return True
            except urllib.error.HTTPError as e:
                log(f"receipt {payload['kind']} refused: HTTP {e.code}")
                return False
            except (urllib.error.URLError, OSError) as e:
                log(f"receipt {payload['kind']}: server not reachable ({e}), attempt {attempt}")
                if attempt < self.attempts:
                    self.sleep(self.pause)
        return False


class Setup:
    def __init__(self, network=nm, printer=None, portal_factory=None, clock=time.monotonic,
                 sleep=time.sleep, boot_grace=120, lost_grace=600, retry_every=600,
                 retry_wait=60, poll=30):
        self.nm = network
        self.printer = printer or ReceiptPrinter()
        self.portal_factory = portal_factory or self._default_portal
        self.clock = clock
        self.sleep = sleep
        self.boot_grace = boot_grace
        self.lost_grace = lost_grace
        self.retry_every = retry_every
        self.retry_wait = retry_wait
        self.poll = poll
        self.requests = queue.Queue()
        self.state = "watch"
        self.started = clock()
        self.ever_online = False
        self.offline_since = None
        self.session = None
        self._texts = None

    def _default_portal(self, networks, on_submit):
        if self._texts is None:
            self._texts = load_texts()
        return Portal(self.nm.HOTSPOT_ADDRESS, 80, networks, on_submit, self._texts)

    # --- one round ---------------------------------------------------------

    def step(self):
        try:
            devs = self.nm.devices()
        except self.nm.NmError as e:
            log(f"NetworkManager not available: {e}")
            return
        if self.state == "watch":
            self._watch(devs)
        else:
            self._in_setup(devs)

    def run(self):
        self.nm.stop_hotspot()  # left over from a previous run that was killed
        while True:
            self.step()
            if self.state == "setup":
                try:
                    request = self.requests.get(timeout=self.poll)
                except queue.Empty:
                    continue
                self.handle_request(*request)
            else:
                self.sleep(self.poll)

    def shutdown(self):
        if self.session:
            self._close_network()
            self.session = None
        self.state = "watch"

    # --- watching ----------------------------------------------------------

    def _watch(self, devs):
        now = self.clock()
        if self.nm.is_online(devs):
            self.ever_online = True
            self.offline_since = None
            return
        if self.offline_since is None:
            # Before the first network the time counts from the start.
            self.offline_since = now if self.ever_online else self.started
        limit = self.lost_grace if self.ever_online else self.boot_grace
        if now - self.offline_since >= limit:
            self._enter_setup(devs)

    def _enter_setup(self, devs):
        device = self.nm.wifi_device(devs)
        if device is None:
            log("no Wi-Fi device NetworkManager manages; setup network not possible")
            self.offline_since = self.clock()  # look again after the next waiting time
            return
        ssid = f"ReceiptPi-{self.nm.mac_suffix(device)}"
        try:
            networks = self.nm.scan(device, own_ssid=ssid)
        except self.nm.NmError as e:
            log(f"scan failed: {e}")
            networks = []
        self.session = {
            "device": device, "ssid": ssid, "password": new_password(), "networks": networks,
            "portal": None, "last_retry": self.clock(), "last_activity": float("-inf"),
        }
        if not self._open_network():
            self.session = None
            self.offline_since = self.clock()
            return
        self.state = "setup"
        log(f"setup network {ssid} is up on {device}, {len(networks)} networks found")
        self.printer.send({"kind": "hotspot", "ssid": ssid, "password": self.session["password"]})

    # --- in setup ----------------------------------------------------------

    def _in_setup(self, devs):
        if self.nm.is_online(devs):
            # A cable was plugged in, or another device reached a network.
            self._finish(devs, network="")
            return
        s = self.session
        now = self.clock()
        if now - s["last_retry"] < self.retry_every or now - s["last_activity"] < ACTIVITY_QUIET_S:
            return
        s["last_retry"] = now
        try:
            saved = self.nm.saved_wifi_profiles()
        except self.nm.NmError:
            saved = []
        if not saved:
            return
        log("trying the saved networks")
        self._close_network()
        deadline = self.clock() + self.retry_wait
        while self.clock() < deadline:
            self.sleep(5)
            try:
                devs = self.nm.devices()
            except self.nm.NmError:
                continue
            if self.nm.is_online(devs):
                self._finish(devs, network="")
                return
        self._open_network()

    def handle_request(self, ssid, password, hidden):
        s = self.session
        if s is None:
            return
        s["last_activity"] = self.clock()
        self.sleep(PAGE_GRACE_S)
        self._close_network()
        log(f"joining the network chosen on the setup page on {s['device']}")
        ok, detail = self.nm.connect(s["device"], ssid, password, hidden=hidden)
        try:
            devs = self.nm.devices()
        except self.nm.NmError:
            devs = []
        if ok and self.nm.is_online(devs):
            self._finish(devs, network=ssid)
            return
        log(f"joining failed: {detail or 'no connection afterwards'}")
        if self._open_network():
            self.printer.send({"kind": "failed", "ssid": s["ssid"], "password": s["password"],
                               "network": ssid})

    # --- helpers -----------------------------------------------------------

    def _open_network(self):
        s = self.session
        try:
            self.nm.start_hotspot(s["device"], s["ssid"], s["password"])
        except self.nm.NmError as e:
            log(f"setup network could not be opened: {e}")
            return False
        try:
            s["portal"] = self.portal_factory(s["networks"], self._submit)
            s["portal"].start()
        except OSError as e:
            log(f"setup page could not be started: {e}")
            s["portal"] = None
        return True

    def _close_network(self):
        s = self.session
        if s and s.get("portal") is not None:
            s["portal"].stop()
            s["portal"] = None
        self.nm.stop_hotspot()

    def _submit(self, ssid, password, hidden):
        self.requests.put((ssid, password, hidden))

    def _finish(self, devs, network):
        if self.session:
            self._close_network()
            self.session = None
        self.state = "watch"
        self.ever_online = True
        self.offline_since = None
        _, address = self.nm.first_ipv4(devs)
        if address:
            log(f"on the network with {address}")
            self.printer.send({"kind": "connected", "network": network,
                               "url": f"http://{address}:5000/"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--boot-grace", type=int, default=120)
    parser.add_argument("--lost-grace", type=int, default=600)
    parser.add_argument("--retry-every", type=int, default=600)
    parser.add_argument("--retry-wait", type=int, default=60)
    parser.add_argument("--poll", type=int, default=30)
    args = parser.parse_args(argv)

    if not os.path.exists(MARKER):
        log(f"{MARKER} missing: Wi-Fi setup is not enabled on this device")
        return 0
    setup = Setup(boot_grace=args.boot_grace, lost_grace=args.lost_grace,
                  retry_every=args.retry_every, retry_wait=args.retry_wait, poll=args.poll)

    def stop(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        setup.run()
    finally:
        setup.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
