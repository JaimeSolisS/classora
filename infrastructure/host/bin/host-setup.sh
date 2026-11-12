#!/usr/bin/env bash
# First-boot setup of a Classora app host (ADR-0010, #614), run by cloud-init
# as root. Idempotent: a second run changes nothing. Steps:
#   1. a 1 GiB swap file;
#   2. Docker (Amazon Linux package) and the pinned Compose plugin (sha256-checked);
#   3. Docker's daemon.json: json-file rotation, Compose service name on log lines;
#   4. the data volume (mount-data-volume.sh: formats only an empty device);
#   5. the systemd units (data stack, then app stack, nightly backup timer),
#      Docker's mount dependency and logrotate (install-units.sh);
#   6. if a release is already on the data volume (a replaced instance), install
#      its host scripts and units (before step 5) and start it.
set -euo pipefail

# Docker Compose plugin: >= 2.24 for !reset/!override in compose.aws.yaml.
COMPOSE_VERSION=v5.6.1
COMPOSE_SHA256=42cfa75f51ad0ccf290bad37899e0038a8c10a0069e85233ecc1d53e3ff1347d

# Containers that write to the data volume run as these users.
EDGE_UID=10002       # Caddy (compose.aws.yaml `edge`)
COLLECTOR_UID=10001  # OpenTelemetry Collector image user

DATA=/srv/classora-data
BIN=/opt/classora/bin

log() { echo "host-setup: $*"; }

setup_swap() {
  if swapon --show=NAME --noheadings | grep -qx /swapfile; then
    return
  fi
  if [ ! -f /swapfile ]; then
    log "creating a 1 GiB swap file"
    dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
    chmod 0600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap defaults 0 0' >>/etc/fstab
}

install_docker() {
  if ! rpm -q docker >/dev/null 2>&1; then
    log "installing Docker"
    dnf install -y -q docker
  fi
  local plugin=/usr/local/lib/docker/cli-plugins/docker-compose
  if [ ! -x "$plugin" ] || ! "$plugin" version --short 2>/dev/null | grep -qx "${COMPOSE_VERSION#v}"; then
    log "installing Docker Compose $COMPOSE_VERSION"
    local tmp
    tmp=$(mktemp)
    curl -fsSL --retry 5 -o "$tmp" \
      "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-aarch64"
    echo "$COMPOSE_SHA256  $tmp" | sha256sum -c --quiet -
    install -D -m 0755 "$tmp" "$plugin"
    rm -f "$tmp"
  fi
  # json-file rotation; each line carries the Compose service name, which the
  # collector agent turns into container.name (#611 D4).
  install -d -m 0755 /etc/docker
  cat >/etc/docker/daemon.json.new <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3",
    "labels": "com.docker.compose.service"
  }
}
JSON
  mv /etc/docker/daemon.json.new /etc/docker/daemon.json
}

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

setup_swap
install_docker
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
