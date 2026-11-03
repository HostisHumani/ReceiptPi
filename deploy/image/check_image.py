"""
Checks a ReceiptPi image before it is packed. The build stops if any
check fails.

  check_image.py root <mounted root> --base-state DIR --apt-config FILE \\
      --expected-apt-config FILE
  check_image.py image <image file> --base-mbr FILE

"root" checks the mounted root file system (with the boot partition at
boot/firmware) against the state recorded from the base image before it
was changed. "image" checks the partition table of the finished, shrunk
image against the master boot record of the base image.

Prints one PASS or FAIL line per check and a RESULT line. Exit status 1
if any check failed. Matches of the secret scan are reported by kind and
path only, never by content.

Only the standard library is used, so it runs with any Python 3 on the
build host.
"""
import argparse
import os
import re
import struct
import sys

RESULTS = []

# The units of the base image that do the work of the first boot, and
# ours. They must still be enabled after the image was changed.
REQUIRED_ENABLED = [
    "cloud-init.target.wants/cloud-init-local.service",
    "cloud-init.target.wants/cloud-init-main.service",
    "cloud-init.target.wants/cloud-init-network.service",
    "cloud-init.target.wants/cloud-config.service",
    "cloud-init.target.wants/cloud-final.service",
    "sysinit.target.wants/rpi-resize.service",
    "sysinit.target.wants/regenerate_ssh_host_keys.service",
    "ssh.service.wants/sshd-keygen.service",
    "multi-user.target.wants/receiptpi.service",
    "timers.target.wants/receiptpi-github-star.timer",
    "timers.target.wants/receiptpi-storm-warning.timer",
    "timers.target.wants/receiptpi-update-check.timer",
    "timers.target.wants/apt-daily.timer",
    "timers.target.wants/apt-daily-upgrade.timer",
]
NOT_ENABLED = ["receiptpi-wifi-watch.timer", "ssh.service", "sshd.service", "ssh.socket"]

PRESET_LINES = [
    "enable receiptpi.service",
    "enable receiptpi-github-star.timer",
    "enable receiptpi-storm-warning.timer",
    "enable receiptpi-update-check.timer",
    "disable receiptpi-wifi-watch.timer",
    "disable ssh.service",
]

SECRET_PATTERN = re.compile(
    rb"(?P<private_key>-----BEGIN [A-Z ]*PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{40,})"
    rb"|(?P<github_token>ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})"
    rb"|(?P<secret_key>SECRET_KEY *= *.[0-9a-f]{32,})")
SCAN_DIRS = ["etc", "root", "home", "opt", "srv", "usr/local", "var/lib", "boot/firmware"]

HOSTNAME = "receiptpi"
TIMEZONE = "Europe/Berlin"
WIFI_COUNTRY = "DE"


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})"
    print(line)


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def child_entries(path):
    try:
        return sorted(os.listdir(path))
    except FileNotFoundError:
        return []


def enabled_units(root):
    """Every symlink below etc/systemd/system, as path relative to it."""
    base = os.path.join(root, "etc/systemd/system")
    found = set()
    for dirpath, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                found.add(os.path.relpath(path, base))
    return found


def scan_secrets(root):
    """Yields (kind, path) for every match below SCAN_DIRS."""
    for top in SCAN_DIRS:
        for dirpath, _dirnames, filenames in os.walk(os.path.join(root, top)):
            for name in filenames:
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                with open(path, "rb") as f:
                    for m in SECRET_PATTERN.finditer(f.read()):
                        yield m.lastgroup, os.path.relpath(path, root)


def secret_files(root):
    """Files that hold a secret by their name or place."""
    hits = []
    for rel in ("etc/receiptpi/config.py",):
        if os.path.lexists(os.path.join(root, rel)):
            hits.append(rel)
    for top in ("opt/receiptpi", "etc/receiptpi", "var/lib/receiptpi"):
        for dirpath, _dirnames, filenames in os.walk(os.path.join(root, top)):
            rel_dir = os.path.relpath(dirpath, root)
            if "/site-packages/" in rel_dir + "/":
                continue
            for name in filenames:
                rel = os.path.join(rel_dir, name)
                # The only config.py allowed there is the symlink to /etc.
                secret_name = name in ("secret.key", "auth.db", "settings.json", "config.py")
                if secret_name and rel != "opt/receiptpi/config.py":
                    hits.append(rel)
    homes = ["root"] + [os.path.join("home", h) for h in child_entries(os.path.join(root, "home"))]
    for home in homes:
        for name in child_entries(os.path.join(root, home, ".ssh")):
            hits.append(os.path.join(home, ".ssh", name))
    for name in child_entries(os.path.join(root, "etc/ssl/private")):
        hits.append(os.path.join("etc/ssl/private", name))
    return sorted(set(hits))


