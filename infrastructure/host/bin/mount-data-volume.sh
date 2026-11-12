#!/usr/bin/env bash
# Mount the host's data volume at /srv/classora-data (ADR-0010, #614).
#
#   mount-data-volume.sh                     the attached EBS volume DATA_VOLUME_ID
#                                            (/etc/classora/host.env), at /srv/classora-data
#   mount-data-volume.sh <device> <dir>      any block device (tests use a loop device)
#
# SAFETY: the device is formatted (XFS, label `classora`: XFS labels hold at
# most 12 characters) ONLY when blkid's low-level probe exits 2, "nothing
# found": no filesystem, no partition table, no signature. A device
# with anything on it is never formatted; if it isn't a mountable filesystem the
# script fails instead. It adds one /etc/fstab line by UUID with nofail
# (FSTAB=<file> overrides the path, for tests). A second run is a no-op.
set -euo pipefail

FSTAB=${FSTAB:-/etc/fstab}
WAIT_SECONDS=${WAIT_SECONDS:-600}

log() { echo "mount-data-volume: $*"; }
die() {
  echo "mount-data-volume: $*" >&2
  exit 1
}

if [ $# -eq 2 ]; then
  device=$1
  mount_point=$2
elif [ $# -eq 0 ]; then
  # shellcheck source=/dev/null
  . /etc/classora/host.env
  : "${DATA_VOLUME_ID:?DATA_VOLUME_ID missing from /etc/classora/host.env}"
  # Nitro instances expose EBS volumes as NVMe devices; the serial is the
  # volume ID without its dash.
  device=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}
  mount_point=/srv/classora-data
else
  die "usage: mount-data-volume.sh [<device> <mount point>]"
fi

# Terraform attaches the volume after the instance starts: wait for it.
waited=0
until [ -b "$device" ]; do
  [ "$waited" -lt "$WAIT_SECONDS" ] || die "device $device did not appear within ${WAIT_SECONDS}s"
  sleep 5
  waited=$((waited + 5))
done
device=$(readlink -f "$device")

status=0
probe=$(blkid -p -o export "$device" 2>/dev/null) || status=$?
if [ "$status" -eq 2 ] && [ -z "$probe" ]; then
  log "$device is empty: creating an XFS filesystem"
  mkfs.xfs -q -L classora "$device"
  probe=$(blkid -p -o export "$device")
elif [ "$status" -ne 0 ]; then
  die "blkid could not identify $device (exit $status); refusing to format or mount it"
fi

fs_type=$(printf '%s\n' "$probe" | sed -n 's/^TYPE=//p')
uuid=$(printf '%s\n' "$probe" | sed -n 's/^UUID=//p')
[ "$fs_type" = xfs ] || die "$device holds '${fs_type:-no filesystem}' (blkid: $(printf '%s' "$probe" | tr '\n' ' ')); refusing to format or mount it"
[ -n "$uuid" ] || die "$device has no filesystem UUID"

install -d -m 0755 "$mount_point"
if ! grep -q "^UUID=$uuid " "$FSTAB"; then
  log "adding UUID=$uuid to $FSTAB"
  echo "UUID=$uuid $mount_point xfs defaults,nofail,x-systemd.device-timeout=120 0 2" >>"$FSTAB"
  if [ "$FSTAB" = /etc/fstab ]; then systemctl daemon-reload; fi
fi

if mountpoint -q "$mount_point"; then
  log "$mount_point is already mounted"
else
  mount -t xfs "$device" "$mount_point"
  log "mounted $device ($uuid) at $mount_point"
fi

for dir in postgres caddy collector releases; do
  [ -d "$mount_point/$dir" ] || install -d -m 0755 "$mount_point/$dir"
done
