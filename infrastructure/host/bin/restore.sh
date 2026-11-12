#!/usr/bin/env bash
# Restore drill (ADR-0013, #616), as root. Never touches the live database:
# everything happens in a scratch PostgreSQL container `classora-restore` on
# its own internal network, with no published port, a 384 MB memory limit and
# a scratch directory on the data volume; all are removed at the end.
#   restore.sh [--self-test] latest
#   restore.sh [--self-test] backups/<YYYY-MM-DD>/classora.dump
#   restore.sh [--self-test] s3://<ops or archive bucket>/<prefix>/classora.dump
#       downloads the dump and its counts.json, pg_restore --no-owner
#       --exit-on-error, then compares exact per-table row counts with
#       counts.json. --self-test deletes one row in the scratch copy before
#       the comparison, which must then fail.
#   restore.sh --from-device <block device>
#       snapshot drill (runbook _docs/runbooks/restore.md): mounts a volume
#       created from a data volume snapshot read-only (never the live
#       volume), copies its PostgreSQL directory, starts the scratch
#       container on the copy (crash recovery) and reports the row counts
#       next to the latest dump's. Differences are writes made between the
#       dump and the snapshot, so they are reported, not failed.
# Prints a table, then one JSON verdict line (also in /var/log/classora/backup.log):
#   {"event": "restore", "env": ..., "status": "ok|mismatch|failed", "source": ...,
#    "tables": N, "mismatched": N, "self_test": true|false, "duration_s": N}
# Exit 0: every table matches (or, --from-device, the copy started and was
# counted); 1: a mismatch; 2: usage; 3: the restore itself failed.
set -euo pipefail

BIN=/opt/classora/bin
# shellcheck source=/dev/null # (backup-common.sh)
. "$BIN/backup-common.sh"
# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}" "${AWS_REGION:?}" "${OPS_BUCKET:?}"

SCRATCH_NAME=classora-restore
SCRATCH_LABEL=classora.scratch=restore
SCRATCH_DIR=/srv/classora-data/restore-scratch
LIVE_PGDATA=/srv/classora-data/postgres
RESTORE_DB=classora_restore

usage() {
  reported=1
  echo "usage: restore.sh [--self-test] <latest | backups/<date>/classora.dump | s3://<bucket>/<key>> | --from-device <device>" >&2
  exit 2
}

reported=0
self_test=false
device=""
source_ref=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test=true ;;
    --from-device)
      [ $# -ge 2 ] || usage
      device=$2
      shift
      ;;
    -*) usage ;;
    *)
      [ -z "$source_ref" ] || usage
      source_ref=$1
      ;;
  esac
  shift
done
if [ -n "$device" ]; then
  [ -z "$source_ref" ] && [ "$self_test" = false ] || usage
else
  [ -n "$source_ref" ] || usage
fi

event_source=${device:-$source_ref}
tables=0
mismatched=0
mounted=""
started=0

emit() { # emit <status> [error]
  reported=1
  local error=""
  [ -z "${2:-}" ] || error=$(printf ', "error": "%s"' "${2//\"/\'}")
  result_line "$(printf '{"event": "restore", "env": "%s", "status": "%s", "source": "%s", "tables": %d, "mismatched": %d, "self_test": %s, "duration_s": %d%s}' \
    "$CLASSORA_ENV" "$1" "$event_source" "$tables" "$mismatched" "$self_test" "$SECONDS" "$error")"
}

fail() {
  echo "restore: $1" >&2
  emit failed "$1"
  exit 3
}

log() { echo "restore: $(date -u +%H:%M:%S) $*" >&2; }

# The scratch container only ever has our name AND our label; anything else
# (above all the live `db` of project classora) is refused.
is_scratch() {
  [ "$(docker inspect --format '{{index .Config.Labels "classora.scratch"}}|{{index .Config.Labels "com.docker.compose.project"}}' "$SCRATCH_NAME" 2>/dev/null)" = "restore|" ]
}

cleanup() {
  if docker inspect "$SCRATCH_NAME" >/dev/null 2>&1; then
    if is_scratch; then
      docker rm -f "$SCRATCH_NAME" >/dev/null 2>&1 || true
    else
      echo "restore: WARNING: $SCRATCH_NAME is not a scratch container; left alone" >&2
    fi
  fi
  docker network rm "$SCRATCH_NAME" >/dev/null 2>&1 || true
  [ -z "$mounted" ] || umount "$mounted" 2>/dev/null || true
  rm -rf "$SCRATCH_DIR"
}

# shellcheck disable=SC2329 # invoked by the EXIT trap
on_exit() {
  local code=$?
  cleanup
  if [ "$reported" -eq 0 ]; then
    echo "restore: failed (exit $code)" >&2
    emit failed "unexpected error (exit $code)"
    code=3
  fi
  exit "$code"
}

