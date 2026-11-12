#!/usr/bin/env bash
# `docker compose` for the monitoring stack (ADR-0014; M9-09, #617), as root:
#   monitoring-compose.sh <compose arguments>    e.g. ps, logs grafana
# Project `monitoring`: the shared infrastructure/observability/
# compose.observability.yaml (the same file as the local profile) plus
# infrastructure/host/monitoring/compose.monitoring.yaml, with the rendered
# /opt/classora/env/monitoring.env (render-monitoring-env.sh).
#   - Dedicated monitoring host (CLASSORA_ENV=monitoring): the synced bundle in
#     /srv/classora-data/monitoring/releases/current, and Caddy (profile edge).
#   - Colocated on an app host: the app's current release, plus
#     compose.colocated.yaml (the app network; the app's edge serves Grafana).
# MONITORING_RELEASE=<dir> picks another release directory.
set -euo pipefail

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}"

env_file=/opt/classora/env/monitoring.env
if [ "$CLASSORA_ENV" = monitoring ]; then
  release=${MONITORING_RELEASE:-/srv/classora-data/monitoring/releases/current}
else
  release=${MONITORING_RELEASE:-/srv/classora-data/releases/current}
fi
obs=$release/infrastructure/observability
host=$release/infrastructure/host/monitoring

[ -f "$obs/compose.observability.yaml" ] && [ -f "$host/compose.monitoring.yaml" ] || {
  echo "monitoring-compose.sh: no monitoring files in $release" >&2
  exit 1
}
[ -f "$env_file" ] || {
  echo "monitoring-compose.sh: $env_file is missing (run render-monitoring-env.sh)" >&2
  exit 1
}

files=(-f "$obs/compose.observability.yaml" -f "$host/compose.monitoring.yaml")
if [ "$CLASSORA_ENV" = monitoring ]; then
  files+=(--profile edge)
else
  files+=(-f "$host/compose.colocated.yaml")
fi

# Every config is bind-mounted through `current`, a path that never changes:
# a hash of the release's monitoring files (a label on each service,
# compose.monitoring.yaml) makes `up` recreate the stack when they change.
MONITORING_CONFIG_SHA256=$(cd "$release" && find infrastructure/observability infrastructure/host/monitoring \
  -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
export MONITORING_CONFIG_SHA256

exec docker compose --project-name monitoring --project-directory "$obs" \
  "${files[@]}" --env-file "$env_file" "$@"
