#!/usr/bin/env bash
# Deploy downtime on DEV (#152 AC7), from this machine over SSM. DEV only:
#   infrastructure/load/downtime-probe.sh dev
# Probes https://dev.classora.dev/api/v1/auth/me (401 = up) and / (200 = up)
# once a second, recreates api and worker on the host the way deploy.sh
# switches them (`compose.sh up -d --force-recreate api worker`, with the load
# override when load mode is on), keeps probing for 60 s and prints the
# longest gap of each probe. Writes nothing to any database.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=infrastructure/load/lib.sh
. "$HERE/lib.sh"

[ $# -eq 1 ] || {
  echo "usage: downtime-probe.sh dev" >&2
  exit 2
}
require_dev "$1"
instance=$(dev_instance)
work=$(mktemp -d "${TMPDIR:-/tmp}/classora-probe.XXXXXX")
step=$work/recreate.sh
cat >"$step" <<'SH'
set -euo pipefail
if [ -x /root/classora-load-compose.sh ]; then
  compose=/root/classora-load-compose.sh
else
  compose=/opt/classora/bin/compose.sh
fi
start=$(date +%s.%N)
"$compose" up -d --force-recreate api worker
echo "RECREATE_SECONDS=$(awk -v s="$start" -v e="$(date +%s.%N)" 'BEGIN { printf "%.1f", e - s }')"
SH

probe() { # probe <url> <file>
  while [ -f "$work/run" ]; do
    printf '%s %s\n' "$(date +%s)" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$1" || true)" >>"$2"
    sleep 1
  done
}
touch "$work/run"
probe https://dev.classora.dev/api/v1/auth/me "$work/api.log" &
probe https://dev.classora.dev/ "$work/spa.log" &
sleep 10
remote "$instance" 300 "$step"
sleep 60
rm -f "$work/run"
wait

gap() { # gap <file> <expected status>: the longest run of other answers, in seconds
  awk -v ok="$2" '
    $2 != ok { if (!start) start = $1; last = $1; next }
    { if (start) { g = $1 - start; if (g > max) max = g; start = 0 } }
    END { if (start) { g = last - start + 1; if (g > max) max = g }; printf "%d", max }' "$1"
}
api_gap=$(gap "$work/api.log" 401)
spa_gap=$(gap "$work/spa.log" 200)
log "API unavailable for at most ${api_gap}s ($(awk '$2 != 401' "$work/api.log" | wc -l | tr -d ' ') of $(wc -l <"$work/api.log" | tr -d ' ') probes off)"
log "SPA unavailable for at most ${spa_gap}s ($(awk '$2 != 200' "$work/spa.log" | wc -l | tr -d ' ') of $(wc -l <"$work/spa.log" | tr -d ' ') probes off)"
echo "API answers during the window (count, status):" >&2
awk '{ print $2 }' "$work/api.log" | sort | uniq -c >&2
rm -rf "$work"
