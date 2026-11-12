# Load test report: DEV on t4g.small (M10-04)

The measurements behind [ADR-0018](adr/0018-sizing-load-and-recovery-objectives.md). How to run it again: [`runbooks/load-test.md`](runbooks/load-test.md). It holds no credentials.

## Run facts

- **Date:** 2026-11-12, 19:41–22:15 UTC (DEV in load mode; times per run below).
- **Code:** DEV ran release `112c32b` (develop, M10-02) the whole time; the harness is on branch `m10-04-load-sizing`. Tuning candidates were applied with the load-mode override, then committed (ADR-0018).
- **Host:** DEV `classora-dev`, t4g.small (2 vCPU, 2 GiB; the kernel shows 1,841 MiB), 1 GiB swap, CPU credits `unlimited`.
- **Dataset:** the simulation (FULL, `es`), scrubbed: 45 MB restored (`pg_dump -Fc` 2.7 MB). Largest tables: `audit_events` 19,277, `notifications` 2,359, `class_session_participants` 1,562, `class_session_attendance` 1,172, `class_sessions` 1,115, `approval_requests` 1,105, `charges` 1,040, `session_closeouts` 820, `payroll_lines` 713. 52 sign-in accounts.
- **Tool:** k6 2.3.0 (`grafana/k6:2.3.0@sha256:2dd4756e…`), from the operator's machine over the internet.
- **Network:** TCP connect to `dev.classora.dev` about 50 ms, TLS done at about 110 ms. Client-side figures include this; the thresholds are judged server-side.
- **Server-side sources:** Prometheus on the monitoring host (OpenTelemetry FastAPI and SQLAlchemy histograms, `deployment_environment_name="dev"`); `docker stats`, `/proc/meminfo`, `/proc/vmstat` and `pg_stat_activity`/`pg_stat_database` sampled on the host every 10–30 s; CloudWatch for CPU credits.


## Load profile

Proposed by the PM in #152 (E1 (a)), **owner-delegated, pending owner review**. The center is the simulation's FULL profile (#543): 40 students in 30 families, 12 tutors and 4 staff. Each virtual user (VU) is one signed-in person of a role.

| Step | Users | Mix (staff / tutors / guardians / students) | Duration |
|---|---|---|---|
| P1, a busy afternoon (1×) | 32 | 4 / 8 / 15 / 5 | 30 min |
| P2, growth (3×, about a 120-student center) | 96 | 12 / 24 / 45 / 15 | 15 min |
| P3, breakpoint | 96 → 256 | 3× → 8× | 1 min to 3×, 10 min ramp to 8×, 2 min at 8×; stops at client p95 > 2 s or errors > 2 % |
| staff-600 | 12 | staff only at 3× | 5 min, with the default per-IP limit (600/min) on |

