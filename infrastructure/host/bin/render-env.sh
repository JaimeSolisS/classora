#!/usr/bin/env bash
# Render /opt/classora/env/app.env from Parameter Store (ADR-0012, #614).
# Runs as root at boot (classora-data.service) and on every deploy. Reads
# /classora/<env>/ and /classora/shared/ with the instance role, writes the
# file atomically with mode 0600 owner root, and never prints a value.
#
# The file is Compose's --env-file (interpolation only, compose.sh): each
# service receives only the variables its `environment:` names, so api, worker
# and pgbouncer get the runtime role classora_app and never the owner's
# password; db and the migrate tool get the owner's (#165).
#
# If Parameter Store can't be read and an env file already exists, the old
# file is kept (exit 0 with a warning), so a reboot without network access to
# SSM still starts the stacks. Without an existing file it fails.
set -euo pipefail

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}" "${AWS_REGION:?}"

ENV_DIR=/opt/classora/env
ENV_FILE=$ENV_DIR/app.env

install -d -m 0700 "$ENV_DIR"
umask 077
tmp=$(mktemp "$ENV_DIR/.app.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT

fetch() {
  aws ssm get-parameters-by-path --region "$AWS_REGION" --path "$1" \
    --recursive --with-decryption --output json
}

if ! own=$(fetch "/classora/$CLASSORA_ENV/") || ! shared=$(fetch /classora/shared/); then
  if [ -s "$ENV_FILE" ]; then
    echo "render-env: WARNING: Parameter Store unreachable; keeping the existing $ENV_FILE" >&2
    exit 0
  fi
  echo "render-env: could not read Parameter Store and no $ENV_FILE exists" >&2
  exit 1
fi

# Values travel through the environment into Python, never through argv.
OWN_JSON=$own SHARED_JSON=$shared CLASSORA_ENV=$CLASSORA_ENV OUT=$tmp DOCKER_GID=$(getent group docker | cut -d: -f3) \
  python3 - <<'PY'
import json, os, re, sys

env = os.environ["CLASSORA_ENV"]

def params(var, prefix):
    out = {}
    for p in json.loads(os.environ[var]).get("Parameters", []):
        out[p["Name"][len(prefix):]] = p["Value"]
    return out

own = params("OWN_JSON", f"/classora/{env}/")
shared = params("SHARED_JSON", "/classora/shared/")

required = ["postgres_password", "app_db_password", "monitor_db_password", "app_origin", "site_addresses"]
missing = [name for name in required if not own.get(name)]
if missing:
    sys.exit(f"render-env: missing parameters under /classora/{env}/: {', '.join(missing)}")

otlp = shared.get("otlp_endpoint", "").strip()
values = {
    # The database: owner (db, migrate) and the runtime role (api, worker, pgbouncer).
    "POSTGRES_USER": "classora",
    "POSTGRES_DB": "classora",
    "POSTGRES_PASSWORD": own["postgres_password"],
    "POSTGRES_APP_PASSWORD": own["app_db_password"],
    # App settings (app/core/config.py).
    "APP_ENV": env,
    "APP_ORIGIN": own["app_origin"],
    "SESSION_COOKIE_SECURE": "true",
    # The edge's fixed address on the app network (compose.aws.yaml).
    "TRUSTED_PROXIES": "172.30.0.10/32",
    # The edge (Caddyfile).
    "SITE_ADDRESS": own["site_addresses"],
    "ACME_CA": own.get("acme_ca", "production"),
    # Telemetry (ADR-0014): on only when the monitoring gateway exists.
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318" if otlp else "",
    "OTLP_GATEWAY_ENDPOINT": otlp,
    "DEPLOYMENT_ENVIRONMENT": env,
    "PG_MONITOR_USER": "classora_monitor",
    "PG_MONITOR_PASSWORD": own["monitor_db_password"],
    "PG_MONITOR_DATABASE": "classora",
    "DOCKER_GID": os.environ.get("DOCKER_GID", ""),
    # Where the monitoring stack runs (envs/<env> monitoring_mode, #617):
    # deploy.sh starts it on this host only when colocated.
    "MONITORING_MODE": own.get("monitoring_mode", "dedicated"),
}
safe = re.compile(r"^[A-Za-z0-9_.:/,@ +=-]*$")
with open(os.environ["OUT"], "w", encoding="utf-8") as out:
    out.write("# Rendered by /opt/classora/bin/render-env.sh from Parameter Store. Do not edit.\n")
    for key, value in values.items():
        if "\n" in value or not safe.match(value):
            sys.exit(f"render-env: {key} has characters an env file can't carry")
        out.write(f"{key}={value}\n")
PY

chmod 0600 "$tmp"
chown root:root "$tmp"
mv -f "$tmp" "$ENV_FILE"
trap - EXIT
echo "render-env: wrote $ENV_FILE"
