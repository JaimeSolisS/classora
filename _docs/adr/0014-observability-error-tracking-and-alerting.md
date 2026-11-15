# ADR-0014: Observability, error tracking and alerting

## Status

Accepted

## Date

2026-11-11

## Sign-off

Owner: JaimeSolisS. Direction given, and the M9 plan approved, on 2026-11-11: no Sentry; full OTel Collector + Prometheus + Loki + Tempo + Grafana on its own monitoring EC2, with a Terraform switch to run it on DEV; an optional local profile, off by default, with instrumentation that does nothing when it is off; decisions 6, 7, 9, 13 and 15. Escalations E3 (what the budget counts) and E5 (Loki/Tempo storage) of the plan: the owner chose option (a) for each, the `Project=classora` tag filter and the monitoring host's own disk. Recorded by M9-01 (#609).

## Context

Resolves architecture §10 **ISSUE-O1**, **ISSUE-O2** and **ISSUE-O3** in [`../architecture.md`](../architecture.md) §10.11, and spec [`../spec.md`](../spec.md) §18 item 3. Changes spec §10 "Observability" (alerts were dashboard-only, and Sentry was planned).

- The API logs one JSON object per line through stdlib `logging`, with `request_id`, and redacts sensitive `extra` keys (`app.core.logging`).
- `/health/live` and `/health/ready` exist.
- Failed jobs are recorded in `job_failures` (#128).
- The owner's machine has 8 GB of RAM.
- The data includes minors' records, so telemetry must not carry personal data.
- The AWS account also holds unrelated projects.

## Decision

### Error tracking (O1)

**No Sentry.** Unhandled exceptions are recorded as span events (exception type and stack, see "Personal data") and as `ERROR` log lines carrying `request_id` and `trace_id`. Grafana has an "Errors" dashboard. Browser errors are not collected in this version.

### Pipeline (O2)

- **Agent:** each app host runs an **OpenTelemetry Collector (contrib) agent** in the app stack, in Compose profile `telemetry`. It:
  - receives OTLP/HTTP from `api` and `worker`;
  - tails container logs with `filelog` (Caddy, PostgreSQL, PgBouncer and the backup result lines);
  - collects `hostmetrics`, `docker_stats` and `postgresql` metrics;
  - checks `/health/ready` from inside the host network with `httpcheck`;
  - runs `sqlquery` for Procrastinate: jobs by status, failed jobs in the last hour, `doing` jobs older than 30 minutes;
  - exports OTLP/HTTP to the gateway.

  No node_exporter or postgres_exporter, and no queue-depth code in the app.
- **Gateway:** the monitoring stack (`infrastructure/observability/compose.observability.yaml`) runs a **Collector gateway** that fans out to **Prometheus** (OTLP receiver), **Loki** (OTLP) and **Tempo** (OTLP), with **Grafana** on top. Dashboards, data sources and alert rules are provisioned from files.
- **Storage (plan E5 (a), decision 15):** Prometheus, Loki and Tempo use filesystem storage on the **monitoring host's encrypted gp3 data volume**, the same as the local profile. There is **no S3 storage** for telemetry: no bucket, no IAM policy for it. Retention defaults: metrics 15 days, logs 7 days, traces 7 days, as Terraform variables.
- **Placement** (Terraform `monitoring_mode` in `envs/dev`):
  - `dedicated` (default): its own t4g.small host in `envs/monitoring`.
  - `colocated`: the same Compose project runs on DEV, and DEV becomes t4g.medium.
  - `off`: no monitoring stack.

  The active gateway endpoint is published as `/classora/shared/otlp_endpoint`. When it is absent, the agents and the app's exporter stay off.
- **Ingest security:** the gateway listens on 4318 on its private IP only. Its security group admits 4318 only from the app hosts' security groups. Traffic stays inside the VPC in one AZ. It is not TLS-encrypted, which is accepted for in-VPC traffic at this scale.

### Application instrumentation

- `OTEL_EXPORTER_OTLP_ENDPOINT` is a validated setting in `app.core.config`, empty by default. **When it is empty, no SDK provider, exporter, processor or instrumentation is created**: the OTel API stays the no-op default, and nothing runs per request.
- When set:
  - FastAPI and SQLAlchemy instrumentation;
  - one span per Procrastinate job;
  - OTLP/HTTP export of traces, metrics and logs;
  - `trace_id` and `span_id` added to every JSON log line.

  The new runtime dependencies are the OpenTelemetry API, SDK, OTLP/HTTP exporter and the FastAPI and SQLAlchemy instrumentations (approved in M9-04 (#612)).
- Service names are `classora-api` and `classora-worker`, with `deployment.environment.name` set to `local`, `dev` or `prod`.

### Personal data

- Spans keep `http.route` (the template), method and status. They drop the query string, `url.full`, request and response headers and bodies, and client address.
- SQL spans carry the statement text with placeholders and never bind values.
- Exception events keep the type and the stack frames. They drop the exception **message**, including the last traceback line, because driver messages such as asyncpg's `DETAIL: Key (username)=(…)` contain values.
- Logs exported over OTLP pass through the same `redact()` as stdout.
- Procrastinate's own job lines keep the task name and job ID only. Job arguments and results are dropped from the message and the extras, on stdout and over OTLP (#624). No user, student or guardian identifiers are added as span or metric attributes.
- A test plants distinctive personal data and asserts that none of it reaches any exported span, event, log or metric.

### Grafana access (O3)

- `https://grafana.<domain>` through Caddy (ADR-0010) on whichever host runs the stack.
- Grafana login, with the admin password from Parameter Store (ADR-0012). Anonymous access off, sign-up off, and Grafana's built-in brute-force login lockout on.
- Locally, Grafana listens on 127.0.0.1 only.

### Alerts

- Grafana-managed rules, provisioned as files:
  - host CPU above 90% for 10 minutes;
  - available memory below 10% for 10 minutes;
  - disk above 85% (root and data);
  - readiness failing for 2 minutes;
  - any failed job in the last hour;
  - a job `doing` for over 30 minutes;
  - no successful backup in 26 hours, or a failed backup.
- **Delivery by e-mail** to the owner through Amazon SES in us-east-2 (decision 7). The domain is verified through DKIM records in Route 53. While the account is in the SES sandbox, the recipient is a verified identity. The SMTP credentials follow ADR-0012.
- Locally, rules evaluate and show in Grafana with no contact point.
- **Budget alarm:** an AWS Budget of US$75/month e-mails the owner directly at 80% forecast and 100% actual (decision 9). It counts **only costs tagged `Project=classora`** (plan E3 (a), decision 13), because the account also holds other projects. The owner activates `Project` as a user-defined cost allocation tag in the Billing console once (it appears up to 24 hours after the first tagged resource). Untaggable costs, such as the domain fee, are not counted.
- These e-mails are operational, to the owner only. User-facing e-mail stays out of scope (spec §14).

### Local profile

`make dev-observability` starts the same five services from the same configuration files, plus a local collector that plays agent and gateway, through Compose profile `observability`. It is off by default (memory), and `make dev` starts none of it. CI validates every configuration file and starts nothing.

### Delivery

M9-03 (#611) builds the local profile and shared configuration; M9-04 (#612) the app telemetry, dashboards and alert rules; M9-05 (#613) the SES identity and the budget; M9-09 (#617) the monitoring host, Grafana over HTTPS and e-mail delivery.

## Consequences

- The monitoring host costs about $19/month dedicated, or $12/month extra on DEV when colocated.
- Telemetry is lost when its host is destroyed. This is accepted for ~20-day environments.
- Spec §10 "Observability" (no Sentry; e-mail alerts to the owner) and §14 ("Infrastructure alerts are dashboard-only", "Sentry hosting") were rewritten, and spec §18.3 closed, in M9-01 (#609).
- The old issue "Error tracking per ADR" (#146) is closed as not planned.
- Web client errors stay invisible to operators until a follow-up adds a reporting endpoint.
- The budget alarm does not see costs that carry no `Project=classora` tag, or that arrive before the tag is activated.

## Alternatives considered

- **Sentry SaaS** (O1 option A). Rejected by the owner: a third-party processor for minors' data.
- **Self-hosted Sentry.** Needs Redis, Kafka and ClickHouse, which contradicts "no Redis" and won't fit the host.
- **Agents writing straight to Prometheus, Loki and Tempo** (no gateway). One fewer process, but the owner chose a collector on the monitoring host. The gateway also keeps agents unaware of the backends.
- **Loki and Tempo on S3** (plan E5 (b)). Survives host destroy, at the cost of buckets, IAM and a local/AWS difference.
- **A whole-account budget** (plan E3 (b)). No console step, but it would e-mail about costs other projects cause.
- **Dashboard-only alerts** (spec as written). Replaced by decision 7: nobody watches the dashboard of a 20-day environment.
