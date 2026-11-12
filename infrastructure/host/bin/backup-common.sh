#!/usr/bin/env bash
# Shared by backup.sh, restore.sh and final-dump.sh (ADR-0013, #616); sourced,
# not run. Defines:
#   COUNTS_SQL        one query returning a JSON object {"schema.table": rows}
#                     with an exact count(*) of every table (Procrastinate's
#                     queue tables included), the same on the live database
#                     and on a scratch restore;
#   result_line       prints one JSON result line on stdout and appends it to
#                     /var/log/classora/backup.log (logrotate weekly; the
#                     collector agent tails it, M9-09);
#   archive_setting   reads /classora/<env>/archive_bucket or archive_prefix.
# No database password is used anywhere: the scripts run psql and pg_dump
# inside the containers over the local socket.

# shellcheck disable=SC2034 # used by the scripts that source this file
COUNTS_SQL="SELECT coalesce(json_object_agg(name, n ORDER BY name), '{}') FROM (
  SELECT format('%I.%I', schemaname, tablename) AS name,
         (xpath('/row/n/text()', query_to_xml(
            format('SELECT count(*) AS n FROM %I.%I', schemaname, tablename),
            false, true, '')))[1]::text::bigint AS n
  FROM pg_tables
  WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
) AS t;"

BACKUP_LOG_DIR=/var/log/classora
BACKUP_LOG=$BACKUP_LOG_DIR/backup.log

# result_line <json>: stdout and the backup log (never a secret: only the
# event, environment, status, sizes, durations and S3 keys).
result_line() {
  echo "$1"
  install -d -m 0755 "$BACKUP_LOG_DIR"
  printf '%s\n' "$1" >>"$BACKUP_LOG" || true
  chmod 0644 "$BACKUP_LOG" 2>/dev/null || true
}

# archive_setting <name>: a plain parameter under /classora/<env>/.
archive_setting() {
  aws ssm get-parameter --region "$AWS_REGION" --name "/classora/$CLASSORA_ENV/$1" \
    --query Parameter.Value --output text
}