[ "$(id -u)" -eq 0 ] || {
  echo "restore: run as root" >&2
  exit 2
}
exec 8>/run/classora-restore.lock
flock -n 8 || {
  echo "restore: another restore is running" >&2
  exit 3
}
trap on_exit EXIT

# Leftovers of an interrupted drill.
if docker inspect "$SCRATCH_NAME" >/dev/null 2>&1 && ! is_scratch; then
  reported=1
  echo "restore: a container named $SCRATCH_NAME exists and is not a scratch container; refusing" >&2
  exit 3
fi
cleanup
install -d -m 0700 "$SCRATCH_DIR" "$SCRATCH_DIR/in"
# The container's postgres user must traverse its mount root (the cluster
# directory below it is 0700).
install -d -m 0755 "$SCRATCH_DIR/pg"
[ "$(realpath "$SCRATCH_DIR/pg")" != "$(realpath "$LIVE_PGDATA")" ] || fail "scratch directory is the live data directory"

# The image of the live db (postgres:18, ADR-0001); never pulled here.
image=$(docker inspect --format '{{.Image}}' "$("$BIN/compose.sh" ps --all --quiet db 2>/dev/null | head -n1)" 2>/dev/null || true)
[ -n "$image" ] || image=postgres:18

# s3_get <bucket> <key> <file>
s3_get() {
  aws s3 cp --only-show-errors --region "$AWS_REGION" "s3://$1/$2" "$3"
}

# The comparison source: counts.json next to the dump.
if [ -n "$device" ]; then
  # Latest nightly counts, for reference only.
  latest=$(aws s3api list-objects-v2 --region "$AWS_REGION" --bucket "$OPS_BUCKET" --prefix backups/ \
    --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' |
    grep -E '^backups/[0-9]{4}-[0-9]{2}-[0-9]{2}/counts\.json$' | sort | tail -n1 || true)
  counts_ref=""
  if [ -n "$latest" ]; then
    s3_get "$OPS_BUCKET" "$latest" "$SCRATCH_DIR/in/counts.json" && counts_ref=$latest
  fi
