# Load test report: DEV on t4g.small (M10-04)

The measurements behind [ADR-0018](adr/0018-sizing-load-and-recovery-objectives.md). How to run it again: [`runbooks/load-test.md`](runbooks/load-test.md). It holds no credentials.

## Run facts

<!-- RUN-FACTS -->

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

<!-- RESULTS -->
