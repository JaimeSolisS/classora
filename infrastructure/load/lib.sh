#!/usr/bin/env bash
# Shared helpers for the load-test scripts (#152), sourced, never run. DEV
# only: every AWS call uses profile jsolisdev in us-east-2, and the target is
# the instance in /classora/dev/instance_id whose Name tag is classora-dev.
# shellcheck shell=bash

export AWS_PROFILE=jsolisdev
export AWS_REGION=us-east-2
export AWS_PAGER=""

log() { echo "${0##*/}: $(date -u +%H:%M:%S) $*" >&2; }
die() {
  echo "${0##*/}: $*" >&2
  exit 1
}

# require_dev <target>: anything but "dev" is refused.
require_dev() {
  [ "${1:-}" = dev ] || die "the only allowed target is 'dev' (got '${1:-}')"
}

# dev_instance: the DEV app host, checked by its Name tag.
dev_instance() {
  local id name
  id=$(aws ssm get-parameter --name /classora/dev/instance_id --query Parameter.Value --output text)
  # shellcheck disable=SC2016 # JMESPath literals, not shell expansions
  name=$(aws ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[0].Instances[0].Tags[?Key==`Name`]|[0].Value' --output text)
  [ "$name" = classora-dev ] || die "instance $id is tagged '$name', not classora-dev"
  echo "$id"
}

# dev_bucket: the one classora-dev-ops-* bucket.
dev_bucket() {
  local bucket
  # shellcheck disable=SC2016 # a JMESPath literal
  bucket=$(aws s3api list-buckets --query 'Buckets[?starts_with(Name, `classora-dev-ops-`)].Name' --output text)
  [ -n "$bucket" ] && [ "$(wc -w <<<"$bucket")" -eq 1 ] || die "expected one classora-dev-ops-* bucket, got '$bucket'"
  echo "$bucket"
}

# remote <instance> <timeout seconds> <script file> [args...]: runs the local
# script as root on the host (AWS-RunShellScript), waits up to the timeout
# and prints its output. Returns 0 only when the command succeeded.
remote() {
  local instance=$1 timeout=$2 script=$3
  shift 3
  local b64 args="" arg command status=Pending deadline
  b64=$(base64 <"$script" | tr -d '\n')
  for arg in "$@"; do
    [[ "$arg" =~ ^[A-Za-z0-9._/:=-]*$ ]] || die "unsafe remote argument '$arg'"
    args="$args $arg"
  done
  # shellcheck disable=SC2016 # $? and $rc are for the host's shell
  command=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "classora load test (#152) ${script##*/}" \
    --parameters "$(printf '{"commands":["echo %s | base64 -d > /root/.classora-load-step.sh && bash /root/.classora-load-step.sh%s; rc=$?; rm -f /root/.classora-load-step.sh; exit $rc"],"executionTimeout":["%s"]}' "$b64" "$args" "$timeout")" \
    --query Command.CommandId --output text)
  log "host step ${script##*/} (command $command, up to ${timeout}s)"
  deadline=$((SECONDS + timeout + 60))
  while [ "$SECONDS" -lt "$deadline" ]; do
    status=$(aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
      --query Status --output text 2>/dev/null || echo Pending)
    case $status in
      Pending | InProgress | Delayed) sleep 5 ;;
      *) break ;;
    esac
  done
  aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
    --query '[StandardOutputContent, StandardErrorContent]' --output text
  log "host step ${script##*/}: $status"
  [ "$status" = Success ]
}

# check_site: the DEV SPA answers 200 and the API 401 when signed out.
check_site() {
  local spa api
  spa=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://dev.classora.dev/)
  api=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://dev.classora.dev/api/v1/auth/me)
  log "https://dev.classora.dev/ -> $spa, /api/v1/auth/me -> $api"
  [ "$spa" = 200 ] && [ "$api" = 401 ]
}
