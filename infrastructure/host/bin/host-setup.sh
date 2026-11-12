#!/usr/bin/env bash
# First-boot setup of a Classora app host (ADR-0010, #614), run by cloud-init
# as root. Idempotent: a second run changes nothing. Steps:
#   1. a 1 GiB swap file;
#   2. Docker (Amazon Linux package) and the pinned Compose plugin (sha256-checked);
#   3. Docker's daemon.json: json-file rotation, Compose service name on log lines;
#   4. the data volume (mount-data-volume.sh: formats only an empty device);
#   5. the systemd units (data stack, then app stack) and Docker's mount dependency;
#   6. if a release is already on the data volume (a replaced instance), start it.
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

install_units() {
  install -m 0644 /opt/classora/systemd/classora-data.service /etc/systemd/system/
  install -m 0644 /opt/classora/systemd/classora-app.service /etc/systemd/system/
  # Docker (and so every container's restart policy) waits for the data
  # volume: PostgreSQL never starts on an empty directory of the root volume.
  install -D -m 0644 /opt/classora/systemd/docker-classora.conf \
    /etc/systemd/system/docker.service.d/classora.conf
  systemctl daemon-reload
  systemctl enable --now docker.service
  systemctl enable classora-data.service classora-app.service
}

setup_swap
install_docker
prepare_data
install_units

if [ -f "$DATA/releases/current/images.env" ]; then
  log "release $(basename "$(readlink -f "$DATA/releases/current")") found on the data volume: starting it"
  systemctl start classora-app.service
else
  log "no release yet: deploy one with $BIN/deploy.sh <sha>"
fi
log "done"
