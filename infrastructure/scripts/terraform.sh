#!/usr/bin/env bash
# Terraform wrapper behind `make tf-plan`, `make tf-apply`, `make tf-destroy` and
# `make check-infra` (ADR-0011, #613). Runbook: _docs/runbooks/terraform.md.
#
#   terraform.sh plan <root> [opts] init (S3 backend) and save a plan to <root>/tfplan;
#                                   extra plan options, e.g. -replace=<address> (#614)
#   terraform.sh apply <root>       show the saved plan, ask for the root name, apply it
#   terraform.sh destroy <root>     plan a destroy, ask for the root name, apply it
#                                   (refuses bootstrap; refuses prod until M9-10's guard)
#   terraform.sh migrate-bootstrap  after the first bootstrap apply: write backend.hcl
#                                   and move the local bootstrap state into the bucket
#   terraform.sh check              fmt -check and validate every root (pinned
#                                   TERRAFORM_IMAGE) and shellcheck (SHELLCHECK_IMAGE)
#
# Roots: bootstrap, shared, dev, prod, monitoring (envs/<name>). Plans and applies
# use the local terraform binary with AWS_PROFILE (default jsolisdev); `check`
# needs only Docker and no AWS credentials. There is no auto-approve.
set -euo pipefail

cd "$(dirname "$0")/../.."
tf_dir=infrastructure/terraform
backend_file="$PWD/$tf_dir/backend.hcl"
local_override=local_backend_override.tf
export AWS_PROFILE="${AWS_PROFILE:-jsolisdev}"

die() {
  echo "terraform.sh: $*" >&2
  exit 2
}

root_dir() {
  local dir
  case "$1" in
    bootstrap | shared) dir="$tf_dir/$1" ;;
    dev | prod | monitoring) dir="$tf_dir/envs/$1" ;;
    *) die "unknown root '$1' (bootstrap, shared, dev, prod, monitoring)" ;;
  esac
  [ -f "$dir/versions.tf" ] || die "root '$1' does not exist yet ($dir)"
  printf '%s\n' "$dir"
}

state_key() {
  case "$1" in
    bootstrap | shared) printf '%s/terraform.tfstate\n' "$1" ;;
    *) printf 'envs/%s/terraform.tfstate\n' "$1" ;;
  esac
}

tf_init() {
  local root=$1 dir=$2
  if [ "$root" = bootstrap ] && [ ! -f "$backend_file" ]; then
    echo "First bootstrap: local state until 'terraform.sh migrate-bootstrap' (runbook)."
    printf 'terraform {\n  backend "local" {}\n}\n' >"$dir/$local_override"
    terraform -chdir="$dir" init -input=false
    return
  fi
  [ -f "$backend_file" ] || die "missing $tf_dir/backend.hcl: apply bootstrap and run migrate-bootstrap first"
  [ ! -f "$dir/$local_override" ] || die "bootstrap still uses local state: run 'terraform.sh migrate-bootstrap'"
  # envs/* read the bootstrap and shared outputs from the same bucket (#614).
  TF_VAR_state_bucket=$(sed -n 's/^bucket *= *"\(.*\)"$/\1/p' "$backend_file")
  [ -n "$TF_VAR_state_bucket" ] || die "no bucket in $tf_dir/backend.hcl"
  export TF_VAR_state_bucket
  terraform -chdir="$dir" init -input=false \
    -backend-config="$backend_file" -backend-config="key=$(state_key "$root")"
}

plan_summary() {
  terraform -chdir="$1" show -no-color "$2" | grep -E '^  # |^Plan:|^No changes' || true
}

confirm() {
  local root=$1 verb=$2 answer
  read -r -p "Type the root name to $verb: " answer
  if [ "$answer" != "$root" ]; then
    echo "Answer did not match '$root'; nothing was done." >&2
    return 1
  fi
}

cmd_plan() {
  local root=$1 dir
  shift
  dir=$(root_dir "$root")
  tf_init "$root" "$dir"
  terraform -chdir="$dir" plan -input=false -out=tfplan "$@"
  echo "Saved plan: $dir/tfplan. Apply it with: make tf-apply ENV=$root"
}

cmd_apply() {
  local root=$1 dir
  dir=$(root_dir "$root")
  [ -f "$dir/tfplan" ] || die "no saved plan for '$root': run make tf-plan ENV=$root first"
  echo "Saved plan for '$root':"
  plan_summary "$dir" tfplan
  confirm "$root" apply || exit 1
  local status=0
  terraform -chdir="$dir" apply -input=false tfplan || status=$?
  rm -f "$dir/tfplan"
  return "$status"
}

