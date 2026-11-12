#!/usr/bin/env bash
# Update the dedicated monitoring host from the synced bundle (ADR-0014;
# M9-09, #617), as root. Run by the SSM document classora-monitoring-sync,
# which `make monitoring-sync` sends after uploading the bundle (no CI).
#   1. download releases/monitoring.tgz from this host's ops bucket: the
#      observability configs, dashboards and rules, and the host scripts, from
#      one commit (RELEASE holds its SHA);
#   2. install this host's scripts and unit from it;
#   3. switch `current` and run monitoring-up.sh (env, directories, contact
#      point, Grafana site, `up -d`, Caddy reload);
#   4. keep the newest three bundles.
# The last line on stdout is one JSON object: {"result": ..., "sha": ...}.
set -euo pipefail

BIN=/opt/classora/bin
RELEASES=/srv/classora-data/monitoring/releases
KEEP=3

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${OPS_BUCKET:?}" "${AWS_REGION:?}"
[ "$CLASSORA_ENV" = monitoring ] || {
  echo "deploy-monitoring: only on the monitoring host" >&2
  exit 2
}

exec 9>/run/classora-monitoring-sync.lock
flock -w 120 9 || {
  echo "deploy-monitoring: another sync is running" >&2
  exit 1
}
mountpoint -q /srv/classora-data || {
  echo "deploy-monitoring: /srv/classora-data is not mounted" >&2
  exit 1
}

install -d -m 0755 "$RELEASES"
tmp=$(mktemp -d "$RELEASES/.download.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
aws s3 cp --only-show-errors --region "$AWS_REGION" "s3://$OPS_BUCKET/releases/monitoring.tgz" "$tmp/bundle.tgz"
mkdir "$tmp/release"
tar -xzf "$tmp/bundle.tgz" -C "$tmp/release" --no-same-owner
sha=$(cat "$tmp/release/RELEASE" 2>/dev/null || true)
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "deploy-monitoring: the bundle has no valid RELEASE" >&2
  exit 1
}
rm -rf "${RELEASES:?}/$sha"
mv "$tmp/release" "$RELEASES/$sha"
new=$RELEASES/$sha

echo "deploy-monitoring: installing the host scripts and unit of $sha"
for file in "$new"/infrastructure/host/bin/{install-docker,mount-data-volume,render-monitoring-env,monitoring-compose,monitoring-up}.sh \
  "$new"/infrastructure/host/monitoring/{host-setup,deploy-monitoring}.sh; do
  # Copy, then rename: a running script (this one) keeps its old inode.
  install -m 0755 "$file" "$BIN/.$(basename "$file").new"
  mv -f "$BIN/.$(basename "$file").new" "$BIN/$(basename "$file")"
done
install -d -m 0755 /opt/classora/systemd
install -m 0644 "$new"/infrastructure/host/systemd/{classora-monitoring.service,docker-classora.conf} /opt/classora/systemd/

ln -sfn "$new" "$RELEASES/.current.new"
mv -T "$RELEASES/.current.new" "$RELEASES/current"
"$BIN/monitoring-up.sh"

find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9a-f]{40}' \
  -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2 | tail -n +$((KEEP + 1)) |
  while IFS= read -r dir; do
    [ "$dir" = "$new" ] || rm -rf "$dir"
  done
printf '{"result": "synced", "sha": "%s", "duration_s": %d}\n' "$sha" "$SECONDS"
