"""
NetworkManager access for the Wi-Fi setup, through nmcli.

Every call runs nmcli with a fixed argument list (no shell) and a
timeout. Lists use the terse format with escaping (-t -e yes), so network
names with colons or backslashes are read correctly. The setup network
exists in memory only ("save no"): neither it nor its password is ever
written to the SD card. The profile for the home network is stored by
NetworkManager itself, as for any network joined with nmcli.

Passwords are passed on the nmcli command line, which other local
accounts could read from /proc for the few seconds the call runs. nmcli
has no other way to hand over a Wi-Fi password without a D-Bus library,
and the device is only in setup while it has no network at all.
"""
import os
import subprocess

NMCLI = "/usr/bin/nmcli"
HOTSPOT = "receiptpi-setup"
HOTSPOT_ADDRESS = "10.42.0.1"
SYS_CLASS_NET = "/sys/class/net"
# Device states that count as "on the network" (see nmcli(1),
# "nmcli device status"). "connected (externally)" is a device
# NetworkManager didn't configure itself but that is up.
_CONNECTED = ("connected", "connected (externally)")


class NmError(Exception):
    """nmcli failed; the message is nmcli's own, at most 300 characters."""


def run(args, timeout=30):
    try:
        result = subprocess.run([NMCLI, *args], capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NmError(str(e)[:300]) from None
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or f"nmcli exit status {result.returncode}"
        raise NmError(message[:300])
    return result.stdout


def split_terse(line):
    """One line of `nmcli -t -e yes` output into its fields: ':' separates
    them, a backslash escapes ':' and '\\' inside a value."""
    fields, current, escaped = [], [], False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def _rows(out, names):
    rows = []
    for line in out.splitlines():
        if line:
            values = split_terse(line)
            if len(values) == len(names):
                rows.append(dict(zip(names, values)))
    return rows


def devices():
    out = run(["-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    return _rows(out, ("device", "type", "state", "connection"))


def is_online(devs):
    """On a network through cable or Wi-Fi, other than the setup network.
    A local network is enough: the web UI only has to be reachable there."""
    return any(d["type"] in ("ethernet", "wifi") and d["state"] in _CONNECTED
               and d["connection"] != HOTSPOT for d in devs)


def wifi_device(devs, sys_class_net=SYS_CLASS_NET):
    """The Wi-Fi device for the setup. A USB adapter comes first: whoever
    plugs one in usually does so because the built-in Wi-Fi doesn't work
    for them. Devices NetworkManager doesn't manage are left alone."""
    names = [d["device"] for d in devs if d["type"] == "wifi" and d["state"] != "unmanaged"]
    usb = [n for n in names
           if "/usb" in os.path.realpath(os.path.join(sys_class_net, n, "device"))]
    return (usb or names or [None])[0]


def mac_suffix(device, sys_class_net=SYS_CLASS_NET):
    """The last four hex digits of the device's MAC address, upper case."""
    try:
        with open(os.path.join(sys_class_net, device, "address"), encoding="ascii") as f:
            mac = f.read().strip()
    except OSError:
        return "0000"
    digits = mac.replace(":", "").upper()
    return digits[-4:] if len(digits) >= 4 else "0000"


def scan(device, own_ssid=None, limit=30):
    """Networks in range, strongest first, each name once."""
    out = run(["-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
               "--rescan", "yes", "ifname", device], timeout=45)
    best = {}
    for row in _rows(out, ("ssid", "signal", "security")):
        ssid = row["ssid"]
        if not ssid or ssid == own_ssid:
            continue
        signal = int(row["signal"]) if row["signal"].isdigit() else 0
        secured = row["security"].strip() not in ("", "--")
        if ssid not in best or signal > best[ssid]["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal, "secured": secured}
    return sorted(best.values(), key=lambda n: (-n["signal"], n["ssid"]))[:limit]


def saved_wifi_profiles():
    out = run(["-t", "-e", "yes", "-f", "NAME,TYPE", "connection", "show"])
    return [r["name"] for r in _rows(out, ("name", "type"))
            if r["type"] == "802-11-wireless" and r["name"] != HOTSPOT]


def start_hotspot(device, ssid, password):
    """WPA2 network with NetworkManager's shared mode: NetworkManager hands
    out addresses with its own dnsmasq and answers DNS for the clients."""
    stop_hotspot()
    run(["connection", "add", "save", "no", "type", "wifi", "ifname", device,
         "con-name", HOTSPOT, "autoconnect", "no", "ssid", ssid,
         "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
         "ipv4.method", "shared", "ipv4.addresses", f"{HOTSPOT_ADDRESS}/24",
         "ipv6.method", "disabled",
         "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn",
         "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp",
         "wifi-sec.psk", password])
    run(["connection", "up", HOTSPOT], timeout=60)


def stop_hotspot():
    for args in (["connection", "down", HOTSPOT], ["connection", "delete", HOTSPOT]):
        try:
            run(args)
        except NmError:
            pass  # not there: nothing to stop


def connect(device, ssid, password, hidden=False, wait=60):
    """Joins the home network and keeps its profile for the next boots.
    Returns (True, "") or (False, nmcli's message). A failed attempt
    leaves no new profile behind; a profile that existed before stays,
    since the network may only be out of reach for the moment."""
    try:
        existed = ssid in saved_wifi_profiles()
    except NmError:
        existed = True  # unknown: rather keep a profile than delete a working one
    try:
        run(["device", "wifi", "rescan", "ifname", device])
    except NmError:
        pass  # a scan right after the hotspot stops can be refused; connect scans as well
    args = ["--wait", str(wait), "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    args += ["ifname", device, "name", ssid]
    if hidden:
        args += ["hidden", "yes"]
    try:
        run(args, timeout=wait + 15)
        return True, ""
    except NmError as e:
        if not existed:
            try:
                run(["connection", "delete", "id", ssid])
            except NmError:
                pass
        return False, str(e)


def ipv4_address(device):
    out = run(["-t", "-f", "IP4.ADDRESS", "device", "show", device])
    for line in out.splitlines():
        _, _, value = line.partition(":")
        if value:
            return value.split("/")[0]
    return None


def first_ipv4(devs):
    """Address of the first device that is on a network, for the receipt."""
    for d in devs:
        if d["type"] in ("ethernet", "wifi") and d["state"] in _CONNECTED and d["connection"] != HOTSPOT:
            try:
                address = ipv4_address(d["device"])
            except NmError:
                continue
            if address:
                return d["device"], address
    return None, None
