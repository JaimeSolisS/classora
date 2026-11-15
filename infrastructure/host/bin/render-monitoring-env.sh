#!/usr/bin/env bash
# Render /opt/classora/env/monitoring.env from Parameter Store (ADR-0012,
# ADR-0014; M9-09, #617), as root. The monitoring stack's Compose --env-file
# (interpolation only, monitoring-compose.sh): Grafana's admin password, root
# URL and SMTP (SES) settings, the alert recipient, retention and memory
# limits. Mode 0600 owner root; no value is ever printed.
#
# Parameters: /classora/monitoring/ on the dedicated monitoring host
# (CLASSORA_ENV=monitoring), /classora/<env>/monitoring/ on an app host that
# runs the stack colocated (monitoring_mode=colocated in envs/<env>).
# If Parameter Store can't be read and the file exists, it is kept (exit 0
# with a warning), so a reboot without SSM still starts the stack.
set -euo pipefail

# shellcheck source=/dev/null
. /etc/classora/host.env
: "${CLASSORA_ENV:?}" "${AWS_REGION:?}"

if [ "$CLASSORA_ENV" = monitoring ]; then
  prefix=/classora/monitoring/
else
  prefix=/classora/$CLASSORA_ENV/monitoring/
fi

ENV_DIR=/opt/classora/env
ENV_FILE=$ENV_DIR/monitoring.env

install -d -m 0700 "$ENV_DIR"
umask 077
tmp=$(mktemp "$ENV_DIR/.monitoring.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT

if ! params=$(aws ssm get-parameters-by-path --region "$AWS_REGION" --path "$prefix" \
  --recursive --with-decryption --output json); then
  if [ -s "$ENV_FILE" ]; then
    echo "render-monitoring-env: WARNING: Parameter Store unreachable; keeping the existing $ENV_FILE" >&2
    exit 0
  fi
  echo "render-monitoring-env: could not read $prefix and no $ENV_FILE exists" >&2
  exit 1
fi

# Values travel through the environment into Python, never through argv.
PARAMS_JSON=$params PREFIX=$prefix OUT=$tmp DEDICATED=$([ "$CLASSORA_ENV" = monitoring ] && echo 1 || echo 0) \
  python3 - <<'PY'
import json, os, re, sys

prefix = os.environ["PREFIX"]
dedicated = os.environ["DEDICATED"] == "1"
p = {
    item["Name"][len(prefix):]: item["Value"]
    for item in json.loads(os.environ["PARAMS_JSON"]).get("Parameters", [])
}
required = [
    "grafana_admin_password", "grafana_host", "alert_email", "smtp_host",
    "smtp_from", "smtp_username", "smtp_password",
    "retention_metrics", "retention_logs", "retention_traces",
]
if dedicated:
    required.append("ingest_address")
missing = [name for name in required if not p.get(name)]
if missing:
    sys.exit(f"render-monitoring-env: missing parameters under {prefix}: {', '.join(missing)}")
if not re.fullmatch(r"[a-z0-9.-]+", p["grafana_host"]):
    sys.exit("render-monitoring-env: grafana_host is not a hostname")

values = {
    # Grafana (compose.observability.yaml and compose.monitoring.yaml).
    "GRAFANA_ADMIN_PASSWORD": p["grafana_admin_password"],
    "GRAFANA_HOST": p["grafana_host"],
    "GRAFANA_ROOT_URL": f"https://{p['grafana_host']}/",
    "ALERT_EMAIL": p["alert_email"],
    "SMTP_HOST": p["smtp_host"],
    "SMTP_FROM": p["smtp_from"],
    "SMTP_USERNAME": p["smtp_username"],
    "SMTP_PASSWORD": p["smtp_password"],
    # Retention (ADR-0014: metrics 15 days, logs and traces 7).
    "PROMETHEUS_RETENTION": p["retention_metrics"],
    "LOKI_RETENTION": p["retention_logs"],
    "TEMPO_RETENTION": p["retention_traces"],
    # The gateway's published port: the private IP (dedicated host only).
    "MONITORING_INGEST_ADDRESS": p.get("ingest_address", ""),
    # Memory: about 1.4 GB for the stack on a t4g.small (2 GiB, 1 GiB swap),
    # or next to DEV's app stack on a t4g.medium when colocated.
    "OTEL_COLLECTOR_MEM_LIMIT": "160m", "OTEL_COLLECTOR_GOMEMLIMIT": "128MiB",
    "PROMETHEUS_MEM_LIMIT": "320m", "PROMETHEUS_GOMEMLIMIT": "256MiB",
    "LOKI_MEM_LIMIT": "320m", "LOKI_GOMEMLIMIT": "256MiB",
    "TEMPO_MEM_LIMIT": "256m", "TEMPO_GOMEMLIMIT": "200MiB",
    "GRAFANA_MEM_LIMIT": "288m", "GRAFANA_GOMEMLIMIT": "232MiB",
}
safe = re.compile(r"^[A-Za-z0-9_.:/,@ +=-]*$")
with open(os.environ["OUT"], "w", encoding="utf-8") as out:
    out.write("# Rendered by /opt/classora/bin/render-monitoring-env.sh from Parameter Store. Do not edit.\n")
    for key, value in values.items():
        if "\n" in value or not safe.match(value):
            sys.exit(f"render-monitoring-env: {key} has characters an env file can't carry")
        out.write(f"{key}={value}\n")
PY

chmod 0600 "$tmp"
chown root:root "$tmp"
mv -f "$tmp" "$ENV_FILE"
trap - EXIT
echo "render-monitoring-env: wrote $ENV_FILE"
