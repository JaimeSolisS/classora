#!/usr/bin/env bash
# Deploy release <sha> on this host (ADR-0011, #614, #615), as root:
#   deploy.sh <40-hex commit SHA>
# Run by the SSM document classora-deploy-<env> (deploy-dev.yml), or by hand
# over Session Manager. Steps (ADR-0011 "Deploy"):
#   1. download releases/<sha>.tgz from the ops bucket (Compose files, host
#      scripts and units, images.env with the three image digests) and install
#      the host scripts and units from it;
#   2. render the env file from Parameter Store;
#   3. remember the current release as `previous`;
#   4. log in to ECR with the instance role and pull by digest;
#   5. make sure PostgreSQL is up;
#   6. in a one-off container of the NEW API image, as the database owner on
#      the direct connection: alembic upgrade head, sync-runtime-role, the
#      reference seeds; then db-roles.sh (classora_monitor);
#   7. switch `current` to the new release and `up -d` every service;
#   8. readiness gate, up to 90 s: /health/ready inside the api container, the
#      worker's --healthcheck, and the edge's / (200 with a CSP header) through
#      Caddy on this host;
#   9. on failure, switch back to `previous`, bring it up and check it again.
#      Migrations are never reversed (expand and contract).
# After a healthy start it marks the systemd units active (so the next
# shutdown stops the containers and the next boot starts them in order),
# starts the monitoring stack when it is colocated on this host (#617), keeps
# the newest releases and removes ECR images that are neither the current nor
# the previous release's.
#
# Exit codes (deploy-dev.yml reports them):
#    0  deployed;
#   10  migration step failed: nothing switched, the old version still serves;
#   20  readiness failed, rolled back to the previous release, which is healthy;
#   21  readiness failed and the rollback failed (or there was nothing to roll
#       back to): the site is down;
#   30  bundle, env, lock, pull or database start failed: nothing changed.
# The last line on stdout is one JSON object:
#   {"result": "...", "sha": "...", "previous": "...", "duration_s": N}
# No value from the env file is printed, and command tracing (xtrace) is never on.
set -euo pipefail

BIN=/opt/classora/bin
DATA=/srv/classora-data
RELEASES=$DATA/releases
HEALTH_SECONDS=90
KEEP_RELEASES=3

sha=""
previous=""
tmp=""
phase=prepare
reported=0

log() { echo "deploy: $(date -u +%H:%M:%S) $*"; }

# finish <exit code> <result> [message on stderr]
finish() {
  [ -z "${3:-}" ] || echo "deploy: $3" >&2
  reported=1
  printf '{"result": "%s", "sha": "%s", "previous": "%s", "duration_s": %d}\n' \
    "$2" "$sha" "$(basename "${previous:-}")" "$SECONDS"
  exit "$1"
}

# Any failure not handled below (set -e) maps to its phase's exit code.
# shellcheck disable=SC2329 # invoked by the EXIT trap
on_exit() {
  local code=$?
  [ -z "$tmp" ] || rm -rf "$tmp"
  if [ "$reported" -eq 0 ]; then
    reported=1
    case $phase in
      prepare) code=30 result=not-changed ;;
      migrate) code=10 result=migration-failed ;;
      *) code=21 result=failed ;;
    esac
    echo "deploy: failed during $phase" >&2
    printf '{"result": "%s", "sha": "%s", "previous": "%s", "duration_s": %d}\n' \
      "$result" "$sha" "$(basename "${previous:-}")" "$SECONDS"
  fi
  exit "$code"
}
trap on_exit EXIT

