#!/bin/bash
# Builds the ReceiptPi image from the official Raspberry Pi OS Lite image.
# Runs as root on an arm64 Linux host, so the chroot needs no emulation.
# One step per call, so each step shows up on its own in the CI log:
#
#   build.sh fetch      download the base image, verify both checksums
#   build.sh prepare    grow the root partition, mount, record the base state
#   build.sh customize  install.sh and customize.sh in a chroot, clean up
#   build.sh check      check the mounted root file system
#   build.sh finish     unmount, shrink, check the partitions, compress
#   build.sh unmount    release mounts and the loop device after a failure
#   build.sh summary    print the summary, also as annotations
#
# Everything happens below WORK; the results end up in WORK/out.
#
# With PACKAGE set to an update package (receiptpi-<version>.tar.gz from
# deploy/release/build_package.py), the image installs exactly the files
# of that package instead of the checkout; PACKAGE_VERSION, if set, must
# match its VERSION.
set -Eeuo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
WORK=${WORK:?WORK must point to the build directory}
OUT=$WORK/out
MNT=$WORK/mnt
STATE=$WORK/base-state
IMG=$WORK/receiptpi.img
SRC_IN_IMAGE=/var/tmp/receiptpi-src
# Room for the application, its packages and the apt downloads while
# building. The file system is shrunk again afterwards and grows to the
# card size on the first boot.
GROW_MIB=1024
# Free space left in the shrunk file system, so the first boot has room
# before the file system has grown.
HEADROOM_MIB=64
STEP=${1:-}

summary() { echo "$*" >> "$WORK/summary.txt"; }
fail_note() {
    [ -s "$WORK/first-error.txt" ] || echo "$STEP: line $1: $2" > "$WORK/first-error.txt"
}
die() { echo "build.sh: $*" >&2; fail_note "-" "$*"; exit 1; }
# The checkout belongs to the runner user, git runs as root here.
git_repo() { git -c safe.directory="$REPO" -C "$REPO" "$@"; }

case "$STEP" in
    fetch|prepare|customize|check|finish|unmount|summary) ;;
    *) echo "usage: $0 fetch|prepare|customize|check|finish|unmount|summary" >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "build.sh: must run as root" >&2; exit 1; }
mkdir -p "$WORK/logs" "$OUT"
if [ "$STEP" != summary ]; then
    exec > >(tee -a "$WORK/logs/$STEP.log") 2>&1
    trap 'fail_note "$LINENO" "$BASH_COMMAND"' ERR
fi
echo "### build.sh $STEP"

loop_dev() { cat "$WORK/loop"; }

attach() {
    local dev
    dev=$(losetup --find --show --partscan "$IMG")
    echo "$dev" > "$WORK/loop"
    udevadm settle
    [ -b "${dev}p2" ]
}

detach() {
    [ -s "$WORK/loop" ] || return 0
    losetup -d "$(loop_dev)" || true
    rm -f "$WORK/loop"
}

# e2fsck exits with 1 when it corrected something, which is fine here.
fsck_ok() {
    local rc=0
    e2fsck -fy "$1" || rc=$?
    [ "$rc" -le 1 ]
}

chroot_run() {
    chroot "$MNT" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 "$@"
}

unmount_chroot_fs() {
    local m
    for m in run dev/pts dev sys proc; do
        mountpoint -q "$MNT/$m" && umount "$MNT/$m"
    done
    return 0
}

step_fetch() {
    # shellcheck source=base.env
    . "$REPO/deploy/image/base.env"
    mkdir -p "$WORK/dl"
    curl -fL --retry 3 --no-progress-meter -o "$WORK/dl/base.img.xz" "$BASE_URL"
    local published
    published=$(curl -fsSL --retry 3 "$BASE_URL.sha256" | awk '{print $1}')
    [ "$published" = "$BASE_SHA256" ] || die "published checksum $published differs from base.env"
    echo "$BASE_SHA256  $WORK/dl/base.img.xz" | sha256sum -c -
    xz -dc "$WORK/dl/base.img.xz" > "$IMG"
    rm -f "$WORK/dl/base.img.xz"
    [ "$(stat -c %s "$IMG")" = "$BASE_IMG_SIZE" ] || die "decompressed size differs from base.env"
    echo "$BASE_IMG_SHA256  $IMG" | sha256sum -c -
    mkdir -p "$STATE"
    dd if="$IMG" of="$STATE/mbr.bin" bs=512 count=1 status=none
    summary "base: $(basename "$BASE_URL")"
    summary "base checksums: download and decompressed image OK"
}

