#!/usr/bin/env bash
# Resource sampler for a load run (#152), run as root on the DEV app host
# over SSM (sample-run.sh starts and collects it). Read-only.
#   sample.sh start <seconds>   samples every 10 s in the background into
#                               /root/classora-load-samples.log
#   sample.sh collect <s3 key>  uploads the log to the DEV ops bucket under
#                               backups/load-<date>/ (the only prefix the host
#                               may write; dev-down.sh deletes it), removes it
# Lines (space-separated):
#   C <epoch> <container> <mem bytes> <cpu %>              docker stats
#   H <epoch> <MemAvailable kB> <MemTotal kB> <pswpin> <pswpout> <load1> <iowait jiffies> <total jiffies>
#   P <epoch> <database> <user> <state> <count>            pg_stat_activity (every 30 s)
#   D <epoch> <database> <blks_hit> <blks_read> <deadlocks> <temp_files> <xact_commit>
set -euo pipefail

LOG=/root/classora-load-samples.log
BIN=/opt/classora/bin

to_bytes() { # 186.8MiB -> bytes
  awk -v v="$1" 'BEGIN {
    n = v + 0; u = v; sub(/^[0-9.]+/, "", u)
    m = (u == "KiB") ? 1024 : (u == "MiB") ? 1048576 : (u == "GiB") ? 1073741824 : (u == "kB") ? 1000 : (u == "MB") ? 1000000 : (u == "GB") ? 1000000000 : 1
    printf "%d", n * m }'
}

sample_loop() {
  local end=$(($(date +%s) + $1)) i=0 now
  while [ "$(date +%s)" -lt "$end" ]; do
    now=$(date +%s)
    docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' |
      while read -r name used _ _ cpu; do
        echo "C $now ${name#classora-} $(to_bytes "$used") ${cpu%\%}"
      done
    {
      awk '/^MemAvailable:/ {a = $2} /^MemTotal:/ {t = $2} END {printf "%s %s ", a, t}' /proc/meminfo
      awk '/^pswpin / {i = $2} /^pswpout / {o = $2} END {printf "%s %s ", i, o}' /proc/vmstat
      awk '{printf "%s ", $1}' /proc/loadavg
      awk '/^cpu / {t = 0; for (k = 2; k <= NF; k++) t += $k; printf "%s %s\n", $6, t}' /proc/stat
    } | sed "s/^/H $now /"
    if [ $((i % 3)) -eq 0 ]; then
      # shellcheck disable=SC2016 # expanded by the db container's shell
      "$BIN/compose.sh" exec -T db sh -c 'psql -X -At -F " " -U "$POSTGRES_USER" -d postgres' <<'SQL' || true
SELECT 'P', extract(epoch FROM now())::bigint, coalesce(datname, '-'), coalesce(usename, '-'), coalesce(nullif(state, ''), '-'), count(*)
FROM pg_stat_activity GROUP BY 3, 4, 5;
SELECT 'D', extract(epoch FROM now())::bigint, datname, blks_hit, blks_read, deadlocks, temp_files, xact_commit
FROM pg_stat_database WHERE datname IN ('classora', 'classora_load');
SQL
    fi
    i=$((i + 1))
    sleep 10
  done
}

case "${1:-}" in
  start)
    [[ "${2:-}" =~ ^[0-9]+$ ]] || {
      echo "usage: sample.sh start <seconds>" >&2
      exit 2
    }
    : >"$LOG"
    # A copy: the SSM step file is removed when this step ends.
    install -m 0700 "$0" /root/classora-load-sampler.sh
    nohup bash /root/classora-load-sampler.sh loop "$2" >>"$LOG" 2>/dev/null &
    echo "sampler started for $2 s (pid $!)"
    ;;
  loop) sample_loop "$2" ;;
  collect)
    [[ "${2:-}" =~ ^backups/load-[0-9]{8}/samples-[0-9TZ]+\.log$ ]] || {
      echo "usage: sample.sh collect backups/load-<YYYYMMDD>/samples-<time>.log" >&2
      exit 2
    }
    # shellcheck source=/dev/null
    . /etc/classora/host.env
    pkill -f /root/classora-load-sampler.sh || true
    aws s3 cp --only-show-errors --region "$AWS_REGION" "$LOG" "s3://$OPS_BUCKET/$2"
    echo "uploaded $(wc -l <"$LOG") lines to $2"
    rm -f "$LOG" /root/classora-load-sampler.sh
    ;;
  *)
    echo "usage: sample.sh start <seconds> | collect" >&2
    exit 2
    ;;
esac