- **Think time** 20–40 s between page views. A VU's first page view waits a random 0–40 s, so people arrive spread out.
- **A page view** is the batch of API calls the web app makes for that page (from `apps/web/src/features/*/api.ts`), sent in parallel like the browser. Every page also refreshes the shell's unread count.
- **One SSE notification stream per VU**, held up to 5 minutes and reopened after 5 s (the app's retry). Streams are left out of every latency and error figure.
- **Sessions:** each account signs in once per run and its VUs share the session (at most 40 sign-ins, under `login.ip` 50 per 15 minutes). Staff exports ask for the password again (`reauthenticate`) when the API requires it, as the web app does.
- **Per-user domain limits respected:** no `available-slots` search, session request or change request is sent, and no account has more than 10 open streams (at 8×: at most 8 per staff account).

| Role | Page (share of its views) | API calls |
|---|---|---|
| Staff (Owner, Administrator, Coordinator, Finance) | Home (17 %) | `auth/me`, `reports/dashboard/layout`, `approval-requests?status=open&limit=1`, `billing/overdue-accounts?limit=1`, today's `class-sessions` |
| | Schedule week (17 %) | the week's `class-sessions`, `tutors?limit=100`, `classrooms`, `subjects` |
| | Students and a student (13 %) | `students?limit=50`, then `students/{id}`, its week `schedule`, `academic-history` |
| | Families and billing (13 %) | `families?limit=50`, then `families/{id}`, `billing-account`, `charges`, `payments` |
| | Reports (9 %) | Owner and Finance: `kpis/revenue-trend` (90 days), `finance/balances`, `kpis/tutor-performance`; others: `closeouts/overdue` |
| | Finance or users (9 %) | Finance: `finance/expenses`, `payroll/periods`; others: `users?limit=50` |
| | Payslip PDF (9 %) | Finance: one payslip PDF (about one every 6 minutes); others: the shell only |
| | Export (4 %) | `POST exports` (students, xlsx; about one every 12 minutes per staff VU), `exports` |
| | Notifications (4 %) | `notifications`, `POST notifications/read-all` |
| | Customize dashboard (4 %) | `reports/dashboard/layout`, `PUT` the same layout |
| Tutor | My schedule (33 %) | `auth/me`, the week's `tutors/{me}/schedule`, `assignment-offers?limit=1`, `assignments?due=past` |
| | A session (33 %) | the schedule to today, then that session's `attendance`, `notes`, `closeout`; `PUT attendance` with the same marks when it can be marked |
| | A student's history (17 %) | the schedule, then `students/{id}/academic-history` and `assignments` |
| | Earnings (8 %) | `payroll/tutors/{me}/earnings` |
| | Notifications (8 %) | `notifications`, `POST notifications/read-all` |
| Guardian | Home (33 %) | `auth/me`, `schedule/family-students`, a child's week `schedule`, `assignments?limit=1` |
| | My billing (25 %) | `billing-account`, `charges`, `payments` |
| | Receipt PDF (10 %) | one receipt PDF (about one every 5 minutes) |
| | Homework (17 %) | a child's `assignments`, `academic-history` |
| | Notifications (15 %) | `notifications`, `POST notifications/read-all` |
| Student | Home (40 %) | as the guardian's |
| | Homework (40 %) | own `assignments`, `academic-history` |
| | Notifications (20 %) | `notifications` |

The simulated term has every session up to 18 Nov 2026 closed out, so the tutors' attendance `PUT` is never allowed on this dataset and the tutors' writes are the notification ones. The unsafe requests are `POST notifications/read-all`, `PUT reports/dashboard/layout`, `POST exports` and `POST auth/reauthenticate`.

### Pass thresholds ("carries the load")

P1 for 30 minutes **and** P2 for 15 minutes, both within:

- API p95 ≤ 500 ms for `GET` and ≤ 1,000 ms for unsafe methods, **server-side** (OpenTelemetry FastAPI histograms in Prometheus; PDFs, the SSE stream and the sign-in are left out of these two);
- PDF p95 ≤ 3 s;
- 5xx plus network errors < 0.5 %;
- no container OOM-kill or restart;
- host `MemAvailable` ≥ 15 %;
- no sustained swap-in (≤ 1 MB/s averaged over 1 minute).

## Results

Runs, in order (UTC, 2026-11-12). "Starting values" are the M9 settings; **C1** is the tuning candidate that was then committed: PgBouncer `default_pool_size` 10 → 5, and the hosts' healthchecks every 10 s (api, was 2 s) and 30 s (worker, was 5 s).

| Run | Window | Settings | VUs (browsing + SSE) |
|---|---|---|---|
| P1 baseline | 19:43:50–20:14:30 | starting values | 32 + 32 |
| P2 baseline | 20:23:02–20:38:59 | starting values | 96 + 96 |
| P2 tuned | 20:41:36–20:57:30 | C1 | 96 + 96 |
| P3 ramp | 20:59:21–21:10:40 (stopped by its threshold) | C1 | up to about 7.5× |
| staff-600 | 21:15–21:21 | C1, per-IP limit 600/min | 12 + 12 |
<!-- P1-TUNED-ROW -->

### API (server side)

| Run | Requests | GET p50 / p95 / p99 | Unsafe p50 / p95 / p99 | PDF p95 | 5xx | Client GET p95 (k6) |
|---|---|---|---|---|---|---|
| P1 baseline | 7,337 (4.0/s) | 133 / **441** / 2,757 ms | 37 / 142 / 460 ms | 500 ms | 0 | 462 ms |
| P2 baseline | 10,872 (11.4/s) | 182 / **699** / 1,367 ms | 46 / 414 / 695 ms | 973 ms | 0 | 764 ms |
| P2 tuned | 10,592 (11.1/s) | 137 / **444** / 649 ms | 43 / 260 / 497 ms | 646 ms | 0 | 466 ms |
| P3 ramp | 13,008 (19.2/s) | 306 / 2,100 / 3,717 ms | 165 / 1,855 / 2,474 ms | 2,258 ms | 0 | 2,040 ms |
| staff-600 | 530 (1.5/s) | 87 / 250 / 734 ms | 40 / 159 / 232 ms | (none) | 0 | 308 ms |
<!-- P1-TUNED-API -->

"Unsafe" leaves out the sign-in (Argon2 by design). The P1 baseline's client error rate (3.8 %) was 403 and 404 answers to pages a role may not open; the harness then learned to skip them, as the web app does. Every other run had 0 % client errors, and no run had a 5xx or a network error.

### Containers (host samples every 10 s)

RSS average / peak in MiB (share of `mem_limit`), CPU average / peak in % of one vCPU.

| Container (limit) | P1 baseline | P2 baseline | P2 tuned | P3 ramp |
|---|---|---|---|---|
| api (512m) | 267 / 289 (56 %), CPU 17 / 68 | 284 / 307 (60 %), CPU 41 / 90 | 261 / 287 (56 %), CPU 30 / 74 | 285 / 303 (59 %), CPU 53 / 96 |
| worker (448m) | 208 / 304 (68 %), CPU 48 / 149 | 182 / 299 (67 %), CPU 49 / 99 | 180 / 290 (65 %), CPU 16 / 100 | 167 / 295 (66 %), CPU 12 / 90 |
| db (512m) | 400 / 487 (95 %), CPU 7 / 48 | 499 / 508 (99 %), CPU 17 / 69 | 327 / 444 (87 %), CPU 13 / 58 | 480 / 507 (99 %), CPU 20 / 60 |
| pgbouncer (64m) | 6 / 11 (17 %), CPU 5 / 18 | 6 / 9 (13 %), CPU 8 / 24 | 6 / 10 (16 %), CPU 7 / 18 | 10 / 13 (21 %), CPU 10 / 20 |
| edge (96m) | 35 / 40 (42 %), CPU 1 / 28 | 56 / 60 (63 %), CPU 2 / 5 | 58 / 62 (65 %), CPU 1 / 7 | 69 / 89 (92 %), CPU 4 / 75 |
| collector (160m) | 97 / 100 (63 %), CPU 2 / 12 | 99 / 105 (65 %), CPU 2 / 8 | 100 / 103 (64 %), CPU 2 / 6 | 97 / 102 (63 %), CPU 3 / 6 |

The db container's figure includes the page cache its cgroup is charged for, so it sits near its limit by design; it was never OOM-killed. No container restarted or was OOM-killed in any run (`RestartCount` 0, `OOMKilled` false).

### Host

| Run | MemAvailable min (of 1,841 MiB) | Swap in / out | Worst 1-min swap-in | Load avg / max | I/O wait |
|---|---|---|---|---|---|
| P1 baseline | 275 MiB (**14.9 %**) | 70 / 191 MiB | 0.49 MiB/s | 1.40 / 2.77 | 0.3 % |
| P2 baseline | 242 MiB (**13 %**) | 264 / 472 MiB | 0.89 MiB/s | 2.02 / 3.64 | 1.0 % |
| P2 tuned | 355 MiB (19 %) | 5 / 27 MiB | 0.02 MiB/s | 1.36 / 2.64 | 0.2 % |
| P3 ramp | 230 MiB (12 %) | 120 / 340 MiB | 0.81 MiB/s | 1.97 / 3.34 | 0.7 % |

### PostgreSQL, PgBouncer and the pools

| Run | Backends peak (all) | `classora_load` app: active / idle peak | Cache hit | Deadlocks / temp files | Commits/s | API SQL p95 (incl. PgBouncer wait) | API pool checked out, max |
|---|---|---|---|---|---|---|---|
| P1 baseline | 29 | 2 / 15 | 100 % | 0 / 0 | 6.7 | 25 ms | 5 |
| P2 baseline | 29 | 6 / 14 | 100 % | 0 / 0 | 16.9 | 46 ms | 10 |
| P2 tuned | 23 | 3 / 9 | 100 % | 0 / 0 | 16.5 | 25 ms | 15 |
| P3 ramp | 23 | 3 / 9 | 100 % | 0 / 0 | 26.7 | 72 ms | 13 |

- The whole 45 MB database fits in `shared_buffers` (128 MB): every read was a buffer hit. No query spilled to a temp file (`work_mem` 4 MB), and the peak of 29 backends is far under `max_connections` 100.
- **Each PgBouncer server connection holds about 35–42 MiB of private memory** (PostgreSQL's catalog, relation and plan caches over 139 tables; measured with `/proc/<pid>/status` and `pg_log_backend_memory_contexts`). Ten of them were about 400 MiB, the db container's growth from 195 to 487 MiB during P1. With 5 (C1), the API's up to 15 checked-out connections wait briefly in PgBouncer, and its SQL p95 went down, not up (46 → 25 ms at 3×), because the host stopped swapping.
- PgBouncer has no stats console. Client waits were inferred from the API's SQL span latency (the SQLAlchemy histogram) against PostgreSQL's own activity: at most 6 active backends, so no client waited for long.

### Worker

| Run | Export runs (p95) | Other jobs |
|---|---|---|
| P1 baseline | 14 (325 ms) | `billing:post_session_charges` once (29 s), expire offers, demo periodic: all succeeded |
| P2 baseline | 28 (482 ms) | all succeeded |
| P2 tuned | 25 (248 ms) | all succeeded |
| P3 ramp | 13 (337 ms) | all succeeded |

No queue lag: one job at a time (`WORKER_CONCURRENCY = 1`) cleared every export within a second of its request, and the periodic jobs ran on time.

**The worker's healthcheck cost half a vCPU.** The worker container's cgroup used 1,062 CPU-seconds in 37 minutes, the worker process itself 34. The rest is `python -m app.worker --healthcheck`, every 5 s: each run is a fresh Python process that imports the whole app (4.9 s wall time on DEV). The api's 2 s check added about another 0.05 vCPU. CloudWatch shows DEV at about 33 % CPU **with no load at all** (16:00–19:00 UTC, before the test), above the t4g.small baseline of 20 %. With C1 the worker's CPU at 3× fell from 49 % to 16 % of a vCPU.

### CPU credits (CloudWatch, read-only)

| Metric (DEV) | Value |
|---|---|
| `CPUCreditBalance` | 0 all day (unlimited mode spends surplus credits first) |
| `CPUSurplusCreditBalance` | 272 at 16:00, 346 at 20:00 UTC: about 17 credits per 30 minutes idle, above the baseline |
| `CPUSurplusCreditsCharged` | 0 so far (charges start when the surplus balance reaches the 24-hour earn cap, 576) |
| `CPUUtilization` average | 33 % idle (16:00–19:00); 38–40 % during the runs |

At the idle rate, the surplus reaches the cap in about a day and then costs roughly $8–16 a month per host ($0.04 per vCPU-hour). C1 brings idle use under the baseline.

### P3 breakpoint

Server GET p95 per minute while the ramp went from 3× to 8× (multiplier estimated from the ramp schedule):

| Minute (UTC) | About | Requests/s | GET p95 |
|---|---|---|---|
| 21:01 | 3× | 8.2 | 383 ms |
| 21:02 | 3.5× | 12.7 | 419 ms |
| 21:03 | 4× | 14.5 | 741 ms |
| 21:04 | 4.5× | 17.7 | 626 ms |
| 21:05 | 5× | 16.9 | 720 ms |
| 21:06 | 5.5× | 20.3 | 649 ms |
| 21:07 | 6× | 24.2 | 1,964 ms |
| 21:08 | 6.5× | 25.9 | 987 ms |
| 21:09 | 7× | 26.5 | 2,326 ms |
| 21:10 | 7.5× | 26.8 | 2,694 ms |

The run stopped itself at 21:10:39 (client p95 over 2 s). **Throughput levels off at about 26–27 requests/s**, with the single Uvicorn process near one full vCPU (api CPU peak 96 %). From about 4× the GET p95 is above 500 ms, and at 7× the host was swapping again (MemAvailable 12 %). No errors at any point: the system slows down, it does not fail.

### staff-600

Only the 12 staff VUs (3×), with the default per-IP limit of 600 requests per minute on: about 110 requests per minute including the streams, **no 429**, GET p95 250 ms. A center whose staff share one IP stays far below the limit.

<!-- DOWNTIME -->

### Pass / fail

<!-- PASSFAIL -->

### Anomalies

- **`GET /api/v1/billing/overdue-accounts` is the slowest route:** p95 1.6 s (P1), 4.2 s (P2 baseline), 2.1 s (P2 tuned). It is one aggregate statement over every charge with window totals (`app/modules/billing/overdue.py`, `overdue_accounts`), and the staff home calls it with `limit=1` only for the "overdue" count. It does not break a threshold (those are over all GETs), but it is the first thing to look at if staff pages feel slow. Proposed as a follow-up.
- Other slow routes at 3× (p95 0.6–1 s): `reports/finance/balances`, `closeouts/overdue`, `kpis/revenue-trend`, `kpis/tutor-performance`, the week's `class-sessions`.
- `billing:post_session_charges` ran once for 29 s during P1 (the simulated term's charges); it did not hold up the exports.
- The edge's memory grows with open streams: 89 of 96 MiB at about 7.5× (about 240 open SSE connections). Fine at 3× (62 MiB).

## Commands to re-run

```bash
make db-shared
infrastructure/load/prepare-dataset.sh --upload
infrastructure/load/dev-up.sh dev
infrastructure/load/run.sh p1            # or p2, p3
infrastructure/load/dev-up.sh dev --rate-limit default && infrastructure/load/run.sh staff-600 5m
infrastructure/load/downtime-probe.sh dev
infrastructure/load/dev-down.sh dev
```

Details and safety rules: [`runbooks/load-test.md`](runbooks/load-test.md).

