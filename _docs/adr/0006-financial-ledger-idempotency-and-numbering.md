# ADR-0006: Financial ledger, idempotency and numbering

## Status

Accepted

## Date

2026-10-27

## Sign-off

Owner: JaimeSolisS. Decision items E1–E13 and E15 (ADR-0006), or E14 (ADR-0015), taken as recommended (option (a) for each), owner-delegated, on 2026-10-27 (GitHub issue #86, M6-01). No amendments. The orchestrator recorded the decision on the issue as an "owner-delegated recommended decision" (owner /goal 2026-10-27: "make recommended decisions"). E14 is recorded in [ADR-0015](0015-server-side-i18n.md).

## Context

Resolves architecture §10 **ISSUE-F1** (when lesson charges are posted), **ISSUE-F2** (billed vs actual duration), **ISSUE-F3** (ledger owner and balance derivation), **ISSUE-F4** (API idempotency keys), **ISSUE-F5** (receipt and payslip numbering), **ISSUE-F6** (accrual-basis revenue recognition), the remaining `payment.record` and `payroll.*` parts of **ISSUE-F7**, and **ISSUE-J4** (idempotency of generated financial records) in [`../architecture.md`](../architecture.md) §10.4 and §10.6. Records decision items **E1–E13 and E15** of issue #86. F1, F2, F6 and F7 are product decisions (architecture §12). The schema that implements this ADR is in [`../data-model.md`](../data-model.md).

- Spec [`../spec.md`](../spec.md) §1: MXN only; no online payments, staff record payments made externally; in-app notifications only; no CFDI, only branded non-tax receipts.
- Spec §2: Finance Staff handle charges, payments and payroll preparation; parents see consolidated family billing; sensitive actions need reauthentication.
- Spec §3: "Rescheduling normally preserves an existing charge; cancellations or billable duration changes create adjustment entries"; cancellations follow configurable notice, no-show and makeup policies; each cohort has its own pricing.
- Spec §6: catalog pricing by subject, level, duration and class type; group, course, package and tuition pricing; packages with expiry and no automatic sharing; discounts and custom pricing; effective-dated prices; charges from lessons, packages, enrollment and tuition; family-consolidated billing; "Admins record cash/transfer/other external payments"; due dates with family overrides; receipts; withdrawals; corrections that preserve source transactions; transactional and idempotent financial mutations.
- Spec §8: cash-basis and accrual-basis reporting. Spec §9: audit trail, corrections via adjustments and reversals, business settings (payment methods, receipt templates). Spec §10: PostgreSQL transactions and idempotency keys; Procrastinate; no Redis or Celery. Spec §16: decimal-safe money, local dates, immutable posted financial activity, database-enforced invariants, indexed billing access paths.
- Spec §18 item 1 leaves "finance ledger conventions" open. This ADR resolves that part, together with `_docs/data-model.md` for table names and cardinalities.
- Spec §15 W4 (billing, payments and family accounting) and W7 (background jobs: W7.2 periodic billing without duplicates, W7.4 tests for retries and concurrency).
- **Spec readings, without changing the spec:**
  - W4.4 "Outstanding balances equal posted charges/adjustments less valid allocations and refunds": *refunds* means the credit-side corrections that reduce what is owed (negative adjustments). A cash refund reduces account credit, not the outstanding amount (E5).
  - Issue #96 "method (from settings)": before M8-04's settings page, "settings" is the billing-owned `payment_methods` reference table (E12).
- **Builds on:**
  - [ADR-0002](0002-module-boundaries-and-reporting.md): the `billing` module, cross-module calls through `service.py`, multi-module operations in `app/workflows/` in one Unit of Work, and reporting in `reporting`.
  - [ADR-0003](0003-database-connection-topology.md): Procrastinate, after-commit enqueue, sweepers, `queueing_lock`.
  - [ADR-0004](0004-money-time-and-date-representation.md): `NUMERIC(14,2)`, half-up per line, `DATE` for calendar values, the center time zone.
  - [ADR-0005](0005-scheduling-invariants-overrides-and-recurrence.md) D6: a `skipped_closure` occurrence is never billable, and M6-14 reverses an already-posted charge.
  - [ADR-0007](0007-authentication-sessions-recovery-and-delegation.md): D4 (the delegable list, which includes `finance.refund.approve` and `payroll.finalize`), D10 / ISSUE-S5 (reauthentication for refunds and reversals, financial corrections and payment-method settings) and D17 (billing visibility follows the family billing account).
- **Code it builds on** (merged): families (#172: `families`, `family_students`, `family_guardians`), `class_sessions.scheduled_minutes` and `actual_minutes` (#47), the M4 billing seams (`apply_confirmation_charge` #60, `on_session_cancelled` #55, `on_session_rescheduled` and `get_change_request_outcome` #62, `apply_enrollment_change` and `list_group_enrollment_periods` #56, `apply_cohort_enrollment_change` and `list_cohort_enrollment_periods` #66), `on_closeout_approved` (#78) and the append-only trigger of migration 0004.

## Decision

### Posting timing (E1 (a), ISSUE-F1, product)

- **Per-session charges** (individual sessions, and group sessions priced per session) are posted for each `(class_session_id, student_id)` whose session is `confirmed` or `completed`.
  - A periodic **sweeper** job, `billing:post_session_charges` (every 15 minutes, `queueing_lock`), finds participants without a charge and posts them with `INSERT … ON CONFLICT DO NOTHING` on the natural key.
  - The places that confirm a session (offer accept, series-offer materialization, request confirmation) may also enqueue it **after commit** ([ADR-0003](0003-database-connection-topology.md)).
  - Nothing posts inside the scheduling transaction, so a billing failure never rolls back a schedule change.
  - Consequence: the `apply_confirmation_charge` seam in `app/workflows/confirm_session_request.py` (#60) stays a no-op, because a confirmed request creates an `offered` session, not a `confirmed` one. #91 removes it or documents it.
- **Price:** the price in effect on the session's **local service date** (center zone). Amounts are snapshotted on the charge (W4.2).
- **A rescheduled session keeps its charge** (spec §3). The charge's `service_date` stays the original one; reporting uses the session's current date (E7).
- **Group and course enrollments** are charged at enrollment, in the M4 hooks' Unit of Work (`apply_enrollment_change`, `apply_cohort_enrollment_change`), plus a sweeper over `list_group_enrollment_periods` and `list_cohort_enrollment_periods`.
- **Tuition plans:** one charge per plan per period, from a daily job on the natural key `(tuition_plan_id, period_start)`. Periods follow the center's local dates.
- **Package purchases** post a charge at purchase (a staff POST with an idempotency key). Package-covered sessions follow E10.
- **Billing start floor:** the billing settings row holds `billing_starts_on DATE NULL`. While it is `NULL` the sweepers post nothing; afterwards they post only for service dates on or after it.
- **Not billable:** sessions with no billing account (E4) or no price in effect. The sweeper skips them and retries them on later runs. A staff read lists these **unbilled items** with the reason `no_billing_account` or `no_price` (#91). Nothing is posted at zero.
- **Never billable:** `skipped_closure` ([ADR-0005](0005-scheduling-invariants-overrides-and-recurrence.md) D6), and `unassigned`, `offered`, `conflict` and `cancelled` sessions that were never confirmed.

### Billed duration (E2 (a), ISSUE-F2, product)

- Families are billed on **`class_sessions.scheduled_minutes`**. `actual_minutes` (closeout) is for tutor pay only (M7) and never changes a family charge.
- A staff edit that changes `scheduled_minutes` of a session that already has charges posts one **delta adjustment** per charged participant (#99). Every such edit already needs `schedule.manage`; there is no separate approval.
- **The delta** is the catalog price for the new duration (same service date, same discount snapshot) minus the charge's current net amount. If the new duration has no catalog price, the delta is pro rata by minutes from the original amount, rounded half-up.
- **Idempotent per change:** the adjustment's `source_key` includes the session's `version` after the edit.

### Cancellation outcomes (E3 (a), spec §3, W4.5, product)

- A billing-owned rule table, `cancellation_billing_rules`, effective-dated (`effective_from TIMESTAMPTZ UNIQUE`) and append-only, stores one outcome per classification (`excused`, `on_time`, `late`, `no_show`): `credit` (the full charge is credited) or `charge` (the charge stands).
- **Seeded defaults:** `excused` credit, `on_time` credit, `late` charge, `no_show` charge.
- **Makeup allowed:** when the scheduling policy outcome allows a makeup, the outcome is always `credit`, and the makeup is billed as an ordinary session. No session-to-makeup link is needed.
- **Which rule applies:** the rule in effect at the change's classification instant. For a #62 request that is the request's `created_at` and its stored classification, read through `get_change_request_outcome`. For a center cancellation (#55, `on_session_cancelled`) it is the cancellation instant, classified `excused`.
- **No charge yet:** `credit` does nothing, and `charge` posts the session charge on its natural key.
- **Package-covered students:** `charge` debits the session's units (E10); `credit` debits nothing.
- **Closures:** a closure that turns a charged occurrence into `skipped_closure` always credits it ([ADR-0005](0005-scheduling-invariants-overrides-and-recurrence.md) D6).
- Built by #99, managed with `billing.manage` (E8).

### Account ownership (E4 (a), ISSUE-F3)

- **One billing account per family:** `billing_accounts.family_id UNIQUE`, created on first use with `INSERT … ON CONFLICT DO NOTHING`.
- **No family, no billing:** a student who is not in a family isn't billed. The unbilled list (E1) shows them, and staff add the student to a family through the existing #172 pages (an adult student who pays for themselves gets a one-student family). When the student joins a family, the sweepers pick up their items automatically.
- **Charges keep their account,** even if the student later moves to another family.
- **Family access:** the guardians in `family_guardians` for the account's family see the consolidated account, with a per-child filter (W4.6, #102).
- **Students and tutors** see no billing in M6, whatever the student's age.

### Ledger and balance (E5 (a), ISSUE-F3, W4.4)

- **Typed, append-only tables** in the `billing` module, plus a read-only view (`billing_ledger_entries`) for the history:
  - `charges` (≥ 0, always for one student);
  - `charge_adjustments` (signed, never 0, always against one charge);
  - `payments` and `payment_reversals` (at most one reversal per payment);
  - `payment_allocations` and `allocation_releases` (a release frees a whole allocation);
  - `refunds` (money paid back out of a payment's unallocated remainder).
- **No free-floating credit row:**
  - **Account credit** is a payment's unallocated remainder.
  - **A credit** (cancellation, withdrawal, correction) is a negative adjustment against the charge it corrects. If an adjustment pushes a charge below its valid allocations, the newest allocations are released first, in the same transaction, and a smaller allocation is re-inserted when the release frees more than needed. The released money becomes account credit.
- **Derived, never stored:**
  - `net(charge) = amount + Σ adjustments`;
  - `open(charge) = net − Σ valid allocations`;
  - **outstanding** = Σ open;
  - **account credit** = Σ over payments that aren't reversed of (`amount − valid allocations − refunds`);
  - **net balance** = outstanding − account credit.

  Charge status is derived from these: `pending`, `partially_paid`, `paid`, `overdue` (open > 0 and the due date before today in the center zone) and `void` (net = 0 with no allocation). There is no stored balance. A verified snapshot is allowed only for reporting.
- **W4.4 reading:** *refunds* in "posted charges/adjustments less valid allocations and refunds" are the credit-side corrections (negative adjustments). A cash refund reduces account credit, not the outstanding amount.
- **Immutability:** every ledger table is append-only through `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` triggers raising SQLSTATE **42501**, the `audit_events` pattern of migration 0004. Privileges can't do it, because the local, test and CI role is a superuser; #165 (M10-10) adds the least-privilege role later. A migration may disable a trigger only in its own downgrade, for that one statement.
- **Serialization:** every financial mutation takes the billing account row `FOR UPDATE` as its **last** lock, after any scheduling or courses lock of the calling workflow; the receipt counter comes after it (E13). The cross-row invariants (net ≥ 0; allocations ≤ net; allocations + refunds ≤ payment) are checked under that lock. Same-account and same-student rules are database-enforced through composite foreign keys.
- **Money and currency:** `NUMERIC(14,2)` with half-up rounding per line ([ADR-0004](0004-money-time-and-date-representation.md)). No currency columns (MXN only), no tax computation and no IVA lines. Prices are final amounts, and receipts are not CFDI (spec §1).

### API idempotency keys (E6 (a), ISSUE-F4, ISSUE-J4)

- **Header:** `Idempotency-Key` is required on every endpoint that posts a ledger row: manual charges and adjustments, package purchases, payments, allocations, reversals, refunds, withdrawal confirmations, and later payroll approval and payment (M7).
- **Format:** 16–128 characters of `[A-Za-z0-9_-]`. The web client sends `crypto.randomUUID()`, one per form submission, and reuses it on retry.
- **Errors:** a missing key is `400 idempotency_key_required`; a malformed one is `400 idempotency_key_invalid`.
- **Storage:** core table `idempotency_keys` with `user_id`, `endpoint` (method + route template), `key`, `request_hash` (SHA-256 over method, concrete path and canonical JSON body), `response_status`, `response_body JSONB`, `created_at`, `expires_at`, and `UNIQUE (user_id, endpoint, key)`.
- **Same transaction:** the row is inserted in the business transaction, before any lock. A concurrent duplicate blocks on the unique index until the first commits, then reads the stored response. A failed request rolls back its key, so a retry runs again. Only `2xx` responses are stored.
- **Replay:** the same key and hash returns the stored status and body with `Idempotent-Replayed: true`. The same key with a different hash is `422 idempotency_key_reused`.
- **Expiry:** 7 days, purged by an hourly `idempotency:purge_keys` job, like `ratelimit:purge_counters`.
- **Jobs** never use this table. They use the natural keys of E1, E5, E10 and E11 with `ON CONFLICT DO NOTHING` (J4).

### Accrual-basis revenue recognition (E7 (a), ISSUE-F6, product)

A reporting computation for #117 (M7-14). Nothing extra is stored beyond the dates and links of E5 and E9.

- **Per-session charges:** recognized on the session's **current local date**, net of their adjustments. A credited cancellation nets to 0.
- **Packages:** the purchase is deferred revenue. Each consumption recognizes `net price × units consumed / units granted`, rounded half-up, with the last consumption or the expiry taking the remainder. **Expired unused credit is recognized on the expiry date** (breakage). A refund of unused credit reduces the deferred amount.
- **Course fees:** pro rata over the cohort's not-cancelled and not-skipped sessions, by session date. The last session takes the rounding remainder.
- **Tuition periods and flat group fees:** recognized in full on `period_start` (or the enrollment date).
- **Cash basis** uses the payment's `received_on`, net of reversals and refunds.

### Permissions (E8 (a), ISSUE-F7 `payment.record` and `payroll.*` parts, product)

Permissions, not roles. None of the new ones is delegable, and each is seeded by the first issue that uses it.

| Permission | Holders | Scope | First used by |
|---|---|---|---|
| `billing.view` | Owner, Administrator, Finance Staff | read any family's account, ledger, balances, receipts, unbilled items | #90 |
| `billing.manage` | Owner, Administrator, Finance Staff | price catalog, package products, discounts and custom prices, tuition plans, manual charges and adjustments, due-date changes, cancellation rules, billing settings | #89 |
| `payment.record` | Owner, Administrator, Finance Staff | record and allocate payments | #96 |
| `finance.refund.approve` | Owner; delegable to Administrators (already in code, ADR-0007) | payment reversals and refunds, with reauthentication (`finance.refund_or_reversal`) | #100 |
| `payroll.prepare` / `payroll.finalize` | Finance Staff / Owner, delegable (already in code) | recorded here for M7, as ISSUE-F7 proposed | M7 |

- **Reauthentication** ([ADR-0007](0007-authentication-sessions-recovery-and-delegation.md) D10): a negative manual adjustment against a posted charge is a financial correction (`finance.correction`). It needs `billing.manage` plus reauthentication. Positive manual charges don't.
- **Withdrawals (#101):** `schedule.manage` (it withdraws the enrollment), plus `finance.refund.approve` for a refund outcome or `billing.manage` for an account-credit outcome.
- **Academic Coordinators and tutors** get no billing permission.
- **Families** get access through record policy only (E4).

### Price catalog (E9 (a), spec §6 pricing)

- **Catalog items:** `service_prices`, keyed by `(class_type, pricing_model, subject_id, education_level_id NULL = any level, duration_minutes NULL = any duration)`, unique with `NULLS NOT DISTINCT`.
  - Optional **scope**: `cohort_id` (spec §3, "each cohort has its own … pricing") or `group_id`. It replaces the earlier note that a "cohort price column" would be added (#65 E6).
  - `pricing_model` is `per_session` (individual and group sessions), `full_course` (cohort fee, or a group flat fee per enrollment), `per_period` (group per-student pricing, through a tuition plan linked to the group enrollment) or `package` (a package product, E10).
- **Versions:** `price_versions(service_price_id, effective_from DATE, amount NUMERIC(14,2) NULL)`.
  - Each version runs until the next one's `effective_from`, so ranges can't overlap: `UNIQUE (service_price_id, effective_from)` is the constraint #89 asks for.
  - `amount NULL` means "not offered from this date".
  - A version referenced by any charge or package can't be updated or deleted (42501 trigger); a change is a new version.
- **Lookup precedence,** for the price in effect on the service date:
  1. a custom student or family price (#95);
  2. the cohort- or group-scoped price;
  3. the catalog price with the exact level;
  4. the catalog price with any level.
- **Snapshot:** the student's education level at posting time is snapshotted on the charge.
- **Discounts (#95):** applied after the base price. The applied ones are snapshotted as `charge_discounts` rows, with `charges.list_amount − discount_amount = amount`. #95 documents the stacking rules.
- **Late course enrollment (#92):** prorated by the remaining sessions, `fee × remaining / session_count`, rounded half-up. `remaining` counts the cohort sessions that haven't started at enrollment and aren't cancelled or skipped. A late group flat fee works the same way.
- **Removing a student:**
  - **Per-session charges:** a plain group or cohort withdrawal (the #56 and #66 routes) credits the removed student's charged future sessions automatically (#92, adjustment kind `enrollment_removal`).
  - **Flat and course fees:** changed only through the #101 withdrawal flow, never automatically.

### Prepaid packages (E10 (a), spec §6 packages, ISSUE-F3)

- **Products:** a package product (catalog, `billing.manage`) has a `unit` (`sessions` or `minutes`), a quantity, a price (its own `service_prices` row, `pricing_model = package`), a validity in days and optionally a subject. It covers **individual sessions only**.
- **Packages:** a purchased package belongs to one billing account **and one student**. It is never shared between siblings or families (spec §6). Its purchase charge has kind `package_purchase`. `expires_on` = the purchase local date + validity.
- **Credit:** units live in `package_credit_transactions` (`grant`, `consume`, `expire`, `refund`, `reversal`), as signed integers in sessions or minutes. A minutes package consumes `scheduled_minutes` (E2).
- **Coverage:** when the session sweeper (E1) finds an eligible, unexpired package with credit for the student and subject, it posts **no charge**. At closeout approval revision 1 (`on_closeout_approved`, #78), the session's units are consumed **first-expiring-first**, possibly across packages, with the natural key `(class_session_id, student_id)`.
- **Not enough credit at closeout:** if the eligible packages don't cover the whole session, nothing is consumed and the ordinary session charge is posted on its natural key. Packages bought after a session was charged don't convert that charge.
- **Expiry:** a daily job writes one `expire` transaction per expired package with remaining units (natural key `package_id`). It is a ledger event, and its value feeds E7 breakage.

### Due dates (E11 (a), spec §6)

- **Set at posting:** `charges.due_date DATE NOT NULL`, never updated.
- **Rules:** session → its service date; enrollment → the enrollment's local date, or the cohort's first session if that is later; tuition period → `period_start + plan.due_offset_days`; package → the purchase date; manual → entered by staff.
- **Family override:** `billing_accounts.due_offset_days` (0–60, default 0) is added to the rule at posting.
- **Changing a posted charge's due date:** an append-only `charge_due_date_changes` row (new date, reason, actor). The effective due date is the latest one.
- **Overdue** is computed against today in the center zone ([ADR-0004](0004-money-time-and-date-representation.md)).
- **Reminders (#97):** in-app only, `{type, params}` notifications, with natural key `(charge_id, stage)`.

### Payment methods (E12 (a), spec §6)

- A billing-owned reference table `payment_methods (code, is_active)`, seeded with `cash`, `transfer` and `other`, with labels from the client catalogs. #96 reads it.
- M8-04 (#121) adds editing, behind the reauthentication ADR-0007 D10 already lists ("business-settings changes to payment methods").
- A method is never deleted once used.

### Receipt numbering and contents (E13 (a), ISSUE-F5)

- **Counter:** a core table `number_series (code TEXT, year SMALLINT, last_value INTEGER, PRIMARY KEY (code, year))`. The row is locked `FOR UPDATE` inside the posting transaction, after the account lock, so numbers are gap-free. The format is `R-2026-000123`; the year is the **center-local year of the issue instant**. Series `P` (payslips) is reserved for M7.
- **One receipt per payment,** issued automatically in the payment's transaction (#96 calls the #98 service once it exists).
- **The receipt row is a frozen snapshot:** `receipts (number UNIQUE, payment_id UNIQUE, billing_account_id, issued_at, language, template_version, snapshot JSONB)`. The snapshot holds the center name, address and contact from business settings; the family name and payer; the amount and method; and the allocations as charge ids, amounts and structured labels. Later changes to settings, templates (M8-04) or allocations don't change it.
- **Reversed payments:** the receipt keeps its number. The PDF shows a "reversed" banner from the reversal row; the receipt row isn't touched.
- **Payments recorded before #98 merges** get no receipt. #98 doesn't backfill them (DEV data only).

### Branding without uploads (E15 (a))

- The file deferral (owner decision 2026-10-21, #68–#75) doesn't block #98: receipts are generated, not uploaded, and its #72 dependency is already removed.
- "Branded" in M6 means the center name, address and contact from `business_settings`, plus a default Classora mark and colors bundled with the template in code.
- A center logo upload belongs to the deferred file features and M8-04 "brand". No upload, bucket or stored file is added.

## Consequences

- **Implementing issues** (each records its part; table detail in [`../data-model.md`](../data-model.md)):
  - **#87 (M6-02):** the `Money` value object (E5 money rules, ADR-0004).
  - **#88 (M6-03):** API idempotency: `idempotency_keys`, the header dependency, the purge job, the web helper (E6).
  - **#89 (M6-04):** the price catalog, versions, the guarded trigger and the lookup; seeds `billing.manage` (E8, E9).
  - **#90 (M6-05):** billing accounts, the ledger tables, their triggers, the balances and statuses, the ledger view; seeds `billing.view` (E4, E5, E8).
  - **#91 (M6-06):** the session sweeper, `billing_settings`, the unbilled list, the fate of `apply_confirmation_charge` (E1, E2, E10 skip).
  - **#92 (M6-07):** enrollment charges in the M4 hooks plus sweepers, late proration, `enrollment_removal` (E1, E9).
  - **#93 (M6-08):** tuition plans and the daily period job (E1, E9, E11).
  - **#94 (M6-09):** package products, packages, credit transactions, consumption at closeout and expiry (E10).
  - **#95 (M6-10):** discounts and custom prices, the discount snapshot (E9).
  - **#96 (M6-11):** payments, allocations, `payment_methods`; seeds `payment.record` (E5, E6, E8, E12).
  - **#97 (M6-12):** due-date changes, overdue tracking, in-app reminders (E11).
  - **#98 (M6-13):** receipts, `number_series`, and the PDF dependencies of [ADR-0015](0015-server-side-i18n.md) (E13, E15).
  - **#99 (M6-14):** cancellation rules and the reconciliation seams: cancellation, reschedule, closeout, duration change and closure (E2, E3).
  - **#100 (M6-15):** payment reversals and refunds, with `finance.refund.approve` and reauthentication (E5, E8).
  - **#101 (M6-16):** withdrawals (E8, E9, E10).
  - **#102 (M6-17):** the family portal through `family_guardians` (E4).
  - **#103 (M6-18):** the financial invariant and concurrency suite (I1–I11 of `data-model.md`).
  - **#117 (M7-14):** cash and accrual reports (E7).
  - **#121 (M8-04):** payment-method editing and receipt-template editing (E12, E13).
- The coverage table "M6 issue → decisions and tables it relies on" for #87–#103 is [`../data-model.md` §10](../data-model.md#10-m6-issue-coverage).
- **This ADR adds nothing** to the dependency files, the permission catalog or the migrations. Each implementing issue adds its own tables, seeds and permissions.
- Apart from package-covered sessions (E10: units consumed, or the ordinary charge posted, at closeout approval), family charges never depend on closeout or on tutors' reports: no-shows and sessions never closed out are still charged (E1, E2).
- Billing is off until an administrator sets `billing_starts_on`, so going live never charges DEV history or demo data.
- A student outside a family is visible as unbilled rather than silently skipped (E4).
- Every money row is explainable from immutable history (spec §16); corrections are new rows, never edits (W4.5).
- The single last lock on the account row serializes financial work per family without deadlocks against scheduling locks.
- ISSUE-F1–F7 and J4 are closed in architecture §10; ADR-0007's open point on finance and payroll permissions is closed here.

## Alternatives considered

- **E1 (b), charges posted on the session's local date.** Rejected: spec §3 "rescheduling normally preserves an existing charge" assumes the charge exists before the session.
- **E1 (c), charges posted at closeout approval.** Rejected: no charge at all for no-shows or sessions never closed out, and billing becomes coupled to M5 approvals.
- **E2 (b), bill approved actual minutes.** Rejected: families would see their charge change after closeout, and spec §3 ties adjustments to staff changes of billable duration.
- **E3 (b), a credit percentage of 0–100 per classification.** Rejected: partial credits don't map onto session-unit packages and make reconciliation harder to test; a later version can add it.
- **E3 (c), money outcomes on `scheduling_policies`.** Rejected: couples administration to billing, and a policy edit would become a financial change.
- **E4 (b), create a one-student family automatically on first billing.** Rejected: hidden data creation in `families` from a billing job, bypassing the #172 guardian rules.
- **E4 (c), student logins see their own charges.** Rejected for M6: needs its own record policy, and spec W4.6 names only families.
- **E5 (b), one `ledger_entries` journal table with signed amounts and a `kind`.** Rejected: per-source foreign keys and per-kind checks become nullable-column soup, and natural keys get harder.
- **E5 (c), a running balance stored on the account.** Rejected: ISSUE-F3 forbids it, and spec §16 needs the history to stay explainable.
- **E6 (b), a separate "in progress" key row committed before the business transaction.** Rejected: only needed when work spans transactions, which it never does here.
- **E6 (c), keys kept for 24 hours.** Rejected: too short for a staff member who retries the next morning.
- **E7 (b), recognize all revenue when posted.** Rejected: overstates revenue for prepaid packages and courses, against spec §8's accrual basis.
- **E8 (b), `payment.record` for Finance Staff only (W4.3).** Rejected: spec §6 names admins too, and a small center's administrator often takes cash.
- **E8 (c), one `billing.manage` for everything, payments included.** Rejected: pricing and taking payments are different duties, and ISSUE-F7 asks for separation.
- **E9 (b), group per-student pricing as a one-time fee per enrolled student.** Rejected: odd for an ongoing group with no end date, and already covered by `full_course` for groups.
- **E9 (c), price versions with explicit `[from, to)` ranges and an `EXCLUDE` constraint.** Rejected: closing a used version's range would be an UPDATE of a referenced row.
- **E10 (b), packages shared by the whole family.** Rejected: spec §6 says no automatic sharing, and the charge has to name one student.
- **E10 (c), consume what is left and charge the rest pro rata.** Rejected: fiddly money from partial units, for an edge case staff can handle with a manual charge.
- **E11 (b), edit `charges.due_date` in place, with audit.** Rejected: posted rows are immutable (E5).
- **E12 (b), a fixed method enum on `payments`.** Rejected: M8-04 would need a migration to make methods configurable.
- **E13 (b), a PostgreSQL sequence per year.** Rejected: sequences leave gaps on rollback, against ISSUE-F5.
- **E13 (c), receipts issued on demand at first download.** Rejected: numbers wouldn't follow payment order, and a payment could exist without a receipt.
- **E15 (b), block #98 until logo uploads exist.** Rejected: defers receipts to the next version, against spec §6.

## Implementation notes (M6-03)

- **#88, 2026-10-27, owner-delegated (E2):** a key whose `expires_at` has passed but that the hourly purge hasn't deleted yet counts as **absent**. The claim is one `INSERT … ON CONFLICT (user_id, endpoint, key) DO UPDATE … WHERE expires_at <= now RETURNING id`, which replaces an expired row atomically, so retention is exactly 7 days whatever the purge timing. The E6 decision above is otherwise unchanged.

## References

- [`../architecture.md`](../architecture.md) §4.2, §4.4, §5, §6, §10.4 ISSUE-J4, §10.6 ISSUE-F1–F7, §11, §12
- [`../spec.md`](../spec.md) §1, §2, §3, §6, §8, §9, §10, §16, §18 item 1, §15 W4 and W7
- [`../data-model.md`](../data-model.md) (billing tables, invariants, indexes, migration strategy, coverage)
- [ADR-0002](0002-module-boundaries-and-reporting.md), [ADR-0003](0003-database-connection-topology.md), [ADR-0004](0004-money-time-and-date-representation.md), [ADR-0005](0005-scheduling-invariants-overrides-and-recurrence.md) D6, [ADR-0007](0007-authentication-sessions-recovery-and-delegation.md) D4, D10, D17, [ADR-0015](0015-server-side-i18n.md)
- GitHub issue #86 (this decision gate, items E1–E15); #87–#103 (M6 issues that implement it); #117, #121, #165
