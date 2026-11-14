#!/usr/bin/env bash
# Runs the API and web test suites in Docker (#672 D3, D5, D7), so a machine
# needs only Docker and Make: `make test`, `make test-all`, `make test-api-docker`,
# `make test-web-docker`.
#
#   infrastructure/docker/test-in-docker.sh <both|api|web>
#
# Environment:
#   SUITE        `unit-integration` (default; `make test`): the API unit tests and
#                the integration tests, the same selections as `make test-unit` and
#                `make test-integration` (CI's api-unit and api-integration jobs).
#                `everything` (`make test-all`): also the migration checks, the
#                billing invariant suite and the simulation seed tests.
#   SHARDS       test containers running at the same time (default: the CPUs Docker
#                has, minus one, between 1 and 4). Lower it on a machine with little
#                memory.
#   PYTEST_ARGS  extra pytest arguments, e.g. paths that narrow the API selection
#                (expanded by the container's shell, relative to apps/api). With
#                PYTEST_ARGS, `everything` runs no separate special suites.
#
# It builds the `test` stages of api.Dockerfile and web.Dockerfile once
# (<project>-api-test, <project>-web-test; <project> is COMPOSE_PROJECT_NAME,
# default classora), then works through a queue of parts, at most SHARDS at a
# time, longest first: the special suites (`everything` only), the web part,
# then the API selection split into 2 x SHARDS chunks (`pytest --shard=K/N`,
# apps/api/tests/sharding.py; more chunks than slots, so the slots finish close
# together). Each API part has its own Testcontainers
# PostgreSQL; the API containers reach the host's Docker daemon through its
# socket (PostgreSQL, PgBouncer and Ryuk run as sibling containers, reached at
# host.docker.internal). Coverage is off here; the native targets and CI keep it.
#
# Each part's output goes to test-results/docker/<part>.log. When every part has
# finished, it prints one summary line per part and, for a failed part, its
# failure summary and the last 40 lines of its log. Exit 0 only if every part
# passed. Ctrl-C (or TERM) removes the test containers and this run's
# Testcontainers containers and images (label classora.test-run). Compatible
# with bash 3.2 (macOS).
set -u

usage() {
  echo "usage: $0 <both|api|web>" >&2
  exit 2
}
[ "$#" -eq 1 ] || usage
case "$1" in
  both) run_api=1 run_web=1 ;;
  api) run_api=1 run_web=0 ;;
  web) run_api=0 run_web=1 ;;
  *) usage ;;
esac

SUITE=${SUITE:-unit-integration}
case "$SUITE" in
  unit-integration | everything) ;;
  *)
    echo "SUITE must be unit-integration or everything, got '$SUITE'" >&2
    exit 2
    ;;
esac
PYTEST_ARGS=${PYTEST_ARGS:-}
SHARDS=${SHARDS:-}
if [ -z "$SHARDS" ]; then
  cpus=$(docker info --format '{{.NCPU}}' 2>/dev/null || echo 3)
  case "$cpus" in '' | *[!0-9]*) cpus=3 ;; esac
  SHARDS=$((cpus - 1))
  [ "$SHARDS" -lt 1 ] && SHARDS=1
  [ "$SHARDS" -gt 4 ] && SHARDS=4
fi
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
# Container names and the Testcontainers label carry this run's id, so two runs
# on one daemon never clash and Ctrl-C removes only this run's containers.
RUN_ID=$$
RUN_TAG="$PROJECT-$RUN_ID"
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

# The queue: parallel indexed arrays (bash 3.2 has no associative arrays).
# kind: api (MARK, ARGS for pytest) or web.
q_part=()
q_kind=()
q_mark=()
q_args=()
add() {
  q_part+=("$1")
  q_kind+=("$2")
  q_mark+=("$3")
  q_args+=("$4")
}

if [ "$run_api" = 1 ]; then
  # Same selections as make test-migrations / test-billing-invariants /
  # test-simulation, longest first (CI: about 17, 18 and 5 minutes).
  if [ "$SUITE" = everything ] && [ -z "$PYTEST_ARGS" ]; then
    add api-simulation api '' 'tests/integration/test_simulation_*.py --durations=10'
    add api-migrations api '' 'tests/integration/test_migrations.py'
    add api-billing api '' 'tests/integration/billing_invariants --durations=10'
  fi
  # The web part next when it runs: it is the longest single part of make test
  # (longest first, so the slots finish close together).
  [ "$run_web" = 1 ] && add web web '' ''
  # make test-unit + make test-integration: the unit and integration markers,
  # without the three special suites above.
  if [ "$SHARDS" -eq 1 ]; then chunks=1; else chunks=$((SHARDS * 2)); fi
  k=1
  while [ "$k" -le "$chunks" ]; do
    add "api-$k" api 'unit or integration' "--shard=$k/$chunks \
--ignore=tests/integration/test_migrations.py --ignore=tests/integration/billing_invariants \
--ignore-glob=tests/integration/test_simulation_*.py $PYTEST_ARGS"
    k=$((k + 1))
  done
