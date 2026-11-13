#!/usr/bin/env bash
# Installs the host's systemd units, Docker's mount dependency and the
# logrotate config from /opt/classora/{systemd,logrotate} (#614, #616), as
# root, and enables them: the data and app stacks (started by host-setup.sh or
# deploy.sh) and the nightly backup timer (started now). Masks sshd (#632).
# On DEV only (CLASSORA_ENV=dev in /etc/classora/host.env) it also installs
# and enables the 04:00 UTC DEV data reset (#674); on any other host it
# removes those units if they are there.
# Idempotent; run by host-setup.sh at first boot and by deploy.sh for every
# release. CLASSORA_TEST_ROOT (tests only) prefixes the host.env path and the
# source directory.
set -euo pipefail

SRC=${CLASSORA_TEST_ROOT:-}/opt/classora
HOST_ENV=${CLASSORA_TEST_ROOT:-}/etc/classora/host.env
RESET_UNITS=(classora-dev-data-reset.service classora-dev-data-reset.timer)
env_name=$(sed -n 's/^CLASSORA_ENV=//p' "$HOST_ENV" 2>/dev/null | tail -n 1 || true)

install -m 0644 \
  "$SRC/systemd/classora-data.service" \
  "$SRC/systemd/classora-app.service" \
  "$SRC/systemd/classora-backup.service" \
  "$SRC/systemd/classora-backup.timer" \
  /etc/systemd/system/
# Docker (and so every container's restart policy) waits for the data
# volume: PostgreSQL never starts on an empty directory of the root volume.
install -D -m 0644 "$SRC/systemd/docker-classora.conf" /etc/systemd/system/docker.service.d/classora.conf
install -D -m 0644 "$SRC/logrotate/classora" /etc/logrotate.d/classora
install -d -m 0755 /var/log/classora
# The edge's extra sites (grafana.caddy when monitoring is colocated, #617).
install -d -m 0755 /opt/classora/caddy-sites

# (Release-only units: a first boot before any release has none yet.)
reset_dev=false
if [ "$env_name" = dev ] && [ -f "$SRC/systemd/classora-dev-data-reset.timer" ]; then
  reset_dev=true
  install -m 0644 "$SRC/systemd/classora-dev-data-reset.service" \
    "$SRC/systemd/classora-dev-data-reset.timer" /etc/systemd/system/
elif [ "$env_name" != dev ]; then
  for unit in "${RESET_UNITS[@]}"; do
    if [ -e "/etc/systemd/system/$unit" ]; then
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      rm -f "/etc/systemd/system/$unit"
    fi
  done
fi

systemctl daemon-reload
systemctl enable classora-data.service classora-app.service >/dev/null 2>&1

# No SSH on the hosts (ADR-0010; access is Session Manager only, #632): stop,
# disable and mask sshd. Here so every deploy applies it, not only first boot.
systemctl disable --now sshd.service sshd.socket >/dev/null 2>&1 || true
systemctl mask sshd.service sshd.socket >/dev/null 2>&1 || echo "WARNING: could not mask sshd" >&2
systemctl enable --now classora-backup.timer >/dev/null 2>&1
# The DEV data reset timer (#674): the unit itself only runs once the
# ENABLED marker exists (runbook dev-data.md).
if [ "$reset_dev" = true ]; then
  systemctl enable --now classora-dev-data-reset.timer >/dev/null 2>&1
fi
