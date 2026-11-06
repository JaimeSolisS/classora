# Analytics KPIs

#126 (M8-09; spec §8 "analytics … and other KPIs"; #126 E1–E12,
orchestrator-recommended and owner-delegated, pending owner review). This page
is the one definition of each KPI. Change a KPI only here and in its test. The
route reference is in [`api.md`](api.md) ("Analytics KPIs"). The "Indicadores"
page is M8-09b, and dashboards and widgets are #127; both read these routes and
never redefine a KPI.

The code is in the read-only `reporting` module (`app/modules/reporting/`:
`kpi_rules.py` is pure, `kpis.py` holds the queries and assembly,
`kpi_schemas.py` the shapes, and `kpis_router.py` the routes). There is no
table, migration, index, cache or materialized view: every figure is computed
live.

## Common terms

- `Z` is the center's time zone (business settings). `T` is the center's
  today, and `now` is the request instant.
- A **range** is `from`/`to`: both center-local dates, both inclusive. The
  rules are #117's: `from ≤ to ≤ T` and `to − from < 366`, else `422
  report_range_invalid`. `from > to` and `to > T` are reported at
  `["query","to"]`, and the span at `["query","from"]`. `from = to` is a
  one-day range.
- The **instant window** is `[L(from), L(to + 1))`, where `L(d)` is the local
  midnight of `d` in `Z`. On a DST change day it covers 23 or 25 real hours.
  The queries compare `starts_at` with these two instants, so they can use
  `ix_class_sessions_starts_at`. The local date appears only in a select list,
  never in a filter.
- The **months** of a range are the center-local calendar months that meet
  it. Each is clipped to `[from, to]` and has `month` (`"YYYY-MM"`), `from`
  and `to`. They are listed oldest first, zeros included. A range of 366
  dates that starts mid-month has 13 months.
- An **in-scope session** is #84's set: state `confirmed` or `completed`,
  `starts_at ≤ now`, and `starts_at` in the instant window. A session today
  that hasn't started isn't in scope yet, so with `to = T` the session KPIs
  can rise later in the day.
- A **counted cancellation** is a session in state `cancelled` that had been
  confirmed (`first_confirmed_at` is set), with `starts_at` in the window.
- **Percent** is #84's rule: `numerator / denominator × 100`, half-up to two
  places, as a decimal string (`"66.67"`), or `null` when the denominator is
  0.
- **Money** is a decimal string, as in #117.
- A session's date is its **current** local date: a rescheduled session counts
  in the month it moved to (ADR-0006 E7, #117).

## Who sees which KPI

No new permission (E3). Each KPI is gated like the page its rows come from, so
no KPI widens access. A user with several roles gets the union.

| KPI | Route | Needs | Owner | Administrator | Academic Coordinator | Finance Staff | Tutor, student, guardian |
|---|---|---|---|---|---|---|---|
| K1 Revenue trend | `GET /api/v1/reports/kpis/revenue-trend` | `finance.report.view` | yes | no | no | yes | no |
| K2 Course profitability | `GET /api/v1/reports/kpis/course-profitability` | `finance.report.view` | yes | no | no | yes | no |
| K3 Unpaid balances and payroll | `GET /api/v1/reports/finance/balances` | `finance.report.view` | yes | no | no | yes | no |
| K4 Room utilization | `GET /api/v1/reports/kpis/room-utilization` | `schedule.view` | yes | yes | yes | yes | no |
| K5 Student retention | `GET /api/v1/reports/kpis/student-retention` | `schedule.view` | yes | yes | yes | yes | no |
| K6 Tutor performance, K7 center activity | `GET /api/v1/reports/kpis/tutor-performance` | `schedule.view` and `tutor.view` | yes | yes | yes | no | no |

- The money KPIs are Owner and Finance Staff only, as with the financial
  report. An Administrator gets `403` even with `payroll.finalize` delegated
  (ADR-0017 E12).
- K4 and K5 return minutes, counts and room names, and never a student.
- K6 is the only KPI that names people. It names tutors, as the tutor
  directory does, and needs both permissions, so Finance Staff, who don't see
  the tutor directory, get `403`.
- No money appears outside K1–K3. No KPI returns a student, a family, a
  guardian, a note, a description, a reference or a reason.

## K1 Revenue trend

`GET /api/v1/reports/kpis/revenue-trend?basis=accrual|cash&from=&to=`

- For each month `m` of the range, `revenue_total`, `direct_tutor_cost_total`,
  `gross_profit`, `operating_expenses_total` and `net_result` are **exactly**
  the fields of #117's `profitability` over `m.from..m.to` on the chosen basis
  (`operating_expenses_total` is its `operating_expenses.total`).
- `totals` equals #117's `profitability` over the whole range, so Σ months =
  totals, field by field.
- The **payroll KPI** is `direct_tutor_cost_total`: finalized payroll on the
  accrual basis, unreversed payroll payments on the cash basis.
- Every #117 rule applies unchanged: packages, course windows, refunds,
  unallocated revenue, and restatement. A credit posted later changes the
  earlier month it belongs to (accrual), exactly as in the financial report.
- It is computed in one pass: one load of the revenue entries, cost entries and
  expenses, bucketed by month (cash sums grouped by month in SQL), with the
  same statements as one profitability call.

**Worked example** (range `2031-01-15..2031-03-10`, today `2031-03-12`):

- individual sessions completed on Jan 10, Jan 20 and Feb 3, each with a
  400.00 session charge;
- an expense of 1,000.00 incurred on Feb 1 and paid on Feb 10;
- a finalized February period with the Feb 3 session line of 250.00, paid in
  full on Mar 3 (no other period is finalized, so the January sessions add no
  cost);
- payments of 400.00 on Jan 25 and Mar 2, and one of 400.00 on Feb 5 that is
  reversed;
- a refund of 100.00 on Mar 5.

| basis | month | revenue | tutor cost | gross | opex | net |
|---|---|---|---|---|---|---|
| accrual | 2031-01 (15–31) | 400.00 | 0.00 | 400.00 | 0.00 | 400.00 |
| accrual | 2031-02 | 400.00 | 250.00 | 150.00 | 1000.00 | −850.00 |
| accrual | 2031-03 (1–10) | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| accrual | totals | 800.00 | 250.00 | 550.00 | 1000.00 | −450.00 |
| cash | 2031-01 | 400.00 | 0.00 | 400.00 | 0.00 | 400.00 |
| cash | 2031-02 | 0.00 | 0.00 | 0.00 | 1000.00 | −1000.00 |
| cash | 2031-03 | 300.00 | 250.00 | 50.00 | 0.00 | 50.00 |
| cash | totals | 700.00 | 250.00 | 450.00 | 1000.00 | −550.00 |

The Jan 10 session is before the range. The reversed Feb 5 payment disappears
from February. March's cash revenue is 400.00 − 100.00.

## K2 Course profitability

`GET /api/v1/reports/kpis/course-profitability?from=&to=` (accrual only)

- The entries are #117's accrual entries with class type `course`, dated by
  #117's rules, inside `[from, to]`.
- **Revenue** is attributed to a cohort through the enrollment's cohort (a
  `cohort_enrollment` charge), else through the charge's class session's
  cohort.
- **Cost** is attributed through the finalized line's class session's cohort,
  else the adjustment's class session's cohort (an adjustment line), else the
  origin line's class session's cohort.
- A course-class entry with no cohort goes to `not_attributed`. An example is a
  tuition plan priced as a course.
- Per cohort: `revenue`, `direct_tutor_cost`, and `gross_profit = revenue −
  direct_tutor_cost`. There is no overhead allocation (spec §8).
- `total` equals #117's accrual `course` row for the same range, so Σ cohorts +
  `not_attributed` = `total`.
- The rows are the cohorts with a non-zero revenue or cost in the range, by
  `gross_profit` descending, then cohort id. A cohort is `{id, name}`.
- Cash-basis course profitability is not a KPI here: a cash payment can't be
  tied to a cohort without new rules (E6).

**Worked example** (range `2031-03-01..2031-03-31`, today `2031-04-02`):

- cohort C1: a fee of 3,000.00 over 6 counted occurrences (2 in February, 2 in
  March, 2 in April), so 500.00 each. A per-session tutor at 300.00 has the two
  March sessions in a finalized March period. A `late_approval` adjustment of
  150.00 for a February C1 session is posted to that period, so it is dated
  on its end, Mar 31;
- cohort C2: a fee of 1,000.00 over occurrences on Mar 10, Mar 17 and Apr 20
  (`allocate`: 333.33 / 333.34 / 333.33), with no payroll;
- a tuition-plan charge of 800.00 priced with class type `course`, service
  date Mar 15.

| row | revenue | tutor cost | gross |
|---|---|---|---|
| C2 (listed first: higher gross) | 666.67 | 0.00 | 666.67 |
| C1 | 1000.00 | 750.00 | 250.00 |
| `not_attributed` | 800.00 | 0.00 | 800.00 |
| `total` (= #117's `course` row) | 2466.67 | 750.00 | 1716.67 |

## K3 Unpaid balances and payroll

The existing `GET /api/v1/reports/finance/balances`, unchanged (#117 E9): as
of `T` only. It gives receivables outstanding and overdue, the account credit,
the accounts with outstanding, open and overdue expenses, **payroll unpaid**
with the periods unpaid, and deferred revenue. It is the documented exception
to the "date-range parameters" of the criteria (E10); K1 gives the payroll
cost over time.

## K4 Room utilization

`GET /api/v1/reports/kpis/room-utilization?from=&to=`

- The rooms are the classrooms that are active now. Sessions in inactive
  rooms are not counted.
- The **available minutes** of room `r` on local date `d`:
  - `0` when `d` is in an active closure, or the operating hours have no row
    for `d`'s ISO weekday;
  - otherwise, with `H = [opens_at, closes_at)` for that weekday: `|H|` when
    `r` has no availability windows, else `Σ |w ∩ H|` over `r`'s windows on
    that weekday;
  - in wall-clock minutes (DST is ignored).
- The **used minutes** of `r` are `Σ scheduled_minutes` of the in-scope
  sessions in `r`. They are not clipped to the open hours, so a session booked
  outside them by an override counts in full, and the figure can exceed
  `100.00`.
- `utilization_percent = percent(used, available)`. The `total` row is Σ used
  / Σ available. A room with no available minutes (for example, windows only
  on a closed day) gives `null`, and its used minutes are still shown.
- The rows are ordered by room name (case-insensitive), then id. A room is
  `{id, name}`.

**Worked example** (Mon `2031-03-03` .. Sun `2031-03-09`, today `2031-03-10`):

- operating hours Mon–Fri 09:00–21:00 and Sat 09:00–14:00, none on Sunday;
- an active closure on Wed Mar 5, and an inactive one on Thu Mar 6 (ignored);
- room A has no windows; room B has windows Mon 16:00–20:00 and Sat
  08:00–10:00; room C is inactive and has a completed session (not counted);
- in A: completed 60 min on Mon, completed 60 on Tue, a confirmed 90 on Thu
  that has started, a cancelled 60 on Fri (not counted), and a completed 60
  starting Sun Mar 2 23:00 local (= Mar 3 05:00 UTC; outside the range);
- in B: a completed 120 on Mon at 16:00.

| room | available | used | % |
|---|---|---|---|
| A | 3180 (4 × 720 + 300) | 210 | 6.60 |
| B | 300 (240 + 60) | 120 | 40.00 |
| total | 3480 | 330 | 9.48 |

## K5 Student retention

`GET /api/v1/reports/kpis/student-retention?from=&to=`

- `A(W)` is the set of students with at least one in-scope participation (a
  participant of an in-scope session) in window `W`. The attendance mark
  doesn't matter: a student marked absent was still scheduled and active.
- With `n = to − from + 1`, the previous window is `P = [from − n, from − 1]`,
  and the current one is `R = [from, to]`.
- `active_previous = |A(P)|`, `active_current = |A(R)|`, `retained = |A(P) ∩
  A(R)|`, `new = |A(R) − A(P)|`, `lost = |A(P) − A(R)|`, and
  `retention_percent = percent(retained, active_previous)` (`null` with no
  previous activity).
- The response echoes `previous_from` and `previous_to`, and gives `months[]`
  of `R`, each with `active_students = |A(month)|`.
- It is session-based, not enrollment-based: individual tutoring has no
  enrollment row, and one rule covers every class type (E8). No student id or
  name is returned.

**Worked example** (range `2031-03-01..2031-03-31`, so `n = 31` and `P =
2031-01-29..2031-02-28`; today `2031-04-02`). Completed sessions:

- S1 on Feb 10 and Mar 5; S2 on Feb 20; S3 on Mar 12;
- S4 on Feb 2, plus a Mar 20 session that is **cancelled**;
- S5 on Jan 28 (before `P`) and Mar 3;
- S6 on Feb 15 and Mar 7, both marked **absent**;
- S7 at 23:30 local on Mar 31 (= Apr 1 UTC); S8 at 23:30 local on Feb 28.

Result: `active_previous 5` (S1, S2, S4, S6, S8), `active_current 5` (S1, S3,
S5, S6, S7), `retained 2`, `new 3`, `lost 3`, `retention_percent "40.00"`,
`months [{"2031-03", active_students 5}]`.

## K6 Tutor performance and K7 center activity

`GET /api/v1/reports/kpis/tutor-performance?from=&to=`

- Each session is credited to its **current** `tutor_user_id`, so a substitute
  is credited and the original tutor's row doesn't include it.
- Per tutor:
  - `sessions`: in-scope sessions;
  - `completed`: of those, state `completed`;
  - `pending_closeout`: of those, `confirmed` with `ends_at ≤ now`;
  - `cancelled`: counted cancellations;
  - `scheduled_minutes`: Σ over in-scope sessions;
  - `students`: distinct participants of in-scope sessions;
  - `attendance`: `{present, late, absent, not_recorded}` over the
    participations of in-scope sessions, plus `attendance_percent =
    percent(present + late, present + late + absent)` (#84).
- The rows are the tutors with at least one in-scope session or counted
  cancellation, active or not. A tutor is `{id, display_name}`, ordered by
  display name (case-insensitive), then id.
- `totals` (K7, center activity) holds the same fields center-wide. `students`
  there is distinct center-wide, not a sum. A counted cancellation with no
  tutor counts in the totals only.
- There is no money, grade, evaluation or ranking (E9).

**Worked example** (range `2031-03-01..2031-03-31`, `now` `2031-04-01T12:00`
local):

- tutor T1:
  - a completed 60-min individual session, S1 present;
  - a completed 90-min group session, S2 present, S3 late, S4 absent;
  - a confirmed 60-min session that has ended and isn't closed out, S2 not
    recorded;
  - a session cancelled after confirmation;
  - a session cancelled without ever being confirmed (not counted);
- T2: the session substituted from T1, a completed 60-min session with S5
  present.

| row | sessions | completed | pending | cancelled | minutes | students | present/late/absent/not recorded | % |
|---|---|---|---|---|---|---|---|---|
| T1 | 3 | 2 | 1 | 1 | 210 | 4 | 2/1/1/1 | 75.00 |
| T2 | 1 | 1 | 0 | 0 | 60 | 1 | 1/0/0/0 | 100.00 |
| totals | 4 | 3 | 1 | 1 | 270 | 5 | 3/1/1/1 | 80.00 |

The totals' attendance counts equal Σ over the students of #83/#84's
`scheduling.service.student_attendance_summary` for the range.

## Edge cases

- **No data:** every route returns zeros with `null` percentages. K1 still
  lists every month, K2 has no cohorts and a zero total, and K6 has no tutors
  and a zero totals row.
- **DST:** a range that starts or ends on a DST change day buckets by local
  date (tested in `America/New_York`).
- **A deactivated tutor** is still listed if they taught in the range. A
  deactivated room is not.

## Performance budget

The center is small, so there is no cache, snapshot, index or materialized view
(E11). The budget has four parts, all in
`tests/integration/test_reporting_kpis_budget.py`:

1. **A fixed statement count** per call, the zone and today included, the same
   on the worked examples and with every row doubled (pinned in the test):

   | call | statements | ceiling |
   |---|---|---|
   | K1 accrual | 7 | = #117 accrual on the same data (7) |
   | K1 cash | 3 | = #117 cash (3) |
   | K2 | 7 | #117 accrual + 1 |
   | K4 | 6 | ≤ 7 |
   | K5 | 2 | ≤ 4 |
   | K6 | 4 | ≤ 4 |

   #117's accrual count includes up to three statements for the cohort
   schedules, none when no cohort has a charge, so on an empty database K1 and
   K2 send 5.
2. **Index use.** EXPLAIN of the K4, K5 and K6 session statements, with
   sequential scans off, shows `ix_class_sessions_starts_at` (or the partial
   cancelled index) and no sequential scan of `class_sessions`.
3. **Volume V1**, inserted directly with `INSERT … SELECT generate_series`:
   12 months, 20 tutors, 300 students, 10 active rooms (half with windows),
   operating hours for 6 days, 5 closures, 10,000 individual sessions (85 %
   completed, 5 % confirmed, 10 % cancelled after confirmation), each with one
   participant, 8,000 attendance marks, 3,000 manual charges over 30 accounts
   and 2,000 payments. Budget per in-process call over 366 dates, best of 3
   warm runs: K4, K5 and K6 ≤ **1.0 s** each; K1 (both bases) and K2 ≤
   **2.0 s** each. The test prints the timings.

   Measured at handoff (developer Mac, 8 GB, Testcontainers PostgreSQL):
   K1 accrual 0.146 s, K1 cash 0.032 s, K2 0.149 s, K4 0.046 s, K5 0.055 s,
   K6 0.071 s. These are the baseline for M10-04 (load and sizing). #117's
   accrual loader reads every charge with no date filter, so K1 accrual and K2
   grow with the ledger's history.
4. A miss is a finding to report, never a silent index or cache.

## What is not a KPI here

Follow-up candidates (E2 (b), E6, E9 (b)), not built: tutor utilization
against availability, receivables aging, group or cohort seat fill,
cancellation reasons, grade or evaluation KPIs, revenue per tutor, cash-basis
course profitability, per-template rollups, overhead allocation, and as-of
balances. KPIs are not exported (#124's datasets are list pages).
