"""The check catalogue of the billing invariant suite (#103 C; U4, U5).

Every check has an id, the invariant it states and a query in the suite's
own SQL over the raw tables that returns the **violating rows** (empty =
the invariant holds). Nothing here imports ``app.modules.billing.balances``
or any other billing internal: the reconciliation check (``REC``) compares
this recomputation with the public ``billing.service`` reads and the
``billing_ledger_entries`` view.

The ids match ``_docs/data-model.md`` §4.1 (``I1``…``I12``) and the issue
invariants handed to #103 (``T*`` #93, ``K*`` #94, ``R*`` #97, ``S*`` #99,
``X*`` #100, ``W*`` #101, ``J5`` #92, ``SK`` the adjustment source keys).
The table "invariant → check id → earlier proof" is in
``_docs/testing-guidelines.md`` ("Financial invariant tests").

**Adding a check.** A new billing table, append-only billing table or
billing invariant adds a :class:`Check` here (and, for an append-only table,
its name to :data:`APPEND_ONLY_TABLES`; ``test_billing_invariant_harness``
fails for an append-only billing table the I11 check doesn't cover).

Checks of a **step** (``after`` set) run only after an operation of that
kind: they compare the rows the step wrote with the state right before it.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "charges",
    "charge_adjustments",
    "payments",
    "payment_reversals",
    "payment_allocations",
    "allocation_releases",
    "refunds",
    "charge_discounts",
    "packages",
    "package_credit_transactions",
    "charge_due_date_changes",
    "charge_reminders",
    "receipts",
    "session_cancellation_decisions",
    "charge_duration_repricings",
    "withdrawals",
)
"""Every append-only billing table (I11 hashes each of their rows)."""

NET = (
    "(c.amount + COALESCE((SELECT SUM(a.amount) FROM charge_adjustments a"
    " WHERE a.charge_id = c.id), 0))"
)
VALID = (
    "COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa"
    " WHERE pa.charge_id = c.id AND NOT EXISTS (SELECT 1 FROM"
    " allocation_releases r WHERE r.allocation_id = pa.id)), 0)"
)
PAY_VALID = (
    "COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa"
    " WHERE pa.payment_id = p.id AND NOT EXISTS (SELECT 1 FROM"
    " allocation_releases r WHERE r.allocation_id = pa.id)), 0)"
)
PAY_REFUNDED = (
    "COALESCE((SELECT SUM(f.amount) FROM refunds f WHERE f.payment_id = p.id), 0)"
)
REVERSED = "EXISTS (SELECT 1 FROM payment_reversals x WHERE x.payment_id = p.id)"
REMAINING = (
    "COALESCE((SELECT SUM(t.units) FROM package_credit_transactions t"
    " WHERE t.package_id = k.id), 0)"
)
EFFECTIVE_DUE = (
    "COALESCE((SELECT d.due_date FROM charge_due_date_changes d"
    " WHERE d.charge_id = c.id ORDER BY d.created_at DESC, d.id DESC LIMIT 1),"
    " c.due_date)"
)
ZONE = "(SELECT time_zone FROM business_settings LIMIT 1)"


@dataclass(frozen=True)
class Check:
    id: str
    text: str
    sql: str
    plantable: bool = True
    """App-enforced: the C4 self-test plants a violation (``False``: the DB
    refuses the violating row, so the query is defense in depth)."""
    after: str | None = None
    """Run only after a step of this operation kind (a step check)."""


CHECKS: tuple[Check, ...] = (
    Check(
        "I1",
        "every charge: amount + Σ adjustments ≥ 0",
        f"SELECT c.id, {NET} AS net FROM charges c WHERE {NET} < 0",  # noqa: S608 - constant SQL
    ),
    Check(
        "I2",
        "every charge: Σ valid allocations ≤ net",
        f"SELECT c.id, {NET} AS net, {VALID} AS allocated FROM charges c"  # noqa: S608 - constant SQL
        f" WHERE {VALID} > {NET}",
    ),
    Check(
        "I3",
        "every payment: Σ valid allocations + Σ refunds ≤ amount",
        f"SELECT p.id, p.amount, {PAY_VALID} AS allocated, {PAY_REFUNDED} AS"  # noqa: S608 - constant SQL
        f" refunded FROM payments p WHERE {PAY_VALID} + {PAY_REFUNDED} > p.amount",
    ),
    Check(
        "I3r",
        "a reversed payment has no valid allocation",
        f"SELECT p.id, {PAY_VALID} AS allocated FROM payments p"  # noqa: S608 - constant SQL
        f" WHERE {REVERSED} AND {PAY_VALID} > 0",
    ),
    Check(
        "I3x",
        "a payment with a reversal has no refund (#100: no reversal of a"
        " refunded payment, no refund of a reversed one)",
        f"SELECT p.id FROM payments p WHERE {REVERSED}"  # noqa: S608 - constant SQL
        " AND EXISTS (SELECT 1 FROM refunds f WHERE f.payment_id = p.id)",
    ),
    Check(
        "I4",
        "allocations, reversals, refunds and receipts are on their payment's"
        " account; allocations on their charge's",
        "SELECT pa.id, 'allocation' AS row FROM payment_allocations pa"
        " JOIN payments p ON p.id = pa.payment_id JOIN charges c ON c.id ="
        " pa.charge_id WHERE pa.billing_account_id <> p.billing_account_id"
        " OR pa.billing_account_id <> c.billing_account_id"
        " UNION ALL SELECT x.id, 'reversal' FROM payment_reversals x JOIN payments p"
        " ON p.id = x.payment_id WHERE x.billing_account_id <> p.billing_account_id"
        " UNION ALL SELECT f.id, 'refund' FROM refunds f JOIN payments p ON p.id ="
        " f.payment_id WHERE f.billing_account_id <> p.billing_account_id"
        " UNION ALL SELECT r.id, 'receipt' FROM receipts r JOIN payments p ON p.id ="
        " r.payment_id WHERE r.billing_account_id <> p.billing_account_id",
        plantable=False,
    ),
    Check(
        "I5",
        "every adjustment has its charge's account and student",
        "SELECT a.id FROM charge_adjustments a JOIN charges c ON c.id = a.charge_id"
        " WHERE a.billing_account_id <> c.billing_account_id"
        " OR a.student_id <> c.student_id",
        plantable=False,
    ),
    Check(
        "I6",
        "signs and ranges: charge amounts ≥ 0, adjustment ≠ 0, payment,"
        " allocation and refund > 0, due_offset_days 0–60, package unit signs",
        "SELECT id, 'charge' AS row FROM charges WHERE amount < 0 OR list_amount < 0"
        " OR discount_amount < 0"
        " UNION ALL SELECT id, 'adjustment' FROM charge_adjustments WHERE amount = 0"
        " UNION ALL SELECT id, 'payment' FROM payments WHERE amount <= 0"
        " UNION ALL SELECT id, 'allocation' FROM payment_allocations WHERE amount <= 0"
        " UNION ALL SELECT id, 'refund' FROM refunds WHERE amount <= 0"
        " UNION ALL SELECT id, 'account' FROM billing_accounts"
        " WHERE due_offset_days NOT BETWEEN 0 AND 60"
        " UNION ALL SELECT id, 'units' FROM package_credit_transactions"
        " WHERE NOT ((kind = 'grant' AND units > 0) OR (kind IN ('consume',"
        " 'expire', 'refund') AND units < 0) OR (kind = 'reversal' AND units <> 0))",
        plantable=False,
    ),
    Check(
        "I7",
        "amount = list_amount − discount_amount on every charge",
        "SELECT id FROM charges WHERE amount <> list_amount - discount_amount",
        plantable=False,
    ),
    Check(
        "I7d",
        "Σ charge_discounts.amount = discount_amount where discount lines exist",
        "SELECT c.id, c.discount_amount, s.total FROM charges c JOIN (SELECT"
        " charge_id, SUM(amount) AS total FROM charge_discounts GROUP BY charge_id)"
        " s ON s.charge_id = c.id WHERE s.total <> c.discount_amount",
    ),
    Check(
        "I8",
        "every package: Σ units ≥ 0",
        f"SELECT k.id, {REMAINING} AS remaining FROM packages k WHERE {REMAINING} < 0",  # noqa: S608 - constant SQL
    ),
    Check(
        "I9",
        "no (class_session_id, student_id) with both a session charge and a"
        " consume row",
        "SELECT c.class_session_id, c.student_id FROM charges c"
        " JOIN package_credit_transactions t ON t.kind = 'consume'"
        " AND t.class_session_id = c.class_session_id AND t.student_id ="
        " c.student_id WHERE c.kind = 'session'",
    ),
    Check(
        "I10",
        "exactly one receipt per payment",
        "SELECT p.id, (SELECT COUNT(*) FROM receipts r WHERE r.payment_id = p.id)"
        " AS receipts FROM payments p"
        " WHERE (SELECT COUNT(*) FROM receipts r WHERE r.payment_id = p.id) <> 1",
    ),
    Check(
        "I10n",
        "receipt numbers are unique and, per (series, year), contiguous from 1",
        "SELECT series, year, COUNT(*) AS n, COUNT(DISTINCT seq) AS distinct_n,"
        " MIN(seq) AS low, MAX(seq) AS high FROM (SELECT split_part(number, '-', 1)"
        " AS series, split_part(number, '-', 2)::int AS year,"
        " split_part(number, '-', 3)::int AS seq FROM receipts) r"
        " GROUP BY series, year HAVING COUNT(*) <> COUNT(DISTINCT seq)"
        " OR MIN(seq) <> 1 OR MAX(seq) <> COUNT(*)",
    ),
    Check(
        "I12",
        "#101: at most one withdrawal per enrollment or package",
        "SELECT subject, COUNT(*) AS n FROM (SELECT COALESCE(group_enrollment_id,"
        " cohort_enrollment_id, package_id) AS subject FROM withdrawals) w"
        " GROUP BY subject HAVING COUNT(*) > 1",
        plantable=False,
    ),
    Check(
        "W2",
        "#101: withdrawal adjustments only on group_enrollment,"
        " cohort_enrollment, tuition_period or package_purchase charges",
        "SELECT a.id, c.kind FROM charge_adjustments a JOIN charges c ON c.id ="
        " a.charge_id WHERE a.kind = 'withdrawal' AND c.kind NOT IN"
        " ('group_enrollment', 'cohort_enrollment', 'tuition_period',"
        " 'package_purchase')",
    ),
    Check(
        "W3",
        "#101: Σ refunds with a withdrawal_id = that withdrawal's refund_amount",
        "SELECT w.id, w.refund_amount, COALESCE((SELECT SUM(f.amount) FROM refunds f"
        " WHERE f.withdrawal_id = w.id), 0) AS refunded FROM withdrawals w"
        " WHERE w.refund_amount <> COALESCE((SELECT SUM(f.amount) FROM refunds f"
        " WHERE f.withdrawal_id = w.id), 0)",
    ),
    Check(
        "W4",
        "#101: a refund units row ⇒ the package's remaining = 0",
        f"SELECT k.id, {REMAINING} AS remaining FROM packages k WHERE EXISTS (SELECT"  # noqa: S608 - constant SQL
        " 1 FROM package_credit_transactions t WHERE t.package_id = k.id AND t.kind"
        f" = 'refund') AND {REMAINING} <> 0",
    ),
    Check(
        "W5",
        "#101: no enrollment fee posted for a period after its withdrawal",
        "SELECT c.id, w.id AS withdrawal FROM charges c JOIN withdrawals w ON"
        " (w.group_enrollment_id = c.group_enrollment_id OR w.cohort_enrollment_id"
        " = c.cohort_enrollment_id) WHERE c.kind IN ('group_enrollment',"
        " 'cohort_enrollment') AND c.created_at > w.created_at",
    ),
    Check(
        "J5",
        "#92: no new enrollment fee for a (student, group or cohort) while an"
        " earlier period's fee had net > 0",
        "WITH fees AS (SELECT c.id, c.student_id, c.amount, c.created_at,"
        " COALESCE(ge.group_id, ce.cohort_id) AS scope FROM charges c"
        " LEFT JOIN group_enrollments ge ON ge.id = c.group_enrollment_id"
        " LEFT JOIN cohort_enrollments ce ON ce.id = c.cohort_enrollment_id"
        " WHERE c.kind IN ('group_enrollment', 'cohort_enrollment'))"
        " SELECT later.id, earlier.id AS earlier FROM fees later JOIN fees earlier"
        " ON earlier.student_id = later.student_id AND earlier.scope = later.scope"
        " AND earlier.created_at < later.created_at"
        " WHERE earlier.amount + COALESCE((SELECT SUM(a.amount) FROM"
        " charge_adjustments a WHERE a.charge_id = earlier.id AND a.created_at <"
        " later.created_at), 0) > 0",
    ),
    Check(
        "T1",
        "#93: at most one tuition_period charge per (plan, service_date)",
        "SELECT tuition_plan_id, service_date, COUNT(*) AS n FROM charges"
        " WHERE kind = 'tuition_period' GROUP BY tuition_plan_id, service_date"
        " HAVING COUNT(*) > 1",
        plantable=False,
    ),
    Check(
        "T2",
        "#93: no tuition_period charge whose period starts inside a pause",
        "SELECT c.id, p.id AS pause FROM charges c JOIN tuition_plan_pauses p ON"
        " p.tuition_plan_id = c.tuition_plan_id WHERE c.kind = 'tuition_period'"
        " AND c.service_date >= p.starts_on AND (p.ends_on IS NULL OR"
        " c.service_date <= p.ends_on) AND c.created_at > p.created_at",
    ),
    Check(
        "K1",
        "#94: at most one grant and at most one expire row per package",
        "SELECT package_id, kind, COUNT(*) AS n FROM package_credit_transactions"
        " WHERE kind IN ('grant', 'expire') GROUP BY package_id, kind"
        " HAVING COUNT(*) > 1",
        plantable=False,
    ),
    Check(
        "K2",
        "#94: no consume row for a session dated outside its package's"
        " [purchased_on, expires_on]",
        "SELECT t.id, t.package_id, k.purchased_on, k.expires_on,"  # noqa: S608 - constant SQL
        " (s.starts_at AT TIME ZONE"
        f" {ZONE})::date AS day FROM package_credit_transactions t JOIN packages k"
        " ON k.id = t.package_id JOIN class_sessions s ON s.id = t.class_session_id"
        f" WHERE t.kind = 'consume' AND ((s.starts_at AT TIME ZONE {ZONE})::date"
        f" < k.purchased_on OR (s.starts_at AT TIME ZONE {ZONE})::date >"
        " k.expires_on)",
    ),
    Check(
        "R1",
        "#97: at most one charge_reminders row per (charge, stage)",
        "SELECT charge_id, stage, COUNT(*) AS n FROM charge_reminders"
        " GROUP BY charge_id, stage HAVING COUNT(*) > 1",
        plantable=False,
    ),
    Check(
        "R2",
        "#97: previous_due_date = the effective due date before the row",
        "SELECT * FROM (SELECT d.id, d.previous_due_date, COALESCE(LAG(d.due_date)"
        " OVER (PARTITION BY d.charge_id ORDER BY d.created_at, d.id), c.due_date)"
        " AS expected FROM charge_due_date_changes d JOIN charges c ON c.id ="
        " d.charge_id) r WHERE r.previous_due_date <> r.expected",
    ),
    Check(
        "S1",
        "#99: at most one session_cancellation_decisions row per (session, student)",
        "SELECT class_session_id, student_id, COUNT(*) AS n FROM"
        " session_cancellation_decisions GROUP BY class_session_id, student_id"
        " HAVING COUNT(*) > 1",
        plantable=False,
    ),
    Check(
        "S2",
        "#99: Σ closure_credit per charge is ≤ 0, and when below 0 the charge's"
        " net right after its latest closure credit is 0 (the credit took the"
        " whole net)",
        "SELECT c.id, s.total FROM charges c JOIN (SELECT charge_id, SUM(amount) AS"
        " total FROM charge_adjustments WHERE kind = 'closure_credit' GROUP BY"
        " charge_id) s ON s.charge_id = c.id WHERE s.total > 0 OR (s.total < 0 AND"
        " c.amount + (SELECT SUM(a.amount) FROM charge_adjustments a WHERE"
        " a.charge_id = c.id AND (a.created_at, a.id) <= (SELECT l.created_at, l.id"
        " FROM charge_adjustments l WHERE l.charge_id = c.id AND l.kind ="
        " 'closure_credit' AND l.amount < 0 ORDER BY l.created_at DESC, l.id DESC"
        " LIMIT 1)) <> 0)",
    ),
    Check(
        "S4",
        "#99: no cancellation_credit, closure_credit or duration_change on a"
        " zero or non-session charge",
        "SELECT a.id, a.kind, c.kind AS charge_kind, c.amount FROM"
        " charge_adjustments a JOIN charges c ON c.id = a.charge_id WHERE a.kind IN"
        " ('cancellation_credit', 'closure_credit', 'duration_change') AND"
        " (c.amount = 0 OR c.kind <> 'session')",
    ),
    Check(
        "X2",
        "#100: at most one reversal row per package, and remaining 0 after it",
        f"SELECT k.id, {REMAINING} AS remaining FROM packages k WHERE (SELECT"  # noqa: S608 - constant SQL
        " COUNT(*) FROM package_credit_transactions t WHERE t.package_id = k.id AND"
        " t.kind = 'reversal') > 1 OR (EXISTS (SELECT 1 FROM"
        " package_credit_transactions t WHERE t.package_id = k.id AND t.kind ="
        f" 'reversal') AND {REMAINING} <> 0)",
    ),
    Check(
        "X3",
        "#100: package_cancellation adjustments only on package_purchase charges",
        "SELECT a.id FROM charge_adjustments a JOIN charges c ON c.id = a.charge_id"
        " WHERE a.kind = 'package_cancellation' AND c.kind <> 'package_purchase'",
    ),
    Check(
        "X4",
        "#100: no manual adjustment on a package_purchase charge",
        "SELECT a.id FROM charge_adjustments a JOIN charges c ON c.id = a.charge_id"
        " WHERE a.kind = 'manual' AND c.kind = 'package_purchase'",
    ),
    Check(
        "SK",
        "adjustment source keys are prefixed by their kind and name their charge"
        " (or, for a package cancellation, its package)",
        "SELECT a.id, a.kind, a.source_key FROM charge_adjustments a JOIN charges c"
        " ON c.id = a.charge_id WHERE a.source_key IS NOT NULL AND (a.source_key"
        " NOT LIKE a.kind || ':%' OR position((CASE WHEN a.kind ="
        " 'package_cancellation' THEN c.package_id ELSE c.id END)::text IN"
        " a.source_key) = 0)",
    ),
    Check(
        "S3",
        "#99 after a reconcile run: every eligible charged session's billed"
        " minutes = its scheduled_minutes",
        "SELECT c.id, s.scheduled_minutes, COALESCE((SELECT r.scheduled_minutes FROM"
        " charge_duration_repricings r WHERE r.charge_id = c.id ORDER BY"
        " r.session_version DESC LIMIT 1), c.scheduled_minutes) AS billed"
        " FROM charges c JOIN class_sessions s ON s.id = c.class_session_id"
        " JOIN class_session_participants sp ON sp.class_session_id = s.id AND"
        " sp.student_id = c.student_id WHERE c.kind = 'session' AND c.amount > 0"
        " AND s.state IN ('confirmed', 'completed') AND s.kind IN ('individual',"
        " 'group') AND NOT EXISTS (SELECT 1 FROM charge_adjustments a WHERE"
        " a.charge_id = c.id AND a.kind = 'closure_credit' HAVING SUM(a.amount) <>"
        " 0) AND COALESCE((SELECT r.scheduled_minutes FROM"
        " charge_duration_repricings r WHERE r.charge_id = c.id ORDER BY"
        " r.session_version DESC LIMIT 1), c.scheduled_minutes) IS DISTINCT FROM"
        " s.scheduled_minutes",
        after="run_reconcile",
    ),
)
"""The state checks (C1, C3; step checks last)."""

BY_ID = {check.id: check for check in CHECKS}
STEP_CHECK_KINDS = frozenset(check.after for check in CHECKS if check.after)

RECONCILIATION_ID = "REC"
IMMUTABILITY_ID = "I11"


@dataclass(frozen=True)
class Violation:
    check_id: str
    text: str
    rows: tuple[dict[str, Any], ...]

    def describe(self) -> str:
        rows = "\n".join(f"    {row}" for row in self.rows[:20])
        more = f"\n    … {len(self.rows) - 20} more" if len(self.rows) > 20 else ""
        return f"{self.check_id}: {self.text}\n{rows}{more}"


@dataclass
class Catalogue:
    """The catalogue and its memory: the I11 hash of every append-only row
    the first time it was seen."""

    hashes: dict[str, dict[uuid.UUID, str]] = field(default_factory=dict)

    async def run(
        self,
        session: AsyncSession,
        *,
        today: date,
        after: Iterable[str] = (),
        only: Sequence[str] | None = None,
    ) -> list[Violation]:
        """Every check (``only``: these ids), with the step checks of the
        operation kinds in ``after``."""
        kinds = set(after)
        found: list[Violation] = []
        for check in CHECKS:
            if only is not None and check.id not in only:
                continue
            if check.after is not None and check.after not in kinds and only is None:
                continue
            rows = (await session.execute(text(check.sql))).mappings().all()
            if rows:
                found.append(Violation(check.id, check.text, _plain(rows)))
        if only is None or IMMUTABILITY_ID in only:
            found.extend(await self._immutability(session))
        if only is None or RECONCILIATION_ID in only:
            found.extend(await reconciliation(session, today=today))
        return found

    async def _immutability(self, session: AsyncSession) -> list[Violation]:
        broken: list[dict[str, Any]] = []
        for table in APPEND_ONLY_TABLES:
            rows = (
                await session.execute(
                    text(f"SELECT id, md5(row_to_json(t)::text) AS h FROM {table} t")  # noqa: S608
                )
            ).all()
            now = {row.id: row.h for row in rows}
            seen = self.hashes.setdefault(table, {})
            for row_id, digest in seen.items():
                if now.get(row_id) != digest:
                    broken.append(
                        {"table": table, "id": row_id, "now": now.get(row_id)}
                    )
            for row_id, digest in now.items():
                seen.setdefault(row_id, digest)
        if not broken:
            return []
        return [
            Violation(
                IMMUTABILITY_ID,
                "posted rows never change (each row's hash stays as first seen)",
                tuple(broken),
            )
        ]


def _plain(rows: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    return tuple({k: _value(v) for k, v in dict(row).items()} for row in rows)


def _value(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value


# --- Reconciliation (C2) -------------------------------------------------------------

_CHARGES = f"""
SELECT c.id, c.billing_account_id AS account, c.student_id AS student,
       {NET} AS net, {VALID} AS allocated, {EFFECTIVE_DUE} AS due
