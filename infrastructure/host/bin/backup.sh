#!/usr/bin/env bash
# Logical backup of the environment's database (ADR-0013, #616), as root:
#   backup.sh             nightly: s3://<ops bucket>/backups/<UTC date>/
#   backup.sh --archive   final dump: s3://<archive bucket>/<archive prefix>/<UTC timestamp>/
#                         (final-dump.sh; the prefix is /classora/<env>/archive_prefix)
# Run nightly by classora-backup.timer (08:30 UTC). Writes two objects, SSE-S3:
#   classora.dump   pg_dump --format=custom, run INSIDE the db container (client
#                   and server versions always match), streamed to S3;
#   counts.json     exact per-table row counts, taken in the same snapshot as
#                   the dump (pg_export_snapshot), so a restore must match them.
# A failed pg_dump stops the upload before end of stream: no object (and no
# partial object) is written under the key, and an existing one stays as it was.
# The last stdout line is one JSON result, also appended to
# /var/log/classora/backup.log:
#   {"event": "backup", "env": ..., "status": "ok|failed", "bytes": N,
#    "duration_s": N, "bucket": ..., "key": ..., "tables": N[, "error": ...]}
# Exit 0 only when both objects are written. No password is on any command
# line: psql and pg_dump use the container's local socket.
set -euo pipefail

BIN=/opt/classora/bin
# shellcheck source=/dev/null # (backup-common.sh)
. "$BIN/backup-common.sh"
# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}" "${AWS_REGION:?}" "${OPS_BUCKET:?}"

event=backup
case "${1:-}" in
  "") ;;
  --archive) event=final-dump ;;
  *)
    echo "usage: backup.sh [--archive]" >&2
    exit 2
    ;;
esac

bucket=""
key=""
bytes=0
tables=0
work=""
upload_pid=""
reported=0

emit() { # emit <status> [error]
  reported=1
  local error=""
  [ -z "${2:-}" ] || error=$(printf ', "error": "%s"' "${2//\"/\'}")
  result_line "$(printf '{"event": "%s", "env": "%s", "status": "%s", "bytes": %d, "duration_s": %d, "bucket": "%s", "key": "%s", "tables": %d%s}' \
    "$event" "$CLASSORA_ENV" "$1" "$bytes" "$SECONDS" "$bucket" "$key" "$tables" "$error")"
}

fail() {
  echo "backup: $1" >&2
  emit failed "$1"
  exit 1
}

# shellcheck disable=SC2329 # invoked by the EXIT trap
on_exit() {
  local code=$?
  if [ -n "$upload_pid" ]; then
    kill "$upload_pid" 2>/dev/null || true
  fi
  [ -z "$work" ] || rm -rf "$work"
  if [ "$reported" -eq 0 ]; then
    echo "backup: failed (exit $code)" >&2
    emit failed "unexpected error (exit $code)"
    code=1
  fi
  exit "$code"
}
trap on_exit EXIT

[ "$(id -u)" -eq 0 ] || fail "run as root"

# One backup at a time; never during a deploy (deploy.sh holds this lock).
exec 8>/run/classora-backup.lock
flock -n 8 || fail "another backup is running"
exec 9>/run/classora-deploy.lock
flock -w 900 9 || fail "a deploy has held the lock for 15 minutes"

if [ "$event" = backup ]; then
  bucket=$OPS_BUCKET
  prefix="backups/$(date -u +%F)"
else
  bucket=$(archive_setting archive_bucket) || fail "no /classora/$CLASSORA_ENV/archive_bucket parameter"
  archive_prefix=$(archive_setting archive_prefix) || fail "no /classora/$CLASSORA_ENV/archive_prefix parameter"
  [[ "$archive_prefix" =~ ^[a-z0-9-]+$ ]] || fail "unexpected archive prefix"
  prefix="$archive_prefix/$(date -u +%Y%m%dT%H%M%SZ)"
fi
key="$prefix/classora.dump"

[ -n "$("$BIN/compose.sh" ps --status running --quiet db 2>/dev/null)" ] || fail "the db container is not running"

work=$(mktemp -d /run/classora-backup.XXXXXX)

# A REPEATABLE READ session that exports its snapshot: pg_dump imports it and
# the counts run in it, so both see exactly the same data.
# shellcheck disable=SC2016 # expanded by the db container's shell
coproc SNAP { "$BIN/compose.sh" exec -T db sh -c 'exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }
# shellcheck disable=SC2153 # SNAP_PID is set by coproc
snap_pid=$SNAP_PID
printf '%s\n' "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;" "SELECT pg_export_snapshot();" >&"${SNAP[1]}"
snapshot=""
read -r -t 60 snapshot <&"${SNAP[0]}" || true
[[ "$snapshot" =~ ^[0-9A-F]+-[0-9A-F]+-[0-9]+$ ]] || fail "could not open a snapshot session"

# Stream the dump through a FIFO. This shell keeps the FIFO open for writing,
# so a failed pg_dump never ends the stream: the upload is killed before the
# end of input and S3 never completes the object.
mkfifo -m 0600 "$work/dump"
aws s3 cp --only-show-errors --region "$AWS_REGION" --sse AES256 - "s3://$bucket/$key" <"$work/dump" &
upload_pid=$!
exec 7>"$work/dump"
dump_rc=0
# shellcheck disable=SC2016 # expanded by the db container's shell
"$BIN/compose.sh" exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --snapshot="$1"' sh "$snapshot" >&7 || dump_rc=$?
if [ "$dump_rc" -ne 0 ]; then
  kill "$upload_pid" 2>/dev/null || true
  wait "$upload_pid" 2>/dev/null || true
  upload_pid=""
  exec 7>&-
  fail "pg_dump failed (exit $dump_rc); nothing was written to s3://$bucket/$key"
fi
exec 7>&-
upload_rc=0
wait "$upload_pid" || upload_rc=$?
upload_pid=""
[ "$upload_rc" -eq 0 ] || fail "upload of $key failed (exit $upload_rc)"

# Counts, still in the exported snapshot.
printf '%s\n' "$COUNTS_SQL" >&"${SNAP[1]}"
counts=""
read -r -t 900 counts <&"${SNAP[0]}" || true
printf '%s\n' "COMMIT;" '\q' >&"${SNAP[1]}"
wait "$snap_pid" 2>/dev/null || true

tables=$(COUNTS=$counts ENV_NAME=$CLASSORA_ENV KEY=$key OUT="$work/counts.json" python3 - <<'PY'
import datetime, json, os, sys
try:
    tables = json.loads(os.environ["COUNTS"])
except ValueError:
    sys.exit(1)
if not isinstance(tables, dict) or not tables:
    sys.exit(1)
doc = {
    "env": os.environ["ENV_NAME"],
    "dump": os.environ["KEY"],
    "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "tables": tables,
}
with open(os.environ["OUT"], "w", encoding="utf-8") as out:
    json.dump(doc, out, indent=1, sort_keys=True)
print(len(tables))
PY
) || fail "could not read the row counts"

aws s3 cp --only-show-errors --region "$AWS_REGION" --sse AES256 \
  "$work/counts.json" "s3://$bucket/$prefix/counts.json" || fail "upload of counts.json failed"

head=$(aws s3api head-object --region "$AWS_REGION" --bucket "$bucket" --key "$key" \
  --query '[ContentLength, ServerSideEncryption]' --output text) || fail "could not read back $key"
read -r bytes sse <<<"$head"
[ "$sse" = AES256 ] || fail "$key is not encrypted with SSE-S3"
[ "$bytes" -gt 0 ] || fail "$key is empty"

emit ok