cmd_destroy() {
  local root=$1 dir
  case "$root" in
    bootstrap) die "bootstrap is never destroyed (state bucket, zone and archive are long-lived; ADR-0011)" ;;
    prod) die "prod can't be destroyed until M9-10 (#618) adds the verified final-dump guard (ADR-0013)" ;;
  esac
  dir=$(root_dir "$root")
  tf_init "$root" "$dir"
  terraform -chdir="$dir" plan -destroy -input=false -out=tfplan-destroy
  echo "Destroy plan for '$root':"
  plan_summary "$dir" tfplan-destroy
  local status=0
  confirm "$root" destroy || status=$?
  if [ "$status" -eq 0 ]; then
    terraform -chdir="$dir" apply -input=false tfplan-destroy || status=$?
  fi
  rm -f "$dir/tfplan-destroy"
  return "$status"
}

cmd_migrate_bootstrap() {
  local dir bucket
  dir=$(root_dir bootstrap)
  [ -f "$dir/$local_override" ] || die "bootstrap already uses the S3 backend"
  [ ! -f "$backend_file" ] || die "$tf_dir/backend.hcl already exists"
  bucket=$(terraform -chdir="$dir" output -raw state_bucket)
  cat >"$backend_file" <<EOF
# Written by terraform.sh migrate-bootstrap. Git-ignored: the bucket name is
# account-specific. Each root adds its own key.
bucket       = "$bucket"
region       = "us-east-2"
profile      = "$AWS_PROFILE"
encrypt      = true
use_lockfile = true
EOF
  rm -f "$dir/$local_override"
  terraform -chdir="$dir" init -input=false -migrate-state -force-copy \
    -backend-config="$backend_file" -backend-config="key=$(state_key bootstrap)"
  terraform -chdir="$dir" state list >/dev/null
  rm -f "$dir/terraform.tfstate" "$dir/terraform.tfstate.backup"
  echo "Bootstrap state now lives in s3://$bucket/$(state_key bootstrap); local state files removed."
}

cmd_check() {
  : "${TERRAFORM_IMAGE:?set by the Makefile}" "${SHELLCHECK_IMAGE:?set by the Makefile}"
  local fail=0 roots scripts
  roots=$(find "$tf_dir" -name versions.tf -not -path '*/.terraform/*' -not -path '*/modules/*' \
    -exec dirname {} \; | sort)
  echo "--- terraform fmt -check and validate: $(echo "$roots" | tr '\n' ' ')"
  # The repository is mounted read-only: provider downloads and the init data
  # directory live in the container, and the lock files are only read.
  # shellcheck disable=SC2016 # expanded by the container's shell
  if docker run --rm -v "$PWD:/repo:ro" -w /repo -e TF_IN_AUTOMATION=1 -e ROOTS="$roots" \
    --entrypoint /bin/sh "$TERRAFORM_IMAGE" -c '
      set -eu
      mkdir -p /tmp/plugins
      export TF_PLUGIN_CACHE_DIR=/tmp/plugins
      terraform fmt -check -recursive -diff infrastructure/terraform
      for root in $ROOTS; do
        echo "validate $root"
        export TF_DATA_DIR="/tmp/data/$root"
        terraform -chdir="$root" init -backend=false -input=false -lockfile=readonly >/dev/null
        terraform -chdir="$root" validate -no-color
      done'; then
    echo OK
  else
    echo FAIL >&2
    fail=1
  fi

  local dirs=()
  for dir in infrastructure/scripts infrastructure/host; do
    if [ -d "$dir" ]; then dirs+=("$dir"); fi
  done
  scripts=$(find "${dirs[@]}" -type f |
    while IFS= read -r file; do
      if head -n 1 "$file" | grep -qE '^#!.*(ba)?sh'; then printf '%s\n' "$file"; fi
    done | sort)
  echo "--- shellcheck: $(echo "$scripts" | tr '\n' ' ')"
  # shellcheck disable=SC2086 # one path per word, no spaces in these paths
  if docker run --rm -v "$PWD:/mnt:ro" -w /mnt "$SHELLCHECK_IMAGE" $scripts; then
    echo OK
  else
    echo FAIL >&2
    fail=1
  fi
  return "$fail"
}

[ $# -ge 1 ] || die "usage: terraform.sh plan|apply|destroy <root> | migrate-bootstrap | check"
case "$1" in
  plan)
    [ $# -ge 2 ] || die "usage: terraform.sh plan <root> [plan options]"
    shift
    cmd_plan "$@"
    ;;
  apply | destroy)
    [ $# -eq 2 ] || die "usage: terraform.sh $1 <root>"
    "cmd_$1" "$2"
    ;;
  migrate-bootstrap) cmd_migrate_bootstrap ;;
  check) cmd_check ;;
  *) die "unknown command '$1'" ;;
esac
