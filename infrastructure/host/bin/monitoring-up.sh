#!/usr/bin/env bash
# Start (or update) the monitoring stack on this host (ADR-0014; M9-09, #617),
# as root. Idempotent; run by classora-monitoring.service at boot, by
# deploy-monitoring.sh on the dedicated host, and by deploy.sh on an app host
# whose monitoring_mode is colocated.
#   monitoring-up.sh          render the env file, prepare the data
#                             directories, add the AWS-only contact point,
#                             write the Grafana site for Caddy, `up -d`, reload
#                             Caddy, enable the unit
#   monitoring-up.sh --down   on an app host leaving colocated mode: stop and
#                             remove the stack (its data stays on the volume),
#                             remove the Grafana site, disable the unit
set -euo pipefail

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}"

BIN=/opt/classora/bin
STATE=/srv/classora-data/monitoring
SITES=/opt/classora/caddy-sites
SITE=$SITES/grafana.caddy
UNIT=classora-monitoring.service

log() { echo "monitoring-up: $*"; }

# The Caddy that serves Grafana: the monitoring host's own, or the app edge.
reload_caddy() {
  if [ "$CLASSORA_ENV" = monitoring ]; then
    "$BIN/monitoring-compose.sh" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  else
    "$BIN/compose.sh" exec -T edge caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  fi
}

if [ "${1:-}" = --down ]; then
  [ "$CLASSORA_ENV" != monitoring ] || {
    echo "monitoring-up: --down is for app hosts; destroy envs/monitoring instead" >&2
    exit 2
  }
  systemctl disable "$UNIT" >/dev/null 2>&1 || true
  if [ -n "$(docker ps -aq --filter label=com.docker.compose.project=monitoring)" ]; then
    log "stopping the colocated monitoring stack (its data stays in $STATE)"
    docker compose --project-name monitoring down --remove-orphans
  fi
  if [ -f "$SITE" ]; then
    rm -f "$SITE"
    reload_caddy || log "WARNING: the edge did not reload; restart it to drop the Grafana site"
  fi
  exit 0
fi

mountpoint -q /srv/classora-data || {
  echo "monitoring-up: /srv/classora-data is not mounted" >&2
  exit 1
}
"$BIN/render-monitoring-env.sh"

if [ "$CLASSORA_ENV" = monitoring ]; then
  release=$(readlink -f /srv/classora-data/monitoring/releases/current)
else
  release=$(readlink -f /srv/classora-data/releases/current)
fi
[ -d "$release/infrastructure/host/monitoring" ] || {
  echo "monitoring-up: no monitoring files in $release" >&2
  exit 1
}

# Each store's directory, owned by its image's user (Prometheus: nobody;
# Loki and Tempo: 10001; Grafana: 472).
install -d -m 0755 "$STATE"
install -d -m 0700 -o 65534 -g 65534 "$STATE/prometheus"
install -d -m 0700 -o 10001 -g 10001 "$STATE/loki" "$STATE/tempo"
install -d -m 0700 -o 472 -g 0 "$STATE/grafana"

# The AWS-only contact point and policy (absent locally, M9-04).
install -m 0644 "$release/infrastructure/host/monitoring/notifications.yaml" \
  "$release/infrastructure/observability/grafana/provisioning/alerting/notifications.yaml"

# The Grafana site, for whichever Caddy serves it.
grafana_host=$(sed -n 's/^GRAFANA_HOST=//p' /opt/classora/env/monitoring.env)
install -d -m 0755 "$SITES"
sed "s/@GRAFANA_HOST@/$grafana_host/" "$release/infrastructure/host/monitoring/grafana.caddy" >"$SITE.new"
chmod 0644 "$SITE.new"
mv -f "$SITE.new" "$SITE"

log "starting the monitoring stack from $(basename "$release")"
"$BIN/monitoring-compose.sh" up -d --wait --wait-timeout 300 --remove-orphans
reload_caddy || log "WARNING: Caddy did not reload the Grafana site"

# Started again at boot, after the app stack when colocated.
install -m 0644 "$release/infrastructure/host/systemd/$UNIT" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1
log "done: https://$grafana_host/"
