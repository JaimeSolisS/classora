#!/usr/bin/env bash
# Sync the dedicated monitoring host with this checkout's HEAD (ADR-0014;
# M9-09, #617), from the operator's machine (`make monitoring-sync`; not CI):
#   1. bundle infrastructure/observability and infrastructure/host from HEAD
#      (git archive: committed files only), with RELEASE = the commit SHA;
#   2. upload it to s3://<monitoring ops bucket>/releases/monitoring.tgz;
#   3. send the SSM document classora-monitoring-sync (deploy-monitoring.sh)
#      to the host and wait for it (up to 15 minutes).
# The bucket and the instance come from /classora/monitoring/{ops_bucket,
# instance_id}. Uses AWS_PROFILE (default jsolisdev). When monitoring is
# colocated on DEV, the DEV deploy carries the same files instead.
set -euo pipefail

cd "$(dirname "$0")/../.."
export AWS_PROFILE="${AWS_PROFILE:-jsolisdev}"
region=us-east-2
paths=(infrastructure/observability infrastructure/host)

die() {
  echo "monitoring-sync: $*" >&2
  exit 1
}

param() {
  aws ssm get-parameter --region "$region" --name "/classora/monitoring/$1" \
    --query Parameter.Value --output text 2>/dev/null
}

sha=$(git rev-parse HEAD)
if ! git diff --quiet HEAD -- "${paths[@]}"; then
  echo "monitoring-sync: WARNING: uncommitted changes under ${paths[*]} are NOT synced (HEAD only)" >&2
fi

bucket=$(param ops_bucket) || die "no /classora/monitoring/ops_bucket: is envs/monitoring applied?"
instance=$(param instance_id) || die "no /classora/monitoring/instance_id"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bundle"
git archive --format=tar HEAD "${paths[@]}" | tar -xf - -C "$tmp/bundle"
printf '%s\n' "$sha" >"$tmp/bundle/RELEASE"
COPYFILE_DISABLE=1 tar -czf "$tmp/monitoring.tgz" -C "$tmp/bundle" .
echo "monitoring-sync: bundle $sha ($(wc -c <"$tmp/monitoring.tgz" | tr -d ' ') bytes)"
aws s3 cp --only-show-errors --region "$region" "$tmp/monitoring.tgz" "s3://$bucket/releases/monitoring.tgz"

command_id=$(aws ssm send-command --region "$region" --document-name classora-monitoring-sync \
  --instance-ids "$instance" --comment "monitoring-sync ${sha:0:12}" \
  --query Command.CommandId --output text)
echo "monitoring-sync: command $command_id sent to $instance"

status=Pending
for _ in $(seq 1 180); do
  sleep 5
  status=$(aws ssm get-command-invocation --region "$region" --command-id "$command_id" \
    --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)
  case $status in
    Pending | InProgress | Delayed) ;;
    *) break ;;
  esac
done
aws ssm get-command-invocation --region "$region" --command-id "$command_id" --instance-id "$instance" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text | tail -n 25
[ "$status" = Success ] || die "the sync ended with status $status"
echo "monitoring-sync: done ($sha)"