fi
[ "$run_api" = 0 ] && add web web '' ''

total=${#q_part[@]}
pids=()
codes=()
names=()
launched=0

remove_run_containers() {
  local ids
  ids=$(docker ps -aq --filter "label=classora.test-run=$RUN_TAG" 2>/dev/null)
  # shellcheck disable=SC2086 # one id per word
  [ -n "$ids" ] && docker rm -f $ids >/dev/null 2>&1
  ids=$(docker images -q --filter "label=classora.test-run=$RUN_TAG" 2>/dev/null | sort -u)
  # shellcheck disable=SC2086 # one id per word
  [ -n "$ids" ] && docker rmi -f $ids >/dev/null 2>&1
  return 0
}

cleanup() {
  trap - INT TERM
  echo
  echo "Interrupted: removing the test containers and their databases" >&2
  for name in ${names[@]+"${names[@]}"}; do
    docker rm -f "$name" >/dev/null 2>&1 &
  done
  wait
  # Twice: a database the test process was starting as it was stopped may
  # appear a moment later.
  remove_run_containers
  sleep 3
  remove_run_containers
  exit 130
}
trap cleanup INT TERM

rm -f "$LOG_DIR"/api-*.log "$LOG_DIR/web.log"
started=$(now)

# launch <queue index>
launch() {
  local i=$1 part=${q_part[$1]} name
  name="classora-test-$part-$RUN_ID"
  if [ "${q_kind[$i]}" = api ]; then
    # The docker socket path is the daemon's (on Docker Desktop, its VM's).
    # shellcheck disable=SC2016 # expanded by the container's shell
    docker run --rm --name "$name" -e NO_COLOR=1 \
      -v /var/run/docker.sock:/var/run/docker.sock \
      --add-host=host.docker.internal:host-gateway \
      -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal \
      -e TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock \
      -e CLASSORA_TEST_RUN="$RUN_TAG" \
      -e MARK="${q_mark[$i]}" -e ARGS="${q_args[$i]}" \
      "$API_IMAGE" sh -c 'if [ -n "$MARK" ]; then set -- -m "$MARK"; else set --; fi;
        exec pytest --no-cov "$@" $ARGS' >"$LOG_DIR/$part.log" 2>&1 &
  else
    # Both suites always run; the part fails if either fails. Two Vitest workers
    # (the slot count leaves one CPU spare for the second).
    # shellcheck disable=SC2016 # expanded by the container's shell
    docker run --rm --name "$name" -e NO_COLOR=1 "$WEB_IMAGE" sh -c \
      'corepack pnpm --filter @classora/web test --maxWorkers=2; web=$?;
      corepack pnpm --filter @classora/api-client test; client=$?;
      [ "$web" -eq 0 ] && [ "$client" -eq 0 ]' >"$LOG_DIR/$part.log" 2>&1 &
  fi
  pids[i]=$!
  names[i]=$name
  echo "Started $part at $(($(now) - started)) s (log: $LOG_DIR/$part.log)"
}

echo "Running $total parts, at most $SHARDS at a time (SUITE=$SUITE)"
running=0
finished=0
while [ "$finished" -lt "$total" ]; do
  while [ "$running" -lt "$SHARDS" ] && [ "$launched" -lt "$total" ]; do
    launch "$launched"
    launched=$((launched + 1))
    running=$((running + 1))
  done
  sleep 2
  i=0
  while [ "$i" -lt "$launched" ]; do
    if [ -z "${codes[i]:-}" ] && ! kill -0 "${pids[i]}" 2>/dev/null; then
      wait "${pids[i]}"
      codes[i]=$?
      running=$((running - 1))
      finished=$((finished + 1))
      echo "Finished ${q_part[i]} at $(($(now) - started)) s (exit ${codes[i]})"
    fi
    i=$((i + 1))
  done
done
trap - INT TERM

# pytest exits 5 when a chunk got no test file (a narrow PYTEST_ARGS): that part
# passes, unless every API part collected nothing.
empty_api=0
api_parts=0
i=0
while [ "$i" -lt "$total" ]; do
  if [ "${q_kind[i]}" = api ]; then
    api_parts=$((api_parts + 1))
    [ "${codes[i]}" = 5 ] && empty_api=$((empty_api + 1))
  fi
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
while [ "$i" -lt "$total" ]; do
  part=${q_part[i]}
  code=${codes[i]}
  result=$(counts "$part" "$LOG_DIR/$part.log")
  if [ "$code" = 0 ]; then
    status=PASS
  elif [ "$code" = 5 ] && [ "${q_kind[i]}" = api ] && [ "$empty_api" -lt "$api_parts" ]; then
    status=PASS
    result="no test files in this part"
  else
    status=FAIL
    failed+=("$part")
  fi
  printf '  %-4s %-15s %s (exit %s)\n' "$status" "$part" "${result:-no summary line}" "$code"
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
