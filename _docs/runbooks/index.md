# Runbooks

Operating Classora's AWS environments (M9). The decisions are in [ADR-0010](../adr/0010-hosting-edge-and-https.md) to [ADR-0014](../adr/0014-observability-error-tracking-and-alerting.md); the architecture is in [`../architecture.md`](../architecture.md) §9. Every runbook uses the owner's AWS profile (`export AWS_PROFILE=jsolisdev AWS_REGION=us-east-2`).

| Runbook | When |
|---|---|
| [`terraform.md`](terraform.md) | Plan, apply and destroy any root (`bootstrap`, `shared`, `envs/dev`, `envs/prod`, `envs/monitoring`); the first bootstrap; static checks. |
| [`host.md`](host.md) | Reach an app host (Session Manager, no SSH), its layout, units, logs, certificates, reboot, instance replacement, volume resize, Owner account. |
| [`deploy.md`](deploy.md) | How a merge reaches DEV, reading a failed deploy, redeploy and rollback on DEV, expand-and-contract migrations. |
| [`release.md`](release.md) | PROD releases: the release PR, the owner's approval, verification, refusals, rollback, the first release and the PROD Owner. |
| [`restore.md`](restore.md) | Nightly dumps and snapshots, restore drills, a real restore, the final dump. |
| [`monitoring.md`](monitoring.md) | The monitoring host, Grafana login, alerts, dedicated / colocated / off. |
| [`secrets.md`](secrets.md) | Where each secret lives and how to rotate it (including the PostgreSQL owner password). |
| [`teardown.md`](teardown.md) | The end of the environments' window: final dump, `make env-teardown MODE=dev-only\|all`, what survives, restoring the archive later. |

## Rehearsals (evidence)

| Procedure | Rehearsed | Evidence |
|---|---|---|
| Deploy, readiness rollback, migration failure (DEV) | M9-07, 2026-11-12 | [#615 QA](https://github.com/JaimeSolisS/classora/issues/615#issuecomment-6813508937) |
| Dump and snapshot restore drills, final dump (DEV drill) | M9-08, 2026-11-12 | [#616 QA](https://github.com/JaimeSolisS/classora/issues/616#issuecomment-6814346660) |
| Host first boot, certificate, Session Manager | M9-06, 2026-11-11 | [#614 QA](https://github.com/JaimeSolisS/classora/issues/614#issuecomment-6811066222) |
| Monitoring host, alerts, retention | M9-09, 2026-11-12 | [#617 QA](https://github.com/JaimeSolisS/classora/issues/617#issuecomment-6817834298) |
| PROD apply, HTTPS, destroy guard refusal, approval gate | M9-10 | [#618](https://github.com/JaimeSolisS/classora/issues/618) |
