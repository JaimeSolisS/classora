"""Static checks of the observability configuration (`make check-observability`).

Run by infrastructure/observability/check.sh with PyYAML only (no project):
    uv run --no-project --isolated --with pyyaml==6.0.3 python -I check_config.py <dir>

It never starts anything. It checks what the services' own validators don't:
- Grafana: data sources, the dashboard provider and every dashboard parse; uids
  are unique; every data-source reference (links between data sources and
  every panel's data source) points at a provisioned uid; every panel query
  filters on the $env variable (#624); grafana.ini keeps anonymous access and
  sign-up off.
- Grafana alert rules (provisioning/alerting): structure, unique uids, every
  condition and expression reference resolves, every query's data source is
  provisioned, and the ADR-0014 rules are all there (#612).
- PromQL: with `--promql-out FILE`, every Prometheus expression of the alert
  rules and dashboards is written to FILE as a Prometheus rule file (Grafana
  variables replaced), so check.sh can parse them with `promtool check rules`.
- Collector drift: collector/local.yaml's receivers are agent.yaml's minus
  file_log and host_metrics, and its exporters are gateway.yaml's.
- compose.observability.yaml: every image is pinned by tag and digest, every
  service has a mem_limit (local defaults under 700 MB in total), and only
  Grafana publishes a port, on 127.0.0.1.
"""

import configparser
import json
import re
import sys
from pathlib import Path

import yaml

LOCAL_ONLY_RECEIVERS = {"file_log", "file_log/host", "host_metrics"}
# The ADR-0014 rules (#612, and the backup rules of M9-09, #617).
REQUIRED_ALERT_RULES = {
    "classora-host-cpu-high",
    "classora-host-memory-low",
    "classora-disk-high",
    "classora-readiness-failing",
    "classora-job-failed",
    "classora-job-stalled",
    "classora-backup-failed",
    "classora-backup-missing",
}
# The dashboards ADR-0014 and #612 list.
REQUIRED_DASHBOARDS = {
    "classora-host-containers",
    "classora-api",
    "classora-database",
    "classora-jobs",
    "classora-errors",
    "classora-logs",
}
NO_DATA_STATES = {"OK", "NoData", "Alerting", "KeepLast"}
EXEC_ERR_STATES = {"OK", "Error", "Alerting", "KeepLast"}
DURATION = re.compile(r"\d+[smhd]")
# Grafana's template variables, replaced so promtool can parse the expression.
GRAFANA_VARIABLES = {
    "$__rate_interval": "1m",
    "$__interval": "1m",
    "$__range": "1h",
    "$__auto": "1m",
}
MEM_BUDGET_BYTES = 700 * 1024 * 1024
UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}

errors: list[str] = []
promql: list[tuple[str, str]] = []  # (where, expression)


def fail(message: str) -> None:
    errors.append(message)


def load_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        fail(f"{path}: invalid YAML: {exc}")
        return None


