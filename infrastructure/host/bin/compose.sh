#!/usr/bin/env bash
# `docker compose` for the deployed release (ADR-0010, #614), as root:
#   compose.sh <compose arguments>        e.g. compose.sh ps, compose.sh logs api
# Project `classora`: the data and app stack files of the release plus the
# host overlay compose.aws.yaml. Interpolation values come from the rendered
# env file (render-env.sh) and the release's image digests (images.env).
# CLASSORA_RELEASE=<dir> picks another release directory (deploy.sh).
# The collector agent (profile `telemetry`) runs only when the env file has a
# gateway endpoint (/classora/shared/otlp_endpoint, ADR-0014).
set -euo pipefail

release=${CLASSORA_RELEASE:-/srv/classora-data/releases/current}
env_file=/opt/classora/env/app.env

[ -f "$release/images.env" ] || {
  echo "compose.sh: no release at $release (deploy one with deploy.sh <sha>)" >&2
  exit 1
}
[ -f "$env_file" ] || {
  echo "compose.sh: $env_file is missing (run render-env.sh)" >&2
  exit 1
}

profiles=()
if grep -q '^OTLP_GATEWAY_ENDPOINT=.' "$env_file"; then
  profiles=(--profile telemetry)
fi

exec docker compose --project-name classora \
  --project-directory "$release/infrastructure/docker" \
  -f "$release/infrastructure/docker/compose.data.yaml" \
  -f "$release/infrastructure/docker/compose.app.yaml" \
  -f "$release/infrastructure/host/compose.aws.yaml" \
  --env-file "$env_file" --env-file "$release/images.env" \
  "${profiles[@]}" "$@"
