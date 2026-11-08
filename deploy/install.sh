#!/bin/bash
# Installs ReceiptPi from this source tree on Raspberry Pi OS Lite (Debian
# trixie, arm64), either on a running system or in a chroot while an image
# is being built.
#
#   sudo ./deploy/install.sh
#
# Layout:
#   /opt/receiptpi                    relative symlink to the active release
#   /opt/receiptpi-releases/<version> code, owned by root, read-only for the
#                                     service; the release before keeps its
#                                     directory for going back
#   /opt/receiptpi-venvs/<key>        Python environment from
#                                     deploy/requirements.lock, shared by the
#                                     releases with the same lock file; each
#                                     release links to its own as "venv"
#   /etc/receiptpi/config.py          configuration (root:receiptpi, 0640),
#                                     created on the first start by
#                                     receiptpi-firstboot.service
#   /var/lib/receiptpi                data, created by systemd (StateDirectory=)
#   /var/lib/receiptpi-update         status, backups and downloads of the
#                                     updater (deploy/update/updater.py), and
#                                     the copy of it that runs on boot
#
# A code directory at /opt/receiptpi from an installation without
# releases is replaced by the symlink.
#
# Safe to run again: every step checks or replaces its own result, an
# existing configuration and the data are never touched. No configuration
# and no secret is ever written by this script, so an image built with it
# contains none; each device creates its own on the first start.
set -Eeuo pipefail
trap 'echo "install.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

APP_DIR=/opt/receiptpi
RELEASES_DIR=/opt/receiptpi-releases
VENVS_DIR=/opt/receiptpi-venvs
CONF_DIR=/etc/receiptpi
SERVICE_USER=receiptpi
STATE_DIR=/var/lib/receiptpi
UPDATE_DIR=/var/lib/receiptpi-update
UNIT_DIR=/etc/systemd/system
UDEV_DIR=/etc/udev/rules.d
APT_PACKAGES=(python3 python3-venv libusb-1.0-0 ca-certificates)
# The Fritz!Box guest network timer is installed but not enabled: without
# a Fritz!Box it would only start a Python process every minute to find
# out that no credentials are configured. receiptpi-setup-wifi.service is
# installed as well, but only the image enables it (deploy/image/): it
# opens a Wi-Fi network of its own and must never run where the network
# is set up some other way.
# The nightly update timer is always enabled: the updater itself checks
# whether automatic updates are switched on in the settings.
TIMERS=(receiptpi-github-star.timer receiptpi-storm-warning.timer
        receiptpi-update-check.timer receiptpi-update-auto.timer)
UPDATE_UNITS=(receiptpi-update.path receiptpi-update-recover.service)

