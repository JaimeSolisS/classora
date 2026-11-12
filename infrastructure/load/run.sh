#!/usr/bin/env bash
# One measured load run against DEV in load mode (#152; runbook
# _docs/runbooks/load-test.md):
#   infrastructure/load/run.sh <p1|p2|p3|staff-600> [k6 duration]
# 1. starts the host sampler (docker stats, meminfo, swap, load, iowait,
#    pg_stat_activity and pg_stat_database every 10-30 s; host/sample.sh);
# 2. runs `make load-test PROFILE=<profile>` against https://dev.classora.dev
#    (k6 summary in out/<profile>-<time>/);
# 3. uploads the samples to the ops bucket and downloads them, then reads the
#    server-side histograms of the window from Prometheus (prom.sh);
# 4. prints the summary (summarize.py). DEV only; DEV must be in load mode.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
# shellcheck source=infrastructure/load/lib.sh
. "$HERE/lib.sh"

case "${1:-}" in p1 | p2 | p3 | staff-600) ;; *)
  echo "usage: run.sh <p1|p2|p3|staff-600> [k6 duration]" >&2
  exit 2
  ;;
esac
profile=$1 duration=${2:-}
case $profile in
  p1) seconds=1800 ;;
  p2) seconds=900 ;;
  p3) seconds=780 ;;
  staff-600) seconds=300 ;;
esac
if [ -n "$duration" ]; then
  [[ "$duration" =~ ^([0-9]+)m$ ]] || die "duration must be <minutes>m"
  seconds=$((BASH_REMATCH[1] * 60))
  [ "$profile" != p3 ] || seconds=$((seconds + 180))
fi

instance=$(dev_instance)
bucket=$(dev_bucket)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out=$HERE/out/$profile-$stamp
mkdir -p "$out"

remote "$instance" 60 "$HERE/host/sample.sh" start $((seconds + 120))
start=$(date +%s)
log "k6 $profile from $(date -u +%T) for about ${seconds}s"
status=0
make -C "$ROOT" --no-print-directory load-test PROFILE="$profile" DURATION="$duration" \
  SUMMARY="/out/$profile-$stamp/k6-summary.json" >"$out/k6.txt" 2>&1 || status=$?
end=$(date +%s)
log "k6 ended (exit $status) after $((end - start))s"

key="backups/load-$(date -u +%Y%m%d)/samples-$stamp.log"
# Let the sampler see the cool-down, then stop waiting for it.
remote "$instance" 120 "$HERE/host/sample.sh" collect "$key"
aws s3 cp --only-show-errors "s3://$bucket/$key" "$out/samples.log"
aws s3 rm --only-show-errors "s3://$bucket/$key"
# Prometheus gets the OTLP metrics within a minute.
sleep 60
"$HERE/prom.sh" "$start" "$end" >"$out/prometheus.txt" 2>"$out/prometheus.err" || log "prom.sh failed (see $out/prometheus.err)"
python3 "$HERE/summarize.py" "$out" "$start" "$end" | tee "$out/summary.md"
log "results in $out (k6 exit $status)"
