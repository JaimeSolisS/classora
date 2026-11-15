#!/usr/bin/env bash
# Put DEV in load mode (#152; runbook _docs/runbooks/load-test.md), from this
# machine over SSM (profile jsolisdev, us-east-2). DEV only:
#   infrastructure/load/dev-up.sh dev [--rate-limit lifted|default] [--fresh] [--tuning <yaml>]
# Needs the scrubbed dump uploaded by `prepare-dataset.sh --upload`
# (s3://classora-dev-ops-*/backups/load-<YYYYMMDD>/classora.dump; the newest
# one is used). On the host (host/load-up.sh): restores it into the side
# database classora_load (once; --fresh restores again), points api and
# worker at it through /root/classora-load-override.yaml, lifts the per-IP
# limit to 60000/min (unless --rate-limit default, for the staff-600 run) and,
# with --tuning, stacks a candidate Compose override (E5). The real
# `classora` database is never written. Idempotent. Undo with dev-down.sh;
# load mode stays under 24 hours. A merge to develop redeploys DEV and ends
# load mode silently: run this again.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=infrastructure/load/lib.sh
. "$HERE/lib.sh"

usage() {
  echo "usage: dev-up.sh dev [--rate-limit lifted|default] [--fresh] [--tuning <compose yaml>]" >&2
  exit 2
}

[ $# -ge 1 ] || usage
require_dev "$1"
shift
limit=lifted mode=keep tuning_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rate-limit)
      [ $# -ge 2 ] || usage
      case "$2" in lifted | default) limit=$2 ;; *) usage ;; esac
      shift
      ;;
    --fresh) mode=fresh ;;
    --tuning)
      [ $# -ge 2 ] && [ -f "$2" ] || usage
      tuning_file=$2
      shift
      ;;
    *) usage ;;
  esac
  shift
done

TUNING=/root/classora-load-tuning.yaml
instance=$(dev_instance)
bucket=$(dev_bucket)
key=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix backups/load- \
  --query 'Contents[].Key' --output text | tr '\t' '\n' | grep -E '^backups/load-[0-9]{8}/classora\.dump$' | sort | tail -n1 || true)
[ -n "$key" ] || die "no backups/load-*/classora.dump in $bucket (run prepare-dataset.sh --upload)"
log "target: DEV instance $instance, dump s3://$bucket/$key, rate limit $limit, ${tuning_file:+tuning $tuning_file}"

step=$(mktemp "${TMPDIR:-/tmp}/classora-load-up.XXXXXX")
trap 'rm -f "$step"' EXIT
tuning_arg=""
if [ -n "$tuning_file" ]; then
  tuning_arg=tuning
  {
    echo '#!/usr/bin/env bash'
    echo "base64 -d >$TUNING <<'B64'"
    base64 <"$tuning_file"
    echo 'B64'
    echo "chmod 0600 $TUNING"
  } >"$step"
fi
sed '1d' "$HERE/host/load-up.sh" >>"$step"

started=$(date -u +%FT%TZ)
remote "$instance" 900 "$step" "$key" "$limit" "$mode" $tuning_arg
check_site
log "load mode on since $started (record it; end it with dev-down.sh dev within 24 hours)"