def check_root(args):
    root = args.root
    state = args.base_state

    # Configuration and data are created on the device, never in the image.
    config_link = os.path.join(root, "opt/receiptpi/config.py")
    check("no /etc/receiptpi/config.py",
          not os.path.lexists(os.path.join(root, "etc/receiptpi/config.py")))
    check("/opt/receiptpi/config.py is only the symlink to /etc/receiptpi/config.py",
          os.path.islink(config_link) and os.readlink(config_link) == "/etc/receiptpi/config.py")
    data = child_entries(os.path.join(root, "var/lib/receiptpi"))
    check("nothing in /var/lib/receiptpi", not data, ", ".join(data))

    keys = [n for n in child_entries(os.path.join(root, "etc/ssh")) if n.startswith("ssh_host_")]
    check("no SSH host keys", not keys, ", ".join(keys))

    # "uninitialized" (or no file) marks the first boot, which runs the
    # resize and SSH key services; an empty file would not (machine-id(5)).
    machine_id = os.path.join(root, "etc/machine-id")
    content = read(machine_id) if os.path.lexists(machine_id) else None
    check("/etc/machine-id missing or 'uninitialized'",
          content in (None, "uninitialized\n"), repr(content)[:40])
    dbus_id = os.path.join(root, "var/lib/dbus/machine-id")
    check("no /var/lib/dbus/machine-id file",
          not os.path.lexists(dbus_id) or os.path.islink(dbus_id))

    profiles = child_entries(os.path.join(root, "etc/NetworkManager/system-connections"))
    check("no NetworkManager connection profiles", not profiles, ", ".join(profiles))

    homes = ["root"] + [os.path.join("home", h) for h in child_entries(os.path.join(root, "home"))]
    histories = [os.path.join(h, n) for h in homes for n in (".bash_history", ".python_history")
                 if os.path.lexists(os.path.join(root, h, n))]
    check("no shell or Python history", not histories, ", ".join(histories))
    caches = [os.path.join(h, ".cache/pip") for h in homes + ["var/lib/receiptpi"]
              if os.path.lexists(os.path.join(root, h, ".cache/pip"))]
    check("no pip cache", not caches, ", ".join(caches))

    names = secret_files(root)
    check("no files with secret names", not names, ", ".join(names[:10]))
    matches = list(scan_secrets(root))
    check("no private keys or tokens in file contents", not matches,
          ", ".join(f"{path}: {kind}" for kind, path in matches[:10]))

    # No account can log in with a password from the image.
    with_password = [line.split(":")[0] for line in read(os.path.join(root, "etc/shadow")).splitlines()
                     if line.count(":") >= 2 and line.split(":")[1].startswith("$")]
    check("no account with a password", not with_password, ", ".join(with_password))

    leftovers = [p for p in ("usr/sbin/policy-rc.d", "var/tmp/receiptpi-src")
                 if os.path.lexists(os.path.join(root, p))]
    check("no build leftovers", not leftovers, ", ".join(leftovers))
    resolv = os.path.join(root, "etc/resolv.conf")
    check("/etc/resolv.conf as in the base image",
          os.path.lexists(resolv) and read(resolv, "rb") == read(os.path.join(state, "resolv.conf"), "rb"))

    enabled = enabled_units(root)
    base_enabled = set(read(os.path.join(state, "enabled-units.txt")).split())
    lost = sorted(base_enabled - enabled)
    check("every unit enabled in the base image is still enabled", not lost, ", ".join(lost))
    for unit in REQUIRED_ENABLED:
        check(f"enabled: {unit}", unit in enabled)
    for unit in NOT_ENABLED:
        hits = sorted(u for u in enabled if os.path.basename(u) == unit)
        check(f"not enabled: {unit}", not hits, ", ".join(hits))
    check("receiptpi-firstboot.service installed and wanted by receiptpi.service",
          os.path.isfile(os.path.join(root, "etc/systemd/system/receiptpi-firstboot.service"))
          and "receiptpi-firstboot.service" in read(os.path.join(root, "etc/systemd/system/receiptpi.service")))
    preset = os.path.join(root, "etc/systemd/system-preset/80-receiptpi.preset")
    preset_lines = read(preset).splitlines() if os.path.isfile(preset) else []
    check("first-boot presets installed", all(line in preset_lines for line in PRESET_LINES))

    cmdline = read(os.path.join(root, "boot/firmware/cmdline.txt")).split()
    base_cmdline = read(os.path.join(state, "cmdline.txt")).split()
    regdom = f"cfg80211.ieee80211_regdom={WIFI_COUNTRY}"
    check("cmdline.txt keeps the base arguments, including 'resize'",
          [a for a in cmdline if not a.startswith("cfg80211.ieee80211_regdom=")] == base_cmdline
          and "resize" in cmdline)
    check(f"cmdline.txt sets Wi-Fi country {WIFI_COUNTRY}", regdom in cmdline)

    check(f"hostname {HOSTNAME}", read(os.path.join(root, "etc/hostname")).strip() == HOSTNAME)
    check(f"/etc/hosts maps 127.0.1.1 to {HOSTNAME}",
          any(line.split()[:2] == ["127.0.1.1", HOSTNAME]
              for line in read(os.path.join(root, "etc/hosts")).splitlines()))
    localtime = os.path.join(root, "etc/localtime")
    check(f"time zone {TIMEZONE}",
          read(os.path.join(root, "etc/timezone")).strip() == TIMEZONE
          and os.path.islink(localtime)
          and os.readlink(localtime) == f"/usr/share/zoneinfo/{TIMEZONE}")

    status = read(os.path.join(root, "var/lib/dpkg/status"))
    check("unattended-upgrades installed",
          re.search(r"^Package: unattended-upgrades\nStatus: install ok installed$", status, re.MULTILINE))
    actual = read(args.apt_config).splitlines()
    expected = read(args.expected_apt_config).splitlines()
    check("upgrade settings equal the reference", actual == expected,
          f"{len(actual)} lines, expected {len(expected)}")


