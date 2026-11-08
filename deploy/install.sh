#!/bin/bash
# Installs ReceiptPi from this source tree on Raspberry Pi OS Lite (Debian
# trixie, arm64), either on a running system or in a chroot while an image
# is being built.
#
#   sudo ./deploy/install.sh
#
# Layout:
#   /opt/receiptpi            code, owned by root, read-only for the service
#   /opt/receiptpi/venv       Python environment from deploy/requirements.lock
#   /etc/receiptpi/config.py  configuration (root:receiptpi, 0640), created on
#                             the first start by receiptpi-firstboot.service
#   /var/lib/receiptpi        data, created by systemd (StateDirectory=)
#
# Safe to run again: every step checks or replaces its own result, an
# existing configuration and the data are never touched. No configuration
# and no secret is ever written by this script, so an image built with it
# contains none; each device creates its own on the first start.
set -Eeuo pipefail
trap 'echo "install.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

APP_DIR=/opt/receiptpi
CONF_DIR=/etc/receiptpi
SERVICE_USER=receiptpi
STATE_DIR=/var/lib/receiptpi
UNIT_DIR=/etc/systemd/system
UDEV_DIR=/etc/udev/rules.d
APT_PACKAGES=(python3 python3-venv libusb-1.0-0 ca-certificates)
# The Fritz!Box guest network timer is installed but not enabled: without
# a Fritz!Box it would only start a Python process every minute to find
# out that no credentials are configured. receiptpi-setup-wifi.service is
# installed as well, but only the image enables it (deploy/image/): it
# opens a Wi-Fi network of its own and must never run where the network
# is set up some other way.
TIMERS=(receiptpi-github-star.timer receiptpi-storm-warning.timer
        receiptpi-update-check.timer)

log() { echo "==> $*"; }
die() { echo "install.sh: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (sudo $0)"

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
[ -f "$SRC_DIR/app.py" ] && [ -f "$SRC_DIR/deploy/requirements.lock" ] \
    || die "$SRC_DIR is not a ReceiptPi source tree"
# The code directory is emptied before each copy, so the source must not
# live inside it.
case "$SRC_DIR/" in
    "$APP_DIR"/*) die "run this from a checkout outside $APP_DIR" ;;
esac

# Only a booted system gets its services started. In a chroot or a
# container the units are enabled and start on the device's first boot.
LIVE=0
if [ -d /run/systemd/system ] && ! systemd-detect-virt --quiet --chroot; then
    LIVE=1
fi

log "System packages"
missing=()
for pkg in "${APT_PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" \
        || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends "${missing[@]}"
fi

log "Service user $SERVICE_USER"
# A system account without a login shell. Its home is the data
# directory, so ssh keeps known_hosts for the system report there.
if ! getent passwd "$SERVICE_USER" >/dev/null; then
    useradd --system --user-group --home-dir "$STATE_DIR" --no-create-home \
        --shell /usr/sbin/nologin --comment "ReceiptPi service" "$SERVICE_USER"
fi

log "Code in $APP_DIR"
install -d -m 0755 -o root -g root "$APP_DIR"
# Removes what an earlier version installed, so files deleted from the
# source don't linger. The environment is kept and only updated below.
find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name venv -exec rm -rf {} +
# Excludes everything that is local to a checkout: version control,
# caches, development tools and anything holding a configuration or a
# secret.
tar -C "$SRC_DIR" -cf - \
    --exclude=./.git --exclude=./.github --exclude=./.claude --exclude=./CLAUDE.md \
    --exclude=./venv --exclude='./.venv*' --exclude=./thermo-env --exclude=./.ruff_cache \
    --exclude=__pycache__ --exclude='*.pyc' \
    --exclude=./config.py --exclude=./settings.json --exclude=./settings.json.tmp \
    --exclude=./secret.key \
    . | tar -C "$APP_DIR" --no-same-owner --no-same-permissions -xf -

log "Python environment"
PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [ -x "$APP_DIR/venv/bin/python3" ] \
    && [ "$("$APP_DIR/venv/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" != "$PY_VERSION" ]; then
    # Installed packages are built for one Python version.
    rm -rf "$APP_DIR/venv"
fi
[ -x "$APP_DIR/venv/bin/python3" ] || python3 -m venv "$APP_DIR/venv"
# PIP_CONFIG_FILE=/dev/null ignores /etc/pip.conf: Raspberry Pi OS adds
# piwheels there, whose wheels are not the files the hashes in the lock
# file were taken from. Only prebuilt wheels: there is no compiler on the
# system, and building on a Pi Zero would take hours.
PIP_CONFIG_FILE=/dev/null "$APP_DIR/venv/bin/pip" install \
    --no-input --disable-pip-version-check --no-cache-dir \
    --require-hashes --only-binary=:all: --no-deps \
    -r "$APP_DIR/deploy/requirements.lock"
# Bytecode up front: the code directory is read-only for the service, so
# Python could not cache it and would compile every module on each start.
"$APP_DIR/venv/bin/python3" -m compileall -q \
    -x "^$APP_DIR/(venv/|config\.py$)" "$APP_DIR"
# Owned by root and readable for everyone, independent of the umask and
# of the modes in the source tree.
chown -R root:root "$APP_DIR"
chmod -R u=rwX,go=rX "$APP_DIR"

log "Configuration directory $CONF_DIR"
install -d -m 0750 -o root -g "$SERVICE_USER" "$CONF_DIR"
# The application imports config.py from its own directory.
ln -sfn "$CONF_DIR/config.py" "$APP_DIR/config.py"

log "systemd units and udev rule"
install -d -m 0755 "$UNIT_DIR" "$UDEV_DIR"
install -m 0644 -o root -g root "$APP_DIR"/deploy/systemd/receiptpi* "$UNIT_DIR"/
install -m 0644 -o root -g root "$APP_DIR/deploy/udev/99-receiptpi-printer.rules" "$UDEV_DIR"/

if [ "$LIVE" -eq 1 ]; then
    systemctl daemon-reload
    udevadm control --reload-rules
    udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=04b8
    systemctl enable receiptpi.service "${TIMERS[@]}"
    systemctl restart receiptpi.service
    systemctl start "${TIMERS[@]}"
    log "Done. Web UI: http://$(hostname):5000/setup"
else
    systemctl enable receiptpi.service "${TIMERS[@]}"
    log "Done. The services start on the next boot."
fi
