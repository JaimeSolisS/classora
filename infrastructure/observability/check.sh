#!/usr/bin/env bash
# Validate the observability configuration without starting the stack
# (`make check-observability`, CI job `infra`; ADR-0014). Uses the images
# pinned in compose.observability.yaml, so each file is checked by the exact
# version that runs it. Needs Docker and uv.
#
#  1. the profile is off by default: `docker compose config --services` lists
#     no observability service without `--profile observability`, all five with it;
#  2. `otelcol-contrib validate` for collector/{gateway,agent,local}.yaml;
#  3. `promtool check config` for prometheus/prometheus.yml;
#  4. `loki -verify-config` and `tempo -config.verify` (with env expansion);
#  5. check_config.py: Grafana provisioning, dashboards and alert rules,
#     collector drift, image pins, mem_limits and published ports;
#  6. `promtool check rules` on every PromQL expression of the dashboards and
#     alert rules (written by check_config.py, Grafana variables replaced).
set -euo pipefail

cd "$(dirname "$0")/../.."
obs=infrastructure/observability
compose_file=$obs/compose.observability.yaml
services="otel-collector prometheus loki tempo grafana"
fail=0

image_of() {
  local image
  image=$(sed -n "s|^ *image: \($1[:@].*\)$|\1|p" "$compose_file")
  if [ -z "$image" ]; then
    echo "FAIL no image for $1 in $compose_file" >&2
    exit 1
  fi
  printf '%s\n' "$image"
}

run() {
  echo "--- $1"
  shift
  if "$@"; then
    echo "OK"
  else
    echo "FAIL" >&2
    fail=1
  fi
}

otelcol=$(image_of otel/opentelemetry-collector-contrib)
prometheus=$(image_of prom/prometheus)
loki=$(image_of grafana/loki)
tempo=$(image_of grafana/tempo)
mount=(-v "$PWD/$obs:/obs:ro")

# 1. Off by default. Placeholder values only, and no .env: a developer's
# COMPOSE_PROFILES or project name must not change the answer.
compose_services() {
  env -u COMPOSE_PROFILES COMPOSE_PROJECT_NAME=classora-obs-check \
    POSTGRES_USER=check POSTGRES_PASSWORD=check POSTGRES_DB=check \
    docker compose --env-file /dev/null "$@" config --services
}
check_profile() {
  local default with_profile service
  default=$(compose_services)
  with_profile=$(compose_services --profile observability)
  for service in $services; do
    if grep -qx "$service" <<<"$default"; then
      echo "$service starts without --profile observability" >&2
      return 1
    fi
    if ! grep -qx "$service" <<<"$with_profile"; then
      echo "$service is missing from --profile observability" >&2
      return 1
    fi
  done
  echo "default services: $(tr '\n' ' ' <<<"$default")"
}
run "compose: observability services only under --profile observability" check_profile

# 2. Collector. Placeholder environment: validate needs every ${env:...} set,
# and host_metrics needs its root_path to exist.
for config in gateway agent local; do
  run "otelcol-contrib validate collector/$config.yaml" \
    docker run --rm "${mount[@]}" --mount type=tmpfs,destination=/hostfs \
    -e OTLP_GATEWAY_ENDPOINT=http://gateway.invalid:4318 -e DEPLOYMENT_ENVIRONMENT=check \
    -e PG_MONITOR_USER=check -e PG_MONITOR_PASSWORD=check -e PG_MONITOR_DATABASE=check \
    "$otelcol" validate --config="/obs/collector/$config.yaml"
done

# 3-4. Prometheus, Loki, Tempo.
run "promtool check config prometheus/prometheus.yml" \
  docker run --rm "${mount[@]}" --entrypoint promtool "$prometheus" check config /obs/prometheus/prometheus.yml
run "loki -verify-config loki/loki.yaml" \
  docker run --rm "${mount[@]}" "$loki" -config.file=/obs/loki/loki.yaml -config.expand-env=true -verify-config
run "tempo -config.verify tempo/tempo.yaml" \
  docker run --rm "${mount[@]}" "$tempo" -config.file=/obs/tempo/tempo.yaml -config.expand-env=true -config.verify=true

# 5. Grafana provisioning and alert rules, drift, pins and limits.
promql_dir=$(mktemp -d)
trap 'rm -rf "$promql_dir"' EXIT
# mktemp makes the directory 0700. The pinned images run as non-root users
# (Prometheus: nobody), and on Linux a bind mount keeps host permissions, so
# both the directory and the file must be readable by others.
chmod 755 "$promql_dir"
run "check_config.py" \
  uv run --no-project --isolated --quiet --with pyyaml==6.0.3 python -I "$obs/check_config.py" \
  --promql-out "$promql_dir/promql.yaml" "$obs"

# 6. Every dashboard and alert PromQL expression parses.
if [ -s "$promql_dir/promql.yaml" ]; then
  chmod 644 "$promql_dir/promql.yaml"
  run "promtool check rules (dashboard and alert PromQL)" \
    docker run --rm -v "$promql_dir:/promql:ro" --entrypoint promtool "$prometheus" \
    check rules /promql/promql.yaml
else
  echo "FAIL no PromQL expressions were collected" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "check-observability: FAILED" >&2
  exit 1
fi
echo "check-observability: all checks passed"
