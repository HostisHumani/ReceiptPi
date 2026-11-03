#!/bin/bash
# Image-only settings, run inside the chroot of the Raspberry Pi OS image
# after deploy/install.sh:
#
#   chroot <root> /bin/bash <source>/deploy/image/customize.sh
#
# Sets the defaults of the image (hostname, time zone, Wi-Fi country),
# automatic security upgrades and the unit presets for the first boot.
# Raspberry Pi Imager settings applied by cloud-init on the first boot
# take precedence over the hostname, time zone and Wi-Fi country set here.
set -Eeuo pipefail
trap 'echo "customize.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

HOSTNAME=receiptpi
TIMEZONE=Europe/Berlin
WIFI_COUNTRY=DE
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
CMDLINE=/boot/firmware/cmdline.txt
NM_STATE=/var/lib/NetworkManager/NetworkManager.state
export DEBIAN_FRONTEND=noninteractive

log() { echo "==> $*"; }

log "Hostname $HOSTNAME"
echo "$HOSTNAME" > /etc/hostname
sed -i "s/^[[:space:]]*127\.0\.1\.1[[:space:]].*/127.0.1.1\t$HOSTNAME/" /etc/hosts
grep -q "^127\.0\.1\.1[[:space:]]" /etc/hosts || printf '127.0.1.1\t%s\n' "$HOSTNAME" >> /etc/hosts

log "Time zone $TIMEZONE"
[ -f "/usr/share/zoneinfo/$TIMEZONE" ]
ln -sfn "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
echo "$TIMEZONE" > /etc/timezone

log "Wi-Fi country $WIFI_COUNTRY"
# The same two changes raspi-config makes for a Wi-Fi country in a chroot:
# the regulatory domain on the kernel command line, and Wi-Fi switched on
# in NetworkManager, which ships switched off until a country is set.
sed -i -e 's/[[:space:]]*cfg80211\.ieee80211_regdom=[^[:space:]]*//' \
    -e "s/\$/ cfg80211.ieee80211_regdom=$WIFI_COUNTRY/" "$CMDLINE"
if [ -f "$NM_STATE" ]; then
    sed -i 's/^WirelessEnabled=.*/WirelessEnabled=true/' "$NM_STATE"
fi

log "Automatic security upgrades"
apt-get update -q
apt-get install -y -q unattended-upgrades
install -m 0644 -o root -g root "$SRC/apt/20auto-upgrades" "$SRC/apt/50unattended-upgrades" \
    /etc/apt/apt.conf.d/

log "Unit presets for the first boot"
install -d -m 0755 /etc/systemd/system-preset
install -m 0644 -o root -g root "$SRC/80-receiptpi.preset" /etc/systemd/system-preset/

log "Done"
