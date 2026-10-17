# ADR-0004: Money, time and date representation

## Status

Accepted

## Date

2026-10-17

## Sign-off

Owner: JaimeSolisS. Approved as proposed on 2026-10-17 (GitHub issue #6, orchestrator decision record).

## Context

Resolves architecture §10 **ISSUE-D2** (money representation and rounding) and **ISSUE-D3** ("TIMESTAMPTZ UTC" vs date-only and wall-clock values) in [`../architecture.md`](../architecture.md) §10.3.

- **Money.** Spec [`../spec.md`](../spec.md) §16 requires exact decimal-safe representations and forbids binary floating point for charges, allocations, compensation and balances. It leaves the storage and value-object strategy open. Spec §18 item 1 lists "monetary rounding approach and storage precision" as needing an explicit implementation decision. This ADR is that decision for rounding and precision.
- **Time.** Spec §10 says "TIMESTAMPTZ UTC; ISO 8601 API". Spec §16 says to store instants as UTC-aware timestamps and to use the center's configured time zone for scheduling, local date boundaries, recurrence and due dates, and never to infer class dates from UTC dates alone. Spec §10's "TIMESTAMPTZ UTC" applies to **instants**. Calendar dates and wall-clock recurrence are not instants, and spec §16 requires local date boundaries for them. This ADR therefore does not contradict the spec, and the spec is not changed.
- Architecture §7.4: the center's configured time zone governs scheduling, local date boundaries, recurrence and due dates.

Table names, relationship cardinalities and finance ledger conventions (the rest of spec §18 item 1) stay open for ADR-0006 and `_docs/data-model.md`.

## Decision

### Money (ISSUE-D2)

- Amounts are stored as `NUMERIC(14,2)` in MXN.
- Intermediate rates, for example per-minute compensation, use `NUMERIC(14,4)`.
- Python uses `Decimal` end to end. Building money from a `float` is not allowed.
- Each line item is rounded half-up (`ROUND_HALF_UP`) to the centavo. Totals are sums of rounded lines.
- JSON carries money as strings, for example `"1250.00"`. OpenAPI describes it as `type: string, format: decimal`.
- The frontend formats money with `Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' })` and never does float arithmetic on it.

### Time and dates (ISSUE-D3)

- Calendar values use `DATE`: birthdates, due dates, holidays, payroll period bounds, academic periods and effective dates. The API returns them as `YYYY-MM-DD`.
- Instants use `TIMESTAMPTZ`, stored in UTC and sent as ISO 8601 with an offset.
- A recurrence rule stores a local `TIME`, a duration and a `TZID`.
- Materialized class sessions store `starts_at` and `ends_at` as `TIMESTAMPTZ`.
- The center time zone is a business setting with default `America/Mexico_City`. Changing it after sessions exist requires an explicit migration job, not a plain settings edit.

## Consequences

- Money never passes through binary floating point in the database, the API, the generated client or the UI. The frontend does no float arithmetic on money values.
- Because totals are sums of rounded lines, a total can differ by centavos from rounding the unrounded sum. This is intended and keeps every line reproducible.
- Date-only values are never shifted by time zone conversion, so a due date or birthdate is the same calendar day for every viewer.
- Recurring class times stay at the same local wall-clock time across UTC offset changes, because sessions are materialized from local `TIME` + `TZID`.
- A change of the center time zone is an operational task with its own job, not a configuration toggle.
- The money and time utilities are implemented later (M1-15 and the modules that use them). This ADR records the rules only.
- Table names and ledger conventions remain open for ADR-0006 and `_docs/data-model.md`.

## Alternatives considered

- **Binary floating point for money** (`float`, `double precision`, JavaScript `number` arithmetic). Rejected: spec §16 forbids it.
- **Money as JSON numbers.** Rejected: JSON numbers are parsed as binary floating point in the browser.
- **Rounding only the final total.** Rejected in favor of rounding half-up per line item, so each line is reproducible and totals are sums of rounded lines.
- **`TIMESTAMPTZ` for every temporal value, including calendar dates.** Rejected: it would infer calendar dates from UTC instants, which spec §16 forbids.
- **Recurrence stored as UTC instants.** Rejected: recurrence must be defined in local wall-clock time.

## References

- [`../architecture.md`](../architecture.md) §5, §7.4, §10.3 ISSUE-D2 and ISSUE-D3, §11
- [`../spec.md`](../spec.md) §10 (stack table: keys/time), §16 (money, time), §18 item 1
- GitHub issue #6 (this decision gate)
