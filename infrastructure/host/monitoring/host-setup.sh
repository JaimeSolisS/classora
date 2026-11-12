#!/usr/bin/env bash
# First-boot setup of the dedicated monitoring host (ADR-0014; M9-09, #617),
# run by cloud-init as root (installed as /opt/classora/bin/host-setup.sh).
# Idempotent. Steps:
#   1. install-docker.sh: swap file, Docker, the pinned Compose plugin;
#   2. the data volume (mount-data-volume.sh: formats only an empty device),
#      Caddy's certificate directory, Docker's mount dependency;
#   3. if a synced bundle is already on the data volume (a replaced
#      instance), start the stack; otherwise wait for `make monitoring-sync`.
set -euo pipefail

BIN=/opt/classora/bin
DATA=/srv/classora-data
CADDY_UID=10002

log() { echo "host-setup: $*"; }

"$BIN/install-docker.sh"

"$BIN/mount-data-volume.sh"
install -d -m 0700 -o "$CADDY_UID" -g "$CADDY_UID" "$DATA/caddy"
install -d -m 0755 "$DATA/monitoring" "$DATA/monitoring/releases"
install -d -m 0700 /opt/classora/env
install -d -m 0755 /opt/classora/caddy-sites

# Docker (and so the containers' restart policies) waits for the data volume.
install -D -m 0644 /opt/classora/systemd/docker-classora.conf /etc/systemd/system/docker.service.d/classora.conf
systemctl daemon-reload
systemctl enable --now docker.service

if [ -d "$DATA/monitoring/releases/current/infrastructure/host/monitoring" ]; then
  log "a synced bundle is on the data volume: starting the monitoring stack"
  "$BIN/monitoring-up.sh"
else
  log "no bundle yet: run 'make monitoring-sync' from a checkout"
fi
log "done"
