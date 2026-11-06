# Classora — Data model

**Status:** Accepted for the billing domain (GitHub issue #86, M6-01, 2026-10-27). The rules behind it are in [ADR-0006](adr/0006-financial-ledger-idempotency-and-numbering.md) (ledger, idempotency, numbering) and [ADR-0004](adr/0004-money-time-and-date-representation.md) (money, time and dates). Decision items in §1–§10 are cited as **E1–E15** of issue #86.

The payroll and expense domains (§11 onward, GitHub issue #104, M7-01, 2026-10-31) follow [ADR-0017](adr/0017-payroll-and-expenses.md), which is **Proposed**: orchestrator-recommended, pending owner review. Decision items there are cited as **E1–E18** of issue #104.

## 1. Purpose and scope

Spec [`spec.md`](spec.md) §17 makes this file the home of the final entities, foreign keys, invariants, indexes and migration strategy, and requires a **schema review before implementing financial workflows**. This version is that review for the billing domain and, from §11, for the payroll and expense domains.

- **Covered:** the **billing** domain (module `billing`, M6) and the two **core** tables it needs (`idempotency_keys`, `number_series`); the **payroll** and **expense** domains (modules `payroll` and `finance`, M7, §11–§19).
- **Not covered:** the tables of earlier domains (identity, families, staff, catalog, scheduling, courses, academics, administration). They are documented in their migrations (`apps/api/migrations/versions/`), their model docstrings and their ADRs ([ADR-0005](adr/0005-scheduling-invariants-overrides-and-recurrence.md), [ADR-0007](adr/0007-authentication-sessions-recovery-and-delegation.md)). They are named here only where billing references them.
- **Payroll and expenses** (M7) are designed in §11–§19 and built by #105–#117, #423 and #441. No M7 table exists until its issue merges.
- **Changes:** an implementing issue may rename a table or column, or adjust a column listed here, only by updating this file in the same pull request. A change to a rule of ADR-0006 or ADR-0017 needs an ADR amendment.

## 2. Conventions

- **Primary keys:** `id UUID`, generated as uuidv7 (`uuid.uuid7()` in Python, `uuidv7()` as the server default), like every other table. The exceptions are `payment_methods` (key `code`) and `number_series` (key `(code, year)`).
- **Standard columns:** every model has `created_at` and `updated_at` (`TIMESTAMPTZ NOT NULL`, set by the database; `app/core/models.py`). On an append-only table `updated_at` never moves.
- **Money:** `NUMERIC(14,2)` in MXN; rates and percentages `NUMERIC(14,4)`; Python `Decimal` through the `Money` type of #87; half-up rounding per line ([ADR-0004](adr/0004-money-time-and-date-representation.md)). There is **no currency column** (MXN only, spec §1) and no tax column.
- **Money mapping:** money columns use `app.core.money.MoneyType` and `Money`; sums use `money_sum` (#87).
- **Dates and instants:** `DATE` for calendar values (service dates, due dates, effective dates, periods, `received_on`, `expires_on`); `TIMESTAMPTZ` for instants (`posted_at`, `issued_at`, `effective_from` of cancellation rules). Local dates are computed in the center time zone (`business_settings.time_zone`).
- **Foreign keys:** every billing foreign key is `ON DELETE RESTRICT`. Billing rows are never deleted, so nothing cascades.
- **Audit:** every billing table except `idempotency_keys` and `number_series` is `Audited`. The audit event is inserted in the same transaction as the row (issue #22, `app/core/audit.py`); for mutable configuration rows, updates are audited too.
- **Concurrency token:** `version` (optimistic concurrency, `VersionedMixin`) exists only on **mutable configuration** rows. Append-only rows have none.
- **Staff text:** the only stored free text is staff-entered (`description`, `reason`, `note`, `reference`, product names). Everything a document or screen renders is otherwise stored as structured fields (kind, subject, minutes, period) and translated at display time ([ADR-0015](adr/0015-server-side-i18n.md)).
- **Immutability classes** used below:
  - **append-only:** no UPDATE, DELETE or TRUNCATE ever (§5.1);
  - **guarded:** immutable once another row references it (§5.2); `cancellation_billing_rules` is guarded by time instead: never updated, deletable only while upcoming (#99);
  - **mutable config / mutable reference:** edited in place with `version` and audit, never deleted once referenced (the `RESTRICT` foreign keys make that hold);
  - **transient:** deleted by a purge job;
  - **counter:** one row per series, updated under a row lock.

## 3. Billing tables

Overview. "Creating issue" is the issue whose migration creates the table. Whether `payments`, `refunds`, `payment_reversals` and `allocation_releases` are created by #90 or by #96/#100 may be adjusted by those issues' PMs, as long as this file is updated to say which.

| Table | Module | Creating issue | Class |
|---|---|---|---|
| `billing_accounts` | billing | #90 | mutable config |
| `billing_settings` | billing | #91 | mutable config (singleton) |
| `payment_methods` | billing | #90 (table, seed), #96 (first use) | mutable reference |
| `service_prices` | billing | #89 | mutable config |
| `price_versions` | billing | #89 | guarded: immutable once referenced |
| `custom_prices` | billing | #95 | mutable config |
| `discounts` | billing | #95 | mutable config |
| `discount_assignments` | billing | #95 | mutable config |
| `charge_discounts` | billing | #95 | append-only |
| `charges` | billing | #90 (sources added by #91–#94) | append-only |
| `charge_adjustments` | billing | #90 | append-only |
| `charge_due_date_changes` | billing | #97 | append-only |
| `charge_reminders` | billing | #97 | append-only |
| `payments` | billing | #90 (table), #96 (endpoints) | append-only |
| `payment_reversals` | billing | #90 (table), #100 (endpoints) | append-only |
| `payment_allocations` | billing | #90 | append-only |
| `allocation_releases` | billing | #90 | append-only |
| `refunds` | billing | #90 (table), #100 (endpoint) | append-only |
| `tuition_plans` | billing | #93 | mutable config |
| `package_products` | billing | #94 | mutable config |
| `packages` | billing | #94 | append-only |
| `package_credit_transactions` | billing | #94 | append-only |
| `receipts` | billing | #98 | append-only |
| `cancellation_billing_rules` | billing | #99 | guarded, effective-dated (an upcoming version may be deleted; §5.2) |
| `session_cancellation_decisions` | billing | #99 | append-only |
| `charge_duration_repricings` | billing | #99 | append-only |
| `withdrawals` | billing | #101 | append-only |
| `idempotency_keys` | core | #88 | transient (purged) |
| `number_series` | core | #98 | counter (row-locked UPDATE) |
| view `billing_ledger_entries` | billing | #90 | read-only view |

Referenced tables of earlier domains: `families`, `family_students`, `family_guardians`, `guardians`, `students`, `users`, `class_sessions`, `class_session_participants`, `class_groups`, `group_enrollments`, `course_cohorts`, `cohort_enrollments`, `subjects`, `education_levels`, `business_settings`, `user_preferences`, `audit_events`.

`business_settings` (#37) gains two columns in migration 0087 (#121 B1): `receipt_message_es` and `receipt_message_en`, `VARCHAR(500) NULL` (plain text; `NULL` = no message), each with CHECKs `ck_business_settings_receipt_message_<lang>_trimmed` (`= btrim(...)`) and `…_not_blank` (`~ '[^[:space:]]'`). Edited through `PATCH /api/v1/business-settings` (`settings.manage`, no reauthentication; audited as the existing `business_settings` `update`). New receipts freeze the message of their language into snapshot v2 (§3.21).

### 3.1 `billing_accounts`

One billing account per family (E4). Created on first use (`billing.service.ensure_billing_account`, #90 E4): a `SELECT` by `family_id`, and if there is none an ORM insert inside a savepoint; a violation of `uq_billing_accounts_family_id` (a concurrent first use) rolls the savepoint back, with its audit event, and the row is read again. The table is `Audited`, and the audit guard refuses ORM `INSERT … ON CONFLICT` on audited tables.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `family_id` | UUID | no | FK `families`, **UNIQUE** |
| `due_offset_days` | SMALLINT | no | default 0; `CHECK (due_offset_days BETWEEN 0 AND 60)` (E11 family override) |
| `version` | INTEGER | no | optimistic concurrency |

- **Access:** the guardians in `family_guardians` for `family_id` see the account (record policy, #102). Students and tutors see nothing in M6.
- **Lock:** every financial mutation locks this row `FOR UPDATE` as its last lock (§6).
- **Editing `due_offset_days` (#97 N6):** `PATCH /api/v1/families/{family_id}/billing-account` (`billing.manage`), body `{version | null, due_offset_days}`; `version: null` creates the account of a family that has none. The row is taken `FOR UPDATE`, then the version is checked (`409 version_conflict`); the same value writes nothing. The offset applies **only to charges posted afterwards** (`post_charge` reads it under the same lock, E11 "set at posting"); posted due dates never move.

### 3.2 `billing_settings`

A singleton (the `business_settings` pattern: a `singleton BOOLEAN` column with `CHECK (singleton)` and UNIQUE).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `singleton` | BOOLEAN | no | `CHECK (singleton)`, UNIQUE |
| `billing_starts_on` | DATE | yes | `NULL`: the sweepers post nothing; otherwise they post only for service dates on or after it (E1) |
| `version` | INTEGER | no | |

Created by #91 (migration 0062, which inserts the one row with `billing_starts_on = NULL`). Audited and versioned; a local `DATE`, so no time-zone dependency.

- **Routes (#91):** `GET /api/v1/billing/settings` (`billing.view`) and `PATCH /api/v1/billing/settings` (`billing.manage`, body `{version, billing_starts_on}`, no reauthentication). Any date from 2000-01-01 to 2099-12-31, past included, or `NULL` (billing off). Moving the date later or back to `NULL` pauses posting for the dates it excludes and never changes posted charges. Each change writes one `update` audit event with the old and new date; sending the current value writes nothing.
- **After commit**, a `PATCH` whose result is a date queues one `billing:post_session_charges` run (`app.core.jobs.defer_session_charges`) and one `billing:post_enrollment_charges` run (`defer_enrollment_charges`, #92).
- **Enrollment fees (#92 J6)** compare the floor with their due-date base (`max(enrollment local date, first counted session's local date)`), not with `service_date`.
- **Readers:** the sweepers read the floor with `billing.service.billing_starts_on(session, lock=True)` (`FOR SHARE`, after their own scheduling locks and before the account lock, §6).

The demo seed chooses its own date.

### 3.3 `payment_methods`

Billing-owned reference table (E12), created and seeded with `cash`, `transfer` and `other` by #90's migration (0061), because `payments` and `refunds` reference it; #96 is its first user. Labels come from the client catalogs. M8-04 (#121) adds editing behind reauthentication: activate and deactivate only (see below). The table has no `id`; its audit events use a UUIDv5 of `code` as `record_id` (`PaymentMethod.id`).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `code` | VARCHAR(32) | no | PK; `CHECK (code ~ '^[a-z][a-z0-9_]*$')` |
| `is_active` | BOOLEAN | no | default true |
| `version` | INTEGER | no | |

A method is never deleted once used (`payments` and `refunds` reference it with `RESTRICT`).

**Editing (#121 A, E5).** `PATCH /api/v1/billing/payment-methods/{code}` `{is_active, version}` (`settings.manage` plus reauthentication `settings.payment_methods_or_time_zone`, only when `is_active` changes) through `billing.service.set_payment_method_active`. It locks every `payment_methods` row `FOR UPDATE` in `code` order before counting the active ones, so the last active method can't be deactivated (`409 payment_method_last_active`), even by two concurrent requests. There is no create, rename or delete. A deactivated method is refused by every recording path (payments, refunds, corrections, withdrawal settlements, payroll and expense payments: `payment_method_unavailable`); recorded rows keep it and still render it. Each change is one automatic `update` audit event.

### 3.4 `service_prices`

Catalog items (E9).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `class_type` | VARCHAR(16) | no | `individual` \| `group` \| `course` (the `class_sessions.kind` values) |
| `pricing_model` | VARCHAR(16) | no | `per_session` \| `full_course` \| `per_period` \| `package` |
| `subject_id` | UUID | yes | FK `subjects`; `NULL` only for a `package` row whose product has no subject (E10) |
| `education_level_id` | UUID | yes | FK `education_levels`; `NULL` = any level |
| `duration_minutes` | SMALLINT | yes | 15 to 480, only with `per_session` (#89); `NULL` = any duration |
| `cohort_id` | UUID | yes | FK `course_cohorts`; scope (spec §3, "each cohort has its own … pricing") |
| `group_id` | UUID | yes | FK `class_groups`; scope |
| `period_frequency` | VARCHAR(16) | yes | `weekly` \| `biweekly` \| `monthly`; set **iff** `pricing_model = 'per_period'` (`ck_service_prices_period_frequency`, #93 K2) |
| `version` | INTEGER | no | |

- **Checks:** `NOT (cohort_id IS NOT NULL AND group_id IS NOT NULL)`; `cohort_id` only with `class_type = 'course'`; `group_id` only with `class_type = 'group'`; `package` only with `class_type = 'individual'` (E10). Added by #89 (E4, E5):
  - the allowed combinations: `individual` with `per_session`, `per_period` or `package`; `group` with `per_session`, `full_course` or `per_period`; `course` with `full_course`;
  - `subject_id IS NOT NULL OR pricing_model = 'package'`;
  - `duration_minutes IS NULL OR (pricing_model = 'per_session' AND duration_minutes BETWEEN 15 AND 480)`;
  - a scoped row (cohort or group) has `education_level_id IS NULL AND duration_minutes IS NULL`.
- **API scope (#89 E5):** the #89 routes create, list and read `per_session` and `full_course` items and, since #93 B1, `per_period` items (with their `period_frequency`). `package` rows are created with their product (#94).
- **Pricing models:** `per_session` (individual sessions, and group sessions priced per session); `full_course` (a cohort fee, or a group flat fee per enrollment); `per_period` (group per-student pricing, through a tuition plan linked to the group enrollment, and tuition plans in general); `package` (the price of a package product).
- **Catalog key:** `UNIQUE NULLS NOT DISTINCT (class_type, pricing_model, subject_id, education_level_id, duration_minutes, cohort_id, group_id, period_frequency) WHERE pricing_model <> 'package'` (`period_frequency` added by #93 K2: a weekly and a monthly price of the same subject, level and scope are two items). A `package` row is identified by its product instead (`package_products.service_price_id` is UNIQUE), so two products for the same subject can coexist.
- The key columns don't change once a `price_versions` row exists; a different key is a new item.

### 3.5 `price_versions`

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `service_price_id` | UUID | no | FK `service_prices` |
| `effective_from` | DATE | no | |
| `amount` | NUMERIC(14,2) | yes | `>= 0`; `NULL` = "not offered from this date" |

- **Ranges:** each version runs until the next one's `effective_from`. `UNIQUE (service_price_id, effective_from)` makes overlapping ranges impossible (the #89 constraint).
- **Lookup:** the version in effect on a local date `d` is the one with the greatest `effective_from <= d`. Precedence (E9): (1) custom student or family price (#95); (2) the cohort- or group-scoped price; (3) the catalog price with the exact level; (4) the catalog price with any level. A `NULL` amount means "not offered": with no price in effect the item is unbilled (`no_price`, E1).
- **Lookup ranks (#89 E4; `billing.service.lookup_price`, below step 1):** among the items with the service's class type, pricing model and subject, rank 0 is the item scoped to its cohort or group; 1 exact level and exact duration; 2 exact level, any duration; 3 any level, exact duration; 4 any level, any duration (a student without a level, or a query without a duration, matches only the "any" items). The most specific item **with a version in effect on `d`** decides; an item whose versions all start later is skipped. If the deciding version's amount is `NULL`, the result is no price, and lower ranks are **not** consulted.
- **Effective dates (#89 E3):** an item's first version may have any `effective_from`, past included. Every later version starts on or after the center's local today and after every existing version of the item. Versions are never edited; only an upcoming version (`effective_from` after today) may be deleted. A lookup for a past date therefore never changes once an item has a price.
- **Guarded:** a version referenced by any `charges` or `packages` row can't be updated or deleted (§5.2). A change is a new version.

### 3.6 `custom_prices` (#95)

A custom student or family price, step 1 of the lookup (#95 H6, H7).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `service_price_id` | UUID | no | FK `service_prices` (the item it overrides) |
| `student_id` | UUID | yes | FK `students` |
| `family_id` | UUID | yes | FK `families` |
| `effective_from` | DATE | no | on or after the center's local today when created (no backdating) |
| `expires_on` | DATE | yes | inclusive; `NULL` = open-ended; `>= effective_from` (`ck_custom_prices_dates`) |
| `amount` | NUMERIC(14,2) | no | `>= 0` |
| `version` | INTEGER | no | |

- `CHECK (num_nonnulls(student_id, family_id) = 1)` (`ck_custom_prices_one_target`).
- **No overlap:** `ex_custom_prices_student_overlap` and `ex_custom_prices_family_overlap` (`EXCLUDE USING gist`, partial on the target column) over `(service_price_id =, <target> =, daterange(effective_from, expires_on, '[]') &&)`.
- **Items:** only `per_session` and `full_course` items have custom prices in #95 (the models the #89 routes serve); #93/#94 may widen it.
- **Precedence (H6):** a custom price **matches** a query when its item is one of #89's candidates for the query (ranks 0–4) and it is in effect on the service date. A **student** price beats a **family** price; among one target's prices the item with the lowest rank wins. A matching custom price beats the catalog even when the catalog's deciding version is `NULL` or the item has no version in effect. A family price follows the student's family **at posting**.
- **Ending (H7):** only `expires_on` changes (to today or later, on or after `effective_from`, and on or after the latest `service_date` of a charge at it), or back to `NULL`. The amount, target, item and start never change: a new amount is a new row from a later date. Only an upcoming, unreferenced row may be deleted.
- **Guarded** once referenced by a charge (`charges.custom_price_id`, §5.2), except `expires_on`.

### 3.7 `discounts` (#95)

Reusable discounts, scholarships, sibling discounts and promotions (spec §6; #95 H2, H3, H8, H9).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `kind` | VARCHAR(16) | no | `scholarship` \| `sibling` \| `discount` \| `promotion`; never changes |
| `audience` | VARCHAR(16) | no | `all` (every matching charge) \| `assigned` (only `discount_assignments`); never changes |
| `name` | VARCHAR(120) | no | staff-entered, trimmed, 1–120, unique ignoring case (`uq_discounts_name_lower`) |
| `percent` | NUMERIC(14,4) | yes | `> 0 AND <= 100` |
| `amount` | NUMERIC(14,2) | yes | `> 0` (a fixed amount per charge) |
| `eligibility` | JSONB | no | a JSON object (`ck_discounts_eligibility_object`), default `{}`; the criteria below |
| `starts_on` | DATE | yes | inclusive, against the service date |
| `expires_on` | DATE | yes | inclusive; `starts_on <= expires_on` |
| `is_active` | BOOLEAN | no | default `true` |
| `version` | INTEGER | no | |

- `CHECK (num_nonnulls(percent, amount) = 1)`.
- **Kind rules** (CHECKs): `scholarship` ⇒ `audience = 'assigned'`; `sibling` ⇒ `audience = 'all'`; `promotion` ⇒ both dates. `min_family_students` is set for `sibling` only (checked by the API schema).
- **`eligibility`** (validated by `billing.discounts.DiscountEligibility`, `extra="forbid"`, stored canonical: lists sorted, no duplicates; every key optional, absent = any; every key must hold): `charge_kinds` (subset of `session`, `group_enrollment`, `cohort_enrollment`, `tuition_period`, `package_purchase`; never `manual`), `class_types` (subset of `individual`, `group`, `course`), `subject_ids` (1–50, the charge's subject), `education_level_ids` (1–50, the **student's** level at posting; a student with no level matches only when the key is absent), `min_family_students` (2–20, `sibling` only: the family has at least that many **active** students when the charge is posted; every student of such a family gets it). An empty list is refused.
- **Editable configuration (H8):** an edit or a deactivation affects only charges posted afterwards; posted charges keep their `charge_discounts` snapshot. Discounts are never deleted (no route; `RESTRICT` FKs).
- **Evaluated at posting (H9):** the window against the charge's service date; `is_active`, the assignments, the student's level and the family's active students as they are when the charge is posted. Nothing re-evaluates a posted charge.

### 3.7a `discount_assignments` (#95)

Who an `assigned` discount is for (H2). Mutable config (no `version`): rows are added and hard-deleted by the routes; nothing references them.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `discount_id` | UUID | no | FK `discounts` |
| `student_id` | UUID | yes | FK `students` |
| `family_id` | UUID | yes | FK `families` (covers each student of the family at posting) |

`CHECK (num_nonnulls(student_id, family_id) = 1)`; partial `UNIQUE (discount_id, student_id)` and `UNIQUE (discount_id, family_id)`; partial indexes on `student_id` and `family_id`.

### 3.8 `charge_discounts` (#95)

The discounts applied to a charge, snapshotted when it is posted (W4.2; H8). Append-only (§5.1).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `charge_id` | UUID | no | FK `charges` |
| `discount_id` | UUID | no | FK `discounts` |
| `position` | SMALLINT | no | application order, `>= 1`, `UNIQUE (charge_id, position)` |
| `kind` | VARCHAR(16) | no | the discount's kind at posting |
| `name` | VARCHAR(120) | no | the discount's name at posting |
| `percent` | NUMERIC(14,4) | yes | the discount's percentage at posting |
| `fixed_amount` | NUMERIC(14,2) | yes | the discount's fixed amount at posting; `num_nonnulls(percent, fixed_amount) = 1` |
| `amount` | NUMERIC(14,2) | no | `> 0`, what this line took off |

Σ `amount` per charge = `charges.discount_amount` (App, in the posting transaction: `ChargeDraft` checks it, `post_charge` inserts the lines only when it creates the charge).

### Price calculation (#95)

`billing.service.quote(PriceQuery) -> Quote | None` prices every charge a sweeper or a billing workflow posts (ADR-0006 E9):

1. **Base price** (`base_price`, `list_amount`): a matching custom price of the **student**, then of the **family** (§3.6), else exactly `lookup_price` (§3.5 ranks and `NULL` rule). `None` = no price.
2. **At most one discount per kind:** among the eligible discounts of one kind (§3.7), the one that takes the most off at its position wins (ties: earliest `created_at`, then `id`).
3. **Kinds in a fixed order:** `scholarship` → `sibling` → `discount` → `promotion` (the most specific first), each on the **running remainder**: a percentage line is `remainder × percent / 100` rounded once half-up (`Money.multiply`, ADR-0004); a fixed line is `min(amount, remainder)`. A `0.00` line is not recorded; the amount is never negative.
4. `discount_amount` = Σ lines; `amount = list_amount − discount_amount` (I7). A 100 % scholarship gives a `0.00` charge (`void`).

Discounts apply on top of a custom price too (H5). #92 prorates between `base_price` and `discounts_for` (`quote(query, prorate=(remaining, total))`: the base list amount is prorated first, then the discounts are computed on it); #99 reprices a duration change with `apply_discount_chain` over the charge's snapshot (never a new selection).

### 3.9 `charges`

What the family owes for one student and one thing (E1, E5, E9, E11). Always for one student.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `billing_account_id` | UUID | no | FK `billing_accounts` |
| `student_id` | UUID | no | FK `students` |
| `kind` | VARCHAR(24) | no | `session` \| `group_enrollment` \| `cohort_enrollment` \| `tuition_period` (from #93) \| `package_purchase` (from #94) \| `manual` |
| `class_session_id` | UUID | yes | FK `class_sessions`; source of `session` |
| `group_enrollment_id` | UUID | yes | FK `group_enrollments`; source of `group_enrollment` |
| `cohort_enrollment_id` | UUID | yes | FK `cohort_enrollments`; source of `cohort_enrollment` |
| `tuition_plan_id` | UUID | yes | source of `tuition_period` (column added by #93); composite FK `fk_charges_tuition_plan` `(tuition_plan_id, billing_account_id, student_id) → tuition_plans (id, billing_account_id, student_id)` RESTRICT (K9) |
| `package_id` | UUID | yes | source of `package_purchase` (column added by #94); composite FK `fk_charges_package` `(package_id, billing_account_id, student_id) → packages (id, billing_account_id, student_id)` RESTRICT |
| `service_date` | DATE | no | the session's original local date; the enrollment's local date; the period start (`period_start`); the purchase date; or the staff-entered date |
| `service_period_end` | DATE | yes | last day of a `tuition_period`; `>= service_date` (`ck_charges_service_period_end_valid`; `NULL` for every kind until #93) |
| `price_version_id` | UUID | yes | FK `price_versions`; the version used (`NULL` for `manual` and custom-priced charges) |
| `custom_price_id` | UUID | yes | FK `custom_prices` (column added by #95); the custom price used; never on `manual` (`ck_charges_manual_unpriced`) |
| `education_level_id` | UUID | yes | FK `education_levels`; the student's level at posting (snapshot) |
| `scheduled_minutes` | SMALLINT | yes | the session's `scheduled_minutes` at posting (snapshot; `session` only); 15–480 (`ck_charges_scheduled_minutes_range`, the `class_sessions` range) |
| `list_amount` | NUMERIC(14,2) | no | `>= 0`; price before discounts |
| `discount_amount` | NUMERIC(14,2) | no | `>= 0`; default 0 |
| `amount` | NUMERIC(14,2) | no | `>= 0`; `CHECK (amount = list_amount - discount_amount)` |
| `due_date` | DATE | no | set at posting, never updated (E11) |
| `description` | VARCHAR(200) | yes | `manual` only, trimmed, 1–200 characters (`ck_charges_description_valid`) |
| `posted_at` | TIMESTAMPTZ | no | |
| `posted_by_user_id` | UUID | yes | FK `users`; `NULL` when a job posted it |
| `proration_remaining` | SMALLINT | yes | the sessions left at enrollment of a prorated enrollment fee (#92 J3, J8; column added by #92) |
| `proration_total` | SMALLINT | yes | the proration denominator: the cohort snapshot's `session_count`, or a finite group's counted sessions (#92) |
| `import_job_id` | UUID | yes | FK `import_jobs` RESTRICT (`fk_charges_import_job_id_import_jobs`; column added by #123, migration 0089); the import job that posted this opening balance |
| `import_row_number` | INTEGER | yes | the job's CSV line it came from (#123) |

- **Exactly one source per kind** (`CHECK`):
  - `session` ⇔ `class_session_id IS NOT NULL`, and `scheduled_minutes IS NOT NULL`;
  - `group_enrollment` ⇔ `group_enrollment_id IS NOT NULL`;
  - `cohort_enrollment` ⇔ `cohort_enrollment_id IS NOT NULL`;
  - `tuition_period` ⇔ `tuition_plan_id IS NOT NULL`, and `service_period_end IS NOT NULL`;
  - `package_purchase` ⇔ `package_id IS NOT NULL`;
  - `manual` ⇔ every source column `NULL`, and `description IS NOT NULL`.

  Written as `num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id, tuition_plan_id, package_id) = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END` plus one implication per kind.
- **Staged CHECKs (#90 E3):** #90 creates the kinds whose source exists. `ck_charges_kind` allows `session`, `group_enrollment`, `cohort_enrollment` and `manual`; `ck_charges_one_source` is the rule above over the three existing source columns, with the `session` ⇔ `scheduled_minutes` and `manual` ⇔ `description` implications. #93 (`tuition_plan_id`) and #94 (`package_id`) each add their column, its foreign key and its natural key, and replace both named CHECKs to include their kind. **Since #93 (migration 0065):** `ck_charges_kind` adds `tuition_period`; `ck_charges_one_source` counts `tuition_plan_id` and adds `(kind = 'tuition_period') = (tuition_plan_id IS NOT NULL)` and `(kind = 'tuition_period') = (service_period_end IS NOT NULL)`. **Since #94 (migration 0066):** `ck_charges_kind` adds `package_purchase`; `ck_charges_one_source` counts `package_id` and adds `(kind = 'package_purchase') = (package_id IS NOT NULL)`, keeping every #90/#93 part. `package_purchase` is live: the package sale posts it (service date and due-date base = the purchase date, plus the account's `due_offset_days`, E11; priced by `quote` pinned to the product's item, so #95 discounts apply).
- **`price_version_id` and `custom_price_id`:** at most one is set (`ck_charges_one_price`, #95); a `manual` charge has no custom price (`ck_charges_manual_unpriced`) and no discount lines.
- **Natural keys** (partial unique indexes, J4):
  - `(class_session_id, student_id) WHERE kind = 'session'`;
  - `(group_enrollment_id) WHERE kind = 'group_enrollment'`;
  - `(cohort_enrollment_id) WHERE kind = 'cohort_enrollment'`;
  - `(tuition_plan_id, service_date) WHERE kind = 'tuition_period'`, i.e. `(tuition_plan_id, period_start)`;
  - `(package_id) WHERE kind = 'package_purchase'`.

  - `(student_id, service_date, lower(description)) WHERE import_job_id IS NOT NULL` (an imported opening balance, #123);
  - `UNIQUE (import_job_id, import_row_number)` (one charge per import line).

  #90 names them `uq_charges_session_student`, `uq_charges_group_enrollment` and `uq_charges_cohort_enrollment`; #93 adds `uq_charges_tuition_period`, #94 `uq_charges_package_purchase` and #123 `uq_charges_import_line` and `uq_charges_import_row`. Manual charges posted by hand have no natural key; they rely on the API idempotency key (§3.24), and are never matched by the import key.
- **Import reference (#123 D1, E6; migration 0089):** `ck_charges_import_ref`: `(import_job_id IS NULL) = (import_row_number IS NULL)` and `(import_job_id IS NULL OR kind = 'manual')`. Only the `opening_balances` import sets it, through `post_manual_charge` (the committer is the audit actor and `posted_by_user_id`). `post_charge` checks the import line key under the account lock, and a violation of either unique key in the savepoint counts as already posted. The reference is not exposed in charge reads or the ledger (the audit trail shows the `create` event). The 0089 downgrade refuses while any charge has an import reference. The ledger view is unchanged.
- **Posting (#90 E4):** `billing.service.post_charge` locks the account row, checks the natural key under the lock, and inserts through the ORM in a savepoint; a violation of a named natural-key index (a posting for the same key to another account) counts as already posted (`created = False`).
- **Composite targets:** `UNIQUE (id, billing_account_id)` and `UNIQUE (id, billing_account_id, student_id)`, referenced by the composite foreign keys of allocations and adjustments (I4, I5).
- **Kept account:** a charge keeps the account it was posted to, even if the student later moves to another family. There is no foreign key to `family_students`.
- **Rescheduling:** a rescheduled session keeps its charge and its `service_date`; reporting uses the session's current date (E7).
- **Proration (#92 J8, migration 0064):** `ck_charges_proration`: both proration columns `NULL`, or `kind IN ('group_enrollment', 'cohort_enrollment')` with `proration_total >= 1` and `proration_remaining BETWEEN 0 AND proration_total`. Every cohort fee and every finite-group fee sets both (`10/10` for an on-time enrollment); an open-ended group fee leaves both `NULL`. The full fee stays readable from `price_version_id`/`custom_price_id`.
- **Session charges (#91, #92):** `billing:post_session_charges` charges `individual` and, since #92, `group` sessions (`group/per_session` with `group_id` and the scheduled minutes, through the same eligibility predicate and price key); course sessions are never charged per session (#91 G1). **A group is never charged both per session and by enrollment (#92 J2):** a group session whose participant's covering enrollment period has a `group_enrollment` charge, or is fee-priced (see "Enrollment charges (#92)"), is `enrollment_priced`: no charge, not an unbilled item. Both posting paths re-check this after the account lock. A `0.00` price posts a zero charge (`list_amount = amount = 0.00`, status `void`); it is not an unbilled item (#91 G5). A key that `billing.service.package_covered_keys` returns takes the package path instead (no charge, not listed; #94, see "Prepaid packages (#94)"); the candidates also anti-join the `consume` rows, and `post_charge` refuses a `session` charge for a consumed key (`SessionSettledByPackageError`, counted `package_covered`). Since #95 the price is `billing.service.quote` (custom price, catalog, discounts; "Price calculation (#95)"), and a custom price makes an item the catalog doesn't price billable.

### Enrollment charges (#92)

ADR-0006 E1, E9, E11; the rules live in `app/workflows/enrollment_charges.py`.

- **Fees:** a cohort enrollment period gets the cohort's course fee (`course/full_course` with `cohort_id`); a **fee-priced** group enrollment period gets the group's flat fee (`group/full_course` with `group_id`). Both through `billing.service.quote`, with the owner's subject, `on` = the enrollment's local date, the student's current level and no duration.
- **Group pricing model (J2):** decided per enrollment period. A period is fee-priced when a `group/full_course` base price is in effect on its enrollment date and no `session` charge of the student exists on a group session starting at or after the period's `enrolled_at` (whatever the charge's net after `enrollment_removal` credits, sessions after `withdrawn_at` included: a period once charged per session is never fee-charged, QA #92 D1); otherwise it is session-priced and its confirmed and completed sessions are charged per session. With both prices the fee wins, unless session charges already exist. A group session participant with no covering period is session-priced. #93 extends "priced by enrollment" with a linked tuition plan (`tuition_priced`, see "Tuition plans (#93)").
- **Classification (in order):** fee already posted → J6 due-date base before `billing_starts_on` (`below_floor`) → group with a session charge in the period (`session_priced`) → no fee price (group: `session_priced`; cohort: `no_price`) → another period of the student in the same group or cohort has a fee with a net above `0.00` (`already_charged`, J5) → no family (`no_billing_account`) → billable. A cohort's `no_price` comes before `no_billing_account` (the reverse of #91 G4).
- **Proration (J3):** `list_amount = fee × remaining / total`, one half-up rounding (`Money.prorate`), **before** the discounts. `remaining` counts the owner's occurrences that start after the enrollment instant and are not `cancelled` or `skipped_closure`, materialized rows **and** occurrences of the series rules not materialized yet (minus projected dates on known closures). The denominator is the cohort snapshot's `session_count` (`remaining` clamped to it), or, for a group whose series are all finite, its counted occurrences; an open-ended group pays the full fee with no proration. `remaining = 0` gives a `0.00` charge.
- **Dates (J6, E11):** `service_date` = the enrollment's local date; the due-date base = `max(enrollment local date, first counted session's local date)`, plus the account's `due_offset_days`. The floor is compared with the due-date base, so advance enrollments made before go-live for a course that runs after it are charged.
- **Removal (J4):** see §3.10 `enrollment_removal`. Credits and restorations ignore the floor. A cohort withdrawal posts nothing.
- **Unbilled enrollments (J9):** `GET /api/v1/billing/unbilled-enrollments` lists the sweeper's candidates classified `no_price` or `no_billing_account`; a session-priced group period isn't listed (its sessions are in `/unbilled-items`, which carries `class_type` and `group`).

### 3.10 `charge_adjustments`

Signed corrections against one charge (E2, E3, E5, E8, E9). A credit is a negative adjustment.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `charge_id` | UUID | no | composite FK, see below |
| `billing_account_id` | UUID | no | |
| `student_id` | UUID | no | |
| `kind` | VARCHAR(24) | no | `duration_change` (E2) \| `cancellation_credit` (E3) \| `closure_credit` (E3, ADR-0005 D6) \| `enrollment_removal` (E9, #92) \| `withdrawal` (#101) \| `manual` (E8) \| `package_cancellation` (#100 R6, migration 0070) |
| `amount` | NUMERIC(14,2) | no | `CHECK (amount <> 0)` |
| `reason` | VARCHAR(500) | yes | staff text; required for `manual` (CHECK) and `package_cancellation` (the draft, #100) |
| `source_key` | VARCHAR(200) | yes | idempotency key of a generated adjustment |
| `posted_at` | TIMESTAMPTZ | no | |
| `posted_by_user_id` | UUID | yes | FK `users`; `NULL` for a job |

- **Composite FK:** `(charge_id, billing_account_id, student_id)` → `charges (id, billing_account_id, student_id)` (I5).
- **`source_key`:** partial UNIQUE `WHERE source_key IS NOT NULL` (`uq_charge_adjustments_source_key`). Every kind except `manual` must set it (`CHECK (kind = 'manual' OR source_key IS NOT NULL)`, #90); the implementing issue fixes the format, which includes the triggering event, for example `duration_change:<charge_id>:<session version after the edit>` (E2) or `cancellation_credit:<charge_id>` (E3). Manual adjustments rely on the API idempotency key.
- **`withdrawal` (#101):** `source_key withdrawal:<charge_id>:<withdrawal_id>`, only on `group_enrollment`, `cohort_enrollment`, `tuition_period` or `package_purchase` charges (never `session` or `manual`); `reason` = the withdrawal's reason.
- **Releases:** if an adjustment pushes the charge's net below its valid allocations, the newest allocations are released in the same transaction, and a smaller allocation is re-inserted when the release frees more than needed (E5). The released money becomes account credit.
- **Manual adjustments (#100 C2, R3):** `POST /api/v1/families/{family_id}/billing-account/charges/{charge_id}/adjustments` (`billing.manage`, `Idempotency-Key`) through `billing.service.post_manual_adjustment`. **Both signs** need reauthentication `finance.correction` (a debit also rewrites what a posted charge means; stricter than E8's wording, ADR-0006 implementation note). A credit below the net is `409 charge_net_negative` (I1); a credit releases the newest allocations (E5 above). A `package_purchase` charge is refused (`409 package_purchase_not_adjustable`): it is corrected only by a package cancellation (§3.20), so money and units move together. Manual charges (`kind = 'manual'`, #100 C1, R7) need `billing.manage` only. `post_manual_charge` needs a staff actor; no job calls it, except the import task, which posts `opening_balances` rows through it acting as the committer (#123 C3; the charge carries its import reference, §3.9).
- **`package_cancellation` (#100 R6):** the credit of a package-purchase cancellation, on the `package_purchase` charge only, `amount = −credit`, `source_key = package_cancellation:<package_id>` (one per package), `reason` = the staff reason. Added to `ck_charge_adjustments_kind` by migration 0070; the downgrade restores the 0061 list and fails while such rows exist.
- **Schedule reconciliation keys (#99):** `cancellation_credit:<charge_id>` (one cancellation credit per charge, `−net`); `closure_credit:<charge_id>:<n>`, where `n` is the number of `closure_credit` rows of the charge + 1, read under the account lock: odd `n` is a closure credit (`−net`), even `n` a **positive** restoration of exactly the credit before it (the occurrence is `confirmed` or `completed` again; Q3); `duration_change:<charge_id>:<session version the job saw>` (Q1: edits between two runs coalesce). `duration_change` is signed (shorter → negative, back to the original → positive).
- **`enrollment_removal` is signed (#92 J4):** a group withdrawal credits each `session` charge of the student on a group session that starts after the withdrawal and that the student left, with `−net` (skipped when the net is `0.00`; `source_key enrollment_removal:<charge_id>:<group_enrollment_id>:withdrawn`); a re-enrollment of the student in the same group restores each such charge with a **positive** `enrollment_removal` of minus the sum of its `enrollment_removal` adjustments (`…:<new group_enrollment_id>:enrolled`). Flat group fees and course fees are never adjusted this way (#101).

### 3.11 `charge_due_date_changes` (#97)

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `charge_id` | UUID | no | composite FK `(charge_id, billing_account_id)` → `charges (id, billing_account_id)` (I4 style) |
| `billing_account_id` | UUID | no | the charge's account |
| `previous_due_date` | DATE | no | the effective due date before this change; `CHECK (due_date <> previous_due_date)` |
| `due_date` | DATE | no | the new due date; `CHECK (due_date BETWEEN '2000-01-01' AND '2099-12-31')` |
| `reason` | VARCHAR(500) | no | staff text (`staff_text_check`, trimmed 1–500) |
| `changed_by_user_id` | UUID | no | FK `users` `RESTRICT` |
| `created_at` | TIMESTAMPTZ | no | from `app.core.clock` |

The effective due date of a charge is the `due_date` of its latest change (by `created_at`, `id`), or `charges.due_date` when there is none (`billing.balances._effective_due_date()`; every status, the #96 oldest-first order, the overdue list and the reminders read it, never `charges.due_date` directly). Index `(charge_id, created_at, id)`. Created by migration 0067; `Audited` (one `create` event with the actor) and append-only (§5.1).

- **Writes (#97 N5):** `POST …/charges/{charge_id}/due-date-changes` (`billing.manage`, no `Idempotency-Key`, no reauthentication) under the account row `FOR UPDATE`; the charge's balance is re-read under the lock: `paid`/`void` → `409 charge_settled`; the current effective date → `409 due_date_unchanged`. Earlier dates are allowed.
- **Reads:** `GET …/due-date-changes` is staff only (`billing.view`): guardians never see the reasons. The charge reads carry `original_due_date` (`charges.due_date`) beside the effective `due_date`.

### 3.12 `payments`

An external payment (cash, transfer or other) recorded by staff (spec §1, §6; E8, E12).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `billing_account_id` | UUID | no | FK `billing_accounts` |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `received_on` | DATE | no | the local date the money was received (cash basis, E7) |
| `payment_method_code` | VARCHAR(32) | no | FK `payment_methods` |
| `reference` | VARCHAR(100) | yes | staff text |
| `payer_name` | VARCHAR(200) | yes | staff text; shown on the receipt |
| `note` | VARCHAR(500) | yes | staff text |
| `recorded_by_user_id` | UUID | no | FK `users` |

`UNIQUE (id, billing_account_id)` is the target of the composite foreign keys (I4).

**Recording (#96).** `POST /api/v1/families/{family_id}/billing-account/payments` (`payment.record`, `Idempotency-Key`; no reauthentication, M6) through `billing.service.record_payment`. `amount > 0` within `NUMERIC(14,2)`; `received_on` from 2000-01-01 to the center's today (backdating allowed, no future date); the method must be an **active** `payment_methods` row; `reference`, `payer_name` and `note` are trimmed and blank becomes `NULL` (so the staff-text CHECKs never fire). An inactive family may pay. A family's first payment creates its account (`ensure_billing_account`). `recorded_by_user_id` is the request's actor; a job never records a payment. The entry time is the row's `created_at`. #98 issues the receipt inside the same transaction.

### 3.13 `payment_reversals`

At most one per payment (E5). A reversal releases all of the payment's valid allocations in the same transaction.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `payment_id` | UUID | no | **UNIQUE**; composite FK `(payment_id, billing_account_id)` → `payments (id, billing_account_id)` |
| `billing_account_id` | UUID | no | |
| `reason` | VARCHAR(500) | no | staff text |
| `reversed_by_user_id` | UUID | no | FK `users` |

A payment with refunds can't be reversed (`409`, I3). Needs `finance.refund.approve` plus reauthentication `finance.refund_or_reversal` (E8).

**Reversal flow (#100 C3, R5; `billing.service.reverse_payment`).** `POST /api/v1/families/{family_id}/billing-account/payments/{payment_id}/reversal` `{reason}` (`Idempotency-Key`). Under the account lock: a reversal exists → `409 payment_already_reversed` (the UNIQUE is the backstop); any refund → `409 payment_has_refunds` (#90 QA O3); then the reversal row and one `allocation_releases` row per **valid** allocation of the payment (re-inserted ones included; already released ones untouched), `released_by_user_id` = the actor. A payment without allocations is reversible. The charges reopen (`paid` → `pending`/`overdue`/`partially_paid`) and the payment's remainder stops being account credit. The payment, its allocations and its receipt rows stay as they were; the receipt PDF shows #98's live "reversed" banner. There is no "un-allocate": a mis-allocated payment is reversed and recorded again. Reads (#100 R8): the payment detail carries `reversal` (`reason` and actor `null` for a caller without `billing.view`), the list `reversed_at`.

### 3.14 `payment_allocations`

Part of a payment applied to one charge of the same account (spec W4.3).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `payment_id` | UUID | no | composite FK `(payment_id, billing_account_id)` → `payments (id, billing_account_id)` |
| `charge_id` | UUID | no | composite FK `(charge_id, billing_account_id)` → `charges (id, billing_account_id)` |
| `billing_account_id` | UUID | no | |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `created_by_user_id` | UUID | yes | FK `users`; `NULL` when re-inserted by an adjustment's release |

An allocation is **valid** while it has no `allocation_releases` row.

**Allocation (#96 M2-M4; `app/modules/billing/payments.py`).** A payment is allocated in its own transaction (`oldest_first`, `explicit` or `none`); account credit is allocated later (`POST …/allocations`, `oldest_first` or `explicit`). Both run under the account lock and read every `open` amount after it.

- **Oldest first:** the account's charges with `open > 0`, every student included (a student who left the family too), ordered by **effective due date** (§3.11), then `service_date`, `posted_at`, `id`; each gets `min(open, money left)`. #97's due-date changes reorder it with no change in #96.
- **Explicit:** 1–200 distinct charges of the account, each line `> 0` and `≤ open`, all or nothing (`409 allocation_exceeds_open`, `422 allocation_charge_not_found`); a payment's lines add up to at most its amount (`422 allocation_exceeds_payment`).
- **The rest is account credit:** no row is written for unallocated money (E5).
- **Credit source (M4):** a credit allocation draws from the payments without a reversal whose remainder (`amount − Σ valid allocations − Σ refunds`, the same expression as account credit, `billing.service.payment_remainder_column`) is `> 0`, **oldest payment first** (`received_on`, `created_at`, `id`). One target line can become several rows, one per source payment. More than the credit → `409 insufficient_account_credit`; no credit or no open charge → `409 nothing_to_allocate`.
- There is no "un-allocate": an allocation ends only by a release (§3.15).

### 3.15 `allocation_releases`

A release frees a whole allocation (E5).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `allocation_id` | UUID | no | FK `payment_allocations`, **UNIQUE** |
| `payment_reversal_id` | UUID | yes | FK `payment_reversals`; set when a reversal released it |
| `charge_adjustment_id` | UUID | yes | FK `charge_adjustments`; set when an adjustment released it |
| `released_by_user_id` | UUID | yes | FK `users` |

`CHECK (num_nonnulls(payment_reversal_id, charge_adjustment_id) = 1)`. A #100 reversal writes one per valid allocation of the payment (§3.13); an adjustment's releases are E5's (§3.10).

### 3.16 `refunds`

Money paid back out of a payment's unallocated remainder (E5). A cash refund reduces account credit, not the outstanding amount.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `payment_id` | UUID | no | composite FK `(payment_id, billing_account_id)` → `payments (id, billing_account_id)` |
| `billing_account_id` | UUID | no | |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `refunded_on` | DATE | no | |
| `payment_method_code` | VARCHAR(32) | no | FK `payment_methods` (how the money went back) |
| `reference` | VARCHAR(100) | yes | staff text |
| `reason` | VARCHAR(500) | no | staff text |
| `withdrawal_id` | UUID | yes | FK `withdrawals` RESTRICT, indexed (migration 0071): the withdrawal that paid it out, `NULL` for a #100 refund |
| `refunded_by_user_id` | UUID | no | FK `users` |

Needs `finance.refund.approve` plus reauthentication `finance.refund_or_reversal` (E8).

**Refund rules (#100 C4, R4, R5; `billing.service.refund_payment`, also #101's entry point).** `POST /api/v1/families/{family_id}/billing-account/payments/{payment_id}/refunds` `{amount, refunded_on, payment_method_code, reference?, reason}` (`Idempotency-Key`). **Payment-scoped:** one row per request, drawn on that payment's remainder (`amount − Σ valid allocations − Σ refunds`, the #96 expression `payment_remainder_column`, read under the account lock); several partial refunds per payment are allowed. `refunded_on` from the payment's `received_on` (`422 refunded_on_before_payment`) to the center's today (`422 refunded_on_in_future`); an active method (`422 payment_method_unavailable`); a reversed payment → `409 payment_reversed`; above the remainder → `409 refund_exceeds_remainder` (details `remainder`). A refund lowers the account credit only, never the outstanding amount, and leaves the receipt unchanged (no refund banner, R9). **No undo in M6:** a wrong refund stays (no `refund_reversals` table). Reads: the payment detail's `refunds[]` (`reason` and actor `null` for a caller without `billing.view`; `reference` shown).

### 3.17 `tuition_plans` (#93)

A recurring tuition plan; one charge per plan per period (E1). Mutable config, `Audited`, versioned (migration 0065).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `billing_account_id` | UUID | no | FK `billing_accounts` RESTRICT; the family account the plan bills, fixed at creation (K9) |
| `student_id` | UUID | no | FK `students` RESTRICT |
| `service_price_id` | UUID | no | FK `service_prices` RESTRICT; the pinned `per_period` item (App, K2) |
| `group_enrollment_id` | UUID | yes | FK `group_enrollments` RESTRICT; group per-student pricing (E9, K6) |
| `frequency` | VARCHAR(16) | no | `weekly` \| `biweekly` \| `monthly` (`ck_tuition_plans_frequency_valid`); copied from the item |
| `starts_on` | DATE | no | the anchor of the period grid (K3) |
| `ends_on` | DATE | yes | `>= starts_on` (`ck_tuition_plans_dates`); a period end (App); `NULL` = open-ended |
| `due_offset_days` | SMALLINT | no | 0–60 (`ck_tuition_plans_due_offset_days_range`), default 0; the plan's due rule (E11) |
| `version` | INTEGER | no | |

- **Composite target:** `UNIQUE (id, billing_account_id, student_id)` (`uq_tuition_plans_id_account_student`), referenced by `charges.fk_charges_tuition_plan`, so a tuition charge always posts to its plan's account and student (K9).
- **No overlap** (`EXCLUDE USING gist`, `daterange(starts_on, ends_on, '[]') &&`): `ex_tuition_plans_enrollment_overlap` on `group_enrollment_id =` `WHERE group_enrollment_id IS NOT NULL`; `ex_tuition_plans_student_item_overlap` on `(student_id =, service_price_id =)` `WHERE group_enrollment_id IS NULL`. API: `409 tuition_plan_overlap`.
- **Key trigger** `tuition_plans_key_immutable` (`BEFORE UPDATE`, 42501): `billing_account_id`, `student_id`, `service_price_id`, `group_enrollment_id`, `frequency` and `starts_on` never change (they define the grid and the composite key); only `ends_on`, `due_offset_days` and the standard columns do.
- **Indexes:** `ix_tuition_plans_student_id`, `ix_tuition_plans_billing_account_id`.
- **Deleting** a plan is allowed only while it has no charge (API; the composite FK is RESTRICT).

### 3.17a `tuition_plan_pauses` (#93)

Pauses of a plan (K4). Mutable config, `Audited`, versioned.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `tuition_plan_id` | UUID | no | FK `tuition_plans` **CASCADE** |
| `starts_on` | DATE | no | a period start (App) |
| `ends_on` | DATE | yes | `>= starts_on` (`ck_tuition_plan_pauses_dates`); a period end (App); `NULL` = until resumed |
| `version` | INTEGER | no | |

- `EXCLUDE USING gist (tuition_plan_id WITH =, daterange(starts_on, ends_on, '[]') WITH &&)` (`ex_tuition_plan_pauses_overlap`): pauses of one plan never overlap, so there is at most one open pause.

### Tuition plans (#93)

ADR-0006 E1, E7, E9, E11; the rules live in `app/modules/billing/tuition.py` (pure grid and period state) and `app/workflows/tuition_charges.py` (the job and the unbilled read).

- **Price (K2):** a plan pins its `per_period` item; each period's base price is the item's version in effect on `period_start` (`billing.service.quote` with `PriceQuery.service_price_id`: the item's custom prices, then its version; no rank search), then the discounts (`charge_kind = 'tuition_period'`). `lookup_price` refuses `per_period` (`ValueError`). A new version changes only later periods; posted charges keep `price_version_id`. No custom prices on `per_period` items (K8).
- **Grid (K3):** periods are anchored at `starts_on` and whole: weekly `starts_on + 7k`, biweekly `+ 14k`, monthly `starts_on + k` months with the day clamped and always computed from `starts_on` (31 Jan → 28/29 Feb → 31 Mar → 30 Apr). A period ends the day before the next one starts. Local calendar dates of the center zone (ADR-0004); no instant is stored, so no time-zone dependency is registered. No proration, ever: `ends_on` is a period end and the last period is charged in full.
- **Period state** (`plan_period_state`, in order): `not_due` (`period_start` after the center's today) → `below_floor` (billing off or `period_start < billing_starts_on`) → `after_end` (`period_start > ends_on`) → `paused` (`period_start` inside a pause) → `enrollment_ended` (the linked enrollment was withdrawn on or before `period_start`) → `due`. A due period is then `family_changed` when the student's current family is not the plan's (K9), `no_price` when the pinned base price is `None`, else posted.
- **Posting (K5):** due when `period_start ≤ center today`; the daily job `billing:post_tuition_charges` (`30 7 * * *`, also deferred after a plan create, resume or extension and a non-null settings `PATCH`) posts every due, unposted period oldest first (catch-up). Charge: `kind = 'tuition_period'`, `service_date = period_start`, `service_period_end = period_end`, `due_date = period_start + plan.due_offset_days + account.due_offset_days`, the student's level snapshot. A `0.00` version posts a `0.00` charge (`void`).
- **Pauses (K4):** a paused period is never charged, never caught up; a pause starts after the last posted period and not before today's period; resume sets the open pause's `ends_on` (resuming on its own start deletes it).
- **Group per-student pricing (K6):** a plan linked to a group enrollment pins a `group/per_period` item scoped to the group or unscoped with its subject. The #92 classification gains `tuition_priced` (a period with a linked plan, any window, never gets a flat fee and is not listed); the #91 classification gains `tuition_priced` for a group session whose covering period has a linked plan whose window contains the session's local date (paused periods included): no charge, not listed. Outside the window J2 applies (a linked period is never fee-priced, so those sessions are charged per session). Linking is refused with a flat fee of net > 0 (`409 tuition_plan_enrollment_fee_charged`) or an in-window session charge of net > 0 (`409 tuition_plan_sessions_charged`), checked under the account lock; the #91 session step and the #92 fee step re-check `tuition_priced` under the same lock. A withdrawal stops the periods from its local date; posted periods stay (crediting them is #101).
- **Individual plans (K7):** a standalone recurring charge; they never change per-session charging (a center billing a student only by tuition gives them a `0.00` per-session custom price).
- **Account binding (K9):** a plan bills the family account of the student at creation (`409 student_has_no_family` without a family); after a family change the periods are `family_changed` (listed in `GET /api/v1/billing/unbilled-tuition-periods`, never posted to either account).
- **Unbilled tuition periods:** the due, unposted periods classified `family_changed` or `no_price`, computed on read; billing off → empty.

### 3.18 `package_products` (#94)

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `service_price_id` | UUID | no | FK `service_prices` with `pricing_model = 'package'`, **UNIQUE** |
| `name` | VARCHAR(120) | no | staff text |
| `unit` | VARCHAR(8) | no | `sessions` \| `minutes` |
| `quantity` | INTEGER | no | `> 0` |
| `validity_days` | SMALLINT | no | `> 0` |
| `subject_id` | UUID | yes | FK `subjects`; `NULL` = any subject |
| `is_active` | BOOLEAN | no | |
| `version` | INTEGER | no | |

A product covers **individual sessions only** (E10). Named CHECKs `ck_package_products_name_valid` (trimmed, 1-120), `_unit_valid`, `_quantity_positive`, `_validity_days_positive`. **Fixed fields (#94 L7):** `unit`, `quantity`, `validity_days` and `subject_id` never change after creation (the API refuses them; a different offer is a new product); only `name` and `is_active` change. Never deleted (deactivated). The product's price is the versions of its `service_prices` row (`individual`/`package`, the product's subject or `NULL`), managed through the #89 version service with `pricing_models=("package",)`, so the #89 E3 date rules hold; the #89 catalog routes never serve `package` items. Routes: `/api/v1/billing/package-products` (list, create with the first version, detail, `PATCH` name/is_active, versions add/delete), all `billing.manage`, no `idempotent()` (configuration). The API bounds: 1-500 sessions or 15-30000 minutes in steps of 15, validity 1-730 days.

### 3.19 `packages` (#94)

A purchased package: one billing account and one student, never shared (spec §6).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `billing_account_id` | UUID | no | FK `billing_accounts` |
| `student_id` | UUID | no | FK `students` |
| `package_product_id` | UUID | no | FK `package_products` |
| `price_version_id` | UUID | no | FK `price_versions` (the price paid) |
| `unit` | VARCHAR(8) | no | snapshot of the product |
| `units_granted` | INTEGER | no | `> 0`; snapshot |
| `subject_id` | UUID | yes | snapshot of the product |
| `purchased_on` | DATE | no | local date |
| `expires_on` | DATE | no | `= purchased_on + validity_days` (App); `> purchased_on` |

`UNIQUE (id, billing_account_id, student_id)` (`uq_packages_id_account_student`, the target of `fk_charges_package`); CHECKs `ck_packages_unit_valid`, `ck_packages_units_granted_positive`, `ck_packages_dates`; indexes `(billing_account_id, student_id, expires_on)` and `(price_version_id)`. Append-only (§5.1) and audited. Its purchase charge (`charges.kind = 'package_purchase'`) is posted in the same transaction.

**Purchase (#94 L8):** `POST /api/v1/billing/packages {student_id, package_product_id}` (`billing.manage` + `idempotent()`). One transaction: the idempotency row → the product `FOR SHARE` (unknown `422`, inactive `409 package_product_inactive`) → the student (`422`) → the student's **current** family (`409 student_has_no_family`) → `quote` pinned to the product's item on the center's today (`charge_kind = 'package_purchase'`; none or `NULL` → `409 package_not_offered`) → the family's account `FOR UPDATE` → the `packages` row (`purchased_on` = the center's today, no backdating) → the `package_purchase` charge → the `grant` row. Billing off or a later floor never blocks it (L5).

### 3.20 `package_credit_transactions` (#94)

Units move only through these rows (E10), as signed integers in the package's unit.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `package_id` | UUID | no | FK `packages` |
| `kind` | VARCHAR(16) | no | `grant` \| `consume` \| `expire` \| `reversal` \| `refund` (#101, migration 0071) |
| `units` | INTEGER | no | `grant` > 0; `consume`, `expire`, `refund` < 0; `reversal` <> 0 |
| `class_session_id` | UUID | yes | FK `class_sessions`; `consume` only (`NOT NULL` for it) |
| `student_id` | UUID | yes | FK `students`; set with `class_session_id` |
| `reverses_transaction_id` | UUID | yes | FK `package_credit_transactions`; `reversal` only (`NOT NULL` for it) |
| `withdrawal_id` | UUID | yes | FK `withdrawals` RESTRICT; `CHECK ((kind = 'refund') = (withdrawal_id IS NOT NULL))` (migration 0071) |

- **Staged (#94 L10, the #90 E3 pattern):** #94 creates the table without `withdrawal_id`, and `ck_package_credit_transactions_kind` allows `grant`, `consume`, `expire` and `reversal`; #101 (migration 0071) adds `withdrawal_id` and `refund`, replacing the kind and units-sign CHECKs, plus the partial UNIQUE `uq_package_credit_transactions_refund (package_id) WHERE kind = 'refund'` (one per package, `units = −remaining`). `reversal` and `reverses_transaction_id` exist (self-FK, natural key) but #94 writes none.
- **`reversal` (#100 R6):** written **only** by a package-purchase cancellation (`POST /api/v1/billing/packages/{package_id}/cancellation` `{reason, credit_amount?}`, `billing.manage` + reauthentication `finance.correction` + `Idempotency-Key`; `billing.service.cancel_package`): under the account lock, a package with an earlier `reversal` (`cancelled`), an `expire` row or `expires_on <` the center's today (`expired`), or no units left (`exhausted`) → `409 package_not_cancellable` (details `reason`); otherwise one row `units = −remaining`, `reverses_transaction_id` = the `grant` row, so remaining is 0 afterwards (at most one per grant: the natural key). The purchase charge is credited with one `package_cancellation` adjustment (§3.10): default `net × remaining / units_granted` (`Money.prorate`), an explicit `credit_amount` from `0.00` to the net overrides it (`0.00` writes no adjustment; above the net → `409 charge_net_negative`). Released allocations become account credit (E5); the money goes back only through a refund (§3.16). Sessions the package covered but hadn't consumed lose the coverage (remaining 0) and the next session sweep charges them. #94's status gains `cancelled` (it wins over `expired` and `exhausted`).
- **CHECKs:** `ck_package_credit_transactions_units_sign` (the signs above, I6), `_consume_session` and `_consume_student` (`class_session_id`/`student_id` set ⇔ `consume`), `_reversal_target` (`reverses_transaction_id` set ⇔ `reversal`).
- **Natural keys:** `(package_id) WHERE kind = 'grant'`; `(package_id) WHERE kind = 'expire'`; `(package_id, class_session_id) WHERE kind = 'consume'`; `(reverses_transaction_id) WHERE kind = 'reversal'` (`uq_package_credit_transactions_grant`, `_expire`, `_consume`, `_reversal`). Indexes `(package_id)` and `(class_session_id, student_id) WHERE kind = 'consume'`.
- A minutes package consumes `scheduled_minutes` (E2). Consumption is first-expiring-first, possibly across packages, and all or nothing per `(class_session_id, student_id)` (E10); see "Prepaid packages (#94)".
- Unit movements are not money: they stay out of `billing_ledger_entries` and the #364 ledger (the purchase charge is there).

### Prepaid packages (#94)

ADR-0006 E1, E7, E10, E11; the rules live in `app/modules/billing/packages.py` (rows, coverage, consumption, expiry) and `app/modules/billing/package_rules.py` (the pure L4 plan and the status).

- **Eligible package (L2):** the same student; on the account of the student's **current** family (never a sibling's or a former family's, spec §6); `subject_id IS NULL` or the session's subject; `purchased_on ≤ service_date ≤ expires_on` where `service_date` is the **session's local date**, not today (a session dated before the purchase was normally charged, E10 "not converted"; one dated after `expires_on` is never covered); no `expire` row; remaining > 0.
- **Plan (L4, first-expiring-first):** eligible packages by `(expires_on, purchased_on, id)`. Sessions path: the first `sessions` package gives 1 unit, whatever the length. Minutes path: `minutes` packages in order, `min(remaining, still needed)` each, until `scheduled_minutes` are covered (possible only if their sum is enough). Both possible → the path whose first package expires first (a tie goes to sessions); neither → short (nothing consumed). One `consume` row per package used.
- **Coverage (L3, `package_covered_keys`):** a key is covered when a `consume` row exists for it, or it is an `individual` session whose closeout has **no approval yet** and a plan exists. An approved key that wasn't consumed is not covered: the session sweeper charges it on its next run. The approval never posts a charge. At most three statements per batch.
- **Consumption (`consume_session_credit`):** at closeout approval **revision 1** (`app/workflows/closeout_approval.on_closeout_approved`; later revisions, group and course sessions do nothing; attendance is ignored, L6) and in the expiry job's fallback (L1). Under the account lock: the current family (none → `no_package`) → its account `FOR UPDATE` → a `session` charge for the key → `charged`, nothing written → a `consume` row → `already_consumed` → no eligible package → `no_package` → no plan → `short` → the rows (a natural-key violation → `already_consumed`). The session's state is not checked (#99 consumes for a cancelled session's `charge` outcome).
- **Floor (L5):** `billing_starts_on` gates only automatic session charges. Purchases, consumption and expiry run with billing off.
- **Expiry (L1, `billing:expire_packages`, daily `40 7 * * *`):** for each package with `expires_on <` the center's today, no `expire` row and remaining > 0: first the **fallback consumption** of the student's pending covered sessions in the package's window (individual, `confirmed`/`completed`, local date in `[purchased_on, expires_on]`, the package's subject or any, no session charge, no `consume` row, no approved closeout), each in its own Unit of Work (session `FOR SHARE` → `consume_session_credit`); then one `expire` row for the remainder (account `FOR UPDATE`, remaining recomputed; natural key). A package whose fallback had a failing item waits for the next run. Every covered session therefore ends up consumed (at approval, or at the latest the day after its package expires) or charged by the sweeper. Breakage (E7) is the `expire` rows.
- **Reads (L9):** `GET /api/v1/billing/packages` (filters `student_id`, `family_id`, `status`) and `GET …/{id}` (with the movements), `billing.view`, staff only; #102 adds the family reads. Status: `cancelled` (a `reversal` row, #100) → `expired` (an `expire` row or `expires_on <` today) → `exhausted` (remaining 0) → `active`.

### 3.21 `receipts` (#98)

One receipt per payment, issued in the payment's transaction (E13). The row is a frozen snapshot.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `number` | VARCHAR(16) | no | **UNIQUE**; `R-YYYY-NNNNNN`, e.g. `R-2026-000123` |
| `payment_id` | UUID | no | **UNIQUE**; composite FK `(payment_id, billing_account_id)` → `payments (id, billing_account_id)` |
| `billing_account_id` | UUID | no | |
| `issued_at` | TIMESTAMPTZ | no | its center-local year is the number's year |
| `language` | VARCHAR(2) | no | `es` \| `en` ([ADR-0015](adr/0015-server-side-i18n.md)) |
| `template_version` | SMALLINT | no | `> 0` |
| `snapshot` | JSONB | no | center name, address and contact (business settings); family name and payer; amount and method; allocations as charge ids, amounts and structured labels |

Later changes to settings (including the receipt message, #121) or allocations don't change the row. A reversed payment's receipt keeps its number; the PDF shows a "reversed" banner from the `payment_reversals` row. Payments recorded before #98 merges get no receipt (no backfill).

**Implemented by #98, migration 0068** (`billing.models.Receipt`, `app/modules/billing/receipts.py`):

- **Constraints:** `ck_receipts_number_format` (`number ~ '^R-[0-9]{4}-[0-9]{6}$'`), `ck_receipts_language`, `ck_receipts_template_version_positive`, `ck_receipts_snapshot_object` (`jsonb_typeof(snapshot) = 'object'`), `uq_receipts_number`, `uq_receipts_payment_id`, `fk_receipts_payment_id_payments` (`(payment_id, billing_account_id)` → `payments (id, billing_account_id)` `RESTRICT`, I4); index `ix_receipts_billing_account_id_issued_at`. Append-only and `Audited`: `receipts_append_only()` with `receipts_no_update_delete` and `receipts_no_truncate` (SQLSTATE 42501, §5.1).
- **Issued** inside `billing.service.record_payment`, as its last step (P4): the snapshot inputs are read first, then `number_series` (§3.25), then the insert. A second receipt for one payment is refused.
- **Snapshot v1** (`ReceiptSnapshotV1`, `"v": 1`, validated on write and on read; money as strings): `center {name, address, phone, email}` (business settings at issue); `family {id, name}`; `payer_name` (null → the PDF shows the family name); `payment {id, amount, received_on, method_code, reference}`; `issued_on` (the center-local date of `issued_at`); `allocations [{charge_id, student_id, student_name, kind, service_date, service_period_end, scheduled_minutes, description, amount}]` (the payment's allocations at that moment, oldest first); `totals {allocated_total, unallocated}`. Labels are built at render time from `kind`, the dates, the minutes and the manual description (ADR-0015 language-neutral data). No staff note, no actor names; no subject, group or product names (a later snapshot v2 may add them).
- **Language (P5):** the stored `user_preferences.language` of the first receipt recipient (an active guardian profile with a login in `family_guardians`, by `family_guardians.created_at, id`) that has one; otherwise `es`. Every download renders in it.
- **Snapshot v2 (#121 B4, migration 0087):** every receipt issued after #121 is `template_version = 2` with `ReceiptSnapshotV2` (`"v": 2`): v1's fields plus `message` (the business-settings `receipt_message_es` or `receipt_message_en` of the receipt's own language when it was issued, `null` when that language has none; no fallback). `parse_snapshot` reads both versions (discriminated on `v`); a snapshot `v` that differs from `template_version` is a `500`. v1 receipts render unchanged.
- **Template versions (P8):** `template_version` picks `app/templates/receipts/v<N>/receipt.html` and the catalog keys `receipts.v<N>.*`; an unknown version is a `500`. A wording or layout change that would alter what an issued receipt says creates a new version and leaves the old one intact; styling-only fixes may edit the current one.
- **No backfill:** payments recorded before 0068 keep `receipt: null` on the reads.

### 3.22 `cancellation_billing_rules` (#99)

The money outcome per cancellation classification (E3). One row per effective instant, like `scheduling_policies`. **Guarded** (§5.2): a version never changes; an upcoming one may be deleted.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `effective_from` | TIMESTAMPTZ | no | **UNIQUE** |
| `excused_outcome` | VARCHAR(8) | no | `credit` \| `charge`; seeded `credit` |
| `on_time_outcome` | VARCHAR(8) | no | `credit` \| `charge`; seeded `credit` |
| `late_outcome` | VARCHAR(8) | no | `credit` \| `charge`; seeded `charge` |
| `no_show_outcome` | VARCHAR(8) | no | `credit` \| `charge`; seeded `charge` |
| `created_by_user_id` | UUID | yes | FK `users` RESTRICT; `NULL` for the seeded row |

- **Seed (migration 0069):** version 1, effective `2000-01-01T00:00:00Z`: credit / credit / charge / charge.
- **The outcome:** a cancellation that allows a makeup is always `credit` (E3); otherwise the rule's column for the classification. The rule is the version in force at the **classification instant**: an approved change request's `created_at` (#62), else the session's `cancelled_at` (a center cancellation: `excused`, makeup allowed). Every `excused` cancellation allows a makeup (#61 `classify_change`), so `excused_outcome` is inert today; it stays for a later policy without makeups.
- **Managed** with `billing.manage` (`POST`/`DELETE /api/v1/billing/cancellation-rules`, read with `billing.view`); writes take the advisory lock `b"clsCnRul"`.

### 3.22a `session_cancellation_decisions` (#99)

The billing outcome of one cancelled session for one participant (Q5). Append-only (§5.1), audited. It also explains a "nothing posted" outcome to staff and families.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `class_session_id` | UUID | no | FK `class_sessions` |
| `student_id` | UUID | no | FK `students` |
| `initiated_by` | VARCHAR(8) | no | `center` \| `family` \| `tutor` |
| `change_request_id` | UUID | yes | FK `session_change_requests`; the approved request |
| `classification` | VARCHAR(8) | no | `on_time` \| `late` \| `no_show` \| `excused` (the effective one, #62 E3) |
| `makeup_allowed` | BOOLEAN | no | |
| `classified_at` | TIMESTAMPTZ | no | the request's `created_at`, else `cancelled_at` |
| `rule_id` | UUID | no | FK `cancellation_billing_rules` (the version in force then) |
| `outcome` | VARCHAR(8) | no | `credit` \| `charge` |
| `result` | VARCHAR(16) | no | `credit` → `credited` \| `no_charge` \| `zero_charge` \| `already_zero`; `charge` → `charge_stands` \| `charged` \| `package_consumed` |
| `charge_id` | UUID | yes | FK `charges` |
| `charge_adjustment_id` | UUID | yes | FK `charge_adjustments`; set iff `result = 'credited'` |
| `decided_at` | TIMESTAMPTZ | no | |

`UNIQUE (class_session_id, student_id)` (`uq_session_cancellation_decisions_key`). A `charge` outcome that can't be posted yet (billing off, before the floor, no family, no price) writes **no** row and is retried (Q7). Every foreign key is `RESTRICT`; long names are explicit (`fk_session_cancellation_decisions_<column>`).

### 3.22b `charge_duration_repricings` (#99)

One repricing of a `session` charge to its session's new `scheduled_minutes` (E2, Q2). Append-only (§5.1), audited.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `charge_id` | UUID | no | FK `charges` |
| `class_session_id` | UUID | no | FK `class_sessions` |
| `session_version` | INTEGER | no | the session `version` the job saw |
| `previous_minutes` | SMALLINT | no | 15–480; the billed minutes before |
| `scheduled_minutes` | SMALLINT | no | 15–480; `<> previous_minutes` |
| `target_amount` | NUMERIC(14,2) | no | `>= 0` |
| `delta_amount` | NUMERIC(14,2) | no | may be `0.00` |
| `method` | VARCHAR(8) | no | `original` \| `catalog` \| `pro_rata` |
| `clamped` | BOOLEAN | no | the delta was raised so the net stays `>= 0.00` |
| `charge_adjustment_id` | UUID | yes | FK `charge_adjustments`; `CHECK ((delta_amount <> 0) = (charge_adjustment_id IS NOT NULL))` |

- `UNIQUE (charge_id, session_version)`; index `(charge_id, created_at DESC, id DESC)`: the latest row's `scheduled_minutes` are the charge's **billed minutes** (else `charges.scheduled_minutes`).
- **Target:** `charges.amount` when the new minutes equal the charge's snapshot (`original`); else `base_price` for the new minutes (the charge's service date, level snapshot and account family) with the charge's `charge_discounts` snapshot re-applied in position order (`catalog`, never re-selected); else `charges.amount` pro rata (`pro_rata`, `Money.prorate`). **Delta** = target − (`amount` + Σ `duration_change`): manual, `enrollment_removal`, withdrawal and the #99 credits stay; if the net would go below `0.00` the delta is `−net` (`clamped`). A `0.00` delta writes the row without an adjustment.

### 3.23 `withdrawals` (#101)

One settlement of a withdrawal (#101 A2, migration 0071; S1-S7): a student leaves a group, a course cohort or a prepaid package and the unused part is credited, as account credit or as a refund. Append-only (the §5.1 trigger pair).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `billing_account_id` | UUID | no | FK `billing_accounts` (the account of the lines, B6) |
| `student_id` | UUID | no | FK `students` |
| `group_enrollment_id` | UUID | yes | FK `group_enrollments`, partial UNIQUE `uq_withdrawals_group_enrollment` |
| `cohort_enrollment_id` | UUID | yes | FK `cohort_enrollments`, partial UNIQUE `uq_withdrawals_cohort_enrollment` |
| `package_id` | UUID | yes | composite FK `(package_id, billing_account_id, student_id)` → `packages`, partial UNIQUE `uq_withdrawals_package` (S2) |
| `outcome` | VARCHAR(16) | no | `refund` \| `account_credit` |
| `amount` | NUMERIC(14,2) | no | `>= 0`: Σ the applied credits of the lines |
| `refund_amount` | NUMERIC(14,2) | no | `>= 0`; `CHECK ((outcome = 'refund') = (refund_amount > 0))` |
| `withdrawn_at` | TIMESTAMPTZ | no | the instant used: now for an active enrollment or a package, the enrollment's `withdrawn_at` when settling an M4 withdrawal (S3) |
| `reason` | VARCHAR(500) | no | trimmed staff text |
| `preview` | JSONB | no | the confirmed preview plus `outcome`, `overrides`, `applied` (per charge) and `automatic_credit_keys` |
| `confirmed_by_user_id` | UUID | no | FK `users` `ON DELETE RESTRICT` |

- `CHECK (num_nonnulls(group_enrollment_id, cohort_enrollment_id, package_id) = 1)`: exactly one subject, at most one withdrawal per subject.
- **Lines (B4):** the fee that pays for the period (its own `group_enrollment`/`cohort_enrollment` charge, or the J5 latest earlier fee with net > 0; never a fee another withdrawal already credited), the posted `tuition_period` charges of a plan linked to the group enrollment whose `service_period_end ≥` the withdrawal's local date (#93 K6), or the package's `package_purchase` charge. **Never** a `session` or `manual` charge: #92 J4 credits group session charges in the M4 hook of the same transaction (shown as `automatic_credits`, never in `amount`).
- **Formula (S1):** per line `credit = net × U / D` (`Money.prorate`). Fee: `D` = the owner's counted sessions (materialized + projected, not `cancelled`/`skipped_closure`) after the fee period's `enrolled_at`, `U` = the same after `withdrawn_at`. Linked tuition period: the same counts inside `[service_date, service_period_end]` (local dates). Package: `U` = remaining units, `D` = `units_granted`. Policies: `open_ended_fee` (an open-ended group fee: `0.00`) → `nothing_delivered` (`D = 0`: the net) → `fully_used` (`U = 0`: `0.00`) → `pro_rata`. A per-line override `0.00..net` is allowed; the reason is required either way.
- **Outcomes (S4):** both post one `withdrawal` adjustment per line with an applied credit > 0 (§3.10); E5 releases the newest allocations above the new net. `account_credit` stops there (the freed money stays account credit, never allocated automatically). `refund` additionally refunds the money freed in this transaction (by #101's credits and #92's J4 credits): one `refunds` row per source payment, oldest `received_on` first, `Σ = refund_amount`. Nothing freed → `409 withdrawal_nothing_to_refund`.
- **Due charges (S6):** a billable unposted fee and the due unposted periods of a linked plan are posted in the same transaction, then credited; afterwards the #92 classification answers `withdrawal_settled` for the period, so no fee is posted later.
- **Packages (S2, S7):** a package withdrawal writes one `refund` units row of `−remaining` (§3.20) plus the `withdrawal` adjustment on the purchase charge; the status becomes `withdrawn`. Expired, cancelled, exhausted or withdrawn → `409 package_not_withdrawable`; a started covered session awaiting its closeout approval → `409 withdrawal_closeout_pending`.

### 3.24 `idempotency_keys` (core, #88)

API idempotency (E6). Not `Audited`. Migration `0059` (#88).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `user_id` | UUID | no | FK `users` `ON DELETE RESTRICT` |
| `endpoint` | TEXT | no | method + route template, e.g. `POST /api/v1/billing/payments`; `CHECK (char_length(endpoint) BETWEEN 1 AND 300)` |
| `key` | TEXT | no | `CHECK (key ~ '^[A-Za-z0-9_-]{16,128}$')` |
| `request_hash` | BYTEA | no | raw SHA-256 over method, concrete path and canonical JSON body; `CHECK (octet_length(request_hash) = 32)` |
| `response_status` | SMALLINT | yes | `BETWEEN 200 AND 299` (only `2xx` is stored); `NULL` only inside the claiming transaction, never committed |
| `response_body` | JSONB | yes | `NULL` for a `204` (and inside the claiming transaction) |
| `expires_at` | TIMESTAMPTZ | no | claim time + 7 days; an expired row counts as absent and is replaced by the next claim (ADR-0006 implementation note) |

`UNIQUE (user_id, endpoint, key)` (`uq_idempotency_keys_user_endpoint_key`); index `ix_idempotency_keys_expires_at`. Rows are deleted by the hourly `idempotency:purge_keys` job.

### 3.25 `number_series` (core, #98)

Gap-free yearly counters (E13).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `code` | TEXT | no | `CHECK (code IN ('R', 'P'))`: `R` (receipts), `P` (payslips, reserved for M7) |
| `year` | SMALLINT | no | the center-local year of the issue instant |
| `last_value` | INTEGER | no | `>= 0` |

`PRIMARY KEY (code, year)`. The row is created with `INSERT … ON CONFLICT DO NOTHING`, then locked `FOR UPDATE` and incremented inside the posting transaction, so a rollback returns the number. Not `Audited` (the receipt is).

**Implemented by #98, migration 0068** (`app/core/numbering_models.NumberSeries`, `app/core/numbering.next_number`): `ck_number_series_code`, `ck_number_series_year_range` (2000–9999), `ck_number_series_last_value_non_negative`; no seed rows. `next_number` runs the `INSERT … ON CONFLICT DO NOTHING` and then `UPDATE … SET last_value = last_value + 1 … RETURNING last_value` (the row lock is the `FOR UPDATE`); a value above 999999 raises `NumberSeriesExhaustedError` (`500`; the payment rolls back too).

### 3.26 View `billing_ledger_entries` (#90)

A read-only `UNION ALL` of the money rows of one account, for the cursor-paginated history (architecture §4.4) and the per-student filter. One row per `charges`, `charge_adjustments`, `payments`, `payment_reversals`, `payment_allocations`, `allocation_releases` and `refunds` row. The cursor is `(occurred_at, entry_id)`. The view stores nothing and is never written; it is defined only in migration 0061 and read through a `table()` construct outside the ORM metadata (`billing.balances.LEDGER_VIEW`). The columns (#90):

| Column | charges | charge_adjustments | payments | payment_reversals | payment_allocations | allocation_releases | refunds |
|---|---|---|---|---|---|---|---|
| `entry_type` | `charge` | `adjustment` | `payment` | `payment_reversal` | `allocation` | `allocation_release` | `refund` |
| `entry_id` | id | id | id | id | id | id | id |
| `billing_account_id` | own | own | own | own | own | via allocation | own |
| `kind` | `kind` | `kind` | `payment_method_code` | NULL | NULL | NULL | `payment_method_code` |
| `student_id` | own | own | NULL | NULL | via charge | via allocation → charge | NULL |
| `charge_id` | id | `charge_id` | NULL | NULL | `charge_id` | via allocation | NULL |
| `payment_id` | NULL | NULL | id | `payment_id` | `payment_id` | via allocation | `payment_id` |
| `amount` | `amount` | `amount` (signed) | `amount` | the payment's `amount` | `amount` | the allocation's `amount` | `amount` |
| `balance_effect` | `+amount` | `+amount` (signed) | `−amount` | `+` payment amount | `0` | `0` | `+amount` |
| `occurred_at` | `posted_at` | `posted_at` | `created_at` | `created_at` | `created_at` | `created_at` | `created_at` |

`balance_effect` is the row's effect on the net balance: Σ `balance_effect` over an account equals its net balance (§4.2, given I3: a payment with refunds is never reversed).

### 3.27 `charge_reminders` (#97)

One row per charge and reminder stage, so the reminder job is idempotent (E11, natural key `(charge_id, stage)`). The in-app notifications to the account's guardians are inserted in the same transaction through `create_notification`, as `{type, params}` ([ADR-0015](adr/0015-server-side-i18n.md)). The `notifications` table itself has no dedup key, and one charge stage notifies several guardians, so the key lives here.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `charge_id` | UUID | no | FK `charges` `RESTRICT` |
| `stage` | VARCHAR(24) | no | `CHECK (stage IN ('due_soon', 'overdue', 'overdue_followup'))`, generated from `billing.reminders.REMINDER_STAGES` |
| `due_date` | DATE | no | the effective due date the stage was computed from |
| `sent_at` | TIMESTAMPTZ | no | |

- **Stages and windows (#97 N1):** with D the charge's **effective** due date (§3.11) and today the center's local date: `due_soon` D−3..D, `overdue` D+1..D+13, `overdue_followup` from D+14. A charge gets only the stage of the window it is in at the run; a missed window is never caught up; a due-date change after a stage was sent never re-sends that stage (the extended charge gets only the stages it hasn't had).
- **Natural key:** `UNIQUE (charge_id, stage)` (`uq_charge_reminders_charge_stage`); the job inserts with `INSERT … ON CONFLICT DO NOTHING RETURNING` and notifies only for the rows inserted now.
- **Not `Audited`** (#97 N4): an operational log like `notifications`; the audit guard refuses `ON CONFLICT DO NOTHING` on audited tables.
- **Preferences (#119 D3, E6):** `billing.payment_reminder` is optional. A guardian with a `notification_opt_outs` row for it gets no notification (`create_notification` returns `None`), and the run's `notifications` count only the ones created. The stage row is still inserted when every recipient muted the type, so that stage is never sent later, even after the guardian turns reminders back on. (`notification_opt_outs` is an administration table, documented in migration `0086` and `_docs/api.md` Notifications.)
- **Notifications (#97 N2, N3):** one `billing.payment_reminder` `{family_id, kind: <stage>, count, open_total, due_on}` per recipient, account and stage per run (`due_on` is the earliest effective due date of those charges). The recipients are the logins of the **active** guardian profiles in the account's family (`family_guardians` → `guardians.is_active` → `guardians.user_id`); students' own logins are not notified. With no recipient nothing is written, so the charge is reconsidered on the next run while it is still in a window. Only charges with open > 0 are reminded; inactive families and students are still reminded while money is open.
- **Immutability:** append-only (§5.1 triggers).
- **Index:** the unique key serves the lookup by charge.

## 4. Invariants and derived values

### 4.1 Invariants

**DB** means a constraint, foreign key or trigger. **App** means checked by the billing service **under the billing account lock** (§6) in the mutating transaction. The "Proven by" issue holds the test; #103 also runs them all over randomized sequences.

| ID | Invariant | Enforced | Proven by |
|---|---|---|---|
| I1 | `net(charge) >= 0` | App | #90, #103 |
| I2 | Σ valid allocations ≤ net, per charge | App | #96 (`test_billing_payments_record.py`, `_credit.py`, `_concurrency.py`), #103 |
| I3 | Σ valid allocations + Σ refunds ≤ payment amount; a reversed payment has no valid allocation; a payment with refunds can't be reversed (`409`) | App (`payment_reversals.payment_id` UNIQUE is DB) | #96 (`test_billing_payments_credit.py`, `_concurrency.py`), #100 (`test_billing_payment_reversals.py`, `test_billing_refunds.py`, `test_billing_corrections_concurrency.py`), #103 |
| I4 | An allocation, its payment and its charge are in the same account; so are reversals, refunds and receipts with their payment | DB (composite FKs) | #90 |
| I5 | An adjustment is in the same account and for the same student as its charge | DB (composite FK) | #90 |
| I6 | Sign and range checks: charge amounts ≥ 0; adjustment ≠ 0; payment, allocation, refund > 0; `due_offset_days` 0–60; package unit signs per kind | DB (`CHECK`) | #90, #94 |
| I7 | `charges.amount = list_amount − discount_amount` | DB (`CHECK`) | #90, #95 |
| I8 | Package remaining units ≥ 0 (and exactly 0 after a #100 cancellation) | App | #94, #100 (`test_billing_package_cancellation.py`, `test_billing_corrections_concurrency.py`), #103 |
| I9 | At most one of {session charge, package consumption} per `(class_session_id, student_id)` | App, **both sides under the account lock** (#94 L3): `post_charge` refuses a `session` charge for a consumed key, and the consumption writes nothing for a charged key (each side also has its DB natural key) | #94, #103 |
| I10 | Receipt numbers are gap-free and unique per series and year | DB (`receipts.number` UNIQUE) + App (`number_series` row lock) | #98, #103 |
| I11 | Posted rows never change | DB (triggers, §5) | #90 (and each issue that creates an append-only table) |
| I12 | Withdrawals (#101): at most one per enrollment or package; `withdrawal` adjustments only on `group_enrollment`, `cohort_enrollment`, `tuition_period` or `package_purchase` charges, never `session`; Σ refunds with a `withdrawal_id` = `withdrawals.refund_amount`; a `refund` units row ⇒ remaining 0 and status `withdrawn`; no enrollment fee posted for a settled period | DB (partial UNIQUEs, `refund` CHECKs) + App (the workflow, under the account lock; #92 `withdrawal_settled`) | #101 (`test_billing_withdrawals_*.py`), #103 |

`apps/api/tests/integration/billing_invariants/` (#103) runs every invariant above after every step of randomized operation sequences, races and job reruns; its check ids in `checks.py` match the I numbers (`_docs/testing-guidelines.md`, "The billing invariant suite").

### 4.2 Derived values

Nothing below is stored. A verified snapshot is allowed only for reporting (ISSUE-F3).

- `net(charge) = amount + Σ adjustments`.
- `open(charge) = net − Σ valid allocations`.
- **Outstanding** (account) = Σ `open` over the account's charges.
- **Outstanding per student** = Σ `open` over the account's charges for that student. Account credit is account-level only and is not split per student.
- **Account credit** = Σ over payments that aren't reversed of (`amount − Σ valid allocations − Σ refunds`).
- **Net balance** = outstanding − account credit.
- **Charge status:**
  - `void`: net = 0 and no allocation;
  - `paid`: open = 0 and not `void`;
  - `overdue`: open > 0 and the effective due date (§3.11) is before today in the center's local date;
  - `partially_paid`: open > 0, Σ valid allocations > 0 and not overdue;
  - `pending`: open > 0, no valid allocation and not overdue.
- **Package remaining units** = Σ `package_credit_transactions.units` for the package. A package can cover a session when remaining > 0, it has no `expire` row and `purchased_on ≤` the **session's local date** `≤ expires_on` (#94 L2: by the service date, not by today; the expiry job runs for `expires_on <` the center's today). Several packages cover first-expiring-first with the L4 plan ("Prepaid packages (#94)").

Spec W4.4 "posted charges/adjustments less valid allocations and refunds" is read with *refunds* as the credit-side corrections (negative adjustments) (ADR-0006).

## 5. Immutability mechanism

### 5.1 Append-only tables

Every append-only table gets the migration 0004 pattern (`apps/api/migrations/versions/0004_audit_events.py`), created in the same migration as the table:

- a trigger function `<table>_append_only()` that raises `RAISE EXCEPTION '<table> is append-only' USING ERRCODE = 'insufficient_privilege'` (SQLSTATE **42501**);
- `CREATE TRIGGER <table>_no_update_delete BEFORE UPDATE OR DELETE ON <table> FOR EACH ROW …`;
- `CREATE TRIGGER <table>_no_truncate BEFORE TRUNCATE ON <table> FOR EACH STATEMENT …`.

Privileges alone can't do it: the local, test and CI database role is a superuser. #165 (M10-10) adds the least-privilege role later; the triggers stay. A migration may disable a trigger only in its **own downgrade**, for that one statement.

### 5.2 Guarded tables

`price_versions` (and `custom_prices` from #95) get a `BEFORE UPDATE OR DELETE` row trigger that raises 42501 when the row is referenced by any `charges` or `packages` row, plus the `BEFORE TRUNCATE` trigger. An unreferenced version may still be corrected or removed.

**`custom_prices` (#95 H7):** `custom_prices_guard()` (triggers `custom_prices_no_referenced_change`, `custom_prices_no_truncate`): when any `charges.custom_price_id` references the row, `DELETE` is refused and `UPDATE` is refused **unless only `expires_on`, `version` and `updated_at` change** (ending a custom price is allowed); `TRUNCATE` is always refused. The `RESTRICT` foreign key is the concurrent backstop.

**`cancellation_billing_rules` (#99 Q6):** `cancellation_billing_rules_guard()` (triggers `cancellation_billing_rules_no_change`, `BEFORE UPDATE OR DELETE FOR EACH ROW`, and `cancellation_billing_rules_no_truncate`) refuses every `UPDATE`, the `DELETE` of a version with `effective_from <= now()` and every `TRUNCATE` (42501). Only an upcoming version can be deleted; a decision never references one (decisions reference versions in force at a past instant), and the `RESTRICT` foreign key is the backstop.

**Staged predicate (#89 E2).** `charges` and `packages` don't exist when `price_versions` is created, so the trigger function `price_versions_guard()` (triggers `price_versions_no_referenced_change` and `price_versions_no_truncate`) asks `price_version_is_referenced(version_id uuid) RETURNS boolean` (`LANGUAGE sql VOLATILE`). Migration 0060 (#89) creates it as `SELECT false`. #90's migration replaces it (`CREATE OR REPLACE`) with an `EXISTS` on `charges.price_version_id`, and #94's (0066) adds `OR EXISTS` on `packages.price_version_id`; each downgrade restores the previous body. The `RESTRICT` foreign keys from `charges` and `packages` stay the concurrent backstop.

## 6. Concurrency

- **Lock order** for every financial mutation:
  1. the calling workflow's own scheduling or courses locks (session, group, cohort, per-student advisory locks; ADR-0005), if any;
  2. `billing_accounts` row `FOR UPDATE`, always the **last** business lock;
  3. `number_series` row `FOR UPDATE`, only when a receipt is issued (#98: confirmed; the receipt's snapshot inputs are read before it, so it is held for the insert and until commit).

  The App invariants of §4.1 are checked after step 2.
- **Payments (#96):** the `idempotency_keys` row → the family's account (created if missing) → the account row `FOR UPDATE` → the charges' `open` amounts and the payment remainders, read under it → the payment and allocation rows → (#98) the receipt's snapshot reads → `number_series` → the receipt row. A credit allocation takes the same order without creating an account (and issues no receipt). Two payments, credit allocations or adjustments (`post_adjustment`) on one account serialize on the account row; on different accounts they don't wait for each other's account, but two **payments** in the same year serialize on the receipt's `number_series` row from the receipt to the commit (gap-free numbering, E13; no deadlock: the series row is always the last lock).
- **Corrections, reversals and refunds (#100):** the `idempotency_keys` row → (a manual charge: the family's account, created if missing) → the account row `FOR UPDATE` (the last lock) → everything the rule depends on read under it (reversals, refunds, valid allocations, the payment remainder, the package's units and its purchase charge's net) → the rows. A package cancellation and `consume_session_credit` on the same package serialize on that account row (I8), as do a reversal and a credit allocation drawing on the payment (I3). Corrections on different accounts don't wait for each other.
- **Due dates (#97):** the offset `PATCH` and a due-date change take the account row `FOR UPDATE` (a racing `post_charge` sees the old or the new offset, never a torn value). The reminder job's Unit of Work per account: `billing_settings` `FOR SHARE` (billing off stops the run) → the account row **`FOR SHARE`** (`billing.service.share_lock_billing_account`; it waits for every `FOR UPDATE` writer: payments, adjustments, due-date changes, postings) → the open amounts and effective due dates re-read under it → `charge_reminders` `ON CONFLICT DO NOTHING` → the notifications, committed together.
- **Withdrawals (#101):** the `idempotency_keys` row → for an enrollment, the M4 sequence (students → zone `FOR SHARE` → group/cohort `FOR UPDATE` → producing series `FOR UPDATE` → enrollment `FOR UPDATE`; `lock_group_enrollment_for_settlement` / `cohort_enrollment.lock_for_settlement`), the preview recomputed and its fingerprint checked → the M4 withdrawal of an active enrollment (sessions `FOR UPDATE`; the #92 hook takes the account) → `billing_settings` `FOR SHARE` → `billing_accounts` `FOR UPDATE` (re-entrant, **last**) → the due fee/tuition posted, the real lines and releases checked against the prediction, then the rows. A package takes only the account lock. The #92 and #93 sweepers take the group/cohort row `FOR SHARE` first, so they wait for a confirm (and then see `withdrawal_settled` / `enrollment_ended`); `consume_session_credit` and `cancel_package` serialize with a package withdrawal on the account row.
- **API requests:** the `idempotency_keys` row is inserted first in the business transaction, before any lock. A concurrent duplicate blocks on the unique index until the first commits, then reads the stored response (E6).
- **Jobs and sweepers:** insert on the natural keys and treat a conflict as success. On `Audited` tables (every ledger table) that is not `INSERT … ON CONFLICT DO NOTHING`, which the audit guard refuses: the posting primitives (`post_charge`, `post_adjustment`, `ensure_billing_account`; #90 E4) check the key under the account lock and insert through the ORM in a savepoint, and a violation of a named natural-key index rolls the savepoint back and returns the existing row (`created = False`). The non-audited `number_series` keeps `ON CONFLICT DO NOTHING`; periodic tasks use a Procrastinate `queueing_lock` (ADR-0003). They never use `idempotency_keys`. The jobs: `billing:post_session_charges` (every 15 minutes, #91; in `app/workflows/session_charge_tasks.py`, logic in `app/workflows/session_charges.py`), `billing:post_enrollment_charges` (every 15 minutes, #92; in `app/workflows/enrollment_charge_tasks.py`, logic in `app/workflows/enrollment_charges.py`), `billing:post_tuition_charges` (daily at 07:30 UTC, #93; in `app/workflows/tuition_charge_tasks.py`, logic in `app/workflows/tuition_charges.py`), the daily package expiry `billing:expire_packages` (07:40 UTC, #94; in `app/workflows/package_expiry_tasks.py`, logic in `app/workflows/package_expiry.py`), the reminder job `billing:send_charge_reminders` (daily at 15:00 UTC, #97; in `app/workflows/charge_reminder_tasks.py`, logic in `app/workflows/charge_reminders.py`; natural key `charge_reminders (charge_id, stage)`) and `idempotency:purge_keys` (hourly, #88).
- **Session charges (#91):** one Unit of Work per candidate `(class_session_id, student_id)`, in this order: the session row `FOR SHARE` (`scheduling.service.lock_session_for_billing`; every scheduling writer that changes a session's state, time, duration or participants takes it `FOR UPDATE`; the participants are read after the lock) → `billing_settings` `FOR SHARE` → `billing_accounts` `FOR UPDATE` (in `post_charge`, last). A group session re-checks the J2 rule after the account lock (#92 E3).
- **Enrollment charges (#92):** the sweeper's Unit of Work per candidate period takes the group row (`scheduling.service.lock_group_for_billing`) or the cohort row (`courses.service.lock_cohort_for_billing`) `FOR SHARE` → `billing_settings` `FOR SHARE` → `billing_accounts` `FOR UPDATE` (last), and re-reads the J2/J5 facts under the account lock. The hooks run after the M4 locks (students → zone → group or cohort `FOR UPDATE` → series → sessions) → `billing_settings` `FOR SHARE` (enrollments only) → `billing_accounts` `FOR UPDATE`. The M4 writers take the group or cohort row `FOR UPDATE`, so a sweeper step and an enrollment change serialize.
- **Tuition charges (#93):** the job's Unit of Work per plan takes (linked) the group row `FOR SHARE` (`scheduling.service.lock_group_for_billing`) → the plan row `FOR SHARE` → `billing_settings` `FOR SHARE` → `billing_accounts` `FOR UPDATE` (in `post_charge`, last). The plan writes (`PATCH`, `DELETE`, pauses, resume) take (linked) the group `FOR SHARE` then the plan row `FOR UPDATE`, so a pause waits for a posting (or the posting for the pause). A linked plan create takes the group `FOR SHARE` → the account `FOR UPDATE`, then checks the K6 refusals; the #91 session step and the #92 fee step re-check `tuition_priced` after the same account lock.
- **Packages (#94):** the account row `FOR UPDATE` is the last lock everywhere. Closeout approval: closeout `FOR UPDATE` → session `FOR SHARE` → account (in `consume_session_credit`) → then the approval request row (decided after the hook; no billing path takes it). Session sweeper: session `FOR SHARE` → `billing_settings` `FOR SHARE` → account (`post_charge` re-checks the consumption, I9). Purchase: the idempotency row → the product `FOR SHARE` → account. Expiry job: session `FOR SHARE` (fallback items) → account (consumption and `expire`, remaining recomputed under it, so I8 holds). No cycle.
- **Schedule reconciliation (#99):** the job `billing:reconcile_session_charges` (every 15 minutes; in `app/workflows/schedule_reconciliation_tasks.py`, logic in `app/workflows/schedule_reconciliation.py`; also queued after commit by the session cancel, session `PATCH`, series edit and cancellation, change-request approval and billing settings `PATCH` routes and at the end of `scheduling:materialize_series`) takes, per candidate `(class_session_id, student_id)` in its own Unit of Work: the session row `FOR SHARE` (`lock_session_for_billing`; kind, state and participant re-checked) → `billing_settings` `FOR SHARE` → the key's charge's account `FOR UPDATE` (last; net, closure total, billed amount and minutes read under it), or, without a charge, the account inside `consume_session_credit` or `post_charge` (last). One session per Unit of Work: no deadlock with scheduling writers (sessions `FOR UPDATE`, never an account) or the #92 hooks (sessions → account). Natural keys: `uq_charge_adjustments_source_key`, `uq_session_cancellation_decisions_key`, `uq_charge_duration_repricings_charge_version` (a concurrent worker's row → savepoint rolled back, done).
- **Scheduling transactions** never post charges: a billing failure can't roll back a schedule change (E1). Enrollment charges are the exception: they are posted in the M4 hooks' Unit of Work (`apply_enrollment_change`, `apply_cohort_enrollment_change`), which already holds the enrollment locks.

## 7. Indexes

Beyond primary keys, unique keys and natural keys, for the access paths of spec §16 ("family, … billing status"):

- `charges (billing_account_id, posted_at, id)`: account history;
- `charges (student_id, service_date)`: per-student breakdown;
- `charges (billing_account_id, due_date)`: overdue and reminders;
- `charges (price_version_id) WHERE price_version_id IS NOT NULL` (`ix_charges_price_version_id`, #90): the price-version predicate (§5.2) and the `RESTRICT` check when an unreferenced version is deleted;
- `charges (custom_price_id) WHERE custom_price_id IS NOT NULL` (`ix_charges_custom_price_id`, #95): the custom-price guard and the `expires_on` check;
- `custom_prices (student_id, service_price_id)` and `(family_id, service_price_id)` (partial; #95), besides the exclusion constraints' gist indexes;
- `discount_assignments (student_id)` and `(family_id)` (partial; #95): the per-charge discount lookup;
- `charge_discounts (charge_id, position)` (the unique key);
- `charge_adjustments (charge_id)`;
- `charge_due_date_changes (charge_id, created_at)`;
- `charge_reminders (charge_id, stage)` (the unique key);
- `payments (billing_account_id, received_on, id)`;
- `payment_allocations (charge_id)` and `payment_allocations (payment_id)`;
- `refunds (payment_id)`;
- `packages (billing_account_id, student_id, expires_on)`: first-expiring-first lookup;
- `packages (price_version_id)`: the price-version predicate (§5.2) and the `RESTRICT` check;
- `package_credit_transactions (package_id)`;
- `package_credit_transactions (class_session_id, student_id) WHERE kind = 'consume'`: the I9 checks and the sweeper's anti-join;
- `receipts (billing_account_id, issued_at)`;
- `charge_duration_repricings (charge_id, created_at DESC, id DESC)`: the billed minutes (#99);
- `price_versions (service_price_id, effective_from)` (the unique key);
- `idempotency_keys (expires_at)`: purge.

## 8. Migration strategy

- One Alembic migration per implementing issue, migration-first, expand-and-contract, so the previous image keeps working (architecture §5).
- Triggers and their functions are created in the same migration as their table, and dropped in its downgrade.
- Reference seeds (`payment_methods`, the first `cancellation_billing_rules` row, `billing_settings` with `billing_starts_on = NULL`) are inserted by the creating migration. Permissions are seeded by the first issue that uses them (ADR-0006).
- **No data backfill of charges:** history before `billing_starts_on` is never charged (E1). Receipts are not backfilled (E13).

## 9. Relationships

```mermaid
erDiagram
    families ||--o| billing_accounts : "has (family_id unique)"
    families ||--o{ family_guardians : ""
    families ||--o{ family_students : ""
    billing_accounts ||--o{ charges : ""
    billing_accounts ||--o{ payments : ""
    billing_accounts ||--o{ packages : ""
    billing_accounts ||--o{ tuition_plans : ""
    billing_accounts ||--o{ receipts : ""
    billing_accounts ||--o{ withdrawals : ""
    students ||--o{ charges : "covered student"
    students ||--o{ packages : ""
    charges ||--o{ charge_adjustments : ""
    charges ||--o{ charge_due_date_changes : ""
    charges ||--o{ charge_reminders : ""
    charges ||--o{ charge_discounts : ""
    charges ||--o{ payment_allocations : ""
    payments ||--o{ payment_allocations : ""
    payments ||--o| payment_reversals : ""
    payments ||--o{ refunds : ""
    payments ||--o| receipts : ""
    payment_allocations ||--o| allocation_releases : ""
    service_prices ||--o{ price_versions : ""
    service_prices ||--o| package_products : ""
    price_versions ||--o{ charges : "price used"
    service_prices ||--o{ custom_prices : ""
    students ||--o{ custom_prices : "student price"
    families ||--o{ custom_prices : "family price"
    custom_prices ||--o{ charges : "custom price used"
    discounts ||--o{ discount_assignments : ""
    students ||--o{ discount_assignments : ""
    families ||--o{ discount_assignments : ""
    discounts ||--o{ charge_discounts : ""
    package_products ||--o{ packages : ""
    packages ||--o{ package_credit_transactions : ""
    packages ||--o| charges : "purchase charge"
    tuition_plans ||--o{ charges : "per period"
    class_sessions ||--o{ charges : "session charge"
    class_sessions ||--o{ package_credit_transactions : "consumption"
    group_enrollments ||--o| charges : "enrollment charge"
    cohort_enrollments ||--o| charges : "enrollment charge"
    course_cohorts ||--o{ service_prices : "scope"
    class_groups ||--o{ service_prices : "scope"
    subjects ||--o{ service_prices : ""
    education_levels ||--o{ service_prices : ""
```

## 10. M6 issue coverage

Each M6 issue, the ADR-0006 / ADR-0015 decision items it relies on, and the tables it creates or uses. QA checks each issue's acceptance criteria against its row.

| Issue | Decision items | Tables |
|---|---|---|
| #87 M6-02 Money | E5 (`NUMERIC(14,2)`, half-up per line, no currency), ADR-0004 | none (value object) |
| #88 M6-03 Idempotency | E6 | `idempotency_keys` |
| #89 M6-04 Price catalog | E8 (`billing.manage`), E9 (key, versions, lookup, guarded trigger) | `service_prices`, `price_versions` |
| #90 M6-05 Account and ledger core | E4, E5 (rows, triggers, balances, statuses, the release on a credit), E11 (`due_date`, `due_offset_days`); `billing.view` and the read routes in #364 (M6-05b) | `billing_accounts`, `payment_methods` (table, seed), `charges`, `charge_adjustments`, `payments`, `payment_reversals`, `payment_allocations`, `allocation_releases`, `refunds`, view `billing_ledger_entries` |
| #91 M6-06 Session charges | E1 (sweeper, floor, unbilled list, `apply_confirmation_charge` removed), E2 (`scheduled_minutes`), E9 (lookup), E10 (covered by a package → no charge), E11 (session due date) | `billing_settings`, `charges` |
| #92 M6-07 Enrollment charges | E1 (M4 hooks + sweeper `billing:post_enrollment_charges`, group per-session charges in the #91 sweeper), E9 (`full_course`, late proration, `enrollment_removal`), E11 | `charges` (`proration_remaining`, `proration_total`), `charge_adjustments` |
| #93 M6-08 Tuition plans | E1 (daily job, local periods), E9 (`per_period`, group per-student), E11 (`period_start + due_offset_days`) | `tuition_plans`, `tuition_plan_pauses`, `service_prices` (`period_frequency`), `charges` (`tuition_plan_id`) |
| #94 M6-09 Packages | E1 (purchase; the floor gates only session charges, L5), E6 (purchase key), E7 (`expire` rows), E10 (coverage by service date, first-expiring-first, consumption at approval revision 1, the L1 expiry fallback), E11 (purchase date) | `package_products`, `packages`, `package_credit_transactions` (no `withdrawal_id`/`refund` yet, L10), `service_prices` (`package` items), `charges` (`package_id`) |
| #95 M6-10 Discounts, custom prices | E8 (`billing.manage`), E9 (precedence step 1, stacking, discount snapshot equation) | `custom_prices`, `discounts`, `discount_assignments`, `charge_discounts`, `charges` (`custom_price_id`) |
| #96 M6-11 Payments, allocations | E5 (allocations, account credit, I2/I3), E6, E8 (`payment.record`, seeded by `make seed`; no reauthentication), E12 (method from the seeded table); no migration (the tables exist since 0061) | `payment_methods`, `payments`, `payment_allocations` |
| #97 M6-12 Due dates, reminders | E11 (rules, family override, `charge_due_date_changes`, local-date overdue, `(charge_id, stage)`); migration 0067 | `charge_due_date_changes`, `charge_reminders`, `billing_accounts` (`due_offset_days` edits) |
| #98 M6-13 Receipts | E13 (`number_series`, snapshot incl. template version, one per payment), E14, E15; migration 0068; P1–P9 (a) (issued in `record_payment`, recipient language, snapshot v1, versioned templates, family-scoped download) | `receipts`, `number_series` (PDF and i18n infrastructure: #383, M6-13a) |
| #99 M6-14 Reconciliation | E2 (delta adjustment, Q2 billed base and clamp), E3 (rules, outcomes, closures and their restoration, Q3), E10 (`charge` debits units, Q9); Q1 job over committed schedule state; migration 0069 | `cancellation_billing_rules`, `session_cancellation_decisions`, `charge_duration_repricings`, `charge_adjustments` (`cancellation_credit`, `closure_credit`, `duration_change`) |
| #100 M6-15 Reversals, refunds | E5 (reversal releases allocations, refunds from the remainder, I3), E6, E8 (`finance.refund.approve`, `finance.refund_or_reversal`, `finance.correction` for every manual adjustment, R3), E10 (package-purchase cancellation, R6), E11 (manual charges, R7), E13 (no receipt change, R9); migration 0070 (`package_cancellation` kind) | `charges` (`manual`), `charge_adjustments` (`manual`, `package_cancellation`), `payment_reversals`, `allocation_releases`, `refunds`, `package_credit_transactions` (`reversal`) |
| #101 M6-16 Withdrawals | E8 (permission combination, S5: reauthentication on both outcomes), E9 (flat and course fees only here, S1 pro rata by unused sessions), E10 (unused units, S2 package subject); S3 settle-only, S4 refund = freed money, S6 due charges posted then credited (`withdrawal_settled`), S7 package rules; migration 0071 (`withdrawals`, `refunds.withdrawal_id`, `package_credit_transactions.withdrawal_id` + `refund`) | `withdrawals`, `charge_adjustments` (`withdrawal`), `refunds`, `package_credit_transactions` (`refund`) |
| #102 M6-17 Family portal | E4 (`family_guardians`, per-child filter, no student or tutor access), E13 (receipt download) | reads only |
| #103 M6-18 Invariant suite | E5, E6, E10, E13; invariants I1–I11 (§4.1) | all of the above |

## 11. Payroll tables

The schema behind [ADR-0017](adr/0017-payroll-and-expenses.md), which is **Proposed** (orchestrator-recommended, pending owner review). Decision items in §11–§19 are **E1–E18 of issue #104**, not the E items of #86. Nothing below exists yet: each table is created by the issue named here.

The §2 conventions apply, with "billing" read as "payroll" and "finance". The exceptions are stated per table. Rows are immutable through the classes of §2 and §14.

| Table | Module | Creating issue | Class |
|---|---|---|---|
| `payroll_settings` | payroll | #106 | mutable config (singleton) |
| `tutor_pay_terms` | payroll | #105 | guarded |
| `compensation_rates` | payroll | #105 | guarded |
| `payroll_periods` | payroll | #106 | status-guarded |
| `session_pay_rules` | payroll | #441 | guarded, effective-dated |
| `payroll_lines` | payroll | #107 (`salary` #108, `pay_item` #109, `adjustment` #111) | locked with the period |
| `pay_item_types` | payroll | #109 | mutable reference |
| `pay_items` | payroll | #109 | guarded |
| `payroll_adjustments` | payroll | #111 | append-only |
| `payroll_payments` | payroll | #112 | append-only |
| `payroll_payment_reversals` | payroll | #112 | append-only |
| `payslips` | payroll | #113 | append-only |
| column `class_sessions.makeup_of_session_id` | scheduling | #423 (M7-04a) | nullable self-reference |

The spec's candidate "TeachingHours" entity is the existing `session_closeout_approvals` (E2). There is no `teaching_hours` table.

### 11.1 `payroll_settings` (#106)

A singleton (the `billing_settings` pattern, §3.2). Audited and versioned. Created by #106, whose migration inserts the one row, because the floor and the anchor drive period generation; #108 is the first reader of the basis. Managed with `payroll.manage`.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `singleton` | BOOLEAN | no | `CHECK (singleton)`, UNIQUE |
| `payroll_starts_on` | DATE | yes | `NULL`: payroll is off and no period is opened; afterwards work dated before it is never paid (E2) |
| `biweekly_anchor_on` | DATE | no | a Monday (`CHECK (EXTRACT(ISODOW FROM biweekly_anchor_on) = 1)`); default `2026-01-05` (E6) |
| `salary_proration_basis` | VARCHAR(20) | no | `calendar_days` \| `weekdays`; default `calendar_days` (E7) |
| `version` | INTEGER | no | |

- **Lock (#106 E6):** once any `payroll_periods` row exists, `payroll_starts_on` can't change, `null` included (`409 payroll_start_locked`). Once a `biweekly` period exists, `biweekly_anchor_on` can't (`409 biweekly_anchor_locked`). `salary_proration_basis` stays editable. An earlier floor would create days no period can cover, a later one would strand existing periods, and a new anchor would shift the calendar under existing rows. So a wrong floor can't be fixed in the app after the job has run.
- **No job (#106 E7):** a `PATCH` queues nothing. The daily run is the only trigger.

### 11.2 `tutor_pay_terms` (#105)

What applies to a tutor as a whole (E1). Audited. **Guarded** (§14.2).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `tutor_user_id` | UUID | no | FK `users` RESTRICT |
| `effective_from` | DATE | no | runs until the next version's `effective_from` |
| `pay_frequency` | VARCHAR(10) | yes | `weekly` \| `biweekly` \| `monthly`; `NULL` = not on payroll from this date (the tutor has left) |
| `salary_amount` | NUMERIC(14,2) | yes | `> 0`; per full pay period of this row's frequency; `NULL` when there is no salary |

- **Unique key:** `UNIQUE (tutor_user_id, effective_from)` (invariant P1).
- **CHECK:** `pay_frequency IS NOT NULL OR salary_amount IS NULL`: no salary without a frequency.
- **Refusals (#106 E3, E4):** the terms insert and delete evaluate the terms as they would be after the write, under the advisory lock of §15. A tutor with no periods has neither refusal.
  - `422 effective_from_inside_generated_period`: on every date inside a generated period, the frequency in effect must be `NULL` or that period's. So a stop passes (#105 E7), a same-frequency version passes (dated anywhere, also inside a finalized period, #105 E4), a return with the period's frequency passes, a return or change with another frequency inside a period is refused, and so is a delete that would expose one.
  - `422 effective_from_before_first_period`: no date on or after `payroll_starts_on` and before the tutor's first period may have a non-`NULL` frequency. No period could ever cover it, so that work would be silently unpaid.
- **Audit:** `salary_amount` is masked (`__audit_mask__`), because Administrators hold `audit.view` and see no pay (#105 E2).

### 11.3 `compensation_rates` (#105)

Tutor-specific rates per class kind (E1). Audited. **Guarded** (§14.2).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `tutor_user_id` | UUID | no | FK `users` RESTRICT |
| `class_kind` | VARCHAR(20) | no | `individual` \| `group` \| `course` (the `class_sessions.kind` values) |
| `effective_from` | DATE | no | runs until the next version's `effective_from` |
| `rate_model` | VARCHAR(20) | yes | `per_hour` \| `per_session` \| `per_course`; `per_course` only for `course` |
| `rate` | NUMERIC(14,4) | yes | `>= 0` |

- **Unique key:** `UNIQUE (tutor_user_id, class_kind, effective_from)`, the no-overlap constraint of #105 (invariant P1).
- **CHECKs:** `(rate_model IS NULL) = (rate IS NULL)` (both `NULL` = "no rate from this date"); `rate_model <> 'per_course' OR class_kind = 'course'`.
- **Lookup:** the version with the greatest `effective_from <=` the service date (W3.4). A `per_course` rate uses the cohort's first session's local date instead (E3).
- **Audit:** `rate` is masked (`__audit_mask__`), as in §11.2 (#105 E2).
- **Models (E1):** rates only → hourly, per-session or per-course; `salary_amount` only → fixed salary; both → salary plus extras. There is no default rate and no rate per subject or level.

### 11.4 `payroll_periods` (#106)

One row per tutor per period (E6). Not `Audited`: the job opens them and the status moves carry named audit events (`payroll.period_prepared`, `_returned`, `_finalized`, E9). **Status-guarded** (§14.3).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `tutor_user_id` | UUID | no | FK `users` RESTRICT |
| `starts_on` | DATE | no | inclusive |
| `ends_on` | DATE | no | inclusive; `CHECK (ends_on >= starts_on)` |
| `pay_frequency` | VARCHAR(10) | no | the calendar this period follows; a stub keeps its natural calendar for E7's `natural_days` |
| `is_first_period` | BOOLEAN | no | the tutor's first period |
| `previous_ends_on` | DATE | yes | generated: `CASE WHEN is_first_period THEN NULL ELSE starts_on - 1 END` (stored) |
| `status` | VARCHAR(10) | no | `open` \| `prepared` \| `finalized` |
| `prepared_at`, `prepared_by_user_id` | TIMESTAMPTZ, UUID | yes | set iff `prepared` or `finalized`; cleared by a return |
| `total_amount` | NUMERIC(14,2) | yes | set iff `finalized`; `>= 0` (P5) |
| `finalized_at`, `finalized_by_user_id` | TIMESTAMPTZ, UUID | yes | set iff `finalized` |
| `returned_at`, `returned_by_user_id`, `return_reason` | TIMESTAMPTZ, UUID, VARCHAR(1000) | yes | #110: all set or all `NULL`, set only while `open` (`ck_payroll_periods_returned_fields`); the next `prepare` clears them |

- **The return reason (#110 E7):** on the period, because `approval_requests.reason` is written only by a decision that needs `payroll.finalize`, which Finance doesn't hold, and free text can state an amount, so it stays out of the audit log.
- **No overlaps (P2):** `EXCLUDE USING gist (tutor_user_id WITH =, daterange(starts_on, ends_on, '[]') WITH &&)`. `btree_gist` is already enabled (#38).
- **No gaps (P3):** composite FK `(tutor_user_id, previous_ends_on)` → `UNIQUE (tutor_user_id, ends_on)`, plus a partial unique index on `(tutor_user_id) WHERE is_first_period`. #106 proves it in its migration test. If PostgreSQL refuses the generated foreign key column, #106 uses a plain column with a `CHECK`, and the guarantee stays database-enforced (E6).
- **Natural key:** `UNIQUE (tutor_user_id, starts_on)`, used by `payroll:open_periods`.
- **Composite target:** `UNIQUE (id, tutor_user_id)`, the target of the line, adjustment and payslip foreign keys (P6).
- **Boundaries (E6):** weekly Monday to Sunday; biweekly 14 days from `biweekly_anchor_on`; monthly the calendar month. A stub ends the day before the terms stop or the frequency changes.
- **A generated period's dates never change (#106 E2):** a stub exists only when the stop or change is already recorded when the period is generated. Otherwise the period keeps its natural end, and the days after a stop carry no terms, so #108 pays no salary for them. A leaver's last period can't be prepared until its natural end.
- **First period:** starts on the first covered date on or after `payroll_starts_on`, so terms that began before the floor start at the floor (reading of E6).
- **First covered date (#106 E5):** every period's end comes from the first date on or after its start with a non-`NULL` frequency. The natural end is that of the calendar period containing that date, and `pay_frequency` is the frequency on that date. The end is that natural end, or the day before the next version with another frequency (`NULL` included), whichever comes first. A version with the same frequency doesn't split a period. A period opens once its first covered date is on or before the center's today.
- **`natural_days` (#106 E5):** defined by date, `natural_bounds(frequency, day, anchor)`. #108 uses it for each piece, split at natural boundaries, never `starts_on`/`ends_on`, because a period can be a stub or span several natural periods.
- **After a tutor has left (reading of E6, for #106):** "from the day after the previous one" also holds for a period opened later, so P3 keeps holding.
  - A returning tutor's next period starts the day after their last one and ends per the first covered date. A tutor who left in March and returns weekly in November has one long period from April to the first Sunday on or after the return. Work inside the terms gap stays `no_pay_terms`.
  - A period opened only for an adjustment (E11) uses the last non-null `pay_frequency`.
- **Who gets periods (#106 E8):** tutors in `tutor_pay_terms`, whatever their role or state. A stop row ends them; archiving doesn't.
- **Inbox:** the `payroll.period` request uses `administration.service.open_approval_request`, because `approval_requests` holds one row per `(request_type, subject_id)` and a returned period is prepared again.

### 11.5 `session_pay_rules` (#441)

The configurable late-cancellation, no-show and makeup payments (E4). One row per `(event, effective_from)`, center-wide. Audited. **Guarded by time** (§14.2): the `cancellation_billing_rules` shape.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `event` | VARCHAR(20) | no | `late_cancellation` \| `no_show` \| `makeup` |
| `effective_from` | DATE | no | the rule in effect on the **service date** applies |
| `mode` | VARCHAR(10) | no | `none` \| `percent` \| `fixed` |
| `percent` | NUMERIC(14,4) | yes | `0` to `1000`, a cap that keeps `Rate.to_money` in range; set iff `mode = 'percent'` |
| `amount` | NUMERIC(14,2) | yes | `>= 0`; set iff `mode = 'fixed'` |
| `created_by_user_id` | UUID | yes | FK `users` RESTRICT; `NULL` for the seeded rows |

- **Unique key:** `UNIQUE (event, effective_from)`.
- **Dates (#107 E9):** a new version takes the center's today or later, and later than every version of its event. So no rule reaches a prepared or finalized period, and a rule change never needs an adjustment. Past work can't be repriced by a rule; use a one-time item.
- **Seed (migration `0075`), effective `2000-01-01`:** `late_cancellation` `none`, `no_show` `percent` 100, `makeup` `percent` 100. The owner should review these defaults (ADR-0017).
- **Detection and base (E4):**
  - `late_cancellation`: `cancelled`, `initiated_by = family`, classification `late` (`cancellation_classification`); base: the ordinary line on `scheduled_minutes`.
  - `no_show`: the current revision's `attendance` has every participant `absent`, or a future cancellation classified `no_show`; base: the ordinary line on `approved_minutes`.
  - `makeup`: `class_sessions.makeup_of_session_id IS NOT NULL`; base: the ordinary line.
- **`none` (detail for #107):** a cancellation under `none` writes no line. An approved closeout under `none` writes a `0.00` line of its kind, so the anti-joins of §15 see it as handled.
- **A makeup that is also a no-show (#107 E3):** `no_show` wins, because it describes what happened in the session and `makeup` why it was booked. §11.6 allows one session-sourced line per session.
- **The makeup chain (#107 E4):** pay is per session worked. The `makeup` rule applies to each session with `makeup_of_session_id` and a current revision, by itself; nothing walks the chain, reads the target or re-checks the deadline.
- **Salary coverage (#107 E5):** with salary terms and no rate for the kind on the service date, every approved closeout is a `0.00` `session` line with `session_pay_rule_id` `NULL`, whatever its event, and a cancellation has no line.
- **One basis (#107 E6):** every path, `fixed` included, first resolves terms, then rate, then coverage on the service date. With no basis there is no line.
- **Base (#107 E2):** a `percent` is taken of the exact value, never a rounded line: `Rate.to_money(numerator, denominator, percent=)` rounds once.

### 11.6 `payroll_lines` (#107)

One row per source (E9). Not `Audited`: working data until finalized. **Locked with the period** (§14.3). #107 creates the table with the four session-sourced kinds and without `pay_item_id` and `payroll_adjustment_id`; #108 adds `salary` with its columns, #109 adds `pay_item`, and #111 adds its kind, columns and indexes and widens `kind` and the source CHECK. `recalculate` replaces only the kinds it rebuilds (`REBUILT_KINDS`, #107 E7), the session kinds, `salary` and `pay_item`, so `adjustment` lines survive: written once with their adjustment, never rebuilt, outside drift and inside the total (#110 D3, D4; #111 A4).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `payroll_period_id` | UUID | no | composite FK, below |
| `tutor_user_id` | UUID | no | composite FK, below |
| `kind` | VARCHAR(20) | no | `session` \| `late_cancellation` \| `no_show` \| `makeup` \| `salary` \| `pay_item` \| `adjustment` |
| `amount` | NUMERIC(14,2) | no | rounded half-up once per line (ADR-0004); sign rules below |
| `class_session_id` | UUID | yes | FK `class_sessions` RESTRICT; the source of the four session-sourced kinds |
| `approval_id` | UUID | yes | FK `session_closeout_approvals` RESTRICT; the revision used; set on `session` and `makeup`, on a `no_show` that comes from a revision; `NULL` on `late_cancellation` and on a `no_show` that comes from a cancellation |
| `service_date` | DATE | yes | set iff session-sourced |
| `class_kind` | VARCHAR(20) | yes | set iff session-sourced |
| `minutes` | INTEGER | yes | `approved_minutes`, or `scheduled_minutes` when there is no revision; `>= 0`; set iff session-sourced |
| `rate_model` | VARCHAR(20) | yes | snapshot; `NULL` on a "covered by salary" line |
| `rate` | NUMERIC(14,4) | yes | snapshot; `NULL` iff `rate_model` is |
| `compensation_rate_id` | UUID | yes | FK `compensation_rates` RESTRICT; the rate version; `NULL` iff `rate_model` is |
| `session_pay_rule_id` | UUID | yes | FK `session_pay_rules` RESTRICT; set iff `late_cancellation`, `no_show` or `makeup` |
| `cumulative_sessions`, `cohort_sessions` | INTEGER | yes | E3's `k` and `N`; set iff `rate_model = 'per_course'` |
| `tutor_pay_terms_id` | UUID | yes | FK `tutor_pay_terms` RESTRICT; set iff `salary` (#108) |
| `salary_amount` | NUMERIC(14,2) | yes | `> 0`; the snapshot the line was prorated from; set iff `salary` (#108 E1) |
| `salary_starts_on`, `salary_ends_on` | DATE | yes | the piece, first `<=` last; set iff `salary` (#108 E1) |
| `proration_numerator`, `proration_denominator` | INTEGER | yes | `paid_days` and `natural_days`; `0 <= numerator <= denominator`, `denominator > 0`; set iff `salary`; equal on a full natural period (#108) |
| `proration_basis` | VARCHAR(20) | yes | `calendar_days` \| `weekdays`; set iff `salary` (#108) |
| `pay_item_id` | UUID | yes | composite FK, below; set iff `pay_item` (#109) |
| `payroll_adjustment_id` | UUID | yes | composite FK, below; set iff `adjustment` (#111) |

- **Composite FK:** `(payroll_period_id, tutor_user_id)` → `payroll_periods (id, tutor_user_id)` (P6). `UNIQUE (id, tutor_user_id)` is the target of the adjustment's origin-line FK. `(pay_item_id, tutor_user_id)` → `pay_items (id, tutor_user_id)` `RESTRICT` (P12, #109 tightening the plain FK), so no item reaches another tutor's line. `(payroll_adjustment_id, payroll_period_id, tutor_user_id)` → `payroll_adjustments (id, target_period_id, tutor_user_id)` `RESTRICT` (`fk_payroll_lines_adjustment`, #111): an adjustment line is in its adjustment's target period, and of its tutor, by construction.
- **Source CHECK, per kind:**
  - `session`, `makeup`: `class_session_id` and `approval_id` set, all of `tutor_pay_terms_id`, `pay_item_id`, `payroll_adjustment_id` `NULL`;
  - `no_show`: `class_session_id` set, `approval_id` optional, and the same three `NULL`;
  - `late_cancellation`: `class_session_id` set, `approval_id` `NULL`, and the same three `NULL`;
  - `salary`: `tutor_pay_terms_id` set, `class_session_id`, `approval_id`, `pay_item_id`, `payroll_adjustment_id` `NULL`, and `rate_model` `NULL`. A CHECK sets the seven salary columns together on this kind and leaves them `NULL` on every other;
  - `pay_item`: `pay_item_id` set, the others `NULL`, and `rate_model` `NULL`;
  - `adjustment`: `payroll_adjustment_id` set (`ck_payroll_lines_adjustment_fields`), `class_session_id`, `approval_id` and `rate_model` `NULL`, so every session, salary, item and rate column is `NULL` (`source_per_kind`, `session_fields`, `rate_snapshot`, `salary_fields`, `pay_item_fields`). Its references are on the adjustment.
- **Sign rules:** `ck_payroll_lines_amount_sign`: session-sourced and `salary` lines `>= 0`; `pay_item` lines `<> 0`, positive for a `bonus` and negative for a `deduction` (App, from the type's direction); `adjustment` lines `<> 0`: `(kind IN ('pay_item', 'adjustment') AND amount <> 0) OR (kind NOT IN ('pay_item', 'adjustment') AND amount >= 0)` (#111).
- **"Covered by salary" (E1):** kind `session`, `amount = 0.00`, `rate_model`, `rate` and `compensation_rate_id` `NULL`.
- **Natural keys (partial unique indexes):**
  - `(class_session_id) WHERE kind IN ('session', 'late_cancellation', 'no_show', 'makeup')`: one session-sourced line per session;
  - `(payroll_period_id, salary_starts_on) WHERE kind = 'salary'`: one line per piece (#108 E1, correcting `(payroll_period_id, tutor_pay_terms_id)`: one version can pay several natural periods inside a long period);
  - `(pay_item_id, payroll_period_id) WHERE kind = 'pay_item'` (#109);
  - `(payroll_adjustment_id) WHERE kind = 'adjustment'` (#111).
- **Sort (#109 G2, #111 F4):** `service_date` `NULLS LAST`, `class_session_id`, `salary_starts_on`, `pay_item_id`, `payroll_adjustment_id`: session lines, then salary, then items, oldest item first, then adjustments, oldest first. Never `id`, which changes on every run.
- **Amounts (E2, E3):** `per_hour` = `rate.to_money(approved_minutes, 60)`; `per_session` = `rate.to_money()`; `per_course` = cumulative `rate.to_money(k, N)` now minus before, floored at `0.00` (#442; #107 E11 replaces `Money.prorate` here).
- **Per-course (#442, #107 E11):**
  - **Rate:** the version on the local date of the cohort's first session, the earliest `starts_at` in any state. If it is `per_course` it applies to all the tutor's sessions of the cohort, whatever later versions say. Otherwise each session uses the version on its own date: `per_hour` and `per_session` as usual, `per_course` no line (`no_rate`, fixed by a back-dated version).
  - **`N`:** the cohort's sessions not `cancelled` or `skipped_closure`, at calculation time.
  - **The walk:** the tutor's approved sessions and finalized per-course lines of the cohort, by session start. An approved session not in a finalized period adds one to `k`, in any period and with any event, and `cum = max(previous cum, rate.to_money(k, N))`. A finalized line sets `k` to its stored value and `cum` to the highest `rate.to_money(k, N)` of the finalized lines so far, each from its stored values: a line floored at `0.00` stores less than was already paid. Sessions walked between finalized lines don't feed that maximum. A session approved inside a finalized period has no line; `payroll:post_adjustments` posts it as an **anchor** (M7-08d E1, §11.9), a fixed step like a finalized line: it sets `k` to its stored value and feeds the same maximum. The fixed steps walk in the places they hold, by stored `k`, not by date (oldest-first finalize keeps the approved steps out of the middle of them). Until it is anchored it isn't walked. Work before the first period is walked and never anchored.
  - **A line** is `cum` minus the previous `cum`, written only for a session in the period and with terms on its date. A `no_show` at `percent` 100 is that line; below 100 it is `min(line, max(0.00, rate.to_money(k, N, percent=p) − rate.to_money(k − 1, N, percent=p)))`, so a no-show never pays more than teaching the session (#442 D8); above 100 it is the same difference with no cap; `fixed` is the amount; `none` is `0.00`. A percent above 100 and `fixed` aren't shares of the course: the Owner sets them on purpose, and they can take the course's total above the rate. Each line stores `k` and `N`.
- **Salary (E7, #108):** inside a period, for the terms versions in date order:
  1. A version's window is `[max(effective_from, starts_on), min(next version's effective_from − 1, ends_on)]`. Every version counts as "next", a stop included. An empty window, `pay_frequency NULL` or `salary_amount NULL` gives no line.
  2. The window splits at natural boundaries, `natural_bounds(version.pay_frequency, day, anchor)`, repeated from the day after each piece. One line per piece (#108 E1).
  3. Days count on the basis: `calendar_days` every day, `weekdays` Monday to Friday. `natural_days` counts the natural period, `paid_days` the piece.
  4. The amount is `salary_amount × paid_days / natural_days` (`Money.prorate`), rounded once per line; exactly `salary_amount` when `paid_days = natural_days`.
  - A piece with no paid days is a `0.00` line (#108 E2). Two versions inside one natural period each round once, so they can differ from the whole by a centavo per boundary (#108 E3). Closures, absences and the number of sessions never reduce salary. A salary line keeps no work off the unresolved list.
- **Pay items (E8, #109):** one line per item and period, with the item's amount copied and the type's sign. No rounding and no proration. See §11.8.
- **A version on an open line (#107 E8):** `recalculate` would pick the same version again, so it can't free one. Deleting a rate or a terms version (#108 F) locks every `open` period of that tutor, oldest first, then the version, deletes its lines in those periods, then the version; the period shows fewer lines until the next run. An upcoming pay rule follows the same order, over the `open` periods that hold a line of it. A line of a `prepared` period keeps the version: the `RESTRICT` foreign key refuses (`23001`, `409 compensation_rate_in_use`, `409 pay_terms_in_use`, `409 session_pay_rule_in_use`), and a return and recalculate clears it. A finalized line stays `42501`, the same `409`.

### 11.7 `pay_item_types` (#109)

Staff-defined types (E8). Mutable reference: audited, versioned, never deleted once referenced.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `name` | VARCHAR(100) | no | trimmed, 1–100; unique ignoring case |
| `direction` | VARCHAR(10) | no | `bonus` \| `deduction`; never changes (§14.2) |
| `is_active` | BOOLEAN | no | |
| `version` | INTEGER | no | |

- **No delete and no seed (#109 E8):** `is_active` retires a type, and deactivating refuses (`409 pay_item_type_in_use`) while a recurring item of the type has no end, or one on or after the center's today, with the type `FOR UPDATE` first. A new item reads its type `FOR SHARE`. Reactivating always passes. A renamed type shows its current name on every line; #113's payslip snapshot keeps the old one.

### 11.8 `pay_items` (#109)

Fixed amounts only (E8). Audited. **Guarded** (§14.2).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `tutor_user_id` | UUID | no | FK `users` RESTRICT |
| `pay_item_type_id` | UUID | no | FK `pay_item_types` RESTRICT |
| `description` | TEXT | no | trimmed, 1–500 |
| `amount` | NUMERIC(14,2) | no | `> 0`; the direction comes from the type |
| `recurrence` | VARCHAR(10) | no | `one_time` \| `recurring` |
| `effective_from` | DATE | no | |
| `effective_to` | DATE | yes | `>= effective_from` |
| `version` | INTEGER | no | |

- `recurrence = 'recurring' OR effective_to IS NULL`. `UNIQUE (id, tutor_user_id)` is the target of the line's composite FK.
- **Recurring:** the full amount in every not-finalized period overlapping `[effective_from, effective_to]`, both ends inclusive, no end open, with no proration, a stub included. A weekly tutor gets it every week. It never reaches a finalized period, so a back-dated start pays open periods only, and the difference is a one-time item (#109 E9).
- **One-time (#109 E3, refining ADR E8's wording):** an item with a line in a finalized period is done. Otherwise its period is the tutor's **earliest not-finalized period with `ends_on >= effective_from`**. That is one answer in every state: a date before the first period, in a gap or in a finalized period lands in the next period. If that period is `prepared`, the new item has no line there, so finalize answers `409 payroll_period_stale`, as for any late change, and nothing moves to a later month by itself. A date after the last period waits, with `placed_period: null`: a leaver's item stays there, and #111's `manual` adjustment pays it. It reaches at most one finalized period (P11).
- **Changing an item (#109 E9):** `PATCH` sets `effective_to` only, and never before the start of the latest finalized period holding its line. On an inactive type it can only come earlier: a reopen or a later end is `409 pay_item_type_inactive`, because a retired type doesn't pay again (E8), with the type read `FOR SHARE`. Anything else is a delete and a new item, while no prepared or finalized line uses it; afterwards end it and add another.
- **Pull model:** no hook and no job. A run writes lines, and a change shows on the next one.

### 11.9 `payroll_adjustments` (#111)

Corrections after lock (E11 and its #111 notes). Audited, `amount`, `rate` and `reason` masked (#111 E9). **Append-only** (§14.1): `payroll_adjustments_append_only()` refuses `UPDATE`, `DELETE` and `TRUNCATE` with 42501. Created by migration `0080` with every kind and column M7-08c and M7-08d need.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `kind` | VARCHAR(20) | no | `late_approval` \| `hours_correction` \| `manual` \| `carry_forward` (no `pay_event_change`, #111 E3) |
| `tutor_user_id` | UUID | no | FK `users` RESTRICT; composite FKs, below |
| `target_period_id` | UUID | no | the tutor's earliest `open` period at insert (P9); composite FK, below |
| `amount` | NUMERIC(14,2) | no | `<> 0` (P9) |
| `origin_line_id` | UUID | yes | composite FK, below; the origin of an `hours_correction` (#111 E2) |
| `origin_period_id` | UUID | yes | FK `payroll_periods` RESTRICT; the finalized period a `late_approval` belongs to, or a `carry_forward`'s source |
| `class_session_id` | UUID | yes | FK `class_sessions` RESTRICT |
| `approval_id` | UUID | yes | FK `session_closeout_approvals` RESTRICT; the revision paid |
| `paired_adjustment_id` | UUID | yes | FK `payroll_adjustments` RESTRICT (M7-08c's carry pair) |
| `reason` | TEXT | yes | trimmed, 1–1000 |
| `source_key` | TEXT | yes | **UNIQUE**; `approval:<approval_id>` or `cancellation:<class_session_id>` |
| `created_by_user_id` | UUID | yes | FK `users` RESTRICT; `NULL` for the job |
| `line_kind`, `service_date`, `class_kind`, `minutes` | | yes | the snapshot's session fields: a session line kind, the date, the class kind, the minutes priced (`>= 0`) |
| `rate_model`, `rate`, `compensation_rate_id` | | yes | the rate snapshot (`rate` NUMERIC(14,4), FK `compensation_rates` RESTRICT), all set or all `NULL` |
| `session_pay_rule_id` | UUID | yes | FK `session_pay_rules` RESTRICT; set iff `line_kind` is an event |
| `cumulative_sessions`, `cohort_sessions` | INTEGER | yes | set iff `rate_model = 'per_course'`: an anchor's `k` (capped at `N`) and `N` at posting, or a per-course correction's origin `k` and `N` (M7-08d E1, E2) |

- **CHECKs per kind (#111 A2, P8 refined):**
  - `late_approval`: `class_session_id`, `origin_period_id`, `source_key` and the session snapshot set; `origin_line_id`, `reason`, `created_by_user_id`, `paired_adjustment_id` `NULL`; `approval_id` set iff `source_key` starts `approval:`;
  - `hours_correction`: `origin_line_id`, `class_session_id`, `approval_id`, `source_key` and the session snapshot set; `origin_period_id`, `reason`, `paired_adjustment_id` `NULL`;
  - `manual`: `reason` and `created_by_user_id` set; `source_key`, the whole snapshot, `paired_adjustment_id`, `approval_id`, `origin_period_id` `NULL`; at most one of `origin_line_id` and `class_session_id`;
  - `carry_forward`: `origin_period_id` and `created_by_user_id` set; `source_key`, the whole snapshot, `origin_line_id`, `class_session_id`, `approval_id` `NULL`.
- **Composite FKs:** `(target_period_id, tutor_user_id)` → `payroll_periods (id, tutor_user_id)`; `(origin_line_id, tutor_user_id)` → `payroll_lines (id, tutor_user_id)`. `UNIQUE (id, target_period_id, tutor_user_id)` is the target of the line's FK (§11.6).
- **Indexes:** `UNIQUE (source_key)`; **first payment per session** `uq_payroll_adjustments_first_payment`: `UNIQUE (class_session_id) WHERE kind = 'late_approval' OR (kind = 'manual' AND class_session_id IS NOT NULL)` (P13); `(target_period_id)`, `(origin_line_id)`, `(class_session_id) WHERE class_session_id IS NOT NULL`.
- **Amounts (#111 E2):** a `late_approval` is what the session line would have been, from the same functions, on the service date's basis, with E4's salaried dates (§13.2). An `hours_correction` is the current revision priced on the origin's snapshot (rate model, rate, and the rule on the service date) minus what was paid so far: the origin session line's amount and every earlier `late_approval` and `hours_correction` of the session. Never `0`; a zero difference writes nothing.
- **Per-course anchors (M7-08d E1, D1):** a `late_approval` of a cohort priced `per_course` is a fixed step of the walk (§11.6): `k = K + 1` (after the cohort's finalized lines and earlier anchors of the tutor, and the work before the first period; the other late sessions dated before it, with a basis, not settled, not anchored yet and not already counted by an earlier anchor's jump in `k`, count too but raise nothing), capped at `N`, posted oldest first by the job, the cohort's `N` now, and the rate of the version on the first date. It pays `max(0.00, rate.to_money(k, N) − peak)`, or its event's amount, `peak` being the highest `rate.to_money(k, N)` of the fixed steps. A floored `0.00` writes nothing and is read again.
- **Per-course corrections (M7-08d E2):** an `hours_correction` of a per-course origin (line or anchor) keeps the origin's `k`, `N` and rate. Its kind is the revision's; the origin's kind targets the origin's amount (a minutes-only revision posts nothing), another kind the share `S` (the origin's amount when whole, else `max(0.00, rate.to_money(k, N) − rate.to_money(k − 1, N))`) through `course_event_amount`.
- **Fixed prices (M7-08d E4):** a posted `late_approval` or `hours_correction` with a rate snapshot counts in both rate refusals (§15) as a finalized priced line: ordinary on its service date, per-course on its cohort's first date.
- **Idempotency:** the job finds work by anti-join and inserts through the ORM in a savepoint (the table is `Audited`); a `source_key` or first-payment conflict rolls the savepoint back and counts as done. `manual` and `carry_forward` rows are M7-08c's, with the API `Idempotency-Key` (and `finance.correction` reauthentication for `manual`).
- **`manual` shapes (M7-08c E1):** on a line (`origin_line_id`: a line of the tutor in a **finalized** period, any kind), on unresolved work (`class_session_id`: a current blocking item of the tutor, which it settles, §13.2), or free (neither). The reason is required and tutor-visible once finalized. Its target is the earliest `open` period, opened as #111 E6 opens one.
- **The `carry_forward` pair (M7-08c E3):** `+X` with `target_period_id` = the negative `open`, ended period, and `−X` in the tutor's earliest `open` period starting after it; both have `origin_period_id` = that period, `created_by_user_id` the caller, and the negative row's `paired_adjustment_id` names the positive one. `X` is the negated total after a `recalculate`, so the period's total is then exactly `0.00`.
- **Display:** exactly one line of kind `adjustment` in the target period, inserted in the same transaction and never rebuilt. A downgrade of `0080` refuses while a row exists.

### 11.10 `payroll_payments` (#112)

Payments made outside the platform (E13). Audited. **Append-only** (§5.1).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `payroll_period_id` | UUID | no | FK `payroll_periods` RESTRICT; a finalized period (P7) |
| `paid_on` | DATE | no | |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `method_code` | VARCHAR | no | FK `payment_methods (code)` RESTRICT; validated through `billing.service` (type as `payment_methods.code`) |
| `reference` | TEXT | no | trimmed, 1–100 |
| `note` | TEXT | yes | trimmed, 1–1000 |
| `recorded_by_user_id` | UUID | no | FK `users` RESTRICT |

- **Duplicate:** the same method and reference on the same period under a **different** key is `409 payroll_payment_duplicate`, checked under the period lock. Only payments **without a reversal** count, so a reversed payment frees its pair (#112 E2), and the reference is compared trimmed and case-insensitively (`lower()`), the method by code (#112 E3). No database unique key, for that reason.
- **Cap (P7):** Σ payments without a reversal + the new amount ≤ `total_amount` (adjustment lines included), under the period lock; a `0.00` period refuses any payment (#112 E8). `paid_on` from the period's `starts_on` to the center's today (#112 E4).
- **Indexes:** `(payroll_period_id)` and `(paid_on)` (§16). Append-only (migration `0081`); a downgrade refuses while a payment exists.
- **No file:** `reference` and `note` are the proof (owner decision 2026-10-21).

### 11.11 `payroll_payment_reversals` (#112)

Audited. **Append-only** (§5.1). `id`; `payroll_payment_id` (FK RESTRICT, **UNIQUE**: at most one per payment); `reason` (trimmed, 1–1000); `reversed_by_user_id` (FK `users` RESTRICT). Needs `finance.refund.approve` with `finance.refund_or_reversal` reauthentication (E12).

### 11.12 `payslips` (#113)

One row per finalized period that has lines, issued in the finalize transaction after the period lock (E14). The row is a frozen snapshot, the §3.21 pattern. Audited, `snapshot` masked (#113 A2: it holds amounts, descriptions and reasons, and Administrators hold `audit.view`). **Append-only** (§5.1). Created by migration `0082` (#113).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `number` | VARCHAR(16) | no | **UNIQUE**; `P-YYYY-NNNNNN`, e.g. `P-2026-000123`, from `number_series` `P` |
| `payroll_period_id` | UUID | no | **UNIQUE**; composite FK with `tutor_user_id` → `payroll_periods (id, tutor_user_id)` |
| `tutor_user_id` | UUID | no | |
| `issued_at` | TIMESTAMPTZ | no | its center-local year is the number's year |
| `language` | VARCHAR(2) | no | the tutor's stored preference, else `es` ([ADR-0015](adr/0015-server-side-i18n.md)) |
| `template_version` | SMALLINT | no | `> 0` |
| `snapshot` | JSONB | no | `PayslipSnapshotV1`, below; `jsonb_typeof = 'object'`; no payment dates (they are shown live) |

- **Index:** `(tutor_user_id, issued_at)`; `UNIQUE (number)`, `UNIQUE (payroll_period_id)`. The composite FK is `ON DELETE RESTRICT`. A downgrade of `0082` refuses while a row exists.
- **Issuing (#113 B1):** `finalize`'s last step, after the status, the event, the approval decision and the notification: the inputs (lines, settings, tutor name, language) are read first, then `issued_at`, then `next_number('P', <center-local year of issued_at>)`, the **last lock** (§15), then the insert. A rollback gives the number back. A finalize replay issues nothing; the UNIQUE period is the backstop.
- **Snapshot (`"v": 1`, #113 B5):** `center {name, address, phone, email}`, `tutor {user_id, display_name}`, `period {id, starts_on, ends_on, pay_frequency, finalized_on}`, `issued_on`, `lines` (one per stored line, in the `GET …/lines` order: `kind`, `amount`, `service_date`, `class_kind`, `minutes`, `rate_model`, `rate`, `covered_by_salary`, `cumulative_sessions`, `cohort_sessions`, `pay_rule {event, mode, percent, amount} | null`, `salary {amount, starts_on, ends_on, numerator, denominator, basis} | null`, `pay_item {type_name, direction, description} | null`, `adjustment {kind, reason (manual only), service_date, class_kind, origin_period {starts_on, ends_on} | null, paired_period {starts_on, ends_on} | null} | null`), `summary {class_line_count, class_minutes, classes, salary, bonuses, deductions, adjustments}` and `total`. Money is text. Validated on write and read: Σ lines = `total` = Σ subtotals, and at issue `total` = the period's `total_amount`. No student or subject names, no ids but the period's and the tutor's, no payments and no actor names (#113 E6).
- **Screen and payslip (#113 E4):** the tutor's period read projects the live lines the same way, so both show the same figures. The one expected difference is a pay item type renamed after finalize (§11.7): the screen shows the current name, the payslip keeps the name at issue.
- **Reports never read it (#117):** the snapshot is a display copy; `payroll_periods` and `payroll_lines` are the source.

The PDF is rendered synchronously and streamed, never stored. It states it is not a tax document (no CFDI de nómina, spec §1). Periods finalized before #113 merges get no payslip.

### 11.13 `class_sessions.makeup_of_session_id` (#423, M7-04a)

A nullable self-reference (E5), `ON DELETE RESTRICT`, set by staff with `schedule.manage` when creating a one-off individual session.

- **Create rules (App, #423 E2, E3):** the target is a `cancelled` `individual` session, one-off or occurrence, with `makeup_allowed`; the new session has the target's student and subject and starts by `makeup_until`. The tutor, room and duration are free. The rules run at create only: a later move keeps the link, and no route edits it (#423 E6).
- **Deadline (#423 E4):** an approved cancellation request's stored `final_makeup_until`; otherwise the target's `starts_at` + `makeup_window_days` of the policy in force at `cancelled_at`. Inclusive, with no lower bound.
- **Index:** a partial unique index on `(makeup_of_session_id) WHERE makeup_of_session_id IS NOT NULL AND state <> 'cancelled'`: one **live** makeup per cancelled session. A cancelled makeup keeps its link as history (#423 E5).
- **CHECK `makeup_is_one_off_individual`:** the link needs `kind = 'individual' AND series_id IS NULL`.
- `NULL` keeps every current behavior, and existing rows stay `NULL` (no backfill, #423 E8). **Billing doesn't read it** (ADR-0006 E3).

## 12. Expense tables

Module `finance`. Every table is `Audited`; on generated rows that means the savepoint insert of §6, not `ON CONFLICT`.

| Table | Module | Creating issue | Class |
|---|---|---|---|
| `expense_categories` | finance | #114 | mutable config |
| `vendors` | finance | #114 | mutable config; name unique ignoring case |
| `expenses` | finance | #115 | append-only |
| `expense_payments` | finance | #115 | append-only |
| `expense_payment_reversals` | finance | #115 | append-only |
| `expense_adjustments` | finance | #115 | append-only |
| `expense_templates` | finance | #116 | mutable config |

### 12.1 `expense_categories` (#114)

Referenced by id, so a rename never changes a report grouping (E15). `Audited`, with no mask: no amount (#114 E6).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `name` | VARCHAR(100) | no | trimmed, 1–100 (`ck_expense_categories_name_trimmed`); unique on `lower(name)` (`uq_expense_categories_lower_name`) |
| `system_key` | VARCHAR(20) | yes | **UNIQUE**; CHECK in `rent`, `utilities`, `supplies`, `equipment`, `maintenance` (only on the seeded rows); translated until renamed, when staff text wins |
| `is_active` | BOOLEAN | no | default `true` |
| `version` | INTEGER | no | |
| `created_at`, `updated_at` | TIMESTAMPTZ | no | |

- **Seed:** inserted by migration `0083`, not audited (the `session_pay_rules` precedent): Renta (`rent`), Servicios (`utilities`), Insumos (`supplies`), Equipo (`equipment`), Mantenimiento (`maintenance`). The stored `name` is Spanish.
- **Labels (#114 E3):** while a row holds its `system_key`, clients show the catalog label of the key in the viewer's language (server catalog `finance.json`, `categories.system.<key>`). A real change of name sets `system_key` to `NULL` in the same update, and nothing sets it again; sending the current name keeps it. A case-only change is a change.
- **Name clash (#114 E3):** a new or changed name is refused (`409 expense_category_exists`) when, ignoring case and surrounding spaces, it equals another row's stored name (the unique index) **or** the es or en label of another row that still holds a key (an application check: the labels are constants and a key is only ever lost). A row's own name and labels don't count. An inactive row's name still blocks.
- **Deactivated** rows can't be used on new expenses or templates (`finance.service.usable_category`, which reads the row `FOR SHARE`) but still appear in lists and reports. Deactivating a row a **live** template uses (active and not ended, §12.7) is refused with `409 expense_category_in_use` (`details.template_count` only), on the same `PATCH`, after the row `FOR UPDATE` and the version check; the templates are read with no lock (#116 E2). A paused or ended template doesn't block.
- **No delete (#114 E5):** no route deletes a row; `is_active` retires it and a rename fixes a typo. A `BEFORE TRUNCATE` trigger refuses `TRUNCATE` (42501). **Reports** group by id and show the current name.

### 12.2 `vendors` (#114)

`Audited`, with no mask; `contact` is audited in clear (#114 E6).

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `name` | VARCHAR(200) | no | trimmed, 1–200; unique on `lower(name)` (`uq_vendors_lower_name`, #114 E4) |
| `contact` | TEXT | yes | trimmed, 1–500; free text, may span lines |
| `is_active` | BOOLEAN | no | default `true` |
| `version` | INTEGER | no | |
| `created_at`, `updated_at` | TIMESTAMPTZ | no | |

- The name is the natural key (`409 vendor_exists`); two vendors with one trade name are told apart in the name itself. No label rule.
- The same deactivation (`usable_vendor`; `409 vendor_in_use` while a live template uses it, #116 E2), no-delete and `TRUNCATE` rules as §12.1.

### 12.3 `expenses` (#115)

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `category_id` | UUID | no | FK `expense_categories` RESTRICT |
| `vendor_id` | UUID | yes | FK `vendors` RESTRICT |
| `description` | TEXT | no | trimmed, 1–500 |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `incurred_on` | DATE | no | |
| `due_on` | DATE | no | |
| `receipt_reference` | TEXT | yes | trimmed, 1–100; replaces the attachment; `expense.view` holders only |
| `note` | TEXT | yes | trimmed, 1–1000; `expense.view` holders only |
| `expense_template_id` | UUID | yes | FK `expense_templates` RESTRICT (`fk_expenses_expense_template_id_expense_templates`; added by #116, migration `0085`) |
| `period_start` | DATE | yes | set iff `expense_template_id` is (`ck_expenses_template_period`; added by #116) |
| `created_by_user_id` | UUID | yes | FK `users` RESTRICT; `NULL` for the job |
| `created_at`, `updated_at` | TIMESTAMPTZ | no | `updated_at` never moves: the row is append-only |

- **CHECK (#115 E2):** `due_on >= incurred_on` (`ck_expenses_due_on_after_incurred_on`). The application also refuses an `incurred_on` after the center's today (`422 incurred_on_in_future`); a past `due_on` is allowed (the expense is `overdue` at once).
- **Natural key (#116, X2):** partial unique index `uq_expenses_template_period` on `(expense_template_id, period_start) WHERE expense_template_id IS NOT NULL`. A generated row has `description` = the template's name, `receipt_reference`, `note` and `created_by_user_id` `NULL`; afterwards it is an ordinary expense (payments, adjustments, void). A voided generated expense keeps its key, so the job never posts its period again.
- **Corrections (#115 E3):** no `PATCH`. A wrong amount is an adjustment. A wrong category, vendor, date, description, receipt reference or note is a full negative adjustment (void) and a new expense.
- **Void is final (#115 E4):** once `net = 0`, every payment and adjustment is `409 expense_void`; a void is re-entered as a new expense, never revived.
- **One insert path:** `finance.service.insert_expense` (the route and #116's job), which reads the category and vendor `FOR SHARE` (`usable_*`) and applies the date rules.
- **Audit (#115 E7):** `Audited`, with `amount`, `description`, `receipt_reference` and `note` masked. No duplicate-expense rule (#115 E5: the `Idempotency-Key` covers a retry).
- No tax column (spec §1). No file.

### 12.4 `expense_payments` (#115)

Partial payments are allowed. Marking an expense paid is one of these rows, never an edit.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `expense_id` | UUID | no | FK `expenses` RESTRICT |
| `paid_on` | DATE | no | |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `method_code` | VARCHAR | no | FK `payment_methods (code)` RESTRICT; validated through `billing.service` |
| `reference` | TEXT | yes | trimmed, 1–100 |
| `note` | TEXT | yes | trimmed, 1–1000 |
| `recorded_by_user_id` | UUID | no | FK `users` RESTRICT |
| `created_at`, `updated_at` | TIMESTAMPTZ | no | |

- **Rules (#115 D, E2, E5), under the expense lock:** `paid_on` not after the center's today, with no lower bound (before `incurred_on` passes); the cap (X1); with a `reference`, no other payment without a reversal of the same expense with the same `method_code` and `lower(reference)` (`409 expense_payment_duplicate`). A reversal frees the pair, so no database key.
- **Audit (#115 E7):** `amount`, `reference` and `note` masked.

### 12.5 `expense_payment_reversals` (#115)

`id`; `expense_payment_id` (FK RESTRICT `fk_expense_payment_reversals_payment`, named because the convention's name passes 63 characters; **UNIQUE**: at most one per payment); `reason` (trimmed, 1–1000); `reversed_by_user_id` (FK `users` RESTRICT); `created_at`, `updated_at`. Needs `finance.refund.approve` **and** `expense.view` with `finance.refund_or_reversal` reauthentication (E12, #115 E6): today the Owner only. `reason` is masked in the audit (#115 E7).

### 12.6 `expense_adjustments` (#115)

`id`; `expense_id` (FK RESTRICT); `amount` (`NUMERIC(14,2)`, signed, `<> 0`); `reason` (trimmed, 1–1000, required); `created_by_user_id` (FK `users` RESTRICT); `created_at`, `updated_at`. The only correction of an expense. Needs `expense.manage` plus `finance.correction` reauthentication (E12). Under the expense lock, `amount < -open` is refused (`409 expense_adjustment_exceeds_open`, X1), so an expense goes down to what was paid and no further; exactly `-net` with nothing paid voids it (#115 E4). `amount` and `reason` are masked in the audit (#115 E7).

### 12.7 `expense_templates` (#116)

Mutable config: editing affects only future periods, because generated rows snapshot the values (E17). Migration `0085`.

| Column | Type | Null | Check / note |
|---|---|---|---|
| `id` | UUID | no | PK |
| `name` | VARCHAR(200) | no | trimmed, 1–200 (`ck_expense_templates_name_trimmed`); unique on `lower(name)` (`uq_expense_templates_lower_name`, #116 E3) |
| `category_id` | UUID | no | FK `expense_categories` RESTRICT |
| `vendor_id` | UUID | yes | FK `vendors` RESTRICT |
| `amount` | NUMERIC(14,2) | no | `> 0` |
| `frequency` | VARCHAR(10) | no | `weekly` \| `monthly` \| `annual`; fixed for life (#116 E4) |
| `starts_on` | DATE | no | the anchor (weekly `+7k` days, monthly `+k` months, annual `+k` years, day clamped, always from `starts_on`); fixed for life; created from the center's today − 31 to today + 366 days (#116 E5) |
| `ends_on` | DATE | yes | `>= starts_on`; not before the latest generated `period_start` (application, #116 E6) |
| `due_offset_days` | SMALLINT | no | 0–60 |
| `next_period_start` | DATE | no | `>= starts_on`; the next period the job looks at; `starts_on` on create; reactivating moves it to `greatest(next_period_start, first period on or after today)` (no back-fill, never backwards) |
| `is_active` | BOOLEAN | no | default `true` |
| `version` | INTEGER | no | staff changes only: the job's advance of `next_period_start` leaves it (#116 D7) |
| `created_at`, `updated_at` | TIMESTAMPTZ | no | |

- **Indexes:** `(category_id)` and `(vendor_id)` (the deactivation refusal), `ix_expense_templates_next_period_start_active` on `(next_period_start) WHERE is_active` (the job's candidates).
- **Status (derived, #116 C7):** `ended` (`ends_on` set and `next_period_start > ends_on`), else `paused` (`NOT is_active`), else `active`. **Live** = active and not ended (`ends_on IS NULL OR next_period_start <= ends_on`): only a live template blocks a category or vendor deactivation, and every write that leaves a template live reads its category and vendor `FOR SHARE` (`usable_*`), so a live template's category and vendor are always active.
- **No delete (#116 E10):** `is_active` pauses; a `BEFORE TRUNCATE` trigger refuses `TRUNCATE` (42501). Not append-only. Downgrading `0085` refuses (55000) while a template exists.
- **Audit (#116 E9):** `Audited`, `name` and `amount` masked (the name becomes each generated expense's description).

A generated expense has `incurred_on = period_start` and `due_on = period_start + due_offset_days`. A period is generated iff `period_start` is on or before the center's today and on or before `ends_on`.

## 13. Payroll and expense invariants and derived values

### 13.1 Invariants

**DB** means a constraint, foreign key or trigger. **App (under the period lock)** means checked by the payroll service after `payroll_periods FOR UPDATE`; **App (under the expense lock)** is the same after `expenses FOR UPDATE` (§15).

| ID | Invariant | Enforced | Proven by |
|---|---|---|---|
| P1 | Rate and terms versions are unique per key | DB (`UNIQUE`) | #105 |
| P2 | Periods don't overlap per tutor | DB (`EXCLUDE`) | #106 |
| P3 | Periods have no gaps per tutor | DB (composite FK, first-period partial unique) | #106 |
| P4 | A finalized period's lines and row never change | DB (triggers, §14.3); proved on periods finalized by the route, and under random sequences (#110 K1, K4) | #110, #111 |
| P5 | `total_amount` = Σ lines, and it is ≥ `0.00` | App (under the period lock) for the sum of **all** the lines and the refusal at `prepare`; DB (`CHECK`) for ≥ `0.00`, the backstop | #110 |
| P6 | A line's tutor is its period's tutor; its `service_date` is inside the period, except `adjustment` lines (which, with `salary` and `pay_item` lines, have none); a `salary` line's piece is inside the period | DB (composite FK) for the tutor; App (under the period lock) for the dates | #107, #108 |
| P7 | Σ valid payments ≤ `total_amount`; payments only on finalized periods | App (under the period lock) | #112 |
| P8 | Each adjustment kind names exactly its references (§11.9): `hours_correction` its origin line, session and revision; `late_approval` its session and origin period (and its revision iff keyed `approval:`); `manual` a reason and at most one of a line and a session; `carry_forward` its origin period | DB (`CHECK` per kind) | #111 |
| P9 | An adjustment's target was open at insert; adjustment amounts aren't 0; `source_key` is unique | App (under the period lock) for the target; DB (`CHECK`, `UNIQUE`) for the others | #111 |
| P10 | Payslip numbers are gap-free per series and year; one payslip per period | DB (`payslips.number` and `payroll_period_id` UNIQUE) + App (`number_series` row lock) | #113 |
| P11 | A one-time item reaches at most one finalized period | App (#109 E3's placement, under the period lock, and #110's oldest-first finalize). Placement reaches a later period only once the earlier is finalized, so a line in a later period can't come from a route: `prepare` drops it, and a prepared period holding one goes stale (#110 H10) | #109, #110 |
| P12 | A pay item's line is its tutor's | DB (composite FK) | #109 |
| P13 | Work in a finalized period has at most one first payment, and the lines plus adjustments of a session equal its current revision priced on the origin's snapshot | DB (`uq_payroll_adjustments_first_payment`) for the first; App (the job, under the target period lock) for the sum | #111 |
| X1 | Expense `net ≥ 0`, `open ≥ 0` | App (under the expense lock) | #115 |
| X2 | One expense per `(expense_template_id, period_start)` | DB (partial UNIQUE) | #116 |
| X3 | Posted expense rows never change | DB (triggers, §5.1) | #115 |

### 13.2 Derived values

Nothing below is stored.

- **Period total before finalization:** Σ `payroll_lines.amount`, one figure on both routes (#109 E2): `recalculate` and `GET …/lines` describe all the period's lines, surviving kinds included, and it may be negative. Only `prepare` refuses that (ADR E9). A delete can leave lines summing beyond the money range: both routes answer `409 payroll_amount_out_of_range` until an item is ended or deleted and the period runs. It is stored as `total_amount` only at finalize.
- **The amount of a `before_first_period` item (#109 E4):** typed by Finance, with no formula in the code and no link to the session:
  - `per_hour`, `per_session`: what the line would have paid on the rate in effect on the service date, by the ordinary rules.
  - `per_course`, the `k0` sessions before the first period: `rate.to_money(k0, N1)`, one item, where `N1` is the `cohort_sessions` stored on the tutor's **earliest finalized line of that cohort**. Pay it after that line is finalized: lines plus the item then equal the cohort's final `cum` exactly. An earlier figure may need a later item or a `manual` adjustment if `N` moves.
  - No rate on that date, or a salaried tutor: the amount agreed with the tutor.
  - #111 must not pay this work: nothing links item and session, so a `late_approval` would pay it twice.
- **Period paid state:** with `paid` = Σ payments without a reversal: `unpaid` (`paid` = 0), `partially_paid` (0 < `paid` < `total_amount`), `paid` (`paid` = `total_amount`). A finalized period with a `0.00` total is `paid`.
- **Unresolved work (#443):** work with no session-sourced line, in a period of any status, dated on or after `payroll_starts_on`, and with no basis on the service date (#107 E6, read by the same code as a line). While `payroll_starts_on` is `NULL` nothing is listed. The **`source`** is `closeout` for an approved closeout (current revision), or `cancellation` for a family's `late` or `no_show` cancellation whose rule on that date isn't `none` (#107 E13). A salaried tutor's class kind with no rate isn't unresolved: it is covered by salary (E1), and so is a cancellation, which then has no line. A resolvable closeout not yet recalculated isn't either. The reason is the first that applies:
  1. **`before_first_period` (#106 E4):** the tutor has periods and the date is before the first one's `starts_on`. No terms change can fix it, so it stays on the staff list and doesn't block `prepare`; Finance pays it with a one-time item. Nothing links the two, so it stays listed.
  2. **`no_pay_terms`:** no terms, or `pay_frequency NULL`, on the date. A tutor with no periods at all lands here, since adding terms opens a period.
  3. **`no_rate`:** terms, but no rate model and no salary. A `per_course` version outside its cohort's first date is `no_rate` too (#442 B3). A cohort paid `per_course` needs terms on the date and nothing else.
  - **Which items block `prepare` (#107 E12):** every item but `before_first_period` dated on or before that period's `ends_on`, in whatever status its own period is. A later-dated item doesn't. An item in a finalized period stays listed and blocking until it has a basis; the job's `late_approval` then pays it.
  - **Paying finalized work (#443 D8, #111):** a closeout's `late_approval` has `source_key` `approval:<approval_id>`. A cancellation has no approval, so its `late_approval` has `source_key` `cancellation:<class_session_id>` and `approval_id` `NULL`. Either kind leaves the list when it has a basis, and the job pays it within 15 minutes.
  - **Settled work (#111 E7, M7-08c E2):** a session an adjustment names (`class_session_id`, any kind) is never listed and never blocks, even if its basis later goes. The job never pays it a second time, a later revision included. A run drafts no line for it, in a period of any status, so a rate added later can't pay it again; in a per-course walk it still counts in `k`. A `manual` naming the session is how Finance **pays unresolved work by hand**: only a current item with a blocking reason, classified again under the locks; `before_first_period` work is `409 payroll_work_before_first_period` (a one-time item pays it, #109 E4).
  - **Salaried dates (#111 E4):** on a date a `salary` line of a finalized period paid, the list and the job judge work as it stood at that period's finalize (the row's `updated_at`, its database time): a rate version written later is ignored, and the salary covers whatever has no rate, a late stop included. Covered work is neither listed nor paid. Any other date uses the current basis.
  - **A refused `prepare` (#110 E3):** one transaction, so a refusal rolls its recalculation back, and the `409` lists work the Unresolved page may not show yet. `details.items` names it, and the page then runs `recalculate`, which commits.
  - **The order in #110 (#443 D9):** `prepare` runs `recalculate` first, then `blocking_work`, so a line a recalculation drops has become an item before the check. `finalize` calls `blocking_work` again, because work approved after `prepare` with no basis has no draft and no line, so the drift comparison can't see it.
  - Work paid by a line whose basis later goes isn't listed: the line pays it until a `recalculate` drops it.
- **Expense `net`** = `amount + Σ expense_adjustments.amount`.
- **Expense `open`** = `net − Σ` payments without a reversal.
- **Expense status:** `void` (net = 0); `paid` (open = 0 and not `void`); `overdue` (open > 0 and `due_on` before the center's today); `partially_paid` (open > 0, some valid payment, not overdue); `unpaid` (open > 0, no valid payment, not overdue).

## 14. Payroll and expense immutability

### 14.1 Append-only tables

`payroll_adjustments`, `payroll_payments`, `payroll_payment_reversals`, `payslips`, `expenses`, `expense_payments`, `expense_payment_reversals` and `expense_adjustments` reuse §5.1: the migration 0004 pattern, created with the table, raising 42501.

### 14.2 Guarded tables

- **`tutor_pay_terms` and `compensation_rates`:** the §5.2 `price_versions` pattern, immutable once referenced by a line of a **finalized** period. Triggers `tutor_pay_terms_guard()` and `compensation_rates_guard()` raise 42501 on `UPDATE` or `DELETE` of a referenced row, and on every `TRUNCATE`. The API exposes `DELETE` on an unreferenced version, whatever its date, and no update: a correction is a delete and a new version (#105 E4).
- **Staged predicate:** `payroll_lines` doesn't exist when #105 runs. The triggers ask `compensation_rate_is_referenced(id uuid)` and `tutor_pay_terms_is_referenced(id uuid)` (`LANGUAGE sql VOLATILE`). #105 creates both as `SELECT false`. #107 replaces the first and #108 the second (`CREATE OR REPLACE`) with an `EXISTS` over `payroll_lines` joined to a `finalized` period; each downgrade restores the previous body.
- **`session_pay_rules`:** the `cancellation_billing_rules` shape: `session_pay_rules_guard()` refuses every `UPDATE`, the `DELETE` of a version with `effective_from` on or before the center's today (read from `business_settings.time_zone`) and every `TRUNCATE`. Only an upcoming version can be deleted.
- **`pay_item_types`:** `pay_item_types_guard()` refuses an `UPDATE` of `direction` and every `TRUNCATE` (42501): the direction decides a line's sign, so a wrong one is a new type (#109 E8).
- **`pay_items`:** `pay_items_guard()` refuses, once a finalized line references the row, every `DELETE` and every `UPDATE` other than `effective_to`, `version` and `updated_at` (the `custom_prices` shape, §5.2), and every `TRUNCATE`. The predicate `pay_item_is_referenced(id uuid)` is defined in the same migration (#109), because `payroll_lines` exists by then.

### 14.3 Periods and lines

- **`payroll_periods`:** `payroll_periods_guard()` (`BEFORE UPDATE OR DELETE FOR EACH ROW`) raises 42501 when `OLD.status = 'finalized'`; `payroll_periods_no_truncate` refuses `TRUNCATE`. The move into `finalized` passes, because `OLD.status` is still `prepared`. `SELECT … FOR UPDATE` on a finalized row writes nothing, so payments can still lock it.
- **`payroll_lines`:** `payroll_lines_guard()` (`BEFORE INSERT OR UPDATE OR DELETE FOR EACH ROW`) reads the period's status with `SELECT status … FOR SHARE` and raises 42501 when it is `finalized`; an `UPDATE` checks both the old and the new period. The `FOR SHARE` read makes a line write wait for a concurrent finalize and then see its result, so a write can't slip past it. `payroll_lines_no_truncate` refuses `TRUNCATE`.
- **Created with their tables** (§17): #106 for `payroll_periods`, #107 for `payroll_lines`. #110 is the first issue that can finalize a period, so its tests prove them (P4), the guard's `FOR SHARE` included: a line write that waited for a real finalize fails with 42501.

Line writes are serialized by the period row lock (§15); the trigger is the backstop.

## 15. Payroll and expense concurrency

- **Lock order, payroll mutations:**
  1. the `idempotency_keys` row, first, before any lock (E6 of ADR-0006);
  2. the terms-and-periods advisory lock, where taken;
  3. `payroll_periods` row `FOR UPDATE`, always the **last business lock** of payroll;
  4. `number_series` row (`P`), only when a payslip is issued.

  The App invariants of §13.1 are checked after step 3.
- **Terms and periods (#106 E10):** one `pg_advisory_xact_lock` key (`b"clsPyPrd"`, `payroll.period_rows`), taken first by each per-tutor unit of `payroll:open_periods` and by the terms insert and delete. The refusal reads periods, the job reads terms, and a first write has no period row to lock, so a row lock can't serialize them. Then the job reads `payroll_settings FOR SHARE`, the terms and the last period; a terms delete takes the tutor's open periods and then its row `FOR UPDATE` (#108 F). A settings `PATCH` takes the settings row `FOR UPDATE` and reads the locks after it, so it and the job wait for each other. Every writer of terms takes this lock (#111 too). No scheduling or academics write takes any of these locks.
- **After the period lock (#110):** `recalculate` and `finalize` read the versions and items `FOR KEY SHARE`, and `prepare` and `finalize` then take the `approval_requests` row, always after the period, so the order is the key → the period → versions and items → the request. Scheduling and academics are read with no lock.
- **Oldest first (#110 E2):** `finalize` reads the earlier periods with no lock. A period never leaves `finalized`, so a stale read can only refuse, and an uncommitted earlier finalize reads as `prepared`.
- **Rate inserts (#110 L5):** the tutor's not-finalized periods `FOR UPDATE`, oldest first, then the insert, then the refusal (`422 effective_from_under_finalized_line`, #110 E5; for `course` by the line's and the version's model, with the cohort first date read with no lock), so a rate waiting for a finalize sees its lines, and a finalize waiting for a rate goes stale. Posted `late_approval` and `hours_correction` snapshots count as finalized lines (M7-08d E4).
- **Rate deletes (M7-08d B1, E3):** the tutor's not-finalized periods `FOR UPDATE`, oldest first (not only the open ones), then the version `FOR UPDATE`: `404` → the open lines of the version and the version deleted, and the foreign keys' `409 compensation_rate_in_use` → the add's refusal on the window the version leaves to the previous one, `409 compensation_rate_delete_under_finalized_line` (rolled back with the Unit of Work) → `204`. A delete waiting for a finalize sees its lines and refuses; a finalize waiting for a delete goes stale.
- **Lock order, expense mutations:** the `idempotency_keys` row, on the routes that take a key → `expenses` row `FOR UPDATE`, the **last business lock** of finance. Payments, reversals and adjustments of one expense serialize on it (#115: a reversal reads its payment's expense with no lock first; every rule reads after the lock). A create takes the category and vendor `FOR SHARE` (`usable_*`), which a deactivation's `FOR UPDATE` waits for; neither path takes the other's lock, so no cycle.
- **`recalculate`:** reads committed scheduling and academics state with **no lock on them** and no advisory lock, under the period row lock. No scheduling or academics write ever takes a period lock, so there is no cycle, and a payroll failure can't roll back a closeout or a cancellation (E9). The period row is re-read after the lock, so a run that waited sees a status that changed.
- **Versions in a run (#107 C4, #108 C3, #109 C2):** `tutor_pay_terms`, `compensation_rates`, `session_pay_rules` and `pay_items` are read `FOR KEY SHARE`, after the period lock, so a concurrent delete waits for the run and then fails on the new line, instead of an insert failing with `23503`.
- **Deleting a version (#107 E8):** periods before the version, always, or a run and a delete deadlock: the open periods `FOR UPDATE`, oldest first, then the version `FOR UPDATE`. Two rule deletes also share the `session_pay_rules` advisory lock, taken first, and a terms delete takes the terms-and-periods advisory lock first: the order is the advisory lock, the open periods, the version, the #106 refusals, the version's lines, the version (#108 F).
- **Changing a pay item (#109 F6, F7):** the tutor's not-finalized periods `FOR UPDATE`, oldest first, then the item `FOR UPDATE`, for both `PATCH` and `DELETE`, so neither deadlocks with a run or races a finalize. A delete then removes its lines in `open` periods and the item. A line of a prepared period (`23001`) or a finalized one (`42501`) is `409 pay_item_in_use`, and the rollback keeps the open lines. A new item takes no period lock.
- **After-lock approvals:** an approval committed after a period's finalize sees no line and lands as a `late_approval` adjustment (E11). One committed between `prepare` and `finalize` with a basis is caught by the drift comparison (`409 payroll_period_stale`); one with no basis has no draft, so `finalize`'s call to `blocking_work` catches it (#443 D9).
- **A `manual` adjustment (M7-08c):** the key → the terms-and-periods advisory lock → the tutor's not-finalized periods `FOR UPDATE`, oldest first (as a rate or pay item write) → the reference checks (the line's period status; the session's classification) → the target, the earliest `open` one, opened under the same advisory lock if there is none, `FOR UPDATE` and re-read → the row in a savepoint (a first-payment conflict is `409 payroll_work_settled`) → its line. A finalize that waited for it sees no change to its period; one it waited for has made its period no target.
- **A carry-forward (M7-08c):** the key → the advisory lock → the period read (`404`) → the tutor's not-finalized periods `FOR UPDATE`, oldest first → the period re-read, its status and end → `recalculate` → the total → the target `FOR UPDATE` (opened if needed) → the two rows and their lines. A job unit that posted into the period first is in the total; one that waited posts after it. A `prepare` that waited sees a `0.00` total; a carry that waited for a `prepare` is `409 payroll_period_not_open`.
- **A payment or a reversal (#112 B3, C2):** the key → the period `FOR UPDATE` (for a reversal, the payment's period, read first with no lock) → every rule (the status, the duplicate, the paid total, the reversal) → the row. A finalized row allows `FOR UPDATE` (#110 K1) and its `total_amount` never changes. No other lock is taken, so it can't deadlock with `payroll:post_adjustments` (#469); two payments, or a payment and a reversal, of one period run one after the other.
- **Jobs (`queueing_lock`, natural keys):**
  - `payroll:open_periods`, daily at 07:50 UTC, on `(tutor_user_id, starts_on)`, one Unit of Work per tutor. `payroll_periods` isn't `Audited`, so it uses `ON CONFLICT DO NOTHING`.
  - `payroll:post_adjustments`, every 15 minutes, nothing else enqueues it (#111 E10). The selection reads with no lock. One Unit of Work per adjustment (#111 B4): the terms-and-periods advisory lock, only when a period must be opened → the target period `FOR UPDATE`, re-read, the next open one if its status changed → the versions `FOR KEY SHARE` → the facts re-read and the amount computed → the adjustment in a savepoint (a key or first-payment conflict counts as done) → its line. A rate delete waits for it and then meets the snapshot's foreign key (`409 compensation_rate_in_use`), or the run waits for the delete and sees no basis. An ordinary adjustment locks only the target, so a `finalize` of an earlier period never waits on it. **A per-course anchor (M7-08d A9)** first takes the advisory lock and the tutor's not-finalized periods `FOR UPDATE`, oldest first, as a `manual` does, because its `k` reads the finalized lines: a `finalize` of an earlier `prepared` period either commits first and is read, or waits and finds its lines stale.
  - `finance:generate_recurring_expenses`, daily at 07:55 UTC (also queued after a template create, reactivation or `ends_on` change), on `(expense_template_id, period_start)`. Candidates are read with no lock. One Unit of Work per template: the template row `FOR UPDATE`, re-read → each due period in a savepoint through `insert_expense` (the category and vendor `FOR SHARE`; a natural-key conflict counts as already generated, never `ON CONFLICT`) → `next_period_start`. Two runs take turns on the template row; without it, the unique index and the savepoint still hold.
- **Templates and deactivations (#116 E2):** a template write takes the template row `FOR UPDATE` (a change) or none (a create), then the category and vendor `FOR SHARE`; a category or vendor deactivation takes its own row `FOR UPDATE` and reads the live templates with no lock. Whichever commits first is seen by the other (a deactivation that waited sees the new live template and is `409 …_in_use`; a write that waited sees the inactive row and is `409 …_inactive`), and neither takes the other's lock first, so no cycle. A template change and a job step serialize on the template row: each period takes all the old or all the new values (#116 E7).

## 16. Payroll and expense indexes

Beyond primary keys, unique keys and natural keys, for the access paths of spec §16:

- `payroll_periods (tutor_user_id, starts_on)` (the natural key) and `(status, ends_on)`;
- `payroll_lines (payroll_period_id)`;
- `payroll_lines (class_session_id) WHERE class_session_id IS NOT NULL`;
- `payroll_lines (compensation_rate_id) WHERE compensation_rate_id IS NOT NULL`: the guard predicate and the `RESTRICT` check;
- `payroll_lines (session_pay_rule_id) WHERE session_pay_rule_id IS NOT NULL`: the rule delete and its `RESTRICT` check (#107);
- `payroll_lines (tutor_pay_terms_id) WHERE tutor_pay_terms_id IS NOT NULL`: the guard predicate and the `RESTRICT` check (#108);
- `payroll_lines (pay_item_id) WHERE pay_item_id IS NOT NULL`: the guard predicate and the `RESTRICT` check (#109);
- `pay_items (tutor_user_id, effective_from)` and `(pay_item_type_id)` (#109);
- `payroll_adjustments (target_period_id)` and `(origin_line_id)`;
- `payroll_payments (payroll_period_id)` and `(paid_on)`;
- `expenses (due_on)`, `(incurred_on)`, `(category_id, incurred_on)` and `(vendor_id)`;
- `expense_payments (expense_id)` and `(paid_on)`;
- `expense_adjustments (expense_id)` (#115);
- `expense_templates (category_id)`, `(vendor_id)` and `(next_period_start) WHERE is_active`; `expenses (expense_template_id, period_start) WHERE expense_template_id IS NOT NULL` (the natural key) (#116).

#107's read path over approvals is the existing `ix_session_closeout_approvals_tutor_user_id_session_starts_at` (`tutor_user_id`, `session_starts_at`). Its read of a tutor's cancelled sessions needs a new one, `ix_class_sessions_cancelled_tutor_user_id_starts_at` on `class_sessions (tutor_user_id, starts_at) WHERE state = 'cancelled'`, because `class_sessions` had no btree on the tutor.

## 17. Payroll and expense migration strategy

- The next Alembic revision is **0072**. One migration per implementing issue, as §8.
- Triggers and their functions are created with their table, and predicates are staged as §14.2.
- **No backfill:** work before `payroll_starts_on` is never paid (E2), and periods finalized before #113 get no payslip (E14).
- **Reference seeds** in the creating migration: the `payroll_settings` row with `payroll_starts_on = NULL` (#106), the three `session_pay_rules` rows (#441) and the five `expense_categories` rows (#114). Permissions are seeded by their first user (E12).

## 18. Payroll and expense relationships

```mermaid
erDiagram
    users ||--o{ tutor_pay_terms : "tutor"
    users ||--o{ compensation_rates : "tutor"
    users ||--o{ payroll_periods : "tutor"
    users ||--o{ pay_items : "tutor"
    payroll_periods ||--o{ payroll_lines : ""
    payroll_periods ||--o{ payroll_payments : ""
    payroll_periods ||--o| payslips : ""
    payroll_periods ||--o{ payroll_adjustments : "target"
    payroll_lines ||--o{ payroll_adjustments : "origin"
    payroll_adjustments ||--o| payroll_lines : "shown as"
    payroll_payments ||--o| payroll_payment_reversals : ""
    payment_methods ||--o{ payroll_payments : ""
    compensation_rates ||--o{ payroll_lines : "rate used"
    tutor_pay_terms ||--o{ payroll_lines : "salary piece"
    session_pay_rules ||--o{ payroll_lines : "rule used"
    pay_item_types ||--o{ pay_items : ""
    pay_items ||--o{ payroll_lines : ""
    class_sessions ||--o{ payroll_lines : "source"
    class_sessions ||--o| class_sessions : "makeup of"
    session_closeout_approvals ||--o{ payroll_lines : "revision used"
    expense_categories ||--o{ expenses : ""
    expense_categories ||--o{ expense_templates : ""
    vendors ||--o{ expenses : ""
    vendors ||--o{ expense_templates : ""
    expense_templates ||--o{ expenses : "generated"
    expenses ||--o{ expense_payments : ""
    expenses ||--o{ expense_adjustments : ""
    expense_payments ||--o| expense_payment_reversals : ""
    payment_methods ||--o{ expense_payments : ""
```

## 19. M7 issue coverage

Each M7 issue, the ADR-0017 decision items it relies on, and the tables it creates or uses. QA checks each issue's acceptance criteria against its row.

| Issue | Decision items | Tables |
|---|---|---|
| #105 M7-02 Compensation and rates | E1 (keys, `NULL` semantics, guarded versions, lookup on the service date), E12 (`payroll.view`, `payroll.manage`; tutors see only their own rates by record policy; audit masks on the amounts) | `tutor_pay_terms`, `compensation_rates` |
| #106 M7-03 Payroll periods | E2 (`payroll_starts_on`), E6 (calendars in local `DATE`s, stubs, no overlaps, no gaps, idempotent `payroll:open_periods`, the `effective_from_inside_generated_period` and `effective_from_before_first_period` refusals on the terms insert and delete, not on a stop) | `payroll_settings`, `payroll_periods` |
| #423 M7-04a Makeup link | E5 | `class_sessions.makeup_of_session_id` |
| #441 M7-04c Session pay rules | E4 (the three events, `none`, `percent`, `fixed`, seeds), E12 (`payroll.view`, `payroll.manage`, masked in the audit log); #107 E9 | `session_pay_rules` |
| #107 M7-04 Calculation | E2 (current revision, attribution, service date), E4 (rules consuming the #62 outcomes), E5, E9 (one line per source with its session, repeatable `recalculate`, no hooks), E12 (`payroll.prepare`). Per-course amounts (E3) are #442 | `session_pay_rules` (reads), `payroll_lines` (session-sourced kinds) |
| #443 M7-04e Unresolved work | E2 (`no_pay_terms`, `no_rate`, the staff list), #106 E4 (`before_first_period`), #107 E12 (what blocks `prepare`), E13 (cancellations) | none: derived from `payroll_lines`, `session_closeout_approvals`, `class_sessions` |
| #108 M7-05 Salary and proration | E1 (salary plus extras), E7 (configurable basis, `Money.prorate`, review through line display) | `payroll_lines` (`salary`), `payroll_settings` (`salary_proration_basis`) |
| #109 M7-06 Bonuses and deductions | E8 (recurring per period on `(pay_item_id, payroll_period_id)`, one line per item, fixed amounts) | `pay_item_types`, `pay_items`, `payroll_lines` (`pay_item`) |
| #110 M7-07 Workflow and lock | E9 (negative total, named events), E10 (states, return, drift check, duty separation, inbox, triggers, notifications), E12 (`payroll.prepare`, `payroll.finalize`, reauthentication), ADR-0006 E6 (key); P4, P5, P11 proved | `payroll_periods` (migration `0079`: the return columns), `payroll_lines` |
| #111 M7-08 Corrections after lock | E11 and its #111 notes (E2 one key per revision, E3 no `pay_event_change`, E4 salaried dates, E6 earliest open target, E7 settled work, E8 negative finalize, `payroll:post_adjustments`), E12 (`finance.correction`, M7-08c); P8, P9, P13; migration 0080 | `payroll_adjustments`, `payroll_lines` (`adjustment`) |
| #112 M7-09 Payroll payments | E12 (`payroll.payment.record`, reversals), E13 (finalized only, partial, reference and note with no file, key, duplicate rejected); P7 | `payroll_payments`, `payroll_payment_reversals` |
| #113 M7-10 Earnings portal, payslips | E12 (tutors by record policy, same `404`), E14 (`P-YYYY-NNNNNN`, tutor's language, finalized-only earnings, non-fiscal statement); P10 | `payslips`, `number_series` |
| #114 M7-11 Categories, vendors | E12 (`expense.configure`), E15 (stable ids, current name shown instead of the name used at the time, deactivation) | `expense_categories`, `vendors` |
| #115 M7-12 Expenses | E12 (`expense.view`, `expense.manage`), E16 (payment rows, adjustments, derived status, `receipt_reference` and note for `expense.view` holders, no file); X1, X3 | `expenses`, `expense_payments`, `expense_payment_reversals`, `expense_adjustments` |
| #116 M7-13 Recurring templates | E17 (natural key `(expense_template_id, period_start)`, future-only edits, `finance:generate_recurring_expenses`); X2 | `expense_templates`, `expenses` |
| #117 M7-14 Financial reports | E12 (`finance.report.view`: Owner and Finance only, seeded by `make seed`, not delegable), E18, ADR-0006 E7 (accrual), E5 and E13 (cash); #117 E3–E10 | **no table, no migration**: the `reporting` module reads `charges`, `charge_adjustments`, `payments`, `payment_reversals`, `payment_allocations`, `allocation_releases`, `refunds`, `charge_due_date_changes`, `packages`, `package_credit_transactions`, `tuition_plans`, `service_prices`, `class_sessions`, `class_series`, `cohort_enrollments`, `payroll_periods`, `payroll_lines`, `payroll_adjustments`, `payroll_payments`, `payroll_payment_reversals`, `expenses`, `expense_payments`, `expense_payment_reversals`, `expense_adjustments`, `expense_categories`, `vendors` (never `payslips` or `expense_templates`) |

## 20. Import tables (#122, M8-05)

An administration addition outside the billing, payroll and expense domains, documented here because #122 asked for it (the import pipeline of architecture ISSUE-L1). Migration `0088_administration_imports`; module `administration.imports`. The CSV text itself is **never stored** (#122 E1, owner decision 2026-10-21 "no uploads"): the request is parsed in memory and only the parsed rows below are kept, while the import is open.

Since #123 (migration 0089), `charges.import_job_id` references `import_jobs` `ON DELETE RESTRICT` (§3.9), so a job whose opening balances were posted can never be deleted.

### 20.1 `import_jobs` (mutable, audited, versioned)

| Column | Type | Notes |
|---|---|---|
| `kind` | `VARCHAR(32)` | A registered import kind's code (`students`, `tutors`; #123 adds `schedules`, `opening_balances`). CHECK `^[a-z_]{3,32}$`; no database enum. |
| `status` | `VARCHAR(20)` | `staged → committing → committed`; `committing → failed → committing` (retry); `staged`/`failed` → `discarded` (by hand) or `expired` (7 days after the last change). CHECK on the six values. |
| `file_name` | `VARCHAR(255) NULL` | The uploaded file's name, trimmed, not blank. Staff text. |
| `ignored_columns` | `TEXT[]` | Header names the kind doesn't read (lower case). |
| `total_rows`, `ready_rows`, `duplicate_rows`, `error_rows`, `created_rows`, `skipped_rows`, `failed_rows` | `INTEGER` | A snapshot of the rows' statuses, refreshed by every change; CHECK `>= 0`. Kept after the rows are deleted. |
| `created_by_user_id` | `UUID` → `users` RESTRICT | The importer. |
| `committed_by_user_id` | `UUID NULL` → `users` RESTRICT | The committer; required from `committing` on. The worker acts as this user. |
| `committed_at` | `TIMESTAMPTZ NULL` | Set exactly when `committed`. |

Indexes: `(kind, created_at DESC)` (the list) and `status` where `staged`, `committing` or `failed` (the task). Audit: automatic events only; the row holds ids, codes, counts, the file name and the ignored header names, never a staged value.

### 20.2 `import_rows` (transient, not audited)

| Column | Type | Notes |
|---|---|---|
| `job_id` | `UUID` → `import_jobs` **CASCADE** | Part of the primary key `(job_id, row_number)`. |
| `row_number` | `INTEGER` | The file line the row starts on (header = 1). |
| `status` | `VARCHAR(16)` | `ready`, `duplicate`, `error` at staging; `created`, `skipped`, `failed` after the commit. |
| `values` | `JSONB NULL` | `{column: text}` of the kind's columns while the job is open; `NULL` once it is committed. CHECK: an object or `NULL`. |
| `errors`, `warnings` | `JSONB` | Arrays of `{column, code}`; codes only, never a value. |
| `record_id` | `UUID NULL` | The created record (no foreign key: it lives in the kind's module). Set exactly when `created`. |

**Retention of values (#122 E9):** staged values are personal data (student names, birthdates, contacts; tutor contact data). They are readable only through the import routes, cleared when the job is committed, deleted with the rows when it is discarded, and deleted with the rows when a `staged` or `failed` job expires after 7 days (`administration:run_imports`). They never reach a log line, a problem detail or an audit event. Backups taken while an import is open contain them (#129 retention).

**Concurrency:** the commit runs one transaction per batch of ready rows, with the job row `FOR UPDATE` and a `pg_advisory_xact_lock` per kind, so two imports of one kind never race a natural key. Records an import creates have no natural-key constraint of their own (students may be homonyms); a manual create racing a commit can still duplicate (accepted, #122).

## 21. Export tables (#124, M8-07)

A `reporting` addition, documented here next to the import tables. Migration `0090_reporting_exports`; module `reporting.exports`. An export is built by the worker from a staff list page's own service function; the finished file's bytes are held in `export_files` for **24 hours** and then deleted (#124 E1, owner decision 2026-10-21 "no file storage": no bucket, no disk path, no temporary file). The job's metadata is kept.

### 21.1 `export_jobs` (mutable, audited)

| Column | Type | Notes |
|---|---|---|
| `requested_by_user_id` | `UUID` → `users` RESTRICT | The requester: the only user who can read or download the job. The worker acts as this user. |
| `dataset` | `VARCHAR(32)` | A registered dataset's code (`students`, `class_sessions`, `expenses`). CHECK `^[a-z_]{3,32}$`; no database enum, so a new dataset needs no migration. |
| `format` | `VARCHAR(8)` | `csv` or `xlsx` (CHECK). |
| `status` | `VARCHAR(16)` | `queued → ready | failed`, `ready → expired` (the purge, 24 hours after `finished_at`). CHECK on the four values; no `running` status (#124 E8). |
| `parameters` | `JSONB` | The normalized `ExportParameters` (dataset, format, columns, group_by, filters): ids and codes only. CHECK: an object. |
| `language` | `VARCHAR(8)` | `es` or `en`: the requester's stored language at request (headers and labels). |
| `row_count`, `byte_size` | `INTEGER NULL` | Set when `ready`; CHECK `>= 0`. |
| `file_name` | `VARCHAR(120)` | `classora-<dataset>-<YYYYMMDD>-<HHMM>.<format>`; not blank. |
| `error_code` | `VARCHAR(32) NULL` | `row_limit_exceeded`, `size_limit_exceeded`, `permission_lost` or `error`; set exactly when `failed` (CHECK). |
| `finished_at`, `expires_at` | `TIMESTAMPTZ NULL` | Required for `ready` and `expired` (CHECK); `expires_at = finished_at + 24 h`. `finished_at` is also set on `failed`. |

Indexes: `(requested_by_user_id, created_at DESC)` (the list), `created_at` where `queued` (the task) and `expires_at` where `ready` (the purge). Audit: automatic events only (`create` by the requester; the worker's `update` by the requester; the purge's `update` with no actor). The row never holds a value of the file.

### 21.2 `export_files` (transient, not audited)

| Column | Type | Notes |
|---|---|---|
| `job_id` | `UUID` → `export_jobs` **CASCADE** | Primary key: at most one file per job. |
| `content` | `BYTEA` | The CSV or XLSX bytes, at most 16 MiB. |

**Retention of bytes (#124 E1, E11):** the file can contain personal data (students' names, many of them minors; tutors). It is readable only through `GET /api/v1/exports/{id}/download` by the requester, is deleted by `reporting:run_exports` once `expires_at` has passed, and never reaches a log line, a problem detail, a notification or an audit event. Backups taken inside the 24-hour window contain it (#129 retention).

**Concurrency:** one transaction per job (`REPEATABLE READ`, the job `FOR UPDATE SKIP LOCKED`), so two runs never build a job twice; the request takes a per-user advisory lock to count the `queued` jobs (at most 3).

## 22. Saved export setups (#125, M8-08)

A `reporting` addition. Migration `0091_reporting_report_setups`; module `reporting.setups`. A setup is a name and one export's `ExportParameters`; running it posts them to `POST /api/v1/exports` under the runner's own checks, so it holds no rows and no file.

### 22.1 `report_setups` (mutable, audited, versioned)

| Column | Type | Notes |
|---|---|---|
| `created_by_user_id` | `UUID` → `users` RESTRICT | The creator: the only user who may change or delete it. |
| `name` | `TEXT` | Trimmed, 1–80 characters (CHECK). Unique per creator, case-insensitive (`uq_report_setups_creator_name` on `(created_by_user_id, lower(name))`). Staff free text (#129 retention). |
| `dataset` | `TEXT` | The export dataset's code; CHECK `^[a-z_]{3,32}$` (the #124 rule, so a new dataset needs no migration). |
| `parameters` | `JSONB` | The validated `ExportParameters` dumped as JSON, omitted optional fields kept omitted: ids, codes and absolute dates only. CHECK: an object whose `dataset` equals the column. |
| `shared_role` | `TEXT NULL` | `owner`, `administrator`, `academic_coordinator` or `finance_staff` (CHECK), or null (private). |
| `version` | `INTEGER` | Optimistic version, from 1. |

Indexes: the unique name index; `shared_role` where it is set (the list's shared part). Audit: automatic `create`, `update` and `delete` events by the creator. At most 50 setups per creator (application rule, under a per-user advisory lock). Any later change to an export dataset's columns or filters must migrate the stored `parameters` in the same change.

## 23. Dashboard layouts (#127, M8-10)

A `reporting` addition. Migration `0092_reporting_dashboard_layouts`; module `reporting.dashboard`. The staff home dashboard's per-user layout: which KPI widgets a user shows and in which order. No row means the role default (every widget the user's permissions allow, shown, in the registry order). See `_docs/api.md` Dashboard layout.

### 23.1 `dashboard_layouts` (mutable, not audited, not versioned)

| Column | Type | Notes |
|---|---|---|
| `user_id` | `UUID` → `users` RESTRICT, **unique** | The owner of the layout: one row per user who customized. |
| `widgets` | `JSONB` | The saved `[{widget, visible}]` in display order, as sent (after validation). CHECK `jsonb_typeof(widgets) = 'array' AND jsonb_array_length(widgets) BETWEEN 1 AND 20`. Widget codes and booleans only, never a figure. |

No other index than the key and the unique `user_id`. Not `Audited` and no `version`: a per-user UI preference, like `user_preferences` (#36) and `notification_opt_outs` (#119); the last write wins. Entries that are no longer available (a lost permission, a widget removed in a later release) are ignored on read and dropped on the next save, so no migration is needed for a catalog change. The row stays when the user is deactivated or anonymized (RESTRICT, as `user_preferences`; #129 decides deletion).

## 24. Job failures (#128, M8-11)

A **core** addition (`app/core/job_failures.py`). Migration `0093_job_failures`. Procrastinate stores no error text, so the worker middleware `app.core.jobs.record_job_failure` records the sanitized error of every failed job run here; the failed jobs page reads it (`_docs/api.md` Jobs).

### 24.1 `job_failures` (mutable, not audited, not versioned)

| Column | Type | Notes |
|---|---|---|
| `job_id` | `BIGINT` **PK** → `procrastinate_jobs(id)` **ON DELETE CASCADE** | One row per job that ever failed. Deleting old jobs (Procrastinate's `builtin:remove_old_jobs`, M10-04/#129) deletes the row. |
| `task_name` | `VARCHAR(128)` | The job's task, copied (the task filter's index). |
| `error_class` | `TEXT NULL` | The exception class name (`__qualname__`), CHECK `char_length ≤ 200`. `NULL` for a failure backfilled by the migration ("Unknown"). |
| `error_code` | `TEXT NULL` | An `AppError` code, CHECK `^[a-z][a-z0-9_]{0,63}$`. |
| `sqlstate` | `TEXT NULL` | The database SQLSTATE of the exception (or its `orig`/`__cause__`), CHECK `^[0-9A-Z]{5}$`. |
| `failed_at` | `TIMESTAMPTZ` default `now()` | The latest failure: a job retried and failed again updates its row (`ON CONFLICT (job_id) DO UPDATE`). |

Indexes `ix_job_failures_failed_at_job_id (failed_at DESC, job_id DESC)` (the list order) and `ix_job_failures_task_name_failed_at (task_name, failed_at DESC)`. The list joins `procrastinate_jobs` by primary key only, so it never scans the job table.

Never a message, a traceback or the job's arguments: driver messages quote key values. No personal data. The upgrade backfills one row per existing `failed` job (`error_class` `NULL`, `failed_at` = its latest `failed` event or `now()`); the downgrade drops the table. Not `Audited`: the retry itself is audited (`administration.job_retried`). The FK onto Procrastinate's table couples the migration to Procrastinate's schema; a Procrastinate major upgrade must recheck it and `procrastinate_retry_job_v2`.
