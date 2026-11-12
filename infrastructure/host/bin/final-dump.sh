#!/usr/bin/env bash
# Final dump before an environment is destroyed (ADR-0013 decision 8, #616),
# as root:
#   final-dump.sh
#   1. backup.sh --archive: classora.dump and counts.json to
#      s3://<archive bucket>/<archive prefix>/<UTC timestamp>/ (the bucket and
#      prefix are /classora/<env>/archive_bucket and archive_prefix: `prod` on
#      PROD (M9-10), the drill-only `dev-drill` on DEV);
#   2. restore.sh on that archive object (scratch container, row counts);
#   3. only if every table matches, verified.json next to them.
# `make tf-destroy ENV=prod` (M9-10) refuses without a verified final dump.
# The last stdout line is one JSON result (also in /var/log/classora/backup.log):
#   {"event": "final-dump", "env": ..., "status": "verified|failed", "prefix": "s3://..."}
set -euo pipefail

BIN=/opt/classora/bin
# shellcheck source=/dev/null # (backup-common.sh)
. "$BIN/backup-common.sh"
# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}" "${AWS_REGION:?}"

prefix_url=""

finish() { # finish <status> <exit code>
  result_line "$(printf '{"event": "final-dump", "env": "%s", "status": "%s", "prefix": "%s", "duration_s": %d}' \
    "$CLASSORA_ENV" "$1" "$prefix_url" "$SECONDS")"
  exit "$2"
}

[ "$(id -u)" -eq 0 ] || {
  echo "final-dump: run as root" >&2
  exit 2
}

backup_out=$("$BIN/backup.sh" --archive) || {
  echo "$backup_out"
  echo "final-dump: the archive dump failed" >&2
  finish failed 1
}
echo "$backup_out"
read -r bucket key < <(tail -n1 <<<"$backup_out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["bucket"], d["key"])')
prefix=${key%/classora.dump}
prefix_url="s3://$bucket/$prefix"

restore_out=$("$BIN/restore.sh" "s3://$bucket/$key") || {
  echo "$restore_out"
  echo "final-dump: the restore check of $prefix_url failed; no verified.json" >&2
  finish failed 1
}
echo "$restore_out"
verdict=$(tail -n1 <<<"$restore_out")

work=$(mktemp -d /run/classora-final-dump.XXXXXX)
trap 'rm -rf "$work"' EXIT
VERDICT=$verdict DUMP="s3://$bucket/$key" python3 - >"$work/verified.json" <<'PY' || finish failed 1
import datetime, json, os
verdict = json.loads(os.environ["VERDICT"])
assert verdict["status"] == "ok" and verdict["mismatched"] == 0 and not verdict["self_test"]
print(json.dumps({
    "dump": os.environ["DUMP"],
    "verified_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "tables": verdict["tables"],
    "mismatched": 0,
    "restore": verdict,
}, indent=1, sort_keys=True))
PY
aws s3 cp --only-show-errors --region "$AWS_REGION" --sse AES256 \
  "$work/verified.json" "$prefix_url/verified.json" || finish failed 1
finish verified 0
