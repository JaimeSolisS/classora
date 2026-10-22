# ADR-0005: Scheduling invariants, overrides, recurrence and calendar

## Status

Accepted

## Date

2026-10-19

## Sign-off

Owner: JaimeSolisS. Decision items D1–D9 taken as recommended (option (a) for each), owner-delegated, on 2026-10-19 (GitHub issue #46, M4-01). No amendments. The orchestrator recorded the decision on the issue as an "owner-delegated recommended decision"; the owner reconfirmed it for the M4 run on 2026-10-20.

## Context

Resolves architecture §10 **ISSUE-D4** (overlap rules and overrides), the `btree_gist` part of **ISSUE-D6**, **ISSUE-SC1** (assignment offer lifecycle), **ISSUE-SC2** (recurrence), **ISSUE-SC3** (effective capacity) and **ISSUE-X2** (FullCalendar licensing) in [`../architecture.md`](../architecture.md) §10.3, §10.5 and §10.12. Records decision items **D1–D9** of issue #46, including two product decisions (architecture §12): the offer response deadline (D3) and makeup and billing after closures (D6).

- Spec [`../spec.md`](../spec.md) §1: families **request** individual sessions and never book them; enrollment is admin-only.
- Spec §2: Administrators do scheduling and Coordinators do tutor assignments.
- Spec §3 (schedule administration): prevent student, tutor and room overlap and capacity violations; "authorized administrators may override defined restrictions with reason and audit record"; workload limits produce warnings only; recurrence uses iCalendar `RRULE` patterns with rolling generation, per-occurrence exceptions, future-occurrence changes and holiday skipping; each group or cohort has its own maximum size and respects room capacity; the calendar is a daily, weekly and monthly FullCalendar UI with filters and server-validated drag-and-drop.
- Spec §10 (application stack): FullCalendar ("some resource views may require license"), Procrastinate for jobs, no Redis and no Celery.
- Spec §16: instants are UTC-aware, the center time zone governs scheduling, local date boundaries and recurrence, and database constraints protect business invariants.
- Spec §15 W1 (tutor assignment and class scheduling): W1.2 says a conflicting booking must not be silently created; W1.3–W1.4 cover offers, acceptance and declines; W1.5 protects completed history; W1.6 requires authorization, a reason and an auditable record for overrides. W2 (tutoring requests): W2.2 says a request does not create confirmed calendar occupancy.
- Spec §18 item 6 leaves FullCalendar licensing open if premium scheduler or resource features are required. This ADR resolves it **without changing the spec**: no premium feature is required.
- `btree_gist` was already enabled by #38 (M3-02, revision `0021`). This ADR records the decision that M4 uses it.
- Builds on ADR-0002 (module boundaries), ADR-0003 (Procrastinate, `queueing_lock`), ADR-0004 (local `TIME` + duration + `TZID`, `TIMESTAMPTZ` sessions) and ADR-0007 (`schedule.override` is delegable).

## Decision

### Invariants (D1 (a), ISSUE-D4)

| Class | Rules | Enforcement |
|---|---|---|
| **Hard** | `tutor_overlap`, `room_overlap` | Partial `EXCLUDE USING gist` on `tstzrange(starts_at, ends_at, '[)')`, over the occupying states (D2); `btree_gist`, already enabled by #38 |
| **Hard** | `student_overlap` | Application, inside the booking transaction, under a per-student transaction advisory lock, over sessions in occupying states |
| **Hard** | `room_capacity_exceeded` | Application, under a `FOR UPDATE` row lock on the session row (or the group or cohort row) whenever participants or the room change |
| **Soft** | `tutor_unavailable`, `room_unavailable`, `center_closed`, `outside_operating_hours`, `tutor_not_qualified`, `group_capacity_exceeded`, `notice_window` | Application; overridable with `schedule.override` and a reason, then audited |
| **Warning** | `daily_limit_exceeded`, `weekly_limit_exceeded`, `break_too_short` | Never blocks and never needs an override |

Sources of the soft rules:

- `tutor_unavailable`: outside the published weekly availability, or tutor time off (#45).
- `room_unavailable`: outside the room's weekly windows (#39).
- `center_closed`: a closure from #38. One-off sessions only, because series skip closures (D6).
- `outside_operating_hours`: the center operating hours (#37).
- `tutor_not_qualified`: no approved qualification, or a revoked one (#44, #198).
- `group_capacity_exceeded`: above the group or cohort maximum but within the room capacity (D7).
- `notice_window`: the booking-policy notice windows (M4-16, M4-17).

The warnings come from the teaching limits and breaks of #197.

Data validity is **`422`, not a conflict**: an inactive tutor, a user without the `tutor` role, or an inactive subject, room or level. Back-to-back sessions are allowed, because ranges are half-open.

### Conflict wire format and overrides

- **Conflicts:** `409 scheduling_conflict` with `conflicts: [{code, severity: "hard" | "soft", details}]`, using the codes above.
- **Warnings:** `2xx` responses carry `warnings: [{code, details}]`.
- **Override requests:** an override sends `override: {codes: [...], reason}`. The reason is trimmed and 1–1000 characters. The server recomputes the conflicts; if any soft code is not in the list, it answers `409` again. A hard code is never accepted.
- **Override records:** each override is stored as a row (session or series, codes, reason, actor, time), so the calendar can show that a session was overridden, and it also writes an audit event.
- **Time granularity:** `starts_at` and `ends_at` are whole minutes. A session lasts 15–480 minutes and starts and ends on the same local date.

### Session states (D2 (a), ISSUE-SC1)

- **States:** `unassigned` (no tutor yet), `offered`, `confirmed`, `completed`, `cancelled`, `skipped_closure` (D6) and `conflict` (a materialized occurrence that failed its checks, D4). There is **no `draft`** state: every creation runs the full checks (W1.2). "Declined" belongs to the offer (D3), not to the session.
- **Transitions:**
  - create → `unassigned` or `offered`;
  - `offered` → `confirmed` (accept) or `unassigned` (decline or expiry);
  - `unassigned` → `offered`;
  - `unassigned`, `offered` or `confirmed` → `cancelled` (with a reason);
  - `confirmed` → `completed` (closeout, M5 and M7);
  - `conflict` → `unassigned`, `offered` or `confirmed` once fixed, or → `cancelled`.

  `completed` and `cancelled` are final.
- **Occupying sets:**

  | Resource | Occupied in |
  |---|---|
  | Tutor | `offered`, `confirmed`, `completed` |
  | Room and students | `unassigned`, `offered`, `confirmed`, `completed` |

  `cancelled`, `skipped_closure` and `conflict` occupy nothing.
- **Family visibility:** students and guardians see only `confirmed` and `completed` sessions, plus the cancelled or skipped occurrences of sessions they had already seen as confirmed (#67 decides how they are shown). They never see `unassigned`, `offered` or `conflict`.

### Offers and the response deadline (D3 (a), ISSUE-SC1, product)

- **Scope:** an offer covers one one-off session, or one series assignment: every not-yet-completed occurrence of the series from a given date onward. A substitution (#55) is a one-occurrence offer (see the [amendment](#amendments) for what it does to the session).
- **Offer records:** offers are rows of their own: tutor, scope, status `pending`, `accepted`, `declined`, `expired` or `withdrawn`, `respond_by`, `responded_at` and an optional decline note. The history of who was offered what survives reassignments.
- **Deadline:** `respond_by = min(offered_at + response_hours, starts_at of the first covered occurrence)`. `response_hours` is part of the M4-16 scheduling policy, default **48**. The administrator making the offer may choose an earlier deadline, never one later than the first covered occurrence.
- **Decline or expiry:** the offer becomes `declined` or `expired`. Every covered session goes to `unassigned` with its tutor cleared: the tutor's slot is released, and the room and the students stay held. A system-raised inbox item (#40, `requester_user_id NULL`) asks for reassignment, and the administrators are notified.
- **Expiry task:** a periodic Procrastinate task with a `queueing_lock` (ADR-0003) expires overdue offers every 5 minutes, idempotently.
- **Later occurrences:** occurrences that a series materializes after its assignment was accepted are created `confirmed`.

### Recurrence (D4 (a), ISSUE-SC2)

- **Storage:** a series stores a normalized RFC 5545 `RRULE`, a local start date, a local start `TIME`, a duration in minutes and a `TZID` (the center zone when it is created; ADR-0004).
- **Rule subset:** `FREQ=WEEKLY`, `INTERVAL` 1–4, `BYDAY` with one or more weekdays, and at most one of `UNTIL` (a local date, stored as end of day local) or `COUNT` (1–200). With neither, the series is open-ended and rolling. Excluded dates are exception rows, not `EXDATE` text.
- **API:** the API takes and returns structured fields (`weekdays`, `interval`, `until`/`count`) as well as the derived `rrule` string. The web app never parses `RRULE`s.
- **Expansion and DST:** the backend expands in naive local wall-clock time, then attaches `ZoneInfo(TZID)` with `fold=0`. An ambiguous time (fall back) takes the first instant; a nonexistent time (spring forward) moves forward by the gap. A session must start and end on the same local date. Tests use `America/Tijuana`, which still has DST (`America/Mexico_City` has had none since 2022).
- **Horizon:** a rolling **12-week** horizon from the operator setting `SCHEDULING_HORIZON_WEEKS` (pydantic-settings, 4–26; not a business setting), extended by a nightly periodic task at 08:00 UTC (02:00 in Mexico City). Inserts use `UNIQUE (series_id, original_start)` with `ON CONFLICT DO NOTHING`.
- **Exceptions:** an edited occurrence is marked `is_exception`, and because of the unique key regeneration never touches it. Past, `completed` and `cancelled` occurrences are never rewritten.
- **"This and future" split:** the old series gets `UNTIL` = the local day before the split occurrence, and a new series starts at the split occurrence. Future, not-completed occurrences of the old series are moved to the new series, keeping their row id (so any charge stays attached, spec §3), when the new rule produces an occurrence on the same local date. The others become `cancelled` with the system reason `series_changed`.
- **Checks during materialization:**
  - A series **create** checks every occurrence within the horizon. Any conflict refuses the whole create, with a per-occurrence conflict list. Soft conflicts can be overridden for listed occurrence and code pairs.
  - The **nightly extension** runs the same checks. An occurrence that fails a hard check, or an unoverridden soft check, is inserted as `conflict` (it holds nothing) and raises an inbox item. Nothing is created silently (W1.2), and nothing is dropped.

### Recurrence library (D5 (a))

- **Packages:** the backend uses `python-dateutil`'s `rrule`/`rrulestr`, as a direct dependency `python-dateutil>=2.9.0.post0,<3` (the lockfile already resolves **2.9.0.post0**, through `procrastinate` 3.10.0 and `croniter` 6.2.4), with the dev dependency `types-python-dateutil>=2.9.0.20260807` (typeshed stubs, Apache-2.0, released 2026-08-07) for `mypy --strict`.
- **Licence:** dual Apache-2.0 / BSD-3-Clause, per its PyPI classifiers.
- **Maintenance:** checked 2026-10-19. The last release was 2024-03-01; the repository is active (commits on 2026-10-19) and not archived. `rrule` is a stable, pure-Python module that already ships at runtime through Procrastinate.
- **Who adds them:** #53 (M4-08), not this issue.
- **Frontend:** no JavaScript `rrule` package (2.8.1, last published 2023-11-10, repository not pushed since 2024-06) and no `@fullcalendar/rrule`, which depends on it. The server materializes occurrences and the client renders sessions.

### Closures, makeup and billing (D6 (a), ISSUE-SC2, product)

- **Skipping:** a series occurrence whose local date is in a closure (#38 `closures_between`) is stored as `skipped_closure`, never deleted, and holds nothing.
- **Closures added later:** the next materialization run turns the affected future `unassigned`, `offered` and `confirmed` series occurrences into `skipped_closure` and notifies the participants. Creating a closure also enqueues that run.
- **Closures removed later:** future `skipped_closure` rows on dates that are no longer closed are re-checked and restored; if a check fails, they become `conflict`.
- **One-off sessions** on a new closure are only marked, never changed: administrators see a computed `center_closed` marker.
- **No automatic makeup obligation.**
- **Billing:** a skipped occurrence is **never billable**. Per-session lessons never charge it. If a charge was already posted, M6-14 reverses it with an adjustment. Course and tuition-plan prices are **not prorated** for closures, because the center's calendar is known when they are sold.
- **Makeups:** an administrator can schedule a makeup as an ordinary one-off session, under ordinary rules.

### Effective capacity (D7 (a), ISSUE-SC3)

- **Formula:** effective capacity = `min(group_or_cohort_max, room_capacity)`. With no room, it is `group_or_cohort_max`. An individual session has exactly one student.
- **Group or cohort maximum:** soft (`group_capacity_exceeded`), overridable up to the room capacity.
- **Room capacity:** hard (`room_capacity_exceeded`, D1).

### Calendar (D8 (a), ISSUE-X2, spec §18 item 6)

- **Library:** FullCalendar **v7, standard MIT views only**: time-grid day and week, day-grid month, list, and the `interaction` subpath for drag-and-drop. Tutor and room "columns" are replaced by the filters spec §3 requires (tutor, student, room, type). **No premium or resource views and no licence key.**
- **Pins (exact):** `@fullcalendar/react` **7.1.3** and `temporal-polyfill` **1.0.6**, which bring `@fullcalendar/core` 7.1.3 and `@full-ui/headless-calendar` 7.1.3 (all MIT).
- **Release-age policy:** if pnpm's minimum-release-age policy refuses 7.1.3 or 1.0.6 when the consumer installs them, the engineer takes the newest allowed 7.1.x or 1.0.x and records it (the ADR-0001 precedent).
- **Time zone:** named IANA zones go through the `timeZone` option (Temporal), with no extra plugin.
- **Styling:** `skeleton.css` plus the stock **classic** theme, with its `--fc-classic-*` variables mapped to Classora's tokens in light and dark, in one stylesheet. The FullCalendar shadcn registry blocks are **not copied**, because their source (`fullcalendar-workspace/theming/ui-shadcn`) states no licence.
- **Who adds them:** #57 (M4-12), not this issue.
- **Licence evidence** (primary sources checked 2026-10-19: npm registry, the GitHub repositories, fullcalendar.io/license): v7 is the current major since 7.0.0 (2026-06-19), `latest` 7.1.3 (2026-10-16); the repository is MIT, active and not archived; `@fullcalendar/react` is MIT with peers `react ^17 || ^18 || ^19` and `temporal-polyfill ^1.0.1`. Premium resource views (`@fullcalendar/react-scheduler`, resource time grid and timeline) are offered under a **commercial** licence, **AGPLv3** for fully open-source apps (from v7), or **CC BY-NC-ND** for non-profits only. None is needed, because Classora uses no premium view; a for-profit private product could otherwise only use the commercial licence.

### Permissions (D9 (a))

| Permission | Holders | Delegable | Covers |
|---|---|---|---|
| `schedule.view` | Owner, Administrator, Coordinator, Finance | no | Read the full schedule and calendar |
| `schedule.manage` | Owner, Administrator | no | Create, reschedule and cancel sessions and series; review session, change and cancellation requests; enroll in and withdraw from groups and cohorts |
| `schedule.assign` | Owner, Administrator, Coordinator | no | Offer a tutor, substitute a tutor, see eligibility and replacement suggestions |
| `schedule.override` | unchanged (ADR-0007) | yes | Override soft conflicts |

Tutors, students and guardians reach their own schedules through record-level policies, not permissions. Each new permission is added to the code by the first issue that uses it (#48 `schedule.manage` and `schedule.view`, #52 `schedule.assign`), not here.

## Consequences

- **#47 (M4-02)** implements the session states (D2), the two partial `EXCLUDE` constraints over the occupying sets, and the time-zone dependency registration (#37). It reuses `btree_gist` and does not create it again.
- **#48 (M4-03)** implements the D1 checks and the conflict wire format, including the per-student advisory lock and the capacity row lock.
- **#49 (M4-04)** implements overrides and override rows.
- **#52 (M4-07)** and **#61 (M4-16)** implement offers and `response_hours`. #52 depends on #61 for the policy field.
- **#53 (M4-08)** and **#54 (M4-09)** implement recurrence, the horizon, splits and closure skipping. #53 adds `python-dateutil` and `types-python-dateutil`.
- **#57 (M4-12)** adds the calendar dependencies.
- **M6-14** implements the billing effects of D6: a skipped occurrence is never billed, a posted charge is reversed, and courses and plans are not prorated.
- This ADR adds nothing to the dependency files (`pyproject.toml`, `uv.lock`, `package.json`, `pnpm-lock.yaml`) and changes no code, migration or permission seed.
- Two product decisions are fixed here (D3 deadline, D6 makeup and billing). Later PMs, including M6, must honour them.
- FullCalendar v7 is recent and its API differs from v6; #57 builds against v7 docs only, and `temporal-polyfill` adds size to the lazy-loaded calendar route.

## Alternatives considered

- **D1 (b) — student overlap soft.** Rejected: a double-booked student is a data error, not a policy choice.
- **D1 (c) — everything soft except tutor and room overlap.** Rejected: capacity would no longer be guaranteed, against spec §3 "respects room capacity".
- **D2 (b) — keep an unchecked `draft` state.** Rejected: no spec workflow uses it, and drafts could pile up and conflict when offered.
- **D3 (b) — an expired offer keeps holding the tutor's slot.** Rejected: the tutor who didn't answer stays blocked, and the session looks assigned when it isn't.
- **D3 (c) — no deadline.** Rejected: a session could reach its start time unconfirmed.
- **D4 (b) — full RFC 5545 rules in the API and UI.** Rejected: many more UI and test cases and no spec workflow needs them; the model stays compatible for a later ADR.
- **D4 (c) — store every occurrence up front.** Rejected: open-ended series become impossible and editing large series is costly.
- **D5 (b) — our own weekly expander.** Rejected: we would maintain our own parser and serializer for a standard format (spec §3 "iCalendar RRULE patterns").
- **D5 (c) — `icalendar` + `recurring-ical-events`.** Rejected: more than we need, and two new packages.
- **D6 (b) — a makeup obligation per skipped occurrence.** Rejected: a new entity and workflow the spec doesn't describe.
- **D6 (c) — configurable per class type.** Rejected: both behaviours would be needed in M4 and M6.
- **D7 (b) — room capacity overridable.** Rejected: contradicts spec §3 "respects room capacity".
- **D8 (b) — FullCalendar v6.1.22 with `@fullcalendar/luxon3` + `luxon`.** Rejected: more dependencies and an older, patch-only line.
- **D8 (c) — buy the premium commercial licence.** Rejected: spec §3 doesn't require resource columns, and the cost is recurring.
- **D9 (b) — Coordinators also hold `schedule.manage`.** Rejected: spec §3 gives creating and cancelling classes to administrators.
- **D9 (c) — a single `schedule.manage` with nothing for Coordinators.** Rejected: leaves out the "tutor assignments" spec §2 gives Coordinators.

## Amendments

### A1 — Substitution offers (#55 E1, 2026-10-22, owner-delegated)

Under D3, a **substitution offer** (`assignment_offers.kind = 'substitution'`, always session scope) covers one `confirmed` occurrence that hasn't started. Unlike an assignment offer:

- while it is pending, the session **stays `confirmed` with its original tutor** (there is no `confirmed → offered` transition, so families never lose sight of it). The substitute's slot is not held: the accept answers `409 scheduling_conflict` (`tutor_overlap`) if they were booked meanwhile;
- **accepting** swaps the tutor in one transaction (still `confirmed`, under the tutor `EXCLUDE`), after re-checking that the session is still `confirmed` with the original tutor at the offered times;
- **declining or expiring** leaves the session unchanged (still `confirmed`, original tutor) and raises the same reassignment inbox item and staff notifications as an assignment offer.

D2 is unchanged. A session has at most one pending substitution; writers that move or cancel a `confirmed` row withdraw it. A center cancellation stores no policy classification; billing (M6-14) and pay (M7-04) add their steps to the `app/workflows/session_cancellation.py` seam (#55 E3).

## References

- [`../architecture.md`](../architecture.md) §7.4, §8, §10.3 ISSUE-D4 and ISSUE-D6, §10.5 ISSUE-SC1, ISSUE-SC2 and ISSUE-SC3, §10.12 ISSUE-X2, §11, §12
- [`../spec.md`](../spec.md) §1, §2, §3, §10, §15 W1 and W2, §16, §18 item 6
- [ADR-0002](0002-module-boundaries-and-reporting.md), [ADR-0003](0003-database-connection-topology.md), [ADR-0004](0004-money-time-and-date-representation.md), [ADR-0007](0007-authentication-sessions-recovery-and-delegation.md)
- GitHub issue #46 (this decision gate); groomed issues #37, #38, #39, #44, #45, #197 and #198; #55 (amendment A1)
- Primary sources (checked 2026-10-19): `registry.npmjs.org/@fullcalendar/react`, `/temporal-polyfill` and `/rrule`; `fullcalendar.io/license`, `/docs/react`, `/docs/timeZone`, `/docs/accessibility` and `/docs/shadcn`; `github.com/fullcalendar/fullcalendar`; `pypi.org/project/python-dateutil` and `/types-python-dateutil`; `github.com/dateutil/dateutil`