FROM charges c
"""  # noqa: S608 - constant SQL
_CREDIT = f"""
SELECT p.billing_account_id AS account,
       SUM(p.amount - {PAY_VALID} - {PAY_REFUNDED}) AS credit
FROM payments p WHERE NOT {REVERSED} GROUP BY p.billing_account_id
"""  # noqa: S608 - constant SQL
_LEDGER = """
SELECT billing_account_id AS account, SUM(balance_effect) AS net
FROM billing_ledger_entries GROUP BY billing_account_id
"""


def status_of(net: Money, allocated: Money, due: date, today: date) -> str:
    """Data-model §4.2, written again for the suite."""
    open_ = net - allocated
    if net.is_zero() and allocated.is_zero():
        return "void"
    if not open_.is_positive():
        return "paid"
    if due < today:
        return "overdue"
    return "partially_paid" if allocated.is_positive() else "pending"


async def reconciliation(session: AsyncSession, *, today: date) -> list[Violation]:
    """Per account and student: the suite's recomputation = ``billing.service``
    balances = Σ ``billing_ledger_entries.balance_effect``; every charge's
    open and status = the §4.2 recomputation on ``today``."""
    charges = (await session.execute(text(_CHARGES))).all()
    credit = {
        row.account: Money(row.credit)
        for row in (await session.execute(text(_CREDIT))).all()
    }
    ledger = {
        row.account: Money(row.net)
        for row in (await session.execute(text(_LEDGER))).all()
    }
    accounts = [
        row.id
        for row in (await session.execute(text("SELECT id FROM billing_accounts")))
    ]
    mine: dict[uuid.UUID, dict[str, Any]] = {}
    for row in charges:
        net, allocated = Money(row.net), Money(row.allocated)
        mine[row.id] = {
            "account": row.account,
            "student": row.student,
            "open": net - allocated,
            "status": status_of(net, allocated, row.due, today),
        }
    broken: list[dict[str, Any]] = []
    for account in accounts:
        own = [c for c in mine.values() if c["account"] == account]
        outstanding = Money.total(c["open"] for c in own)
        account_credit = credit.get(account, Money.zero())
        net = outstanding - account_credit
        service = await billing.account_balance(session, account, today=today)
        expected = (outstanding, account_credit, net)
        got = (service.outstanding, service.account_credit, service.net_balance)
        if got != expected:
            broken.append({"account": str(account), "suite": expected, "service": got})
        if ledger.get(account, Money.zero()) != net:
            broken.append(
                {
                    "account": str(account),
                    "suite_net": net,
                    "ledger": ledger.get(account),
                }
            )
        for student in service.by_student:
            suite = Money.total(
                c["open"] for c in own if c["student"] == student.student_id
            )
            if student.outstanding != suite:
                broken.append(
                    {
                        "account": str(account),
                        "student": str(student.student_id),
                        "suite": suite,
                        "service": student.outstanding,
                    }
                )
        for balance in await billing.charge_balances(session, account, today=today):
            ours = mine.get(balance.charge_id)
            if ours is None or (balance.open, balance.status) != (
                ours["open"],
                ours["status"],
            ):
                broken.append(
                    {
                        "charge": str(balance.charge_id),
                        "suite": None
                        if ours is None
                        else (ours["open"], ours["status"]),
                        "service": (balance.open, balance.status),
                    }
                )
    if not broken:
        return []
    return [
        Violation(
            RECONCILIATION_ID,
            "suite recomputation = billing.service balances = Σ ledger"
            " balance_effect (accounts, students, charge open and status)",
            tuple(broken),
        )
    ]


async def net_balances(session: AsyncSession) -> dict[uuid.UUID, Money]:
    """Every account's net balance from ``billing.service`` (the
    conservation oracle reads these before and after each step)."""
    accounts = [
        row.id
        for row in (await session.execute(text("SELECT id FROM billing_accounts")))
    ]
    out: dict[uuid.UUID, Money] = {}
    for account in accounts:
        out[account] = (await billing.account_balance(session, account)).net_balance
    return out
