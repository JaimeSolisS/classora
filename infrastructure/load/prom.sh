#!/usr/bin/env bash
# Read the server-side measurements of a load run (#152) from Prometheus on
# the monitoring host, over SSM. Read-only, and every query is filtered to
# deployment_environment_name="dev":
#   infrastructure/load/prom.sh <start epoch> <end epoch> [queries file]
# Each line of the queries file (default k6/queries.txt) is
# `<name>|<PromQL>`; $RANGE becomes the window length (e.g. 1800s) and the
# query is evaluated at <end>. Prints `<name> <labels> <value>` lines.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=infrastructure/load/lib.sh
. "$HERE/lib.sh"

[ $# -ge 2 ] && [[ "$1" =~ ^[0-9]+$ ]] && [[ "$2" =~ ^[0-9]+$ ]] || {
  echo "usage: prom.sh <start epoch> <end epoch> [queries file]" >&2
  exit 2
}
start=$1 end=$2 queries=${3:-$HERE/k6/queries.txt}
range="$((end - start))s"

monitoring=$(aws ssm get-parameter --name /classora/monitoring/instance_id --query Parameter.Value --output text)
# shellcheck disable=SC2016 # a JMESPath literal
name=$(aws ec2 describe-instances --instance-ids "$monitoring" \
  --query 'Reservations[0].Instances[0].Tags[?Key==`Name`]|[0].Value' --output text)
[ "$name" = classora-monitoring ] || die "instance $monitoring is tagged '$name'"

step=$(mktemp "${TMPDIR:-/tmp}/classora-prom.XXXXXX")
trap 'rm -f "$step"' EXIT
{
  echo 'set -u'
  # shellcheck disable=SC2016 # for the host's shell
  echo 'flat() { python3 -c "import json,sys
d=json.load(sys.stdin)[\"data\"][\"result\"]
for r in d:
    m=r[\"metric\"]; print(sys.argv[1], \",\".join(f\"{k}={v}\" for k,v in sorted(m.items()) if k not in (\"deployment_environment_name\",\"service_name\",\"service_instance_id\",\"instance\")) or \"-\", r[\"value\"][1])" "$1"; }'
  while IFS='|' read -r qname query; do
    [ -n "$qname" ] && [ "${qname:0:1}" != "#" ] || continue
    query=${query//\$RANGE/$range}
    case $query in *'deployment_environment_name="dev"'*) ;; *) die "query $qname is not filtered to dev" ;; esac
    data=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.urlencode({"query": sys.argv[1], "time": sys.argv[2]}))' "$query" "$end")
    echo "docker exec monitoring-prometheus-1 wget -qO- --post-data='$data' http://127.0.0.1:9090/api/v1/query | flat $qname"
  done <"$queries"
} >"$step"
remote "$monitoring" 300 "$step"
