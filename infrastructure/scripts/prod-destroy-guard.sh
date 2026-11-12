#!/usr/bin/env bash
# PROD destroy guard (ADR-0011, ADR-0013, #618), run by `terraform.sh destroy
# prod` (make tf-destroy ENV=prod, make env-teardown) before anything is
# planned. PROD may be destroyed only when the archive bucket holds a verified
# final dump (final-dump.sh: s3://classora-archive-*/prod/<UTC>/verified.json,
# every table's row count matched) that is newer than PROD's last change of
# data owner:
#   - the PROD instance's launch time (a re-created or restarted host),
#   - the PROD data volume's creation time,
#   - the newest classora-deploy-prod command (a release can migrate data).
# Without a PROD instance and data volume there is nothing to lose: it passes.
#
#   prod-destroy-guard.sh      exit 0 allowed, 1 refused, 2 error
#
# Uses the operator's AWS_PROFILE (default jsolisdev). Prints no data.
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-jsolisdev}"
REGION=us-east-2

refuse() {
  echo "prod-destroy-guard: REFUSED: $*" >&2
  echo "prod-destroy-guard: take a final dump first: make env-teardown, or final-dump.sh on the PROD host (runbook _docs/runbooks/teardown.md)." >&2
  exit 1
}

fail() {
  echo "prod-destroy-guard: $*" >&2
  exit 2
}

awsr() { aws --region "$REGION" --output json "$@"; }

instances=$(awsr ec2 describe-instances \
  --filters Name=tag:Project,Values=classora Name=tag:Environment,Values=prod \
  Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].LaunchTime') || fail "could not describe the PROD instances"
volumes=$(awsr ec2 describe-volumes --filters Name=tag:Name,Values=classora-prod-data \
  --query 'Volumes[].CreateTime') || fail "could not describe the PROD data volume"
commands=$(awsr ssm list-commands --filters key=DocumentName,value=classora-deploy-prod \
  --query 'Commands[].RequestedDateTime') || fail "could not list the PROD deploy commands"

buckets=$(awsr s3api list-buckets --query 'Buckets[?starts_with(Name, `classora-archive-`)].Name') ||
  fail "could not list the buckets"
bucket=$(BUCKETS=$buckets python3 -c '
import json, os, sys
names = json.loads(os.environ["BUCKETS"])
if len(names) != 1:
    sys.exit("expected exactly one classora-archive-* bucket, found %d" % len(names))
print(names[0])') || fail "no single archive bucket"

verified=$(awsr s3api list-objects-v2 --bucket "$bucket" --prefix prod/ \
  --query "Contents[?ends_with(Key, '/verified.json')].{key: Key, at: LastModified}") ||
  fail "could not list s3://$bucket/prod/"
[ "$verified" != null ] || verified='[]'

# Decide: prints "pass <reason>", "refuse <reason>" or "check <key>".
decision=$(INSTANCES=$instances VOLUMES=$volumes COMMANDS=$commands VERIFIED=$verified python3 -c '
import datetime, json, os, re

def when(text):
    text = re.sub(r"\.\d+", "", text.replace("Z", "+00:00"))
    return datetime.datetime.fromisoformat(text)

def times(var):
    return [when(t) for t in (json.loads(os.environ[var]) or [])]

instances, volumes, commands = times("INSTANCES"), times("VOLUMES"), times("COMMANDS")
if not instances and not volumes:
    print("pass no PROD instance or data volume exists: nothing to lose")
    raise SystemExit
reference = max(instances + volumes + commands)
dumps = sorted(json.loads(os.environ["VERIFIED"]) or [], key=lambda d: when(d["at"]))
if not dumps:
    print("refuse no verified final dump under prod/ in the archive bucket")
    raise SystemExit
newest = dumps[-1]
if when(newest["at"]) <= reference:
    print("refuse the newest verified final dump (%s, %s) is older than PROD'"'"'s last deploy, launch or volume (%s)"
          % (newest["key"], newest["at"], reference.isoformat()))
    raise SystemExit
print("check " + newest["key"])
')

case $decision in
  pass\ *)
    echo "prod-destroy-guard: allowed: ${decision#pass }"
    exit 0
    ;;
  refuse\ *) refuse "${decision#refuse }" ;;
  check\ *) key=${decision#check } ;;
  *) fail "unexpected decision: $decision" ;;
esac

# The newest verified.json: every table matched, and the dump it names exists.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
awsr s3 cp --only-show-errors "s3://$bucket/$key" "$work/verified.json" ||
  fail "could not read s3://$bucket/$key"
dump=$(python3 -c '
import json, sys
v = json.load(open(sys.argv[1]))
if v.get("mismatched") != 0 or int(v.get("tables", 0)) <= 0 or not v.get("dump"):
    sys.exit(1)
print(v["dump"])' "$work/verified.json" 2>/dev/null) || refuse "s3://$bucket/$key does not record a matching restore"
expected="s3://$bucket/${key%/verified.json}/classora.dump"
[ "$dump" = "$expected" ] || refuse "s3://$bucket/$key names $dump, not $expected"
awsr s3api head-object --bucket "$bucket" --key "${key%/verified.json}/classora.dump" >/dev/null ||
  refuse "the dump $expected is missing"
echo "prod-destroy-guard: allowed: verified final dump s3://$bucket/${key%/verified.json}/"
