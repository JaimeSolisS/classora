# ADR-0018: Sizing, load profile and recovery objectives

## Status

Accepted (owner-delegated, orchestrator-recommended, **pending owner review**)

## Date

2026-11-12

## Sign-off

No owner sign-off yet. The owner delegated the decisions of #152 (M10-04) on 2026-11-12 ("make recommended decisions"). Escalations E1–E7 of #152 were taken as option (a) and recorded as owner-delegated. Two must be shown to the owner:

- **E1, the load profile.** The issue says "agreed with the Owner". The PM proposed it, and the owner has not reviewed it.
- **E2, simulated data on DEV.** This is an exception to `_docs/runbooks/host.md` ("DEV never gets demo or simulated data").

## Context

Resolves the measured part of architecture §10 **ISSUE-I8** in [`../architecture.md`](../architecture.md) and spec [`../spec.md`](../spec.md) §18 item 7: resource sizing on one small host, deploy interruption tolerance, load testing and recovery objectives.

- [ADR-0010](0010-hosting-edge-and-https.md) set the starting size, **t4g.small** (2 vCPU, 2 GiB, arm64) for DEV, PROD and monitoring, and left t4g.micro "to M10-04 as an experiment". [ADR-0013](0013-backups-restore-drills-and-shutdown-data.md) set RPO 24 hours and an RTO of about 30 minutes, with the final values left to M10-04.
- Each app host runs `db` (PostgreSQL 18), `pgbouncer`, `api` (one Uvicorn process; it also renders receipt and payslip PDFs with WeasyPrint), `worker` (Procrastinate, one job at a time), `edge` (Caddy and the SPA) and the `collector` agent, with Compose `mem_limit`s that add up to 1,792 MiB. The kernel shows 1,840 MiB of the 2 GiB, plus a 1 GiB swap file.
- The spec names no center size. The owner-approved realistic center is the simulation's FULL profile (#543): 40 students in 30 families, 12 tutors and 4 staff.

## Decision

### Load profile and thresholds (E1)

The profile in [`../load-test-report.md`](../load-test-report.md) "Load profile": P1 = 32 concurrent users (4 staff, 8 tutors, 15 guardians, 5 students) with 20–40 s of think time, one SSE stream each, receipt and payslip PDFs about every 5–6 minutes, and an export about every 12 minutes per staff user. P2 = 3× (96 users) for 15 minutes. P3 = a ramp from 3× to 8×.

"Carries the load" means P1 for 30 minutes and P2 for 15 minutes, both with server-side API p95 ≤ 500 ms for `GET` and ≤ 1 s for unsafe methods, PDF p95 ≤ 3 s, under 0.5 % 5xx and network errors, no container OOM-kill or restart, host `MemAvailable` ≥ 15 %, and no sustained swap-in (≤ 1 MB/s over a minute). The owner may change the thresholds later without changing the harness.

<!-- DECISION-RESULTS -->

### Data for the measurement (E2)

DEV is measured with a scrubbed copy of the simulated center in a **side database, `classora_load`**, for under 24 hours. Every password is replaced with a random one kept only on the operator's machine, and sessions, tokens, idempotency keys and rate-limit counters are emptied. The README's documented passwords are proven to fail before the dump leaves the machine. During load mode, api and worker point at `classora_load` through a temporary Compose override that also lifts the per-IP limit (#163) to 60,000 per minute. Afterwards the override is removed, `classora_load` is dropped and the dump is deleted. `classora` is never written: its exact row counts are compared before and after. The harness and the procedure are in [`../runbooks/load-test.md`](../runbooks/load-test.md). No PROD data, and no load, ever reaches PROD.

## Consequences

<!-- CONSEQUENCES -->

## Alternatives considered

- **P1 only** (E1 (b)). Smaller, but gives no headroom figure.
- **Building the dataset through the public API on DEV's real database** (E2 (b)). No rule exception, but about twice the work, and the data would stay on DEV for good. **An empty database** (E2 (c)) would understate every query.
- **A permanent Parameter Store knob for the per-IP limit** (E3 (b)). An extra feature; it stays an optional follow-up.
- **Locust or a custom script** (E4 (b), (c)). A new Python dependency, or re-implementing percentiles and ramps. k6 runs from a pinned image.
- **Measuring t4g.micro on real hardware.** That needs a DEV resize, which this issue may not do.
- **Zero-downtime deploys** (a second api container behind Caddy, blue/green). More memory and moving parts than a center with a release window needs.
