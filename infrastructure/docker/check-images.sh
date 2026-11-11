#!/usr/bin/env bash
# Checks the three images built by `make build-images` (#610, ADR-0010):
#   - each image's architecture is <arch> (amd64 locally, arm64 in CI);
#   - the API image runs as uid 10001, not root;
#   - no image's config Env sets a PASSWORD, SECRET, TOKEN or database URL
#     variable to a value (secrets come from the host at runtime);
#   - the build context sent to Docker carries no .env file except
#     .env.example (.dockerignore). Canary .env files are planted at the root
#     and in apps/api and apps/web for the check and removed again.
#
#   infrastructure/docker/check-images.sh <arch> <api image> <web image> <pgbouncer image>
#
# Run through `make check-images`; the CI `images` job runs it. Exits 1 on any failure.
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <arch> <api image> <web image> <pgbouncer image>" >&2
  exit 2
fi
ARCH=$1
API=$2
shift
ROOT=$(cd "$(dirname "$0")/../.." && pwd)

failures=0
pass() { echo "ok    $1"; }
fail() {
  echo "FAIL  $1" >&2
  failures=$((failures + 1))
}

for image in "$@"; do
  arch=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
  if [ "$arch" = "linux/$ARCH" ]; then pass "$image: $arch"; else fail "$image: $arch, expected linux/$ARCH"; fi

  leaked=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" |
    grep -E '^[^=]*(PASSWORD|SECRET|TOKEN|DATABASE_URL|DATABASE_DIRECT_URL)[^=]*=.+' |
    sed -e 's/=.*/=<value>/' || true)
  if [ -z "$leaked" ]; then
    pass "$image: no secret in Env"
  else
    fail "$image: Env sets $(echo "$leaked" | tr '\n' ' ')"
  fi
done

uid=$(docker run --rm --entrypoint id "$API" -u)
if [ "$uid" = 10001 ]; then pass "$API: runs as uid $uid"; else fail "$API: runs as uid $uid, expected 10001"; fi

# The build context, as Docker receives it for the repository-root builds
# (api, web), exported to a directory by a FROM scratch build.
context=$(mktemp -d)
canaries=("$ROOT/.env.check-images-$$" "$ROOT/apps/api/.env.check-images-$$" "$ROOT/apps/web/.env")
planted=()
cleanup() {
  for file in ${planted[@]+"${planted[@]}"}; do rm -f "$file"; done
  rm -rf "$context"
}
trap cleanup EXIT
for file in "${canaries[@]}"; do
  if [ ! -e "$file" ]; then
    : >"$file"
    planted+=("$file")
  fi
done
printf 'FROM scratch\nCOPY . /\n' |
  DOCKER_BUILDKIT=1 docker build --quiet --file - --output "type=local,dest=$context" "$ROOT" >/dev/null
found=$(cd "$context" && find . -name '.env*' ! -name '.env.example' | sort)
if [ -z "$found" ]; then
  pass "build context: no .env file except .env.example"
else
  fail "build context includes $(echo "$found" | tr '\n' ' ')"
fi

if [ "$failures" -ne 0 ]; then
  echo "check-images: $failures check(s) failed" >&2
  exit 1
fi
echo "check-images: all checks passed"
