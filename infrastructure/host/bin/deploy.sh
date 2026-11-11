#!/usr/bin/env bash
# Deploy release <sha> on this host (ADR-0011, #614), as root:
#   deploy.sh <40-hex commit SHA>
# Steps (ADR-0011 "Deploy"):
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
#   8. poll /health/ready (inside the api container) and the edge's / for up to 90 s;
#   9. on failure, switch back to `previous`, bring it up, check it again and
#      exit 1 ("rolled back"). Migrations are never reversed (expand and contract).
# A failure in steps 1-6 exits before anything switches: the running release
# keeps serving. Releases live on the data volume, so a replaced instance
# starts the same one (host-setup.sh). No value from the env file is printed.
# M9-07 (#615) calls this through the SSM document and adds image tagging.
set -euo pipefail

BIN=/opt/classora/bin
DATA=/srv/classora-data
RELEASES=$DATA/releases
HEALTH_SECONDS=90
KEEP_RELEASES=3

log() { echo "deploy: $(date -u +%H:%M:%S) $*"; }
die() {
  echo "deploy: $*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die "run as root"
[ $# -eq 1 ] && [[ "$1" =~ ^[0-9a-f]{40}$ ]] || die "usage: deploy.sh <40-hex commit SHA>"
sha=$1

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${OPS_BUCKET:?}" "${AWS_REGION:?}"

exec 9>/run/classora-deploy.lock
flock -n 9 || die "another deploy is running"
mountpoint -q "$DATA" || die "$DATA is not mounted"

new=$RELEASES/$sha

# 1. Bundle.
if [ ! -f "$new/images.env" ]; then
  log "downloading release $sha"
  tmp=$(mktemp -d "$RELEASES/.download.XXXXXX")
  trap 'rm -rf "$tmp"' EXIT
  aws s3 cp --only-show-errors --region "$AWS_REGION" \
    "s3://$OPS_BUCKET/releases/$sha.tgz" "$tmp/bundle.tgz"
  mkdir "$tmp/release"
  tar -xzf "$tmp/bundle.tgz" -C "$tmp/release" --no-same-owner
  [ -f "$tmp/release/images.env" ] || die "bundle has no images.env"
  grep -Evq '^(#.*|$|(API|WEB|PGBOUNCER)_IMAGE=[a-z0-9./-]+@sha256:[0-9a-f]{64})$' "$tmp/release/images.env" &&
    die "images.env has unexpected lines"
  rm -rf "$new"
  mv "$tmp/release" "$new"
  rm -rf "$tmp"
  trap - EXIT
fi

log "installing host scripts and units from the release"
install -d -m 0755 /opt/classora/systemd
for file in "$new"/infrastructure/host/bin/*.sh; do
  # Copy, then rename: a running script (this one) keeps its old inode.
  install -m 0755 "$file" "$BIN/.$(basename "$file").new"
  mv -f "$BIN/.$(basename "$file").new" "$BIN/$(basename "$file")"
done
for file in "$new"/infrastructure/host/systemd/*; do
  install -m 0644 "$file" /opt/classora/systemd/
done
install -m 0644 /opt/classora/systemd/classora-data.service /opt/classora/systemd/classora-app.service /etc/systemd/system/
install -D -m 0644 /opt/classora/systemd/docker-classora.conf /etc/systemd/system/docker.service.d/classora.conf
systemctl daemon-reload
systemctl enable classora-data.service classora-app.service >/dev/null 2>&1

# 2. Env file.
"$BIN/render-env.sh"

# 3. Previous release.
previous=""
if [ -L "$RELEASES/current" ]; then
  previous=$(readlink -f "$RELEASES/current")
  [ "$previous" != "$new" ] || previous=$(readlink -f "$RELEASES/previous" 2>/dev/null || true)
fi
log "previous release: ${previous:+$(basename "$previous")}${previous:-none}"

# 4. Images.
"$BIN/ecr-login.sh"
log "pulling images"
CLASSORA_RELEASE=$new "$BIN/compose.sh" --profile tools pull --quiet

# 5. PostgreSQL.
log "starting db"
CLASSORA_RELEASE=$new "$BIN/compose.sh" up -d --wait --wait-timeout 120 db

# 6. Migrations, runtime role, reference seeds, monitor role.
log "migrating (alembic upgrade head, sync-runtime-role, seeds)"
CLASSORA_RELEASE=$new "$BIN/compose.sh" --profile tools run --rm -T migrate \
  sh -c 'alembic upgrade head && python -m app.cli sync-runtime-role && python -m app.seeds'
CLASSORA_RELEASE=$new "$BIN/db-roles.sh"

switch_to() {
  ln -sfn "$1" "$RELEASES/.current.new"
  mv -T "$RELEASES/.current.new" "$RELEASES/current"
}

healthy() {
  local deadline=$((SECONDS + HEALTH_SECONDS)) host
  host=$(sed -n 's/^SITE_ADDRESS=//p' /opt/classora/env/app.env | cut -d, -f1 | tr -d ' ')
  while [ "$SECONDS" -lt "$deadline" ]; do
    if "$BIN/compose.sh" exec -T api python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" \
      >/dev/null 2>&1 &&
      curl -fsk -o /dev/null --max-time 5 --resolve "$host:443:127.0.0.1" "https://$host/"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

start_current() {
  "$BIN/compose.sh" up -d --wait --wait-timeout 180 --remove-orphans
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
  # Keep current, previous and the newest few.
  keep=$(printf '%s\n%s\n' "$new" "$previous")
  find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9a-f]{40}' \
    -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2 | tail -n +$((KEEP_RELEASES + 1)) |
    while IFS= read -r dir; do
      grep -qxF "$dir" <<<"$keep" || rm -rf "$dir"
    done
  docker image prune -f >/dev/null
  log "release $sha is live"
  exit 0
fi

# 9. Roll back.
log "release $sha is NOT healthy"
"$BIN/compose.sh" ps --all || true
if [ -z "$previous" ] || [ "$previous" = "$new" ]; then
  die "no previous release to roll back to; $sha stays in place (unhealthy)"
fi
log "rolling back to $(basename "$previous")"
switch_to "$previous"
if start_current && healthy; then
  die "rolled back to $(basename "$previous")"
fi
die "rollback to $(basename "$previous") is not healthy either"
