"""Balances, statuses and the ledger view (#90 E6; D1-D3, D5, A6;
data-model §4.2).

One account with two students and a charge in every status, partial and
full allocations, an allocation released by a credit, a reversed payment
(its allocation released), a refund and an unallocated payment. Hand
computation (today = 2031-03-10 in the center's zone):

======  =======  ======  ======================  =========  ======  ==============
charge  student  amount  adjustments / payments  allocated  open    status
======  =======  ======  ======================  =========  ======  ==============
c1      s1       100                             0          100     pending
c2      s1       200     P1 50                   50         150     partially_paid
c3      s1       150     P1 150                  150        0       paid
c4      s1       120     P2 20, due yesterday    20         100     overdue
c5      s1       80      −80                     0          0       void
c6      s2       60      P2 60 released by −60   0          0       void
c7      s2       300     P3 100, P3 reversed     0          300     pending
c8      s2       50      due yesterday           0          50      overdue
======  =======  ======  ======================  =========  ======  ==============

Payments: P1 300 (200 allocated, 30 refunded → 70 free), P2 200 (20 valid
→ 180 free), P3 100 (reversed → no credit), P4 40 (unallocated → 40 free).
Account credit 290; outstanding 700 (pending 400, partially paid 150,
overdue 150); net balance 410 = Σ ``balance_effect`` of the view.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from app.modules.families import service as families
from tests.conftest import frozen_clock
from tests.factories import billing as factories
from tests.integration.billing_ledger_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")
TODAY = date(2031, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
LATE_EVENING = datetime(2031, 3, 10, 23, 30, tzinfo=ZONE)
"""05:30 UTC on 2031-03-11: the center's date is still the 10th."""


def m(value: str) -> Money:
    return Money(value)


@dataclass(frozen=True)
class Fixture:
    account: uuid.UUID
    s1: uuid.UUID
    s2: uuid.UUID
    charges: dict[str, uuid.UUID]


def _credit(charge_id: uuid.UUID, amount: str) -> AdjustmentDraft:
    return AdjustmentDraft(
        charge_id=charge_id,
        kind="cancellation_credit",
        amount=m(amount),
        source_key=f"test:{uuid.uuid4()}",
    )


@pytest.fixture(scope="module")
def fixture(module_seeded_url: str) -> Fixture:
    async def _make(session: AsyncSession) -> Fixture:
        user = await factories.staff_user_id(session)
        _, [s1, s2] = await factories.family_with_students(session, 2)

        async def post(student: uuid.UUID, amount: str, due: date) -> uuid.UUID:
            posted = await factories.charge(session, student, amount, due=due)
            return posted.charge_id

        c = {
            "c1": await post(s1, "100.00", TOMORROW),
            "c2": await post(s1, "200.00", TODAY),
            "c3": await post(s1, "150.00", TODAY),
            "c4": await post(s1, "120.00", YESTERDAY),
            "c5": await post(s1, "80.00", YESTERDAY),
            "c6": await post(s2, "60.00", YESTERDAY),
            "c7": await post(s2, "300.00", TODAY),
            "c8": await post(s2, "50.00", YESTERDAY),
        }
        account = await billing.ensure_billing_account(
            session, (await _family_of(session, s1))
        )
        p1 = await factories.payment(session, account, "300.00", recorded_by=user)
        await factories.allocation(session, p1, c["c2"], "50.00")
        await factories.allocation(session, p1, c["c3"], "150.00")
        await factories.refund(session, p1, "30.00")
        p2 = await factories.payment(session, account, "200.00", recorded_by=user)
        await factories.allocation(session, p2, c["c4"], "20.00")
        await factories.allocation(session, p2, c["c6"], "60.00")
        p3 = await factories.payment(session, account, "100.00", recorded_by=user)
        await factories.allocation(session, p3, c["c7"], "100.00")
        await factories.reversal(session, p3)
        await factories.payment(session, account, "40.00", recorded_by=user)
        await billing.post_adjustment(session, _credit(c["c5"], "-80.00"))
        released = await billing.post_adjustment(session, _credit(c["c6"], "-60.00"))
        assert len(released.released_allocation_ids) == 1
        return Fixture(account=account, s1=s1, s2=s2, charges=c)

    return in_uow(module_seeded_url, _make)


async def _family_of(session: AsyncSession, student: uuid.UUID) -> uuid.UUID:
    family = await families.family_of_student(session, student)
    assert family is not None
    return family.id


EXPECTED = {
    "c1": ("100.00", "0.00", "0.00", "100.00", "pending"),
    "c2": ("200.00", "0.00", "50.00", "150.00", "partially_paid"),
    "c3": ("150.00", "0.00", "150.00", "0.00", "paid"),
    "c4": ("120.00", "0.00", "20.00", "100.00", "overdue"),
    "c5": ("80.00", "-80.00", "0.00", "0.00", "void"),
    "c6": ("60.00", "-60.00", "0.00", "0.00", "void"),
    "c7": ("300.00", "0.00", "0.00", "300.00", "pending"),
    "c8": ("50.00", "0.00", "0.00", "50.00", "overdue"),
}