else
  case "$source_ref" in
    latest)
      bucket=$OPS_BUCKET
      latest=$(aws s3api list-objects-v2 --region "$AWS_REGION" --bucket "$OPS_BUCKET" --prefix backups/ \
        --query 'Contents[].Key' --output text | tr '\t' '\n' |
        grep -E '^backups/[0-9]{4}-[0-9]{2}-[0-9]{2}/counts\.json$' | sort | tail -n1 || true)
      [ -n "$latest" ] || fail "no backup with counts.json under s3://$OPS_BUCKET/backups/"
      key="${latest%/counts.json}/classora.dump"
      ;;
    s3://*)
      rest=${source_ref#s3://}
      bucket=${rest%%/*}
      key=${rest#*/}
      allowed=$OPS_BUCKET
      archive=$(archive_setting archive_bucket 2>/dev/null || true)
      [ "$bucket" = "$allowed" ] || { [ -n "$archive" ] && [ "$bucket" = "$archive" ]; } ||
        fail "only this environment's ops bucket or the archive bucket"
      ;;
    backups/*)
      bucket=$OPS_BUCKET
      key=$source_ref
      ;;
    *) usage ;;
  esac
  [[ "$key" =~ ^[A-Za-z0-9._/-]+/classora\.dump$ ]] || fail "the key must end in /classora.dump"
  event_source="s3://$bucket/$key"
  counts_ref="${key%/classora.dump}/counts.json"
  log "downloading $event_source"
  s3_get "$bucket" "$key" "$SCRATCH_DIR/in/classora.dump" || fail "could not download $key"
  s3_get "$bucket" "$counts_ref" "$SCRATCH_DIR/in/counts.json" || fail "could not download $counts_ref"
fi

if [ -n "$device" ]; then
  dev=$(realpath "$device")
  [ -b "$dev" ] || fail "$device is not a block device"
  live=$(realpath "$(findmnt -no SOURCE /srv/classora-data)")
  [ "$dev" != "$live" ] || fail "$device is the live data volume"
  ! findmnt -rno SOURCE | grep -qxF "$dev" || fail "$device is mounted"
  mounted=$SCRATCH_DIR/mnt
  install -d -m 0700 "$mounted"
  # Read-only, with a duplicate XFS UUID allowed (the snapshot is a copy of
  # the live volume). The snapshot is crash-consistent: XFS replays its log
  # onto the drill volume (a throwaway copy) while mounting; with
  # norecovery some files would be missing.
  mount -o ro,nouuid "$dev" "$mounted" || fail "could not mount $device read-only"
  [ -d "$mounted/postgres" ] || fail "no postgres directory on $device"
  log "copying the PostgreSQL directory from the snapshot volume"
  cp -a "$mounted/postgres/." "$SCRATCH_DIR/pg/" || fail "could not copy the PostgreSQL directory"
  umount "$mounted"
  mounted=""
  find "$SCRATCH_DIR/pg" -maxdepth 3 -name postmaster.pid -delete
  db=classora
else
  db=$RESTORE_DB
fi

# The scratch server: internal network, no port, trust auth inside it only.
docker network create --internal "$SCRATCH_NAME" >/dev/null
docker run -d --name "$SCRATCH_NAME" --label "$SCRATCH_LABEL" \
  --network "$SCRATCH_NAME" --memory 384m --pull never \
  -e POSTGRES_USER=classora -e POSTGRES_DB="$RESTORE_DB" -e POSTGRES_HOST_AUTH_METHOD=trust \
  -v "$SCRATCH_DIR/pg:/var/lib/postgresql" -v "$SCRATCH_DIR/in:/restore:ro" \
  "$image" postgres -c shared_buffers=64MB -c fsync=off -c full_page_writes=off >/dev/null ||
  fail "could not start the scratch container"
is_scratch || fail "scratch container check failed"

psql_scratch() {
  docker exec -i "$SCRATCH_NAME" psql -X -q -At -v ON_ERROR_STOP=1 -U classora -d "$db" "$@"
}

# Ready on TCP only after the image's init phase (its temporary server
# listens on the socket only); then a query on the socket.
log "waiting for the scratch server"
for _ in $(seq 1 90); do
  if docker exec "$SCRATCH_NAME" pg_isready -q -h 127.0.0.1 -U classora >/dev/null 2>&1 &&
    psql_scratch -c 'SELECT 1' >/dev/null 2>&1; then
    started=1
    break
  fi
  sleep 2
done
[ "$started" -eq 1 ] || {
  docker logs --tail 20 "$SCRATCH_NAME" >&2 || true
  fail "the scratch server did not start"
}

if [ -z "$device" ]; then
  # The roles the dump grants to (#165, db-roles.sh), without login.
  psql_scratch <<'SQL' || fail "could not create the roles"
SELECT 'CREATE ROLE ' || r || ' NOLOGIN' FROM unnest(ARRAY['classora_app', 'classora_monitor']) AS r
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) \gexec
SQL
  log "pg_restore"
  docker exec "$SCRATCH_NAME" pg_restore -U classora -d "$RESTORE_DB" --no-owner --exit-on-error \
    /restore/classora.dump || fail "pg_restore failed"
  if [ "$self_test" = true ]; then
    victim=$(python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["tables"]; print(next(k for k in sorted(t) if t[k] > 0))' \
      "$SCRATCH_DIR/in/counts.json") || fail "self-test: no table with rows"
    log "self-test: deleting one row of $victim in the scratch copy"
    psql_scratch -v victim="$victim" <<'SQL' || fail "self-test delete failed"
SET session_replication_role = replica;
SELECT format('DELETE FROM %s WHERE ctid = (SELECT ctid FROM %s LIMIT 1)', :'victim', :'victim') \gexec
SQL
  fi
fi

restored=$(printf '%s\n' "$COUNTS_SQL" | psql_scratch) || fail "could not count the restored tables"

set +e
summary=$(RESTORED=$restored COUNTS_FILE="$SCRATCH_DIR/in/counts.json" REF="${counts_ref:-}" python3 - <<'PY'
import json, os, sys
restored = json.loads(os.environ["RESTORED"])
source = {}
if os.path.exists(os.environ["COUNTS_FILE"]):
    source = json.load(open(os.environ["COUNTS_FILE"]))["tables"]
names = sorted(set(source) | set(restored))
width = max([len(n) for n in names] + [5])
print(f"{'table':<{width}}  {'source':>10}  {'restored':>10}  result", file=sys.stderr)
bad = 0
for n in names:
    s, r = source.get(n), restored.get(n)
    ok = s == r
    bad += not ok
    fmt = lambda v: "-" if v is None else str(v)
    print(f"{n:<{width}}  {fmt(s):>10}  {fmt(r):>10}  {'match' if ok else 'MISMATCH'}", file=sys.stderr)
print(f"compared with {os.environ['REF'] or 'nothing'}", file=sys.stderr)
print(len(names), bad)
PY
)
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "comparison failed"
read -r tables mismatched <<<"$summary"

if [ -n "$device" ]; then
  # Writes between the dump and the snapshot are expected differences.
  emit ok
  exit 0
fi
if [ "$mismatched" -eq 0 ]; then
  emit ok
  exit 0
fi
emit mismatch "$mismatched of $tables tables differ"
exit 1
