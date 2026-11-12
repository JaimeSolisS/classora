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

### Measured results

DEV, t4g.small, 2026-11-12; full tables in [`../load-test-report.md`](../load-test-report.md). Server-side figures.

| Run | GET p95 | Unsafe p95 | PDF p95 | 5xx | MemAvailable min | Worst 1-min swap-in | Verdict |
|---|---|---|---|---|---|---|---|
| P1, starting values | 441 ms | 142 ms | 500 ms | 0 | 14.9 % | 0.49 MiB/s | fails memory (marginally) |
| P2, starting values | 699 ms | 414 ms | 973 ms | 0 | 13 % | 0.89 MiB/s | fails GET p95 and memory |
| P2, tuned | 444 ms | 260 ms | 646 ms | 0 | 19 % | 0.02 MiB/s | **passes** |
<!-- ADR-P1-TUNED -->
| P3 ramp, tuned | 2.1 s (whole ramp) | 1.9 s | 2.3 s | 0 | 12 % | 0.81 MiB/s | breakpoint about 6–7× |

No container restarted or was OOM-killed. Throughput levels off at about 26–27 requests/s, with the one Uvicorn process near a full vCPU; from about 4× the GET p95 is above 500 ms. The system slows down under overload but returns no errors.

### Tuned values (committed)

- **PgBouncer `default_pool_size` 10 → 5** (`infrastructure/docker/pgbouncer/pgbouncer.ini`). Each PostgreSQL backend that PgBouncer keeps open holds about 40 MiB of catalog and plan caches. Ten of them drove the host into swap at 3×. With five, P2's MemAvailable went from 13 % to 19 % and swap-in from 264 MiB to 5 MiB, and the API's SQL p95 fell from 46 to 25 ms (requests wait briefly in PgBouncer instead of the host swapping).
- **Host healthchecks: api every 10 s (was 2 s), worker every 30 s (was 5 s)**, with `start_interval: 2s` during a 30 s `start_period`, so start-up and deploys still see a healthy container within seconds (`infrastructure/host/compose.aws.yaml`; local Compose is unchanged). Each worker check starts Python and imports the whole app, about 2.5 CPU-seconds on t4g. Every 5 s, that was half a vCPU around the clock: DEV sat at about 33 % CPU with no load, above the t4g.small credit baseline. At 3×, the worker's CPU fell from 49 % to 16 % of a vCPU.

### Kept, with the reason

- **`mem_limit`s** (db 512m, pgbouncer 64m, api 512m, worker 448m, edge 96m, collector 160m; 1,792 MiB in all). Every container peaked below its limit. Limits are ceilings, not reservations: the measured peaks add up to about 1.2 GiB, and the host kept ≥ 19 % available at 3× once tuned. The db's figure includes page cache charged to its cgroup, which the kernel reclaims at the limit; the api keeps 512m for concurrent PDF rendering (peak 307 MiB); the edge reached 89 of 96 MiB only at about 7.5× (240 open streams). On the nominal 2 GiB the limits leave 256 MiB for the system. The kernel shows 1,841 MiB, so the limits could in theory all be reached together; the 1 GiB swap covers that case, and it never happened.
- **PostgreSQL `shared_buffers` 128 MB, `work_mem` 4 MB, `max_connections` 100.** The 45 MB term-sized database is a 100 % buffer hit, no query spilled to a temp file, and at most 29 backends were open.
- **The API's SQLAlchemy pool** (5 plus 10 overflow) and the worker's direct connections. Up to 15 connections checked out at 3×, with no client error.
- **`WORKER_CONCURRENCY = 1`.** Exports finished within a second (p95 under 0.5 s) and no job queued behind another.
- **One Uvicorn process.** It is the throughput limit (about 26 requests/s, from 6–7×). A second process would add about 280 MiB and split #163's per-process limit; that is the lever for a much larger center, together with a resize.

### Instance size

- **DEV and PROD stay on t4g.small** (2 vCPU, 2 GiB). Tuned, it carries the 3× growth profile (about a 120-student center) within every threshold, with breakpoint headroom to about 6×.
- **t4g.micro (1 GiB) is rejected.** The containers alone peak at about 1.2 GiB at 1×, before the OS, Docker and the SSM agent (about 250 MiB). It would need every limit roughly halved, plus PDFs in a 1 GiB host, with no margin. The rule was "only if steady memory fits 1 GiB with 25 % margin"; it does not.
- **Step up to t4g.medium** (4 GiB, same 2 vCPU) if, on PROD, the center grows past about 120 active students or 3× this profile, MemAvailable stays under 15 % or swap-in is sustained, or the API's GET p95 stays above 500 ms. Adding vCPUs (t4g.large) would only help together with a second Uvicorn process. Any resize is a later owner Terraform apply (an `instance_type` variable change); none is applied here.
- **Monitoring host:** not measured here (ADR-0010 and ADR-0014 keep t4g.small).