def test_each_charge_has_its_values_and_status(module_seeded_url, fixture):
    balances = in_uow(
        module_seeded_url,
        lambda s: billing.charge_balances(s, fixture.account, today=TODAY),
    )

    names = {v: k for k, v in fixture.charges.items()}
    got = {
        names[b.charge_id]: (
            str(b.amount),
            str(b.adjustments_total),
            str(b.allocated),
            str(b.open),
            b.status,
        )
        for b in balances
    }
    assert got == EXPECTED
    assert all(b.net == b.amount + b.adjustments_total for b in balances)
    # One service date: the order is posted_at, i.e. posting order.
    assert [b.charge_id for b in balances] == [
        fixture.charges[f"c{n}"] for n in range(1, 9)
    ]


def test_one_students_charges(module_seeded_url, fixture):
    balances = in_uow(
        module_seeded_url,
        lambda s: billing.charge_balances(
            s, fixture.account, student_id=fixture.s2, today=TODAY
        ),
    )

    assert {b.charge_id for b in balances} == {
        fixture.charges[k] for k in ("c6", "c7", "c8")
    }


def test_the_account_balance_matches_the_hand_computation(module_seeded_url, fixture):
    balance = in_uow(
        module_seeded_url,
        lambda s: billing.account_balance(s, fixture.account, today=TODAY),
    )

    assert balance.billing_account_id == fixture.account
    assert (
        balance.outstanding,
        balance.account_credit,
        balance.net_balance,
        balance.pending,
        balance.partially_paid,
        balance.overdue,
    ) == (m("700.00"), m("290.00"), m("410.00"), m("400.00"), m("150.00"), m("150.00"))
    expected = sorted(
        [
            billing.StudentBalance(
                fixture.s1, m("350.00"), m("100.00"), m("150.00"), m("100.00")
            ),
            billing.StudentBalance(
                fixture.s2, m("350.00"), m("300.00"), m("0.00"), m("50.00")
            ),
        ],
        key=lambda s: s.student_id,
    )
    assert list(balance.by_student) == expected
    # The D2 invariants.
    assert balance.outstanding == (
        balance.pending + balance.partially_paid + balance.overdue
    )
    assert balance.net_balance == balance.outstanding - balance.account_credit
    assert Money.total(s.outstanding for s in balance.by_student) == balance.outstanding


def test_the_view_adds_up_to_the_net_balance(module_seeded_url, fixture):
    [row] = run_sql(
        module_seeded_url,
        "SELECT sum(balance_effect) AS total, count(*) AS n FROM"
        " billing_ledger_entries WHERE billing_account_id = :a",
        {"a": fixture.account},
    )
    # 8 charges, 2 adjustments, 4 payments, 1 reversal, 5 allocations,
    # 2 releases, 1 refund.
    assert row.n == 23
    assert Money(row.total) == m("410.00")


@pytest.mark.parametrize(
    ("at", "overdue"),
    [
        pytest.param(LATE_EVENING, ("c4", "c8"), id="23:30-local-is-still-today"),
        pytest.param(
            LATE_EVENING + timedelta(hours=1),
            ("c2", "c4", "c7", "c8"),
            id="00:30-local-the-next-day",
        ),
    ],
)
def test_overdue_switches_at_the_centers_local_date(
    module_seeded_url, fixture, at, overdue
):
    with frozen_clock(at):
        balances = in_uow(
            module_seeded_url,
            lambda s: billing.charge_balances(s, fixture.account),
        )

    names = {v: k for k, v in fixture.charges.items()}
    assert sorted(
        names[b.charge_id] for b in balances if b.status == "overdue"
    ) == list(overdue)


@pytest.mark.parametrize("charges", [1, 40])
def test_account_balance_costs_at_most_three_statements(module_seeded_url, charges):
    url = module_seeded_url

    async def _make(session: AsyncSession) -> uuid.UUID:
        user = await factories.staff_user_id(session)
        _, [student] = await factories.family_with_students(session)
        posted = None
        for _ in range(charges):
            posted = await factories.charge(session, student, "10.00")
        assert posted is not None
        paid = await factories.payment(
            session, posted.billing_account_id, "15.00", recorded_by=user
        )
        await factories.allocation(session, paid, posted.charge_id, "5.00")
        return posted.billing_account_id

    account = in_uow(url, _make)

    with frozen_clock(LATE_EVENING):
        count, balance = count_statements(
            url, lambda s: billing.account_balance(s, account)
        )

    assert count <= 3
    assert balance.outstanding == m("10.00") * charges - m("5.00")
    assert balance.account_credit == m("10.00")


def test_a_family_without_an_account_has_no_balance(module_seeded_url):
    url = module_seeded_url
    family_id, _ = in_uow(url, lambda s: factories.family_with_students(s, 1))

    assert in_uow(url, lambda s: billing.family_balance(s, family_id)) is None
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM billing_accounts WHERE family_id = :f",
            {"f": family_id},
        )[0].n
        == 0
    )


def test_family_balance_reads_the_familys_account(module_seeded_url, fixture):
    url = module_seeded_url

    async def _read(session: AsyncSession) -> billing.AccountBalance | None:
        return await billing.family_balance(
            session, await _family_of(session, fixture.s1), today=TODAY
        )

    balance = in_uow(url, _read)
    assert balance is not None
    assert balance.net_balance == m("410.00")