[ "$(id -u)" -eq 0 ] || finish 30 not-changed "run as root"
[ $# -eq 1 ] && [[ "$1" =~ ^[0-9a-f]{40}$ ]] || finish 30 not-changed "usage: deploy.sh <40-hex commit SHA>"
sha=$1

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${OPS_BUCKET:?}" "${AWS_REGION:?}"

# Deploys never overlap; one started by hand waits up to 2 minutes.
exec 9>/run/classora-deploy.lock
flock -w 120 9 || finish 30 not-changed "another deploy is running"
mountpoint -q "$DATA" || finish 30 not-changed "$DATA is not mounted"

new=$RELEASES/$sha

# 1. Bundle.
if [ ! -f "$new/images.env" ]; then
  log "downloading release $sha"
  tmp=$(mktemp -d "$RELEASES/.download.XXXXXX")
  aws s3 cp --only-show-errors --region "$AWS_REGION" \
    "s3://$OPS_BUCKET/releases/$sha.tgz" "$tmp/bundle.tgz"
  mkdir "$tmp/release"
  tar -xzf "$tmp/bundle.tgz" -C "$tmp/release" --no-same-owner
  [ -f "$tmp/release/images.env" ] || finish 30 not-changed "bundle has no images.env"
  if grep -Evq '^(#.*|$|(API|WEB|PGBOUNCER)_IMAGE=[a-z0-9./-]+@sha256:[0-9a-f]{64})$' "$tmp/release/images.env"; then
    finish 30 not-changed "images.env has unexpected lines"
  fi
  rm -rf "$new"
  mv "$tmp/release" "$new"
  rm -rf "$tmp"
  tmp=""
fi

log "installing host scripts and units from the release"
install -d -m 0755 /opt/classora/systemd /opt/classora/logrotate
for file in "$new"/infrastructure/host/bin/*.sh; do
  # Copy, then rename: a running script (this one) keeps its old inode.
  install -m 0755 "$file" "$BIN/.$(basename "$file").new"
  mv -f "$BIN/.$(basename "$file").new" "$BIN/$(basename "$file")"
done
for file in "$new"/infrastructure/host/systemd/*; do
  install -m 0644 "$file" /opt/classora/systemd/
done
for file in "$new"/infrastructure/host/logrotate/*; do
  install -m 0644 "$file" /opt/classora/logrotate/
done
# The stacks' units, the nightly backup timer (#616) and logrotate.
"$BIN/install-units.sh"

# 2. Env file.
"$BIN/render-env.sh"

# 3. Previous release.
if [ -L "$RELEASES/current" ]; then
  previous=$(readlink -f "$RELEASES/current")
  [ "$previous" != "$new" ] || previous=$(readlink -f "$RELEASES/previous" 2>/dev/null || true)
fi
log "previous release: $(basename "${previous:-none}")"

# 4. Images.
"$BIN/ecr-login.sh"
log "pulling images"
CLASSORA_RELEASE=$new "$BIN/compose.sh" --profile tools pull --quiet

# 5. PostgreSQL.
log "starting db"
CLASSORA_RELEASE=$new "$BIN/compose.sh" up -d --wait --wait-timeout 120 db

# 6. Migrations, runtime role, reference seeds, monitor role.
phase=migrate
log "migrating (alembic upgrade head, sync-runtime-role, seeds)"
CLASSORA_RELEASE=$new "$BIN/compose.sh" --profile tools run --rm -T migrate \
  sh -c 'alembic upgrade head && python -m app.cli sync-runtime-role && python -m app.seeds'
CLASSORA_RELEASE=$new "$BIN/db-roles.sh"
phase=start

switch_to() {
  ln -sfn "$1" "$RELEASES/.current.new"
  mv -T "$RELEASES/.current.new" "$RELEASES/current"
}

api_ready() {
  "$BIN/compose.sh" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" \
    >/dev/null 2>&1
}

worker_ready() {
  "$BIN/compose.sh" exec -T worker python -m app.worker --healthcheck >/dev/null 2>&1
}

# The SPA through Caddy on this host: 200 and the M9-02 CSP header.
edge_ready() {
  local headers
  headers=$(curl -fsk -o /dev/null -D - --max-time 5 --resolve "$1:443:127.0.0.1" "https://$1/" 2>/dev/null) &&
    grep -qi '^content-security-policy:' <<<"$headers"
}

healthy() {
  local deadline=$((SECONDS + HEALTH_SECONDS)) host failing=""
  host=$(sed -n 's/^SITE_ADDRESS=//p' /opt/classora/env/app.env | cut -d, -f1 | tr -d ' ')
  while [ "$SECONDS" -lt "$deadline" ]; do
    failing=""
    api_ready || failing="$failing api"
    worker_ready || failing="$failing worker"
    edge_ready "$host" || failing="$failing edge"
    if [ -z "$failing" ]; then
      return 0
    fi
    sleep 3
  done
  log "not ready after ${HEALTH_SECONDS}s:$failing"
  return 1
}

start_current() {
  "$BIN/compose.sh" up -d --wait --wait-timeout 180 --remove-orphans
}

# The units are oneshot with RemainAfterExit: starting them while the stack is
# up only marks them active, so the next shutdown runs their ExecStop and the
# next boot starts data, then app (QA #614 O1). A failure is only a warning.
mark_units_active() {
  systemctl start classora-data.service classora-app.service ||
    log "WARNING: the systemd units did not start; check journalctl -u classora-data -u classora-app"
}

# The monitoring stack on this host only when envs/<env> has
# monitoring_mode = "colocated" (#617); otherwise make sure it isn't running.
# A failure here never fails the deploy.
monitoring() {
  if grep -qx 'MONITORING_MODE=colocated' /opt/classora/env/app.env; then
    "$BIN/monitoring-up.sh"
  else
    "$BIN/monitoring-up.sh" --down
  fi
}

# Release directories: keep current, previous and the newest few. Images: keep
# only the current and previous releases' ECR digests (and anything in use).
prune() {
  local keep refs
  keep=$(printf '%s\n%s\n' "$new" "$previous")
  find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9a-f]{40}' \
    -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2 | tail -n +$((KEEP_RELEASES + 1)) |
    while IFS= read -r dir; do
      grep -qxF "$dir" <<<"$keep" || rm -rf "$dir"
    done
  refs=$(cat "$new/images.env" "${previous:-/dev/null}/images.env" 2>/dev/null | sed -n 's/^[A-Z]*_IMAGE=//p')
  docker image ls --digests --format '{{.Repository}}@{{.Digest}}' | grep '\.dkr\.ecr\.' |
    while IFS= read -r ref; do
      grep -qxF "$ref" <<<"$refs" || docker image rm "$ref" >/dev/null 2>&1 || true
    done
  docker image prune -f >/dev/null || true
}

# 7. Switch and start.
log "starting release $sha"
switch_to "$new"
if start_current && healthy; then
  # 8. Healthy.
  if [ -n "$previous" ] && [ "$previous" != "$new" ]; then
    ln -sfn "$previous" "$RELEASES/.previous.new"
    mv -T "$RELEASES/.previous.new" "$RELEASES/previous"
  fi
  mark_units_active
  monitoring || log "WARNING: the monitoring stack did not start; see monitoring-up.sh"
  prune || log "WARNING: pruning failed"
  log "release $sha is live"
  finish 0 deployed
fi

# 9. Roll back.
log "release $sha is NOT healthy"
"$BIN/compose.sh" ps --all || true
if [ -z "$previous" ] || [ "$previous" = "$new" ]; then
  finish 21 failed-no-previous "no previous release to roll back to; $sha stays in place (unhealthy)"
fi
log "rolling back to $(basename "$previous")"
switch_to "$previous"
if start_current && healthy; then
  mark_units_active
  finish 20 rolled-back "rolled back to $(basename "$previous")"
fi
finish 21 rollback-failed "rollback to $(basename "$previous") is not healthy either"
