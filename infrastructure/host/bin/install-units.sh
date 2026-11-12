#!/usr/bin/env bash
# Installs the host's systemd units, Docker's mount dependency and the
# logrotate config from /opt/classora/{systemd,logrotate} (#614, #616), as
# root, and enables them: the data and app stacks (started by host-setup.sh or
# deploy.sh) and the nightly backup timer (started now). Idempotent; run by
# host-setup.sh at first boot and by deploy.sh for every release.
set -euo pipefail

SRC=/opt/classora

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

systemctl daemon-reload
systemctl enable classora-data.service classora-app.service >/dev/null 2>&1
systemctl enable --now classora-backup.timer >/dev/null 2>&1