### CPU credits

t4g instances stay in **`unlimited`** credit mode. The baseline is 20 % per vCPU (0.4 vCPU in all on a t4g.small). Before tuning, DEV used about 33 % with no load, so it was always above the baseline: the surplus balance grew about 34 credits an hour and, once capped, would cost roughly $8–16 a month per host. With the tuned healthchecks, idle use falls under the baseline, and load bursts of a center's afternoon are paid from earned credits. The surplus charge (`CPUSurplusCreditsCharged`) is the metric to watch.

### Deploy downtime tolerance

- **Tolerance: up to 2 minutes of API unavailability per release.** Measured on DEV: recreating api and worker (the switch `deploy.sh` makes after pulling and migrating) left the API answering 502 for **8 s**, and the SPA served throughout. The 90 s readiness gate and its rollback stay as they are.
- **PROD releases** happen at a time the owner picks, outside center hours (the release workflow already waits for the owner's approval).
- **No zero-downtime engineering** (a second api container, blue/green): it would cost memory the host does not have spare, for seconds of downtime.

### Recovery objectives

- **RPO: 24 hours**, confirmed (nightly dump, ADR-0013).
- **RTO: at most 1 hour.** Measured: the `pg_restore` of the term-sized dump (45 MB restored) takes 3.1 s on DEV, and its download about 1 s, so a dump restore on the existing host is dominated by the runbook's checks (about 30 minutes). Replacing a lost instance first (Terraform apply, about 2 minutes of cloud-init, a deploy) fits in the remaining half hour. The snapshot drill (M9-08) is the same order of minutes. [`../runbooks/restore.md`](../runbooks/restore.md) states it.


### Data for the measurement (E2)

DEV is measured with a scrubbed copy of the simulated center in a **side database, `classora_load`**, for under 24 hours. Every password is replaced with a random one kept only on the operator's machine, and sessions, tokens, idempotency keys and rate-limit counters are emptied. The README's documented passwords are proven to fail before the dump leaves the machine. During load mode, api and worker point at `classora_load` through a temporary Compose override that also lifts the per-IP limit (#163) to 60,000 per minute. Afterwards the override is removed, `classora_load` is dropped and the dump is deleted. `classora` is never written: its exact row counts are compared before and after. The harness and the procedure are in [`../runbooks/load-test.md`](../runbooks/load-test.md). No PROD data, and no load, ever reaches PROD.

## Consequences

- The load profile and thresholds are proposals until the owner reviews them. Changing them needs no change to the harness: `infrastructure/load/k6/` and [`../runbooks/load-test.md`](../runbooks/load-test.md).
- The harness can be re-run: `make load-test`, `infrastructure/load/run.sh`, with DEV in load mode for under 24 hours. A merge to `develop` during a run redeploys DEV and ends load mode.
- PgBouncer queues requests above 5 concurrent queries per database. A slow statement now delays others sooner, so slow routes matter more: `GET /api/v1/billing/overdue-accounts` (p95 up to 2–4 s) is a follow-up candidate.
- A container that dies is seen as unhealthy after up to 30 s (worker) instead of 5 s. `restart: unless-stopped` restarts a crashed process at once anyway, and deploy.sh's readiness gate runs its own checks.
- No resize, no Terraform change and no new billable resource. The DEV runs cost a few cents of surplus CPU credits.
- Spec §18 item 7 points here. ADR-0010 and ADR-0013 are amended with pointers, and architecture ISSUE-I8 is resolved (measured).


## Alternatives considered

- **P1 only** (E1 (b)). Smaller, but gives no headroom figure.
- **Building the dataset through the public API on DEV's real database** (E2 (b)). No rule exception, but about twice the work, and the data would stay on DEV for good. **An empty database** (E2 (c)) would understate every query.
- **A permanent Parameter Store knob for the per-IP limit** (E3 (b)). An extra feature; it stays an optional follow-up.
- **Locust or a custom script** (E4 (b), (c)). A new Python dependency, or re-implementing percentiles and ramps. k6 runs from a pinned image.
- **Measuring t4g.micro on real hardware.** That needs a DEV resize, which this issue may not do.
- **Zero-downtime deploys** (a second api container behind Caddy, blue/green). More memory and moving parts than a center with a release window needs.