log() { echo "==> $*"; }
die() { echo "install.sh: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (sudo $0)"

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
[ -f "$SRC_DIR/app.py" ] && [ -f "$SRC_DIR/deploy/requirements.lock" ] \
    || die "$SRC_DIR is not a ReceiptPi source tree"
# The release directory is replaced, so the source must not live inside
# the installed code.
case "$SRC_DIR/" in
    "$APP_DIR"/*|"$RELEASES_DIR"/*) die "run this from a checkout outside $APP_DIR" ;;
esac
VERSION=$(tr -d '\r\n' < "$SRC_DIR/VERSION")
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$ ]] \
    || die "VERSION '$VERSION' can't name a release directory"
REL_DIR=$RELEASES_DIR/$VERSION

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

log "Code in $REL_DIR"
install -d -m 0755 -o root -g root "$RELEASES_DIR" "$VENVS_DIR"
# Copied next to the release first and renamed into place at the end, so
# files deleted from the source don't linger and a failed run leaves the
# installed release as it was.
STAGE=$RELEASES_DIR/.install-$VERSION
rm -rf "$STAGE"
install -d -m 0755 "$STAGE"
# Excludes everything that is local to a checkout: version control,
# caches, development tools and anything holding a configuration or a
# secret.
tar -C "$SRC_DIR" -cf - \
    --exclude=./.git --exclude=./.github --exclude=./.claude --exclude=./CLAUDE.md \
    --exclude=./venv --exclude='./.venv*' --exclude=./thermo-env --exclude=./.ruff_cache \
    --exclude=__pycache__ --exclude='*.pyc' \
    --exclude=./config.py --exclude=./settings.json --exclude=./settings.json.tmp \
    --exclude=./secret.key \
    . | tar -C "$STAGE" --no-same-owner --no-same-permissions -xf -

log "Python environment"
# Named by the updater from the lock file and the Python version, so the
# updater finds and reuses it; packages are built for one Python version.
VENV_KEY=$(python3 -I -E -s "$STAGE/deploy/update/updater.py" venv-key "$STAGE/deploy/requirements.lock")
VENV=$VENVS_DIR/$VENV_KEY
if [ ! -f "$VENV/.receiptpi-complete" ]; then
    rm -rf "$VENV"
    python3 -m venv "$VENV"
    # PIP_CONFIG_FILE=/dev/null ignores /etc/pip.conf: Raspberry Pi OS adds
    # piwheels there, whose wheels are not the files the hashes in the lock
    # file were taken from. Only prebuilt wheels: there is no compiler on
    # the system, and building on a Pi Zero would take hours.
    PIP_CONFIG_FILE=/dev/null "$VENV/bin/pip" install \
        --no-input --disable-pip-version-check --no-cache-dir \
        --require-hashes --only-binary=:all: --no-deps \
        -r "$STAGE/deploy/requirements.lock"
    printf 'python %s\nlock %s\n' "$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')" \
        "$(sha256sum < "$STAGE/deploy/requirements.lock" | cut -d' ' -f1)" > "$VENV/.receiptpi-complete"
fi
# Relative links, so they also resolve in a mounted image.
ln -sfn "../../receiptpi-venvs/$VENV_KEY" "$STAGE/venv"
# The application imports config.py from its own directory.
ln -sfn "$CONF_DIR/config.py" "$STAGE/config.py"
# Bytecode up front: the code directory is read-only for the service, so
# Python could not cache it and would compile every module on each start.
"$VENV/bin/python3" -m compileall -q -x "^$STAGE/(venv/|config\.py$)" "$STAGE"
# Owned by root and readable for everyone, independent of the umask and
# of the modes in the source tree.
chown -R root:root "$STAGE" "$VENV"
chmod -R u=rwX,go=rX "$STAGE" "$VENV"

if [ -d "$REL_DIR" ]; then
    rm -rf "$RELEASES_DIR/.old-$VERSION"
    mv -T "$REL_DIR" "$RELEASES_DIR/.old-$VERSION"
fi
mv -T "$STAGE" "$REL_DIR"
rm -rf "$RELEASES_DIR/.old-$VERSION"

PREVIOUS=""
if [ -L "$APP_DIR" ]; then
    PREVIOUS=$(basename "$(readlink "$APP_DIR")")
elif [ -e "$APP_DIR" ]; then
    # A code directory without releases. Its environment is not kept: the
    # scripts in it name the directory's own path.
    rm -rf "$APP_DIR"
fi
# One rename, so the link is never missing.
rm -f "$APP_DIR.new"
ln -s "receiptpi-releases/$VERSION" "$APP_DIR.new"
mv -T "$APP_DIR.new" "$APP_DIR"

# After a switch to another version, the active release and the one
# before it stay. Installing the same version again keeps every release,
# so the one to go back to is not lost. Environments no kept release
# uses go.
if [ "$PREVIOUS" != "$VERSION" ]; then
    find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 ! -name "$VERSION" ! -name "${PREVIOUS:-$VERSION}" \
        -exec rm -rf {} +
fi
KEEP_VENVS=()
for link in "$RELEASES_DIR"/*/venv; do
    [ -L "$link" ] && KEEP_VENVS+=("$(basename "$(readlink "$link")")")
done
for venv in "$VENVS_DIR"/* "$VENVS_DIR"/.[!.]*; do
    [ -e "$venv" ] || continue
    name=$(basename "$venv")
    case " ${KEEP_VENVS[*]} " in *" $name "*) ;; *) rm -rf "$venv" ;; esac
done

log "Configuration directory $CONF_DIR"
install -d -m 0750 -o root -g "$SERVICE_USER" "$CONF_DIR"

log "Updater directory $UPDATE_DIR"
# Readable for the service group, so the web UI can show the status;
# backups and downloads below it are root's only. The updater kept here
# undoes an interrupted update on boot; it is the one of the release
# installed last.
install -d -m 0750 -o root -g "$SERVICE_USER" "$UPDATE_DIR"
install -m 0644 -o root -g root "$REL_DIR/deploy/update/updater.py" "$UPDATE_DIR/updater.py"

log "systemd units and udev rule"
install -d -m 0755 "$UNIT_DIR" "$UDEV_DIR"
install -m 0644 -o root -g root "$REL_DIR"/deploy/systemd/receiptpi* "$UNIT_DIR"/
install -m 0644 -o root -g root "$REL_DIR/deploy/udev/99-receiptpi-printer.rules" "$UDEV_DIR"/

if [ "$LIVE" -eq 1 ]; then
    systemctl daemon-reload
    udevadm control --reload-rules
    udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=04b8
    systemctl enable receiptpi.service "${UPDATE_UNITS[@]}" "${TIMERS[@]}"
    systemctl restart receiptpi.service
    systemctl start receiptpi-update.path "${TIMERS[@]}"
    log "Done. Web UI: http://$(hostname):5000/setup"
else
    systemctl enable receiptpi.service "${UPDATE_UNITS[@]}" "${TIMERS[@]}"
    log "Done. The services start on the next boot."
fi
