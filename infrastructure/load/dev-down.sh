#!/usr/bin/env bash
# End DEV load mode (#152; runbook _docs/runbooks/load-test.md), from this
# machine over SSM (profile jsolisdev, us-east-2). DEV only. Idempotent:
#   infrastructure/load/dev-down.sh dev
# On the host (host/load-down.sh): compares `classora`'s exact row counts
# with the ones taken when load mode started, recreates the release's
# services without the load override (api and worker back on `classora`,
# default per-IP limit), drops classora_load and removes the override files.
# Then deletes every backups/load-* object from the DEV ops bucket and checks
# that https://dev.classora.dev/ answers 200 and /api/v1/auth/me 401.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=infrastructure/load/lib.sh
. "$HERE/lib.sh"

[ $# -eq 1 ] || {
  echo "usage: dev-down.sh dev" >&2
  exit 2
}
require_dev "$1"

instance=$(dev_instance)
bucket=$(dev_bucket)
log "target: DEV instance $instance"
host_ok=0
remote "$instance" 600 "$HERE/host/load-down.sh" || host_ok=1

keys=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix backups/load- \
  --query 'Contents[].Key' --output text | tr '\t' '\n' | grep -E '^backups/load-' || true)
for key in $keys; do
  log "deleting s3://$bucket/$key"
  aws s3 rm --only-show-errors "s3://$bucket/$key"
done
# shellcheck disable=SC2016 # a JMESPath literal, not a shell expansion
left=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix backups/load- --query 'length(Contents || `[]`)' --output text)
log "backups/load-* objects left: $left"

site_ok=0
check_site || site_ok=1
[ "$host_ok" = 0 ] && [ "$site_ok" = 0 ] && [ "$left" = 0 ] || die "DEV is NOT fully back to normal (see above)"
log "load mode off; DEV back to normal at $(date -u +%FT%TZ)"
