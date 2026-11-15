#!/usr/bin/env bash
# First-boot setup of a Classora app host (ADR-0010, #614), run by cloud-init
# as root. Idempotent: a second run changes nothing. Steps:
#   1-3. install-docker.sh: a 1 GiB swap file, Docker and the pinned Compose
#      plugin, Docker's daemon.json (shared with the monitoring host, #617);
#   4. the data volume (mount-data-volume.sh: formats only an empty device);
#   5. the systemd units (data stack, then app stack, nightly backup timer),
#      Docker's mount dependency and logrotate (install-units.sh);
#   6. if a release is already on the data volume (a replaced instance), install
#      its host scripts and units (before step 5) and start it.
set -euo pipefail

# Containers that write to the data volume run as these users.
EDGE_UID=10002       # Caddy (compose.aws.yaml `edge`)
COLLECTOR_UID=10001  # OpenTelemetry Collector image user

DATA=/srv/classora-data
BIN=/opt/classora/bin

log() { echo "host-setup: $*"; }

prepare_data() {
  "$BIN/mount-data-volume.sh"
  install -d -m 0755 "$DATA/releases"
  install -d -m 0700 -o "$EDGE_UID" -g "$EDGE_UID" "$DATA/caddy"
  install -d -m 0700 -o "$COLLECTOR_UID" -g "$COLLECTOR_UID" "$DATA/collector"
  install -d -m 0700 /opt/classora/env
}

# A replaced instance: the current release's host scripts, units and logrotate
# config are newer than the user data's, and include the backup scripts (#616),
# which the user data leaves out. Copy, then rename (this script is running).
refresh_from_release() {
  local host=$DATA/releases/current/infrastructure/host file
  [ -d "$host/bin" ] || return 0
  log "installing the host scripts and units of the release on the data volume"
  install -d -m 0755 /opt/classora/systemd /opt/classora/logrotate
  for file in "$host"/bin/*.sh; do
    install -m 0755 "$file" "$BIN/.$(basename "$file").new"
    mv -f "$BIN/.$(basename "$file").new" "$BIN/$(basename "$file")"
  done
  for file in "$host"/systemd/* "$host"/logrotate/*; do
    [ -f "$file" ] || continue
    install -m 0644 "$file" "/opt/classora/$(basename "$(dirname "$file")")/"
  done
}

install_units() {
  # The units (stacks, nightly backup timer), Docker's mount dependency and
  # the logrotate config.
  "$BIN/install-units.sh"
  systemctl enable --now docker.service
}

"$BIN/install-docker.sh"
prepare_data
refresh_from_release
install_units

if [ -f "$DATA/releases/current/images.env" ]; then
  log "release $(basename "$(readlink -f "$DATA/releases/current")") found on the data volume: starting it"
  systemctl start classora-app.service
else
  log "no release yet: deploy one with $BIN/deploy.sh <sha>"
fi
log "done"
