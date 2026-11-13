#!/usr/bin/env bash
# Runs the API and web test suites in Docker (#672 D3, D5, D7), so a machine
# needs only Docker and Make: `make test`, `make test-api-docker`,
# `make test-web-docker`.
#
#   infrastructure/docker/test-in-docker.sh <all|api|web>
#
# Environment:
#   SHARDS       API shard containers run at the same time (default 4). Each runs
#                `pytest --shard=K/SHARDS` (apps/api/tests/sharding.py) with its own
#                Testcontainers PostgreSQL; 1 or 2 on machines with less memory.
#   PYTEST_ARGS  extra pytest arguments, e.g. paths that narrow the selection
#                (expanded by the container's shell, relative to apps/api).
#
# It builds the `test` stages of api.Dockerfile and web.Dockerfile once
# (<project>-api-test, <project>-web-test; <project> is COMPOSE_PROJECT_NAME,
# default classora), then starts the API shards and the web container together.
# The API containers reach the host's Docker daemon through its socket
# (Testcontainers: PostgreSQL, PgBouncer and Ryuk run as sibling containers,
# reached at host.docker.internal). Coverage is off here; the native targets
# (make test-native, test-api) and CI keep it.
#
# Each part's output goes to test-results/docker/<part>.log. When every part has
# finished, it prints one summary line per part and, for a failed part, its
# failure summary and the last 40 lines of its log. Exit 0 only if every part
# passed. Ctrl-C (or TERM) removes the test containers; Ryuk removes their
# databases. Compatible with bash 3.2 (macOS).
set -u

usage() {
  echo "usage: $0 <all|api|web>" >&2
  exit 2
}
[ "$#" -eq 1 ] || usage
case "$1" in
  all) run_api=1 run_web=1 ;;
  api) run_api=1 run_web=0 ;;
  web) run_api=0 run_web=1 ;;
  *) usage ;;
esac

SHARDS=${SHARDS:-4}
PYTEST_ARGS=${PYTEST_ARGS:-}
case "$SHARDS" in
  '' | *[!0-9]* | 0*)
    echo "SHARDS must be a whole number from 1, got '$SHARDS'" >&2
    exit 2
    ;;
esac

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT" || exit 1
PROJECT=${COMPOSE_PROJECT_NAME:-classora}
API_IMAGE="$PROJECT-api-test"
WEB_IMAGE="$PROJECT-web-test"
LOG_DIR=test-results/docker
# Container names carry this run's id, so two runs on one daemon never clash.
RUN_ID=$$
mkdir -p "$LOG_DIR"

now() { date +%s; }

# build <image> <dockerfile> <log name>
build() {
  local image=$1 dockerfile=$2 log="$LOG_DIR/build-$3.log" started
  started=$(now)
  echo "Building $image (test stage of $dockerfile; log: $log)"
  if docker build --target test -f "$dockerfile" -t "$image" . >"$log" 2>&1; then
    echo "Built $image in $(($(now) - started)) s"
  else
    echo "FAILED to build $image; last 40 lines of $log:" >&2
    tail -n 40 "$log" >&2
    exit 1
  fi
}

[ "$run_api" = 1 ] && build "$API_IMAGE" infrastructure/docker/api.Dockerfile api
[ "$run_web" = 1 ] && build "$WEB_IMAGE" infrastructure/docker/web.Dockerfile web

# Parallel indexed arrays (bash 3.2 has no associative arrays).
parts=()
containers=()
pids=()
codes=()

cleanup() {
  trap - INT TERM
  echo
  echo "Interrupted: removing the test containers" >&2
  for name in ${containers[@]+"${containers[@]}"}; do
    docker rm -f "$name" >/dev/null 2>&1 &
  done
  wait
  exit 130
}
trap cleanup INT TERM

rm -f "$LOG_DIR"/api-*.log "$LOG_DIR/web.log"
started=$(now)

# start <part> <docker run arguments...>
start() {
  local part=$1 name="classora-test-$1-$RUN_ID"
  shift
  # Plain text logs: Vitest colors its output even without a terminal.
  docker run --rm --name "$name" -e NO_COLOR=1 "$@" >"$LOG_DIR/$part.log" 2>&1 &
  parts+=("$part")
  containers+=("$name")
  pids+=("$!")
  echo "Started $part (container $name; log: $LOG_DIR/$part.log)"
}

