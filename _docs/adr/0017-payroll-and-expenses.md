# ADR-0017: Payroll and expenses

## Status

Proposed (orchestrator-recommended, pending owner review)

## Date

2026-10-31

## Sign-off

No owner sign-off yet. Decision items E1–E18 are **orchestrator-recommended** (option (a) for each), taken on 2026-10-31 (GitHub issue #104, M7-01) so that M7 can proceed. The owner has **not reviewed them**, so this ADR stays Proposed. M7 issues build on these decisions in the meantime. The owner's review result, and any amendment, will be recorded in this section.

Items flagged for the owner's review:

- **(product):** E1, E2, E3, E4, E5, E6, E7, E8, E9 (the negative-total rule only), E10, E12, E14 (the non-fiscal wording), E15 and E18.
- **E4 defaults:** the three seeded defaults (late cancellation `none`, no-show 100%, makeup 100%). With `none`, a tutor earns nothing for a late-cancelled slot.
- **E14:** whether a payslip that is not a tax document (no CFDI de nómina) is acceptable for the center's legal payroll process, which stays outside the platform.
- **(spec reading):** E3, E4, E5, E10, E11 and E16, recorded under Context.

## Context

Records decision items **E1–E18** of issue #104, the M7 decision gate. The schema that implements this ADR is in [`../data-model.md`](../data-model.md) §11 onward. Architecture context is in [`../architecture.md`](../architecture.md) §4.1, §5, §10.6 and §12.

- Spec [`../spec.md`](../spec.md) §1: MXN only; staff record payments made outside the platform; in-app notifications only; no CFDI or SAT integration.
- Spec §2: Academic Coordinators verify teaching hours; Finance Staff prepare payroll and track payments and expenses; sensitive actions need password reauthentication; tutors see only their own compensation details.
- Spec §3: approved closeouts and re-approval; corrections after approval.
- Spec §7: the compensation models, tutor-specific rates with history, individual payroll frequencies, configurable late-cancellation, no-show and makeup payments, the workflow, proration, bonuses and deductions, the lock, logged payments, the tutor portal.
- Spec §8: expense records, categories, recurring templates, vendors, cash and accrual reporting, payables apart from receivables, direct tutor cost by class type, and no shared-overhead allocation.
- Spec §9: audit trail, corrections through adjustments and reversals. Spec §10: PostgreSQL transactions, idempotency keys, Procrastinate, no Redis or Celery.
- Spec §16: decimal-safe money, local dates, payroll locks, immutable posted activity, database-enforced invariants, indexed access paths.
- Spec §17: `data-model.md` is the schema review required before financial workflows. Spec §18 item 1 leaves table names, cardinalities and finance ledger conventions open; this ADR resolves the payroll and expense part, together with `data-model.md`.
- Spec §15 W3 (session closeout to payroll, in particular W3.2, W3.4 and W3.5), W4 (the billing conventions this ADR mirrors) and W7 (background jobs: repeatable without duplicates, tested for retries and concurrency).
- **File deferral:** the 2026-10-21 owner decision (no uploads in this version, #68–#75 deferred) applies to #112 and #115. A reference and a note replace the proof or receipt file.
- **Spec readings, without changing the spec:**
  - **E3:** a per-course amount is agreed once, so its rate version is the one in effect on the cohort's first session's local date, not on each session's date. W3.4 stays literal for every other model.
  - **E4:** a no-show is a current approval revision with every participant `absent`, or a future cancellation classified `no_show`. No M4 path produces that classification today (ADR-0006 Q8).
  - **E5:** spec §7's configurable makeup payment needs to know which session is a makeup, so scheduling gains a link that billing ignores.
  - **E10:** "Academic Coordinators verify hours" is the closeout approval (`teaching_hours.approve`, #78). There is no second verification step.
  - **E11:** #111's "each adjustment references the original line" can't hold for a `late_approval`, which has no line; it references the session and the origin period.
  - **E16:** spec §8's "paid/unpaid status" is derived, never stored.
- **Builds on:**
  - [ADR-0002](0002-module-boundaries-and-reporting.md): the `payroll` and `finance` modules, cross-module calls through `service.py`, and read-only reporting in `reporting`.
  - [ADR-0003](0003-database-connection-topology.md): Procrastinate, after-commit enqueue, sweepers and `queueing_lock`.
  - [ADR-0004](0004-money-time-and-date-representation.md): `NUMERIC(14,2)`, half-up per line, `DATE` for calendar values, the center time zone.
  - [ADR-0005](0005-scheduling-invariants-overrides-and-recurrence.md) D6: `skipped_closure` sessions and makeups.
  - [ADR-0006](0006-financial-ledger-idempotency-and-numbering.md): E3 (cancellation outcomes), E5 (ledger shape and triggers), E6 (idempotency keys), E7 (recognition), E8 (permissions) and E13 (the `P` number series).
  - [ADR-0007](0007-authentication-sessions-recovery-and-delegation.md): D4 (`payroll.finalize` is delegable) and D10 (reauthentication).
  - [ADR-0015](0015-server-side-i18n.md): server-rendered, language-neutral documents.
- **Code it builds on** (merged): `session_closeouts.current_approval_id` and `session_closeout_approvals` (#78), on-behalf closeouts (#301), accepted substitutes (#55), change-request outcomes read through `get_change_request_outcome` and `cancellation_classification` (#62, #99), the seams `on_closeout_approved`, `on_session_cancelled` and `on_session_rescheduled`, `payment_methods` (#90), `number_series` (#98) and `Money.prorate` (#87).

## Decision

No new runtime dependency is needed; WeasyPrint, Jinja2 and `number_series` already exist. No Redis, Celery, RDS, uploads or file storage.

Each (a) is the additive, reversible choice: money errors go to the side a later positive adjustment can fix, and every configurable rule stays effective-dated.

### Compensation (E1 (a), spec §7, #105, product)

- Two effective-dated, per-tutor tables in the new `payroll` module:
  - **`tutor_pay_terms`** `(tutor_user_id, effective_from DATE, pay_frequency NULL, salary_amount NULL)`, `UNIQUE (tutor_user_id, effective_from)`, holds what applies to the tutor as a whole. `salary_amount` is per full pay period of that row's frequency, so salary and frequency can't disagree. `pay_frequency NULL` means "not on payroll from this date" (the tutor has left).
  - **`compensation_rates`** `(tutor_user_id, class_kind, effective_from DATE, rate_model NULL, rate NUMERIC(14,4) NULL)`, `UNIQUE (tutor_user_id, class_kind, effective_from)`. That key is the no-overlap constraint #105 asks for, by the ADR-0006 E9 pattern: each version runs until the next one's `effective_from`. `class_kind` ∈ `individual`, `group`, `course` (the `class_sessions.kind` values). `rate_model` ∈ `per_hour`, `per_session`, `per_course`, with `per_course` only for `course`. `rate_model` and `rate` are both `NULL` to mean "no rate from this date".
- **The five spec models are combinations:** rates only → hourly, per-session or per-course; `salary_amount` only → fixed salary; both → salary plus extras.
- **"Additional paid hours or sessions"** is work in a class kind that has a rate. A salaried tutor's work in a class kind with no rate is **covered by salary**: it gets a `0.00` line, for traceability. There is no quota.
- **No center-wide default rates** and no rate per subject or level: the spec says tutor-specific.
- **Versions** are immutable once referenced by a line of a finalized period (guarded, like `price_versions`); a change is a new version.

### Paid work (E2 (a), spec §3, §7, W3, ISSUE-F2, #107, #111, #117, product)

- **Source of hours:** only the **current approval revision** (`session_closeouts.current_approval_id`, #78). A closeout without one is never paid. A closeout back in review keeps its revision in force, as #78 and #94 already read it. There is no `teaching_hours` table: the spec's candidate "TeachingHours" entity **is** `session_closeout_approvals`.
- **Who is paid:** the approval's `tutor_user_id`, which is the session's current tutor at closeout (an accepted substitute, #55). A closeout submitted on behalf stays the tutor's (#301).
- **Service date:** the center-local date of the approval's `session_starts_at`. It decides both the rate in effect (W3.4) and the period. A started session can't move, so it is stable.
- **Amounts, rounded half-up once per line (ADR-0004):** `per_hour` = `rate × approved_minutes / 60`; `per_session` = `rate`, whatever the minutes or the number of students; `per_course` = E3. Families stay billed on `scheduled_minutes` (ISSUE-F2).
- **Go-live floor:** `payroll_settings.payroll_starts_on DATE NULL`. `NULL` means payroll is off and no period is opened; afterwards work dated before the floor is never paid. It mirrors `billing_starts_on`, so going live never pays DEV history or demo data.
- **Unresolved work:** an approved closeout dated on or after the floor whose tutor has no terms or no rate gets no line and appears on a staff list with reason `no_pay_terms` or `no_rate`. Nothing is paid at zero by accident. Open items block `prepare` for that tutor (`409 payroll_unresolved_work`).

### Per-course pay (E3 (a), spec §7, W3.4, #107, #117, spec reading, product)

- A `per_course` rate is one amount for teaching a whole cohort, earned session by session with cumulative rounding, the same shape as ADR-0006 E7 for course fees.
- For a tutor with `k` approved sessions in a cohort of `N` sessions, the cumulative earned amount is `rate × k / N` (`Money.prorate`). Each line takes "cumulative now − cumulative before", floored at `0.00`. `N` counts the cohort's sessions that aren't cancelled or skipped at calculation time.
- **Spec reading:** the rate version is the one in effect on the **cohort's first session's local date**, not each session's date. W3.4 is otherwise literal for every other model.
- A substitute with their own `per_course` rate earns their share by the same formula. A substitute with a `per_session` or `per_hour` rate on `course` is paid by that rate.

### Pay events (E4 (a), spec §7, M4-17 outcomes, #107, product, spec reading)

A payroll-owned, center-wide table `session_pay_rules`, with one row per `(event, effective_from DATE)`, append-only with an upcoming version deletable (the `cancellation_billing_rules` shape). The rule in effect on the **service date** applies. Each row has `mode` ∈ `none`, `percent`, `fixed`, with `percent` or `amount`.

| Event | Detected from | Base for `percent` | Seeded default |
|---|---|---|---|
| `late_cancellation` | `cancelled` session, `initiated_by = family`, classification `late` | the ordinary line on `scheduled_minutes` | `none` |
| `no_show` | current revision with every participant `absent` (spec reading), or a future cancellation classified `no_show` | the ordinary line on `approved_minutes` | `percent` 100 |
| `makeup` | the session has `makeup_of_session_id` (E5) | the ordinary line | `percent` 100 |

- **Defaults:** approved minutes stay payable as W3.4 says (no-show and makeup at 100%). Late cancellation pays nothing until the owner sets a rule, because no work happened; a positive adjustment can fix an underpayment, while an overpayment on a locked period is harder to recover. **The owner should review these three defaults.**
- **Never paid:** on-time and excused cancellations, center cancellations, tutor-initiated cancellations, and `skipped_closure`.
- **Reschedules have no pay effect:** the spec lists none. `on_session_rescheduled` stays a no-op and #107 corrects its docstring.
- A partly absent group is an ordinary session.
- The outcomes come from what the code exposes: `get_change_request_outcome` and `cancellation_classification`. Requests exist only for confirmed, not-started individual sessions, and a center cancellation is `excused`.

### Makeup link (E5 (a), spec §7, ADR-0006 E3, #423 M7-04a, #107, product, spec reading)

- A nullable self-reference `class_sessions.makeup_of_session_id` (`ON DELETE RESTRICT`), set by staff with `schedule.manage` when creating a one-off individual session.
- The target must be `cancelled` with `makeup_allowed`, the new session must start by `makeup_until`, and a partial unique index allows one makeup per cancelled session.
- `NULL` keeps every current behavior. **Billing doesn't read it**, so E3 of ADR-0006 stands.

### Period calendars (E6 (a), spec §7, ADR-0004, #106, product)

- Periods are per tutor, inclusive `DATE` ranges `starts_on … ends_on`, on fixed calendars so tutors of one frequency share boundaries:
  - **weekly:** Monday to Sunday;
  - **biweekly:** 14 days from `payroll_settings.biweekly_anchor_on`, a Monday (default `2026-01-05`);
  - **monthly:** the calendar month.
- A period runs from the day after the previous one (or the first terms date on or after the floor) to the natural end of its calendar, or to the day before the terms stop or the frequency changes. So a **stub period** is possible. A frequency change must be dated after every generated period (`422 effective_from_inside_generated_period`).
- **No overlaps:** `EXCLUDE USING gist (tutor_user_id WITH =, daterange(starts_on, ends_on, '[]') WITH &&)`.
- **No gaps:** a generated `previous_ends_on` (`starts_on − 1`, `NULL` for the tutor's first period) with a composite foreign key onto `UNIQUE (tutor_user_id, ends_on)`, plus one partial unique "first period" per tutor. #106 proves it in its migration test; if PostgreSQL refuses the generated foreign key column, it uses a plain column with a `CHECK`, and the guarantee stays database-enforced.
- **Job:** `payroll:open_periods`, daily at 07:50 UTC with a `queueing_lock`, on the natural key `(tutor_user_id, starts_on)`. It opens each period up to the one containing the center's today, with catch-up, and one more when an adjustment needs an open period (E11).

### Salary and proration (E7 (a), spec §7, #108, product)

- A full period with one terms version pays exactly `salary_amount`.
- Otherwise each terms piece inside the period pays `salary_amount × paid_days / natural_days` (`Money.prorate`, one `salary` line per piece). `natural_days` belongs to the natural calendar period, not a stub.
- `payroll_settings.salary_proration_basis` is `calendar_days` (default) or `weekdays` (Monday to Friday), and each line stores its numerator, denominator and basis. A change of basis affects open periods only.
- **Finance review** is the line display before `prepare`; a different amount is a one-time pay item (E8), never an edited line.
- Closures and absences don't reduce salary.

### Bonuses and deductions (E8 (a), spec §7, #109, product)

- `pay_item_types` (staff-defined name, `direction` `bonus` or `deduction`, `is_active`), and `pay_items` (tutor, type, description, positive `amount`, `recurrence` `one_time` or `recurring`, `effective_from`, `effective_to NULL`).
- **Recurring:** the full amount in every period overlapping the range, with no proration. **One-time:** in the period containing `effective_from`, or the tutor's next open period if that one is finalized.
- **Natural key:** `(pay_item_id, payroll_period_id)` on the lines. Each is a separate line; deductions are negative lines.
- **Fixed amounts only, no percentages:** there is no tax or social-security calculation (spec, #109 out of scope). An item applied to a finalized line can only have its `effective_to` set.

### Lines and calculation (E9 (a), spec §7, §16, ADR-0004, #107, #110; product for the negative-total rule only)

- **One `payroll_lines` row per source:** a session, a pay event, a salary piece, a pay item or an adjustment. A rate change mid-period therefore splits naturally by service date, each line references its session, and an adjustment can reference the exact line. Each line snapshots `service_date`, `class_kind`, `minutes`, `rate_model`, `rate`, the rate version and the rule version. The period total is the sum of rounded lines (ADR-0004).
- **State-derived calculation:** `recalculate(period)` under the period row lock deletes the open period's lines and rebuilds them from committed state, so it is repeatable. It runs on demand and at `prepare`. **No hook posts pay:** `on_closeout_approved`, `on_session_cancelled` and `on_session_rescheduled` stay free of pay steps, and #107 updates their docstrings, which still promise one. This is the #99 Q1 reasoning: a payroll failure never rolls back a closeout or a cancellation.
- **Audit:** lines are working data until finalized, so they aren't `Audited`; named audit events on the period (`payroll.period_prepared`, `_returned`, `_finalized`) carry the totals, the #78 precedent. Rates, terms, rules, items, adjustments and payments are audited.
- **Negative total (product):** `prepare` refuses a period whose total is below `0.00` (`409 payroll_negative_total`); Finance reduces the deduction or moves it to a later period. No carry-forward.

### Workflow and lock (E10 (a), spec §7, W3.5, ISSUE-F7, #110, product, spec reading)

- **States** are `open → prepared → finalized`, plus a return `prepared → open` with a reason, so the approver can send a period back. #110's forward states are unchanged.
- **"Coordinators verify hours" is the closeout approval** (`teaching_hours.approve`, #78). There is no second verification step (spec reading).
- **`prepare`** (`payroll.prepare`): only after `ends_on` is before the center's today; refused with unresolved work (E2) or a negative total (E9); recalculates. Closeouts still unapproved are shown as a warning, not a block; they arrive later as `late_approval` adjustments (E11).
- **`finalize`** (`payroll.finalize`, reauthentication, `Idempotency-Key`): recomputes the lines and compares them with the prepared ones. Any drift is `409 payroll_period_stale`, and the period must be returned and prepared again. Then it stores `total_amount`, `finalized_at` and `finalized_by_user_id`.
- **Duty separation:** the preparer can't finalize, and nobody can finalize their own pay (`403 payroll_self_finalize`), unless they hold the Owner role (the `closeout_self_review` precedent). A delegated Administrator needs no `payroll.view` to review: holding `payroll.finalize` reads the period.
- **Inbox:** a prepared period creates an approval request of type `payroll.period`, decided only on its page (`409 payroll_review_route_required` from the generic route, the #60 and #62 precedent).
- **Lock:** a database trigger on `payroll_lines` raises 42501 on any `INSERT`, `UPDATE` or `DELETE` whose period is `finalized`, and refuses `TRUNCATE`; `payroll_periods` refuses any change to a finalized row. Line writes are serialized by the period row lock.
- **Notifications:** in-app only, `payroll.period_finalized` and `payroll.payment_recorded` to the tutor, with no amounts in `params`.

### Corrections after lock (E11 (a), spec §3, §7, W3.5, #111, spec reading)

Append-only `payroll_adjustments`:

- **Kinds:** `hours_correction` (a superseding revision of a closeout on a finalized line), `pay_event_change` (a finalized pay event no longer holds), `late_approval` (a closeout approved after its service date's period was finalized) and `manual` (including a wrong rate).
- **Target:** the tutor's **earliest open period**. `payroll:open_periods` opens one if needed, even after the tutor has left. A prepared period is not a target.
- **Amount:** `target − (origin line + earlier adjustments of that line)`, using the origin line's **rate snapshot**, never 0. `late_approval` uses the service date's rate and rule.
- **Reference:** `origin_line_id NOT NULL`, except for `late_approval`, which has no line and stores `class_session_id` and `origin_period_id`.
- **Natural key:** `source_key` unique (`approval:<approval_id>`, `event:<line_id>:<n>`). A job `payroll:post_adjustments` every 15 minutes with a `queueing_lock` finds them by anti-join, and closeout corrections may enqueue it after commit. `manual` rows use the API idempotency key.
- **Rate corrections:** a used rate version is immutable (E1), so a wrong finalized rate is a `manual` adjustment on the line, with `payroll.manage` plus `finance.correction` reauthentication.
- An adjustment appears as a line (kind `adjustment`) in its target period. The finalized period doesn't change.

### Permissions (E12 (a), ISSUE-F7 follow-on, spec §2, §7, §8, #105, #107, #110, #112, #114, #115, #117, product)

Permissions, not roles; each seeded by its first user; only `payroll.finalize` is delegable. `payroll.finalize` is in the code; `payroll.prepare` is not, and ADR-0006 E8 is clarified accordingly.

| Permission | Holders | Scope | First used by |
|---|---|---|---|
| `payroll.view` | Owner, Finance Staff | read all terms, rates, periods, lines, payments | #105 |
| `payroll.manage` | Owner, Finance Staff | terms, rates, pay rules, pay items, payroll settings, manual adjustments | #105 |
| `payroll.prepare` | Owner, Finance Staff | recalculate, prepare, return | #107 |
| `payroll.finalize` | Owner; delegable to Administrators (in code) | finalize, return, with reauthentication | #110 |
| `payroll.payment.record` | Owner, Finance Staff | log payroll payments | #112 |
| `expense.configure` | Owner, Administrator, Finance Staff | categories and vendors ("administrator-defined", spec §8) | #114 |
| `expense.view` | Owner, Finance Staff | read expenses, templates, payments | #115 |
| `expense.manage` | Owner, Finance Staff | expenses, payments, adjustments, templates | #115 |
| `finance.report.view` | Owner, Finance Staff | #117 reports | #117 |

- **Administrators see no pay and no expense amounts** unless delegated `payroll.finalize` (#105, #115 and #117 name only Finance and the Owner). **Academic Coordinators** get no payroll or expense permission.
- **Tutors** read their own terms, rates, finalized lines, payments and payslips through record policy only, with no permission. Another tutor's id is the same `404`.
- **Reversals** of payroll or expense payments reuse `finance.refund.approve` with `finance.refund_or_reversal` reauthentication. **Manual payroll adjustments and expense adjustments** need `finance.correction` reauthentication. Recording a payment needs none (the ADR-0006 M6 clarification).

### Payroll payments (E13 (a), spec §7, #112, #117)

- Append-only `payroll_payments` (`payroll_period_id`, `paid_on DATE`, `amount > 0`, `method_code` → the billing `payment_methods` table through `billing.service`, `reference` required 1–100, `note NULL` 1–1000, `recorded_by_user_id`). No file.
- A finalized period only. **Partial payments are allowed**: Σ valid payments ≤ `total_amount`, checked under the period lock. Paid state is derived: `unpaid`, `partially_paid`, `paid`.
- `Idempotency-Key` is required, and the same method and reference on the same period under a **different** key is `409 payroll_payment_duplicate`.
- A mistaken payment gets one `payroll_payment_reversals` row (E12), never an edit.

### Payslips (E14 (a), spec §1, §7, ISSUE-F5, #113, ADR-0015, product for the non-fiscal wording)

- The #98 pattern. One `payslips` row per finalized period that has lines, issued in the finalize transaction after the period lock, numbered from `number_series` `P` by the center-local year, in the format `P-2026-000123` (`P-YYYY-NNNNNN`).
- It holds a frozen JSONB snapshot (center, tutor, period, lines, total), `language` (the tutor's stored preference, else `es`) and `template_version`. It is rendered synchronously and streamed, never stored.
- Payment dates aren't in the snapshot; they are shown live.
- **Only finalized lines count as earnings** in the portal.
- Periods finalized before #113 merges get no payslip (DEV data only).
- **The payslip states it is not a tax document (no CFDI de nómina, spec §1).** The owner should confirm that this is acceptable for the center's legal payroll process, which stays outside the platform.

### Categories and vendors (E15 (a), spec §8, #114, #116, #117, product)

- Module `finance`. `expense_categories` (`name`, `is_active`, `system_key NULL`, unique name ignoring case) is seeded with `rent`, `utilities`, `supplies`, `equipment` and `maintenance`; a seeded row keeps its `system_key` for translation until renamed, when staff text wins. `vendors` (`name`, `contact NULL`, `is_active`).
- Both are mutable config with `version`, never deleted once referenced.
- **Reports group by id and show the current name**, which answers #114's "if that is decided".
- Deactivated rows can't be used on new expenses or templates but still appear in reports. Deactivating a row used by an active template is `409 in_use_by_template`.

### Expense ledger (E16 (a), spec §8, §16, #115, #117, spec reading)

The E5 shape of ADR-0006, smaller, all append-only:

- `expenses` (`category_id`, `vendor_id NULL`, `description`, `amount > 0`, `incurred_on`, `due_on`, `receipt_reference NULL`, `note NULL`, `expense_template_id NULL`, `period_start NULL`);
- `expense_payments` (`paid_on`, `amount`, `method_code`, `reference NULL`, `note NULL`), partial payments allowed;
- `expense_payment_reversals` (at most one per payment);
- `expense_adjustments` (signed, never 0, reason), the only correction.

**Derived:** `net = amount + Σ adjustments ≥ 0`; `open = net − Σ valid payments ≥ 0`; status `unpaid`, `partially_paid`, `paid`, `overdue` (open > 0 and `due_on` before the center's today), `void` (net = 0). Spec reading of "paid/unpaid status": derived, never stored.

**A wrong category, vendor or date** is corrected by voiding (a full negative adjustment) and entering a new expense. `receipt_reference` and `note` replace the attachment and are visible to `expense.view` holders only. No file. No tax column (spec §1). The expense row `FOR UPDATE` is the last lock.

### Recurring templates (E17 (a), spec §8, #116, ISSUE-J4)

- `expense_templates` (`name`, `category_id`, `vendor_id NULL`, `amount`, `frequency` `weekly`, `monthly` or `annual`, `starts_on`, `ends_on NULL`, `due_offset_days` 0–60, `next_period_start`, `is_active`, `version`).
- Periods are anchored at `starts_on` (weekly `+7k` days, monthly `+k` months, annual `+k` years, day clamped, the #93 K3 rule).
- The job `finance:generate_recurring_expenses`, daily at 07:55 UTC with a `queueing_lock`, posts each due period (`period_start ≤` the center's today), with catch-up, on the natural key `(expense_template_id, period_start)` from [`../architecture.md`](../architecture.md) ISSUE-J4.
- `incurred_on = period_start`, `due_on = period_start + due_offset_days`. Generated rows snapshot the template's values, so editing a template affects only future periods.
- Reactivating sets `next_period_start` to the first period on or after today, so a paused template never back-fills.

### Reporting (E18 (a), spec §8, ADR-0006 E7, #117, product)

A reporting rule for #117, in the new read-only `reporting` module (ADR-0002 D3).

- **Only finalized payroll counts.** Open and prepared periods are never reported as cost.
- **Accrual:** session and pay-event lines by `service_date`; salary and pay items on their period's `ends_on`; adjustments on their target period's `ends_on`, so past reports don't change; expenses by `incurred_on`, net of adjustments.
- **Cash:** payroll by `paid_on`, expenses by `paid_on`, net of reversals.
- **Direct tutor cost by class type:** session and pay-event lines by `class_kind`; an adjustment inherits its origin line's kind. **Salary and pay items are not allocated** to class types: they are a separate center-wide row (spec §8 selected no overhead allocation). So a fully salaried tutor shows no direct cost by class type. Cash-basis profitability is center-wide only.
- **Payables:** Σ open expenses plus Σ unpaid finalized payroll, reported apart from receivables.

## Consequences

- **Implementing issues** (each records its part; table detail in [`../data-model.md`](../data-model.md)):
  - **#105 (M7-02):** `tutor_pay_terms`, `compensation_rates`, the lookup, the guarded triggers with their staged predicates; seeds `payroll.view` and `payroll.manage` (E1, E12).
  - **#106 (M7-03):** `payroll_settings`, `payroll_periods`, the no-overlap and no-gap mechanisms, the job `payroll:open_periods` (E2 floor, E6).
  - **#423 (M7-04a):** `class_sessions.makeup_of_session_id` and its form field (E5). A prerequisite of #107.
  - **#107 (M7-04):** `session_pay_rules`, `payroll_lines`, `recalculate`, per-hour, per-session and per-course lines, pay events, unresolved work, the seam docstrings; seeds `payroll.prepare` (E2, E3, E4, E9, E12).
  - **#108 (M7-05):** `salary` lines and proration (E1, E7).
  - **#109 (M7-06):** `pay_item_types`, `pay_items`, `pay_item` lines (E8).
  - **#110 (M7-07):** the workflow, finalization, the proof of the lock triggers (created with their tables in #106 and #107), the inbox type, notifications (E9, E10).
  - **#111 (M7-08):** `payroll_adjustments`, `adjustment` lines, the job `payroll:post_adjustments` (E11).
  - **#112 (M7-09):** `payroll_payments`, `payroll_payment_reversals`; seeds `payroll.payment.record` (E12, E13).
  - **#113 (M7-10):** `payslips` and the tutor earnings portal (E12, E14).
  - **#114 (M7-11):** `expense_categories`, `vendors`; seeds `expense.configure` (E12, E15).
  - **#115 (M7-12):** `expenses`, `expense_payments`, `expense_payment_reversals`, `expense_adjustments`; seeds `expense.view` and `expense.manage` (E12, E16).
  - **#116 (M7-13):** `expense_templates` and the job `finance:generate_recurring_expenses` (E17).
  - **#117 (M7-14):** the `reporting` module and the reports; seeds `finance.report.view` (E12, E18).
- The coverage table "M7 issue → decisions and tables" is [`../data-model.md` §19](../data-model.md#19-m7-issue-coverage).
- **This ADR adds nothing** to the dependency files, the permission catalog or the migrations. Each implementing issue adds its own tables, seeds, permissions and jobs. **No new runtime dependency is needed.**
- This ADR is Proposed: a different owner answer on any item is an amendment here and in `data-model.md`, plus a follow-up issue for any part already implemented.
- Payroll is off until an administrator sets `payroll_starts_on`, so going live never pays DEV history or demo data (E2).
- A payroll failure can't roll back a closeout or a cancellation, because pay is derived from committed state (E9).
- An approved closeout with no terms or rate is visible and blocks `prepare`, rather than being silently paid at zero (E2).
- A finalized period never changes; later corrections are lines in a later open period (E10, E11, W3.5).
- A fully salaried tutor shows no direct cost by class type (E18).

## Alternatives considered

- **E1 (b), `included_minutes` per period, paying only minutes above a quota.** Rejected: it needs an ordering of sessions across rates and mid-period rate changes. A later version can add it, and today Finance can pay the excess with a bonus.
- **E1 (c), a separate `compensation_models` row with a model enum per tutor.** Rejected: it duplicates what the rows already say and can disagree with them.
- **E2 (b), assign work to periods by approval date.** Rejected: a tutor's hours land in a period that doesn't contain them, against W3.4's "service date".
- **E3 (b), pay the whole rate as a lump sum when the cohort ends.** Rejected: a tutor waits months, and direct tutor cost wouldn't line up with E7 revenue in #117.
- **E3 (c), use each session's own rate version.** Rejected: a mid-course change makes the cohort total unexplainable.
- **E4 (b), per-tutor pay rules.** Rejected: it multiplies configuration for a one-location center; a later version can add a tutor override.
- **E4 (c), pay outcomes on `scheduling_policies`.** Rejected for billing in ADR-0006 E3(c), for the same reason.
- **E5 (b), Finance tags a line as a makeup while preparing payroll.** Rejected: Finance doesn't know which sessions were makeups, and the fact would live outside the schedule.
- **E5 (c), no marker, makeups paid as ordinary sessions.** Rejected: it drops a spec §7 line and would need an explicit scope decision.
- **E6 (b), anchor each tutor's periods on their own start date.** Rejected: no shared pay days, so Finance prepares payroll on a different day per tutor.
- **E6 (c), one center-wide period for all tutors.** Rejected: contradicts "individual payroll frequencies".
- **E7 (b), a free override of the salary line with a reason.** Rejected: a second path to the same result as a one-time item.
- **E8 (b), percentage-based deductions.** Rejected: invites tax-like calculations the spec excludes.
- **E9 (b), one aggregate line per rate version with a `payroll_line_sources` child table.** Rejected: adjustments would have to split an aggregate line, and the payslip can group lines for display anyway.
- **E9 (c), post lines from the hooks in the closeout transaction.** Rejected: couples academics to payroll, and "recalculating gives the same result" gets harder.
- **E10 (b), a separate coordinator "verified" state per period.** Rejected: it re-approves what `teaching_hours.approve` already approved session by session.
- **E10 (c), no return path.** Rejected: a wrong prepared period could only be finalized and corrected later.
- **E11 (b), reopen a finalized period.** Rejected by spec §7.
- **E11 (c), adjust the period containing the approval date, even if already prepared.** Rejected: that would change a period someone is reviewing.
- **E12 (b), give Administrators `payroll.view` and `expense.view`.** Rejected: #105 and #115 restrict them and spec §2 keeps pay private. Reversible with one seed change.
- **E12 (c), one `payroll.manage` for everything.** Rejected: ISSUE-F7 asks for separation between preparing and paying.
- **E13 (b), one payment per period.** Rejected: it doesn't fit paying a tutor in two transfers.
- **E13 (c), a separate payroll payment-methods table.** Rejected: two lists to keep in step for M8-04.
- **E14 (b), issue the payslip at first download.** Rejected for receipts in E13(c) of ADR-0006.
- **E15 (b), show the name used at the time, with a name-history table.** Rejected: a lot of machinery for a rename, and the audit trail already holds old names.
- **E16 (b), mutable expenses, audited, while unpaid.** Rejected: #115 says corrections use adjustments.
- **E17 (b), generate only the current period.** Rejected: a stopped worker would silently lose rent.
- **E18 (b), allocate salary to class types by approved minutes.** Rejected: it is an allocation the spec didn't select, and it changes when later minutes are approved.

## References

- [`../architecture.md`](../architecture.md) §4.2, §4.4, §5, §6, §7.4, §10.4 ISSUE-J4, §10.6 ISSUE-F2, F4, F5, F6, F7, §11, §12
- [`../spec.md`](../spec.md) §1, §2, §3, §7, §8, §9, §10, §12, §16, §17, §18 item 1, §15 W3, W4 and W7
- [`../data-model.md`](../data-model.md) §11 onward (payroll and expense tables, invariants, immutability, concurrency, indexes, migration strategy, coverage)
- [`../api.md`](../api.md) "Approved fields for payroll"
- [ADR-0002](0002-module-boundaries-and-reporting.md), [ADR-0003](0003-database-connection-topology.md), [ADR-0004](0004-money-time-and-date-representation.md), [ADR-0005](0005-scheduling-invariants-overrides-and-recurrence.md) D6, [ADR-0006](0006-financial-ledger-idempotency-and-numbering.md) E3, E5, E6, E7, E8, E13, [ADR-0007](0007-authentication-sessions-recovery-and-delegation.md) D4, D10, [ADR-0015](0015-server-side-i18n.md)
- GitHub issue #104 (this decision gate, items E1–E18); #105–#117 and #423 (the M7 issues that implement it)
