#!/usr/bin/env bash
# Snapshot restore drill (ADR-0013, #616), run by an operator from their own
# machine with the AWS CLI (profile jsolisdev) and Session Manager; never with
# the instance role. Runbook: _docs/runbooks/restore.md "Restore drill (snapshot)".
#   infrastructure/scripts/snapshot-drill.sh [dev|prod] [--snapshot <snap-id>]
# 1. picks the newest completed Data Lifecycle Manager snapshot of the
#    environment's data volume (or the given one);
# 2. creates a gp3 volume from it in the host's zone, tagged Purpose=drill;
# 3. attaches it to the host as a second device;
# 4. on the host (AWS-RunShellScript), restore.sh --from-device: mounts it
#    read-only, copies the PostgreSQL directory, starts the scratch container
#    on the copy and prints its row counts next to the latest dump's;
# 5. always: detaches and deletes the drill volume, and checks that no
#    Purpose=drill volume is left.
# Exit 0 when the scratch server started on the snapshot's data.
set -euo pipefail

export AWS_PROFILE=${AWS_PROFILE:-jsolisdev}
export AWS_REGION=${AWS_REGION:-us-east-2}
export AWS_PAGER=""

env_name=dev
snapshot=""
while [ $# -gt 0 ]; do
  case "$1" in
    dev | prod) env_name=$1 ;;
    --snapshot)
      [ $# -ge 2 ] || {
        echo "usage: snapshot-drill.sh [dev|prod] [--snapshot <snap-id>]" >&2
        exit 2
      }
      snapshot=$2
      shift
      ;;
    *)
      echo "usage: snapshot-drill.sh [dev|prod] [--snapshot <snap-id>]" >&2
      exit 2
      ;;
  esac
  shift
done

log() { echo "snapshot-drill: $(date -u +%H:%M:%S) $*" >&2; }

instance=$(aws ssm get-parameter --name "/classora/$env_name/instance_id" --query Parameter.Value --output text)
zone=$(aws ec2 describe-instances --instance-ids "$instance" \
  --query 'Reservations[0].Instances[0].Placement.AvailabilityZone' --output text)

if [ -z "$snapshot" ]; then
  snapshot=$(aws ec2 describe-snapshots --owner-ids self \
    --filters "Name=tag:Name,Values=classora-$env_name-data" "Name=tag-key,Values=aws:dlm:lifecycle-policy-id" \
    "Name=status,Values=completed" \
    --query 'sort_by(Snapshots, &StartTime)[-1].SnapshotId' --output text)
  [ -n "$snapshot" ] && [ "$snapshot" != None ] || {
    echo "snapshot-drill: no completed DLM snapshot of classora-$env_name-data yet (daily at 09:00 UTC)" >&2
    exit 1
  }
fi
log "snapshot $snapshot ($(aws ec2 describe-snapshots --snapshot-ids "$snapshot" --query 'Snapshots[0].StartTime' --output text))"

volume=""
cleanup() {
  [ -n "$volume" ] || return 0
  log "detaching and deleting $volume"
  if [ "$(aws ec2 describe-volumes --volume-ids "$volume" --query 'Volumes[0].State' --output text 2>/dev/null)" = in-use ]; then
    aws ec2 detach-volume --volume-id "$volume" >/dev/null || aws ec2 detach-volume --volume-id "$volume" --force >/dev/null || true
  fi
  aws ec2 wait volume-available --volume-ids "$volume" || true
  aws ec2 delete-volume --volume-id "$volume" || true
  aws ec2 wait volume-deleted --volume-ids "$volume" || true
  left=$(aws ec2 describe-volumes --filters "Name=tag:Purpose,Values=drill" "Name=tag:Environment,Values=$env_name" \
    --query 'length(Volumes)' --output text)
  log "Purpose=drill volumes left: $left"
}
trap cleanup EXIT

volume=$(aws ec2 create-volume --snapshot-id "$snapshot" --availability-zone "$zone" --volume-type gp3 \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=classora-$env_name-drill},{Key=Purpose,Value=drill},{Key=Project,Value=classora},{Key=Environment,Value=$env_name}]" \
  --query VolumeId --output text)
log "created $volume in $zone"
aws ec2 wait volume-available --volume-ids "$volume"
aws ec2 attach-volume --volume-id "$volume" --instance-id "$instance" --device /dev/sdg >/dev/null
aws ec2 wait volume-in-use --volume-ids "$volume" \
  --filters Name=attachment.status,Values=attached
log "attached $volume to $instance"

# The NVMe device's by-id name carries the volume ID without the dash.
device="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${volume/-/}"
script="for i in \$(seq 1 30); do [ -b $device ] && break; sleep 2; done; /opt/classora/bin/restore.sh --from-device $device"
command=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
  --comment "classora snapshot drill $snapshot" \
  --parameters "$(printf '{"commands":["%s"],"executionTimeout":["900"]}' "$script")" \
  --query Command.CommandId --output text)
log "running restore.sh --from-device on the host (command $command)"
status=Pending
for _ in $(seq 1 200); do
  status=$(aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
    --query Status --output text 2>/dev/null || echo Pending)
  case $status in
    Pending | InProgress | Delayed) sleep 5 ;;
    *) break ;;
  esac
done
aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
  --query '[StandardErrorContent, StandardOutputContent]' --output text
log "host step: $status"
[ "$status" = Success ]
