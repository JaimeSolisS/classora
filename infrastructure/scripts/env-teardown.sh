#!/usr/bin/env bash
# End of the environments' window (ADR-0011, ADR-0013; #618 E2 (a)). Runbook:
# _docs/runbooks/teardown.md.
#
#   env-teardown.sh dev-only   PROD (after a verified final dump) and the
#                              monitoring host go; DEV stays with monitoring
#                              off (MONITORING=colocated: colocated instead);
#                              shared and bootstrap stay.
#   env-teardown.sh all        also DEV and shared (ECR images included); only
#                              bootstrap stays (zone, state, archive with the
#                              final dump, budget, OIDC roles).
#
# Steps, each confirmed (type yes; anything else skips it):
#   1. final-dump.sh on the PROD host (SSM Run Command), and wait;
#   2. check the verified final dump (prod-destroy-guard.sh);
#   3. make tf-destroy ENV=prod (guard again, then type "prod");
#   4. make tf-destroy ENV=monitoring (type "monitoring");
#   5. dev-only: envs/dev monitoring_mode off (or colocated), kept in the
#      git-ignored envs/dev/monitoring.auto.tfvars; all: destroy dev;
#   6. all: destroy shared.
# Then the destroyed environments' leftover snapshots and the unmanaged
# /classora/prod/owner_initial_password, and a list of what remains.
# bootstrap is never destroyed. Uses AWS_PROFILE (default jsolisdev).
set -euo pipefail

cd "$(dirname "$0")/../.."
export AWS_PROFILE="${AWS_PROFILE:-jsolisdev}"
export AWS_REGION=us-east-2
TF=infrastructure/scripts/terraform.sh
MODE=${1:-}
MONITORING=${MONITORING:-off}

die() {
  echo "env-teardown: $*" >&2
  exit 2
}

case $MODE in
  dev-only | all) ;;
  bootstrap) die "bootstrap is never torn down (zone, state, archive, budget, OIDC)" ;;
  *) die "usage: env-teardown.sh dev-only|all (MONITORING=colocated keeps monitoring on DEV in dev-only)" ;;
esac
case $MONITORING in
  off | colocated) ;;
  *) die "MONITORING must be off or colocated" ;;
esac

step() { echo; echo "=== $*"; }

ask() { # ask <question>: 0 when the operator types yes
  local answer
  read -r -p "$1 Type yes to continue, anything else skips: " answer
  [ "$answer" = yes ]
}

param() { aws ssm get-parameter --name "$1" --query Parameter.Value --output text 2>/dev/null || true; }

running_instance() { # running_instance <env>: the instance ID if it runs
  local id state
  id=$(param "/classora/$1/instance_id")
  [ -n "$id" ] || return 0
  state=$(aws ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo missing)
  [ "$state" != running ] || echo "$id"
}

env_exists() { # env_exists <env>: any instance of that environment, any live state
  [ -n "$(aws ec2 describe-instances --filters Name=tag:Project,Values=classora "Name=tag:Environment,Values=$1" \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[].Instances[].InstanceId' --output text)" ]
}

leftover_snapshots() { # leftover_snapshots <env>: DLM snapshots outlive their policy
  local ids
  ids=$(aws ec2 describe-snapshots --owner-ids self \
    --filters Name=tag:Project,Values=classora "Name=tag:Environment,Values=$1" \
    --query 'Snapshots[].SnapshotId' --output text)
  [ -n "$ids" ] || return 0
  echo "Snapshots of the destroyed $1 data volume: $ids"
  if ask "Delete them (the archive and dumps are the recovery path)?"; then
    for id in $ids; do aws ec2 delete-snapshot --snapshot-id "$id" && echo "deleted $id"; done
  fi
}

echo "env-teardown $MODE (profile $AWS_PROFILE). Nothing happens without a typed yes or root name."