def partitions(mbr):
    """(type, start, sectors) of the four primary entries."""
    entries = []
    for i in range(4):
        entry = mbr[446 + 16 * i: 462 + 16 * i]
        start, sectors = struct.unpack_from("<II", entry, 8)
        entries.append((entry[4], start, sectors))
    return entries


def check_image(args):
    size = os.path.getsize(args.image)
    with open(args.image, "rb") as f:
        mbr = f.read(512)
        base = read(args.base_mbr, "rb")[:512]
        new, old = partitions(mbr), partitions(base)
        check("MBR signature", mbr[510:512] == b"\x55\xaa")
        check("disk identifier as in the base image", mbr[440:444] == base[440:444],
              f"0x{struct.unpack_from('<I', mbr, 440)[0]:08x}")
        check("boot partition unchanged", mbr[446:462] == base[446:462])
        ptype, start, sectors = new[1]
        check("root partition: type and start unchanged", (ptype, start) == old[1][:2])
        check("root partition ends at the end of the image", (start + sectors) * 512 == size,
              f"{size} bytes")
        f.seek(start * 512 + 1024)
        superblock = f.read(1024)
    magic = struct.unpack_from("<H", superblock, 56)[0]
    blocks = struct.unpack_from("<I", superblock, 4)[0]
    block_size = 1024 << struct.unpack_from("<I", superblock, 24)[0]
    check("ext4 file system fits into the root partition",
          magic == 0xEF53 and blocks * block_size <= sectors * 512,
          f"{blocks * block_size} of {sectors * 512} bytes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    p_root = sub.add_parser("root")
    p_root.add_argument("root")
    p_root.add_argument("--base-state", required=True)
    p_root.add_argument("--apt-config", required=True)
    p_root.add_argument("--expected-apt-config", required=True)
    p_image = sub.add_parser("image")
    p_image.add_argument("image")
    p_image.add_argument("--base-mbr", required=True)
    args = parser.parse_args(argv)

    if args.mode == "root":
        check_root(args)
    else:
        check_image(args)
    failed = RESULTS.count(False)
    print(f"RESULT: {RESULTS.count(True)} PASS, {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
