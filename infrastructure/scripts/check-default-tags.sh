#!/usr/bin/env bash
# Static check of the App cost tag (#669). Runs from `make check-infra`
# (terraform.sh check) and needs only bash, find, sort and awk: no Docker, no AWS.
#
#   check-default-tags.sh [terraform_dir]   default infrastructure/terraform
#
# Every root (a directory with versions.tf, outside modules/ and .terraform/) maps
# to one App value. Each `provider "aws"` block in the root's *.tf files (aliases
# included) needs a default_tags block with that App value. Every
# root_block_device tags block under modules/ needs an App key too, because
# default_tags do not reach root volumes on their own. Prints "OK <root> App=<value>"
# per root and "FAIL <root>: <reason>" per problem; exits 1 on any FAIL.
set -euo pipefail

tf_dir=${1:-"$(dirname "$0")/../terraform"}
[ -d "$tf_dir" ] || {
  echo "check-default-tags.sh: no directory $tf_dir" >&2
  exit 2
}
cd "$tf_dir"

expected_app() {
  case "$1" in
    bootstrap | shared | envs/monitoring) echo classora-shared ;;
    envs/dev) echo classora-dev ;;
    envs/prod) echo classora-prod ;;
    *) return 1 ;;
  esac
}

# Prints one line per `provider "aws"` block: "PROVIDER <App value>" (value empty
# when the block has no default_tags App). String literals are dropped before
# braces are counted, so "${var.x}" interpolations do not move the depth.
provider_apps() {
  awk '
    function depth_change(line,   copy, opens, closes) {
      copy = line
      gsub(/"[^"]*"/, "", copy)
      opens = gsub(/\{/, "{", copy)
      closes = gsub(/\}/, "}", copy)
      return opens - closes
    }
    /^[ \t]*(#|\/\/)/ { next }
    {
      line = $0
      if (!in_prov && line ~ /^[ \t]*provider[ \t]+"aws"[ \t]*\{/) {
        in_prov = 1; prov_depth = depth; app = ""; in_dt = 0
      } else if (in_prov && !in_dt && line ~ /^[ \t]*default_tags[ \t]*\{/) {
        in_dt = 1; dt_depth = depth
      } else if (in_dt && line ~ /^[ \t]*App[ \t]*=[ \t]*"/) {
        value = line
        sub(/^[ \t]*App[ \t]*=[ \t]*"/, "", value)
        sub(/".*$/, "", value)
        app = value
      }
      depth += depth_change(line)
      if (in_dt && depth <= dt_depth) in_dt = 0
      if (in_prov && depth <= prov_depth) {
        print "PROVIDER " app
        in_prov = 0
      }
    }
  ' "$@"
}

# Prints "<file>:<line>" for each tags block inside a root_block_device block
# that has no App key.
root_volume_tags_without_app() {
  awk '
    function depth_change(line,   copy, opens, closes) {
      copy = line
      gsub(/"[^"]*"/, "", copy)
      opens = gsub(/\{/, "{", copy)
      closes = gsub(/\}/, "}", copy)
      return opens - closes
    }
    FNR == 1 { depth = 0; in_rbd = 0; in_tags = 0 }
    /^[ \t]*(#|\/\/)/ { next }
    {
      line = $0
      if (!in_rbd && line ~ /^[ \t]*root_block_device[ \t]*\{/) {
        in_rbd = 1; rbd_depth = depth
      } else if (in_rbd && !in_tags && line ~ /^[ \t]*tags[ \t]*=[ \t]*\{/) {
        in_tags = 1; tags_depth = depth; has_app = 0; where = FILENAME ":" FNR
      } else if (in_tags && line ~ /^[ \t]*App[ \t]*=/) {
        has_app = 1
      }
      depth += depth_change(line)
      if (in_tags && depth <= tags_depth) {
        if (!has_app) print where
        in_tags = 0
      }
      if (in_rbd && depth <= rbd_depth) in_rbd = 0
    }
  ' "$@"
}

fail=0
roots=$(find . -name versions.tf -not -path '*/.terraform/*' -not -path '*/modules/*' \
  -exec dirname {} \; | sed 's|^\./||' | sort)
[ -n "$roots" ] || {
  echo "FAIL $tf_dir: no Terraform roots found"
  exit 1
}

for root in $roots; do
  if ! want=$(expected_app "$root"); then
    echo "FAIL $root: not in the App map (check-default-tags.sh expected_app)"
    fail=1
    continue
  fi
  providers=$(provider_apps "$root"/*.tf)
  if [ -z "$providers" ]; then
    echo "FAIL $root: no provider \"aws\" block"
    fail=1
    continue
  fi
  root_ok=1
  while IFS= read -r entry; do
    got=${entry#PROVIDER}
    got=${got# }
    if [ -z "$got" ]; then
      echo "FAIL $root: a provider \"aws\" block has no default_tags App"
      root_ok=0
    elif [ "$got" != "$want" ]; then
      echo "FAIL $root: default_tags App=$got, expected $want"
      root_ok=0
    fi
  done <<<"$providers"
  if [ "$root_ok" -eq 1 ]; then
    echo "OK $root App=$want"
  else
    fail=1
  fi
done

if [ -d modules ]; then
  module_files=$(find modules -name '*.tf' -not -path '*/.terraform/*' | sort)
  if [ -n "$module_files" ]; then
    # shellcheck disable=SC2086 # one path per word, no spaces in these paths
    missing=$(root_volume_tags_without_app $module_files)
    while IFS= read -r where; do
      [ -n "$where" ] || continue
      echo "FAIL ${where%%:*}: root_block_device tags without App (line ${where##*:})"
      fail=1
    done <<<"$missing"
  fi
fi

exit "$fail"
