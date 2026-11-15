#!/usr/bin/env bash
# Docker on a Classora host (ADR-0010, #614; shared by the app hosts'
# host-setup.sh and the monitoring host's, #617), as root. Idempotent:
#   1. a 1 GiB swap file;
#   2. Docker (Amazon Linux package) and the pinned Compose plugin (sha256-checked);
#   3. Docker's daemon.json: json-file rotation, Compose service name on log lines.
set -euo pipefail

# Docker Compose plugin: >= 2.24 for !reset/!override in compose.aws.yaml.
COMPOSE_VERSION=v5.6.1
COMPOSE_SHA256=42cfa75f51ad0ccf290bad37899e0038a8c10a0069e85233ecc1d53e3ff1347d

log() { echo "install-docker: $*"; }

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

setup_swap
install_docker