step_prepare() {
    truncate -s "+${GROW_MIB}M" "$IMG"
    echo ", +" | sfdisk --no-reread --no-tell-kernel -N 2 "$IMG"
    attach
    local dev
    dev=$(loop_dev)
    fsck_ok "${dev}p2"
    resize2fs "${dev}p2"
    mkdir -p "$MNT"
    mount "${dev}p2" "$MNT"
    mount "${dev}p1" "$MNT/boot/firmware"

    # Reference for the checks: what the base image enables and holds.
    find "$MNT/etc/systemd/system" -type l -printf '%P\n' | sort > "$STATE/enabled-units.txt"
    cp -a "$MNT/etc/resolv.conf" "$STATE/resolv.conf"
    cp -a "$MNT/etc/machine-id" "$STATE/machine-id"
    cp "$MNT/boot/firmware/cmdline.txt" "$STATE/cmdline.txt"
    df -h "$MNT"
}

step_customize() {
    mount -t proc proc "$MNT/proc"
    mount -t sysfs sys "$MNT/sys"
    mount --bind /dev "$MNT/dev"
    mount --bind /dev/pts "$MNT/dev/pts"
    # An empty /run: install.sh then sees no running systemd and only
    # enables the units instead of starting them.
    mount -t tmpfs tmpfs "$MNT/run"
    # Keeps package scripts from starting services inside the chroot.
    printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"
    chmod 755 "$MNT/usr/sbin/policy-rc.d"
    rm -f "$MNT/etc/resolv.conf"
    cp -L /etc/resolv.conf "$MNT/etc/resolv.conf"

    mkdir -p "$MNT$SRC_IN_IMAGE"
    if [ -n "${PACKAGE:-}" ]; then
        # The release's update package, so image and update hold the same files.
        [ -f "$PACKAGE" ] || die "package $PACKAGE not found"
        tar -C "$MNT$SRC_IN_IMAGE" --strip-components=1 --no-same-owner -xzf "$PACKAGE"
    else
        # Only files tracked in git, as a fresh checkout would have them.
        git_repo archive --format=tar HEAD | tar -C "$MNT$SRC_IN_IMAGE" -xf -
    fi
    tr -d '\r\n' < "$MNT$SRC_IN_IMAGE/VERSION" > "$WORK/version"
    if [ -n "${PACKAGE_VERSION:-}" ] && [ "$(cat "$WORK/version")" != "$PACKAGE_VERSION" ]; then
        die "package holds version $(cat "$WORK/version"), expected $PACKAGE_VERSION"
    fi
    if [ -n "${PACKAGE:-}" ]; then
        summary "source: package $(basename "$PACKAGE"), version $(cat "$WORK/version")"
    else
        summary "source: git checkout, version $(cat "$WORK/version")"
    fi
    chroot_run /bin/bash "$SRC_IN_IMAGE/deploy/install.sh"
    chroot_run /bin/bash "$SRC_IN_IMAGE/deploy/image/customize.sh"

    chroot_run env COLUMNS=200 dpkg -l > "$WORK/dpkg-l.txt"
    chroot_run env PIP_CONFIG_FILE=/dev/null /opt/receiptpi/venv/bin/pip freeze > "$WORK/pip-freeze.txt"
    chroot_run apt-config dump | grep -E '^(Unattended-Upgrade|APT::Periodic)' > "$WORK/apt-config.txt"

    # Clean up: caches, logs and everything only the build needed. The SSH
    # host keys and the machine ID are created on the first boot.
    chroot_run apt-get clean
    rm -rf "${MNT:?}$SRC_IN_IMAGE" "$MNT/usr/sbin/policy-rc.d"
    rm -f "$MNT/etc/resolv.conf"
    cp -a "$STATE/resolv.conf" "$MNT/etc/resolv.conf"
    cp -a "$STATE/machine-id" "$MNT/etc/machine-id"
    rm -f "$MNT/var/lib/dbus/machine-id" "$MNT"/etc/ssh/ssh_host_*
    rm -rf "$MNT/root/.cache" "$MNT"/root/.*_history "$MNT"/home/*/.*_history
    find "$MNT/tmp" "$MNT/var/tmp" -mindepth 1 -delete
    find "$MNT/var/log" -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete
    find "$MNT/var/log/journal" -mindepth 1 -delete 2>/dev/null || true
    find "$MNT/var/log" -type f -exec truncate -s 0 {} +
    unmount_chroot_fs
    df -h "$MNT"
}

step_check() {
    local rc=0
    python3 "$REPO/deploy/image/check_image.py" root "$MNT" --base-state "$STATE" \
        --apt-config "$WORK/apt-config.txt" \
        --expected-apt-config "$REPO/deploy/image/apt/apt-config.expected" \
        > "$WORK/check-root.txt" || rc=$?
    cat "$WORK/check-root.txt"
    summary "check root file system: $(tail -n 1 "$WORK/check-root.txt")"
    grep '^FAIL' "$WORK/check-root.txt" | head -n 3 | sed 's/^/  /' >> "$WORK/summary.txt" || true
    return "$rc"
}

step_finish() {
    local dev bs blocks start sectors version name rc=0
    dev=$(loop_dev)
    umount "$MNT/boot/firmware" "$MNT"
    fsck_ok "${dev}p2"
    resize2fs -M "${dev}p2"
    bs=$(dumpe2fs -h "${dev}p2" 2>/dev/null | awk -F: '/^Block size/ {gsub(/ /, "", $2); print $2}')
    blocks=$(dumpe2fs -h "${dev}p2" 2>/dev/null | awk -F: '/^Block count/ {gsub(/ /, "", $2); print $2}')
    blocks=$((blocks + HEADROOM_MIB * 1024 * 1024 / bs))
    resize2fs "${dev}p2" "$blocks"
    fsck_ok "${dev}p2"
    detach

    # Partition end on a 1 MiB boundary, image cut right after it.
    start=$(python3 -c 'import struct, sys
print(struct.unpack_from("<I", open(sys.argv[1], "rb").read(512), 462 + 8)[0])' "$IMG")
    sectors=$(( (blocks * bs / 512 + 2047) / 2048 * 2048 ))
    echo ", $sectors" | sfdisk --no-reread --no-tell-kernel -N 2 "$IMG"
    truncate -s $(( (start + sectors) * 512 )) "$IMG"

    python3 "$REPO/deploy/image/check_image.py" image "$IMG" --base-mbr "$STATE/mbr.bin" \
        > "$WORK/check-image.txt" || rc=$?
    cat "$WORK/check-image.txt"
    summary "check partitions: $(tail -n 1 "$WORK/check-image.txt")"
    [ "$rc" -eq 0 ]

    # The version of what was installed: the package's, or the checkout's.
    version=$(cat "$WORK/version" 2>/dev/null || tr -d '\r\n' < "$REPO/VERSION")
    name=receiptpi-$version-arm64
    summary "image: $name.img, $(stat -c %s "$IMG") bytes, sha256 $(sha256sum < "$IMG" | cut -d' ' -f1)"
    xz -T0 -6 -c "$IMG" > "$OUT/$name.img.xz"
    (cd "$OUT" && sha256sum "$name.img.xz" > SHA256SUMS)
    summary "compressed: $name.img.xz, $(stat -c %s "$OUT/$name.img.xz") bytes, sha256 $(cut -d' ' -f1 "$OUT/SHA256SUMS")"
    {
        echo "# $name"
        echo "# commit $(git_repo rev-parse HEAD)"
        sed -n 's/^BASE_URL=/# base /p' "$REPO/deploy/image/base.env"
        echo
        echo "## dpkg -l"
        cat "$WORK/dpkg-l.txt"
        echo
        echo "## pip freeze (/opt/receiptpi/venv)"
        cat "$WORK/pip-freeze.txt"
    } > "$OUT/$name.manifest.txt"
    cp "$WORK/check-root.txt" "$WORK/check-image.txt" "$OUT/"
    rm -f "$IMG"
}

step_unmount() {
    mountpoint -q "$MNT/boot/firmware" && umount "$MNT/boot/firmware"
    unmount_chroot_fs
    mountpoint -q "$MNT" && umount "$MNT"
    detach
}

# Workflow commands need %, CR and LF escaped in the message.
annotate() {
    local msg=$2
    msg=${msg//'%'/'%25'}
    msg=${msg//$'\r'/'%0D'}
    msg=${msg//$'\n'/'%0A'}
    echo "::$1 title=Image build::$msg"
}

step_summary() {
    local first=""
    [ -s "$WORK/first-error.txt" ] && first=$(cat "$WORK/first-error.txt")
    if [ -n "$first" ]; then
        # The first error message of a tool, from the log of the failed step.
        local step=${first%%:*} line=""
        [ -f "$WORK/logs/$step.log" ] && line=$(grep -m 1 -E \
            'failed at line|^E: |[Ee]rror|FAIL |FAILED|No space|cannot|not found' \
            "$WORK/logs/$step.log" || true)
        summary "first error: $first"
        [ -n "$line" ] && summary "first error line in the log: $line"
    fi
    [ -f "$WORK/summary.txt" ] || summary "no step has run"
    echo "===== IMAGE BUILD SUMMARY ====="
    cat "$WORK/summary.txt"
    echo "==============================="
    annotate notice "$(head -c 4000 "$WORK/summary.txt")"
    [ -z "$first" ] || annotate error "$first"
    chown -R "${SUDO_UID:-0}:${SUDO_GID:-0}" "$WORK/out" "$WORK/logs" "$WORK/summary.txt"
}

"step_$STEP"