# --- 1-3. PROD ---------------------------------------------------------------
if env_exists prod; then
  step "1. Final dump of PROD to the archive (final-dump.sh)"
  prod=$(running_instance prod)
  if [ -z "$prod" ]; then
    echo "The PROD instance is not running: start it, or rely on an earlier verified dump."
  elif ask "Run final-dump.sh on PROD ($prod) now? Writes after it are not in the archive."; then
    command_id=$(aws ssm send-command --document-name AWS-RunShellScript --instance-ids "$prod" \
      --comment "env-teardown: final dump" --timeout-seconds 120 \
      --parameters '{"commands":["/opt/classora/bin/final-dump.sh"],"executionTimeout":["3600"]}' \
      --query Command.CommandId --output text)
    echo "SSM command $command_id: waiting (up to 60 minutes)"
    status=Pending
    for _ in $(seq 1 360); do
      sleep 10
      status=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$prod" \
        --query Status --output text 2>/dev/null || echo Pending)
      case $status in Pending | InProgress | Delayed) ;; *) break ;; esac
    done
    result=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$prod" \
      --query StandardOutputContent --output text | grep '"event": "final-dump"' | tail -n 1 || true)
    echo "final-dump: ${result:-no result line} (SSM $status)"
    case $result in
      *'"status": "verified"'*) ;;
      *) die "the final dump did not verify; PROD is kept (runbook teardown.md)" ;;
    esac
  fi

  step "2. Check the verified final dump"
  infrastructure/scripts/prod-destroy-guard.sh || die "no fresh verified final dump; PROD is kept"

  step "3. Destroy PROD"
  if ask "Destroy envs/prod (instance, data volume, EIP, app.classora.dev record, ops bucket, parameters)?"; then
    "$TF" destroy prod
    leftover_snapshots prod
    if [ -n "$(param /classora/prod/owner_initial_password)" ] &&
      ask "Delete the unmanaged /classora/prod/owner_initial_password?"; then
      aws ssm delete-parameter --name /classora/prod/owner_initial_password && echo "deleted"
    fi
  fi
else
  echo "No PROD instance: steps 1-3 skipped."
fi

# --- 4. Monitoring -----------------------------------------------------------
# The alert address (never committed) is needed to plan monitoring and a
# colocated DEV: read it before the monitoring root removes it.
if [ -z "${TF_VAR_alert_email:-}" ]; then
  TF_VAR_alert_email=$(aws ssm get-parameter --name /classora/monitoring/alert_email --with-decryption \
    --query Parameter.Value --output text 2>/dev/null || true)
  export TF_VAR_alert_email
fi
if env_exists monitoring; then
  step "4. Destroy the monitoring host"
  [ -n "$TF_VAR_alert_email" ] || die "export TF_VAR_alert_email (the alert address) first"
  if ask "Destroy envs/monitoring (Grafana, all telemetry, the grafana record, SMTP user)?"; then
    "$TF" destroy monitoring
  fi
else
  echo "No monitoring host: step 4 skipped."
fi

# --- 5. DEV ------------------------------------------------------------------
if [ "$MODE" = dev-only ]; then
  step "5. DEV keeps running with monitoring_mode=$MONITORING"
  if env_exists dev && ask "Plan and apply envs/dev with monitoring_mode=$MONITORING?"; then
    printf '# Written by env-teardown.sh (git-ignored): keeps the mode for later plans.\nmonitoring_mode = "%s"\n' \
      "$MONITORING" >infrastructure/terraform/envs/dev/monitoring.auto.tfvars
    "$TF" plan dev
    "$TF" apply dev
    echo "Redeploy DEV so the agent stops (or the colocated stack starts):"
    echo "  gh workflow run deploy-dev.yml --ref develop"
  fi
else
  step "5. Destroy DEV"
  if env_exists dev && ask "Destroy envs/dev (instance, data volume, EIP, dev record, ops bucket, parameters)?"; then
    "$TF" destroy dev
    leftover_snapshots dev
    rm -f infrastructure/terraform/envs/dev/monitoring.auto.tfvars
  fi

  step "6. Destroy shared (VPC, subnets, ECR repositories and every image)"
  if ask "Destroy shared? DEV and PROD can't be re-created until it is applied again."; then
    "$TF" destroy shared
  fi
fi

step "What remains (Project=classora)"
# shellcheck disable=SC2016 # JMESPath backticks, not shell
aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=classora \
  --query 'ResourceTagMappingList[].[Tags[?Key==`Environment`]|[0].Value, ResourceARN]' --output text |
  sed -E 's/[0-9]{12}/<acct>/' | sort