if [ "$run_api" = 1 ]; then
  shard=1
  while [ "$shard" -le "$SHARDS" ]; do
    # The docker socket path is the daemon's (on Docker Desktop, its VM's).
    # shellcheck disable=SC2016 # expanded by the container's shell
    start "api-$shard" \
      -v /var/run/docker.sock:/var/run/docker.sock \
      --add-host=host.docker.internal:host-gateway \
      -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal \
      -e TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock \
      -e SHARD="$shard/$SHARDS" -e PYTEST_ARGS="$PYTEST_ARGS" \
      "$API_IMAGE" sh -c 'exec pytest --no-cov --shard="$SHARD" $PYTEST_ARGS'
    shard=$((shard + 1))
  done
fi
if [ "$run_web" = 1 ]; then
  # Both suites always run; the part fails if either fails. Two Vitest workers:
  # the API shards share the CPU, and a starved full-app render times out.
  # shellcheck disable=SC2016 # expanded by the container's shell
  start web "$WEB_IMAGE" sh -c 'corepack pnpm --filter @classora/web test --maxWorkers=2; web=$?;
    corepack pnpm --filter @classora/api-client test; client=$?;
    [ "$web" -eq 0 ] && [ "$client" -eq 0 ]'
fi

i=0
while [ "$i" -lt "${#pids[@]}" ]; do
  wait "${pids[$i]}"
  codes+=("$?")
  i=$((i + 1))
done
trap - INT TERM

# pytest exits 5 when a shard got no test file (a narrow PYTEST_ARGS): that shard
# passes, unless every API shard collected nothing.
empty_api=0
api_parts=0
i=0
while [ "$i" -lt "${#parts[@]}" ]; do
  case "${parts[$i]}" in
    api-*)
      api_parts=$((api_parts + 1))
      [ "${codes[$i]}" = 5 ] && empty_api=$((empty_api + 1))
      ;;
  esac
  i=$((i + 1))
done

# The final count line(s) of a part's log: pytest's "== ... in Ns ==", Vitest's
# "Tests  N passed (N)" (one per suite in the web part).
# Any color codes are dropped first (portable: no GNU sed escapes).
counts() {
  local text
  text=$(tr -d '\033' <"$2" | sed -e 's/\[[0-9;]*m//g')
  case "$1" in
    web) echo "$text" | grep -E '^ *Tests +[0-9]' | sed -e 's/^ *//' -e 's/  */ /g' | paste -sd ';' - ;;
    *) echo "$text" | grep -E '^=+ .* in [0-9.]+s.* =+$' | tail -n 1 | sed -e 's/^=* //' -e 's/ =*$//' ;;
  esac
}

echo
echo "Summary ($(($(now) - started)) s; logs in $LOG_DIR/):"
failed=()
i=0
while [ "$i" -lt "${#parts[@]}" ]; do
  part=${parts[$i]}
  code=${codes[$i]}
  log="$LOG_DIR/$part.log"
  result=$(counts "$part" "$log")
  if [ "$code" = 0 ]; then
    status=PASS
  elif [ "$code" = 5 ] && [ "$empty_api" -lt "$api_parts" ]; then
    status=PASS
    result="no test files in this shard"
  else
    status=FAIL
    failed+=("$part")
  fi
  printf '  %-4s %-8s %s (exit %s)\n' "$status" "$part" "${result:-no summary line}" "$code"
  i=$((i + 1))
done

for part in ${failed[@]+"${failed[@]}"}; do
  log="$LOG_DIR/$part.log"
  echo
  echo "===== $part failed ($log) ====="
  if grep -q 'short test summary info' "$log"; then
    sed -n '/short test summary info/,$p' "$log" | head -n 100
    echo "----- last 40 lines -----"
  fi
  tail -n 40 "$log"
done

if [ "${#failed[@]}" -ne 0 ]; then
  echo
  echo "FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "All test parts passed."