def walk(node: object, key: str) -> list[object]:
    """Every value stored under `key`, at any depth."""
    found: list[object] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(walk(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(walk(item, key))
    return found


def check_grafana(root: Path) -> dict[str, str]:
    grafana = root / "grafana"
    datasources: dict[str, str] = {}
    ds_files = sorted((grafana / "provisioning" / "datasources").glob("*.yaml"))
    if not ds_files:
        fail("grafana/provisioning/datasources: no data source file")
    for path in ds_files:
        doc = load_yaml(path)
        if not isinstance(doc, dict) or doc.get("apiVersion") != 1:
            fail(f"{path}: expected a mapping with apiVersion: 1")
            continue
        for ds in doc.get("datasources") or []:
            uid, name = ds.get("uid"), ds.get("name")
            if not uid or not name or not ds.get("type") or not ds.get("url"):
                fail(f"{path}: data source {name!r} needs name, uid, type and url")
                continue
            if uid in datasources:
                fail(f"{path}: duplicate data source uid {uid!r}")
            datasources[uid] = ds["type"]
        for ref in walk(doc, "datasourceUid"):
            if ref not in datasources:
                fail(f"{path}: datasourceUid {ref!r} is not a provisioned data source")

    dashboard_dirs: list[str] = []
    for path in sorted((grafana / "provisioning" / "dashboards").glob("*.yaml")):
        doc = load_yaml(path)
        if not isinstance(doc, dict) or doc.get("apiVersion") != 1:
            fail(f"{path}: expected a mapping with apiVersion: 1")
            continue
        for provider in doc.get("providers") or []:
            dashboard_dirs.append((provider.get("options") or {}).get("path", ""))
    if dashboard_dirs != ["/etc/grafana/dashboards"]:
        fail(f"dashboard providers: expected /etc/grafana/dashboards: {dashboard_dirs}")

    dashboard_files = sorted((grafana / "dashboards").glob("*.json"))
    if not dashboard_files:
        fail("grafana/dashboards: no dashboard")
    uids: set[str] = set()
    for path in dashboard_files:
        try:
            dashboard = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            fail(f"{path}: invalid JSON: {exc}")
            continue
        uid = dashboard.get("uid")
        if not uid or not dashboard.get("title"):
            fail(f"{path}: needs uid and title")
        elif uid in uids:
            fail(f"{path}: duplicate dashboard uid {uid!r}")
        else:
            uids.add(uid)
        if dashboard.get("id") is not None:
            fail(f"{path}: id must be null (Grafana assigns it)")
        if not dashboard.get("panels"):
            fail(f"{path}: no panels")
        for panel in dashboard.get("panels") or []:
            for target in panel.get("targets") or []:
                ds = target.get("datasource") or panel.get("datasource") or {}
                # One monitoring host serves DEV and PROD: every query filters
                # on the dashboard's env variable (#624, QA #617 N1).
                query = target.get("expr") or target.get("query") or ""
                if not re.search(r"\$(?:env\b|\{env[}:])", query):
                    fail(
                        f"{path.name}: {panel.get('title')!r} "
                        f"{target.get('refId')}: query doesn't filter on $env"
                    )
                if ds.get("type") == "prometheus" and target.get("expr"):
                    where = f"{path.name}: {panel.get('title')!r} {target.get('refId')}"
                    promql.append((where, target["expr"]))
        for ref in walk(dashboard, "datasource"):
            if not isinstance(ref, dict):
                fail(f"{path}: data source {ref!r} must be {{type, uid}}")
                continue
            ds_uid, ds_type = ref.get("uid"), ref.get("type")
            if ds_uid not in datasources:
                fail(f"{path}: panel data source {ds_uid!r} is not provisioned")
            elif ds_type != datasources[ds_uid]:
                fail(f"{path}: data source {ds_uid!r} has type {ds_type!r}")

    ini = configparser.ConfigParser()
    ini.read_string((grafana / "grafana.ini").read_text())
    if ini.get("auth.anonymous", "enabled", fallback="") != "false":
        fail("grafana.ini: [auth.anonymous] enabled must be false")
    if ini.get("users", "allow_sign_up", fallback="") != "false":
        fail("grafana.ini: [users] allow_sign_up must be false")
    if (
        ini.get("security", "disable_brute_force_login_protection", fallback="")
        != "false"
    ):
        fail("grafana.ini: brute-force login protection must stay on")
    home = ini.get("dashboards", "default_home_dashboard_path", fallback="")
    if home and Path(home).name not in {p.name for p in dashboard_files}:
        fail(f"grafana.ini: home dashboard {home} is not in grafana/dashboards")
    missing = REQUIRED_DASHBOARDS - uids
    if missing:
        fail(f"grafana/dashboards: missing dashboards {sorted(missing)}")
    return datasources


def check_alerting(root: Path, datasources: dict[str, str]) -> None:
    files = sorted((root / "grafana" / "provisioning" / "alerting").glob("*.yaml"))
    if not files:
        fail("grafana/provisioning/alerting: no alert rule file")
    uids: set[str] = set()
    for path in files:
        doc = load_yaml(path)
        if not isinstance(doc, dict) or doc.get("apiVersion") != 1:
            fail(f"{path}: expected a mapping with apiVersion: 1")
            continue
        for group in doc.get("groups") or []:
            gname = group.get("name")
            if not gname or not group.get("folder") or group.get("orgId") != 1:
                fail(f"{path}: group {gname!r} needs name, folder and orgId: 1")
            if not DURATION.fullmatch(str(group.get("interval", ""))):
                fail(f"{path}: group {gname!r}: interval must be a duration")
            for rule in group.get("rules") or []:
                check_rule(path, rule, uids, datasources)
    missing = REQUIRED_ALERT_RULES - uids
    if missing:
        fail(f"grafana/provisioning/alerting: missing rules {sorted(missing)}")


def check_rule(
    path: Path, rule: dict, uids: set[str], datasources: dict[str, str]
) -> None:
    uid = rule.get("uid")
    where = f"{path}: rule {uid!r}"
    if not uid or len(uid) > 40 or not re.fullmatch(r"[a-zA-Z0-9_-]+", uid):
        fail(f"{where}: uid must be 1-40 letters, digits, - or _")
    elif uid in uids:
        fail(f"{where}: duplicate uid")
    else:
        uids.add(uid)
    if not rule.get("title"):
        fail(f"{where}: no title")
    if not DURATION.fullmatch(str(rule.get("for", ""))):
        fail(f"{where}: 'for' must be a duration")
    if rule.get("noDataState") not in NO_DATA_STATES:
        fail(f"{where}: noDataState must be one of {sorted(NO_DATA_STATES)}")
    if rule.get("execErrState") not in EXEC_ERR_STATES:
        fail(f"{where}: execErrState must be one of {sorted(EXEC_ERR_STATES)}")
    if not (rule.get("annotations") or {}).get("summary"):
        fail(f"{where}: no summary annotation")
    data = rule.get("data") or []
    ref_ids = [d.get("refId") for d in data]
    if len(set(ref_ids)) != len(ref_ids) or not data:
        fail(f"{where}: data needs unique refIds")
    if rule.get("condition") not in ref_ids:
        fail(f"{where}: condition {rule.get('condition')!r} is not a refId")
    for query in data:
        ref, ds_uid = query.get("refId"), query.get("datasourceUid")
        model = query.get("model") or {}
        if model.get("refId") != ref:
            fail(f"{where}: {ref}: model.refId must match")
        if ds_uid == "__expr__":
            if model.get("expression") not in ref_ids:
                fail(f"{where}: {ref}: expression {model.get('expression')!r} unknown")
            if model.get("type") not in {"reduce", "threshold", "math", "classic_conditions", "resample"}:
                fail(f"{where}: {ref}: unknown expression type {model.get('type')!r}")
            continue
        if ds_uid not in datasources:
            fail(f"{where}: {ref}: data source {ds_uid!r} is not provisioned")
            continue
        if not (query.get("relativeTimeRange") or {}).get("from"):
            fail(f"{where}: {ref}: needs relativeTimeRange.from")
        if datasources[ds_uid] == "prometheus":
            if not model.get("expr"):
                fail(f"{where}: {ref}: no expr")
            else:
                promql.append((f"{path.name}: {uid} {ref}", model["expr"]))


def write_promql_rules(out: Path) -> None:
    """Every collected PromQL expression as a Prometheus recording-rule file."""
    rules = []
    for n, (where, expr) in enumerate(promql):
        for variable, value in GRAFANA_VARIABLES.items():
            expr = expr.replace(variable, value)
        expr = re.sub(r"\$\{?(\w+)\}?", ".*", expr)  # dashboard variables ($env)
        rules.append({"record": f"check:expr_{n}", "expr": expr, "labels": {"source": where}})
    out.write_text(yaml.safe_dump({"groups": [{"name": "check", "rules": rules}]}))
    print(f"{len(rules)} PromQL expressions written for promtool")


def check_collector_drift(root: Path) -> None:
    collector = root / "collector"
    agent = load_yaml(collector / "agent.yaml")
    gateway = load_yaml(collector / "gateway.yaml")
    local = load_yaml(collector / "local.yaml")
    if not all(isinstance(doc, dict) for doc in (agent, gateway, local)):
        fail("collector: agent.yaml, gateway.yaml and local.yaml must be mappings")
        return
    expected = {
        k: v for k, v in agent["receivers"].items() if k not in LOCAL_ONLY_RECEIVERS
    }
    if local.get("receivers") != expected:
        fail("collector/local.yaml: receivers differ from agent.yaml's")
    if local.get("exporters") != gateway.get("exporters"):
        fail("collector/local.yaml: exporters differ from gateway.yaml's")


def check_notifications(path: Path) -> None:
    """The AWS-only contact point and notification policy (M9-09, #617)."""
    doc = load_yaml(path)
    if not isinstance(doc, dict) or doc.get("apiVersion") != 1:
        fail(f"{path}: expected a mapping with apiVersion: 1")
        return
    names = {cp.get("name") for cp in doc.get("contactPoints") or []}
    receivers = [
        r for cp in doc.get("contactPoints") or [] for r in cp.get("receivers") or []
    ]
    if names != {"owner-email"} or [r.get("type") for r in receivers] != ["email"]:
        fail(f"{path}: expected one e-mail contact point named owner-email")
    for receiver in receivers:
        if (receiver.get("settings") or {}).get("addresses") != "${ALERT_EMAIL}":
            fail(f"{path}: the address must come from ${{ALERT_EMAIL}}, never the file")
    policies = doc.get("policies") or []
    if len(policies) != 1 or policies[0].get("receiver") != "owner-email":
        fail(f"{path}: expected one policy routing to owner-email")
    elif not DURATION.fullmatch(str(policies[0].get("repeat_interval", ""))):
        fail(f"{path}: the policy needs a repeat_interval")


def mem_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+)([kmg]?)b?", value.strip().lower())
    if not match:
        raise ValueError(value)
    return int(match.group(1)) * UNITS.get(match.group(2), 1)


def default_of(value: object) -> str:
    """The default in a `${VAR:-default}` value, or the value itself."""
    text = str(value)
    match = re.fullmatch(r"\$\{[A-Z0-9_]+:-([^}]*)\}", text)
    return match.group(1) if match else text


def check_compose(root: Path) -> None:
    path = root / "compose.observability.yaml"
    doc = load_yaml(path)
    if not isinstance(doc, dict):
        fail(f"{path}: not a mapping")
        return
    total = 0
    for name, service in (doc.get("services") or {}).items():
        image = str(service.get("image", ""))
        if not re.fullmatch(r"[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}", image):
            fail(f"{path}: {name}: pin as name:tag@sha256:<digest>: {image!r}")
        if "mem_limit" not in service:
            fail(f"{path}: {name}: no mem_limit")
        else:
            try:
                total += mem_bytes(default_of(service["mem_limit"]))
            except ValueError:
                fail(f"{path}: {name}: unreadable mem_limit {service['mem_limit']!r}")
        ports = service.get("ports") or []
        if name != "grafana" and ports:
            fail(f"{path}: {name}: publishes ports; only grafana may")
        for port in ports:
            if not str(port).startswith("127.0.0.1:"):
                fail(f"{path}: {name}: port {port!r} must be bound to 127.0.0.1")
    mib = total // 1024**2
    if total >= MEM_BUDGET_BYTES:
        fail(f"{path}: default mem_limits add up to {mib} MiB (budget 700)")
    else:
        print(f"compose.observability.yaml: default mem_limits add up to {mib} MiB")


def main() -> int:
    args = sys.argv[1:]
    promql_out = None
    if "--promql-out" in args:
        at = args.index("--promql-out")
        promql_out = Path(args[at + 1])
        del args[at : at + 2]
    root = Path(args[0]) if args else Path(__file__).parent
    datasources = check_grafana(root)
    check_alerting(root, datasources)
    check_collector_drift(root)
    check_compose(root)
    check_notifications(root.parent / "host" / "monitoring" / "notifications.yaml")
    if promql_out is not None:
        write_promql_rules(promql_out)
    for message in errors:
        print(f"FAIL {message}", file=sys.stderr)
    if not errors:
        print("grafana provisioning, alert rules, collector drift and compose limits: OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
