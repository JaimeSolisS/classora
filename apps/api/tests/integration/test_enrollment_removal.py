"""Removal credits and re-enrollment restorations (#92 G4, G5; C2, J4, J5):
a group withdrawal credits each charged future session the student left
with an ``enrollment_removal`` of ``−net`` (the charge is never edited); a
re-enrollment restores them; flat group fees and course fees are never
adjusted here (#101)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.core.money import Money
from app.modules.billing import service as billing
from tests.conftest import frozen_clock
from tests.factories.billing import allocation, payment, staff_user_id
from tests.integration import enrollment_charge_support as ec
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import new_student, new_subject


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


def charged_group(url: str, client: Any, subject: uuid.UUID, tl: Any) -> dict[str, Any]:
    """A per-session group of one student with a family and four confirmed,
    charged sessions (W1 Mon, W1 Wed, W2 Mon, W2 Wed at 120.00)."""
    ec.set_floor(url, ec.floor_of(tl))
    student = new_student(url)
    family = ec.add_family(url, student)
    ec.group_session_price(url, subject, "120.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [student])
    sessions = ec.sessions_of(url, group_id=group["id"])[:4]
    ec.confirm_sessions(url, [s.id for s in sessions])
    assert ec.run_sessions(url).posted == 4
    charges = {c.class_session_id: c for c in ec.charges_of(url, student, "session")}
    return {
        "group": group,
        "student": student,
        "family": family,
        "sessions": sessions,
        "charges": [charges[s.id] for s in sessions],
    }


def _adjust(url: str, charge: Any, amount: str, kind: str = "duration_change") -> None:
    run_sql(
        url,
        "INSERT INTO charge_adjustments (charge_id, billing_account_id, student_id,"
        " kind, amount, source_key, posted_at) VALUES (:c, :a, :s, :k, :m, :key,"
        " now())",
        {
            "c": charge.id,
            "a": charge.billing_account_id,
            "s": charge.student_id,
            "k": kind,
            "m": Money(amount).amount,
            "key": f"test:{uuid.uuid4()}",
        },
    )


def _withdraw(url, client, group, student, at) -> uuid.UUID:
    period = ec.group_enrollment_id(url, group["id"], student)
    with frozen_clock(at):
        cookie = owner_cookie(url, client)
        ec.withdraw_group(url, client, cookie, group["id"], period)
    return period


def _enroll(url, client, group, student, at) -> uuid.UUID:
    with frozen_clock(at):
        cookie = owner_cookie(url, client)
        ec.enroll_group(client, cookie, group["id"], student)
    return ec.group_enrollment_id(url, group["id"], student)


def _snapshot(url: str, charge_id: Any) -> Any:
    [row] = run_sql(url, "SELECT * FROM charges WHERE id = :c", {"c": charge_id})
    return row


def test_a_withdrawal_credits_each_charged_future_session(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    g = charged_group(url, client, subject, tl)
    past, zero, shortened, plain = g["charges"]
    _adjust(url, zero, "-120.00")  # net 0: nothing to credit
    _adjust(url, shortened, "-30.00")  # credited by its net, 90.00
    before = {c.id: _snapshot(url, c.id) for c in g["charges"]}

    # Monday 18:00: the first session has started and keeps its charge.
    period = _withdraw(url, client, g["group"], g["student"], tl.at(tl.monday, "18:00"))

    assert ec.adjustments_of(url, past.id) == []
    assert [a.kind for a in ec.adjustments_of(url, zero.id)] == ["duration_change"]
    [_, credit] = ec.adjustments_of(url, shortened.id)
    assert (credit.kind, f"{credit.amount:.2f}") == ("enrollment_removal", "-90.00")
    assert credit.source_key == f"enrollment_removal:{shortened.id}:{period}:withdrawn"
    [credit] = ec.adjustments_of(url, plain.id)
    assert f"{credit.amount:.2f}" == "-120.00"
    for charge in (zero, shortened, plain):
        assert ec.net_of(url, charge.id) == "0.00"
    assert ec.net_of(url, past.id) == "120.00"
    after = {c.id: _snapshot(url, c.id) for c in g["charges"]}
    assert after == before  # never edited

    async def _statuses(session: Any) -> dict[Any, str]:
        rows = await billing.charge_balances(session, past.billing_account_id)
        return {row.charge_id: row.status for row in rows}

    statuses = in_uow(url, _statuses)
    assert statuses[plain.id] == statuses[shortened.id] == "void"


def test_re_enrollment_restores_and_a_second_withdrawal_credits_again(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    g = charged_group(url, client, subject, tl)
    _, first, second, third = g["charges"]
    at = tl.at(tl.monday, "18:00")
    gone = _withdraw(url, client, g["group"], g["student"], at)

    back = _enroll(url, client, g["group"], g["student"], at + timedelta(hours=1))

    for charge in (first, second, third):
        credit, restore = ec.adjustments_of(url, charge.id)
        assert f"{restore.amount:.2f}" == "120.00"
        assert restore.kind == "enrollment_removal"
        assert restore.source_key == f"enrollment_removal:{charge.id}:{back}:enrolled"
        assert credit.source_key.endswith(f":{gone}:withdrawn")
        assert ec.net_of(url, charge.id) == "120.00"
    assert ec.run_sessions(url).posted == 0  # no second session charge
    assert len(ec.charges_of(url, g["student"], "session")) == 4

    again = _withdraw(url, client, g["group"], g["student"], at + timedelta(hours=2))

    for charge in (first, second, third):
        *_, last = ec.adjustments_of(url, charge.id)
        assert last.source_key == f"enrollment_removal:{charge.id}:{again}:withdrawn"
        assert ec.net_of(url, charge.id) == "0.00"


def test_repeating_the_withdrawal_step_posts_no_second_credit(
    module_seeded_url, client, subject
):
    from app.workflows import enrollment_charges

    url = module_seeded_url
    tl = timeline()
    g = charged_group(url, client, subject, tl)
    at = tl.at(tl.monday, "18:00")
    period = _withdraw(url, client, g["group"], g["student"], at)

    created = in_uow(
        url,
        lambda s: enrollment_charges.credit_removed_sessions(
            s,
            group_id=uuid.UUID(g["group"]["id"]),
            student_id=g["student"],
            enrollment_id=period,
            effective_at=at,
        ),
    )

    assert created == 0
    assert all(len(ec.adjustments_of(url, c.id)) == 1 for c in g["charges"][1:])


def test_a_credit_releases_allocations_into_account_credit(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    g = charged_group(url, client, subject, tl)
    target = g["charges"][1]

    async def _pay(session: Any) -> None:
        staff = await staff_user_id(session)
        older = await payment(
            session, target.billing_account_id, "50.00", recorded_by=staff
        )
        newer = await payment(
            session, target.billing_account_id, "70.00", recorded_by=staff
        )
        await allocation(session, older, target.id, "50.00")
        await allocation(session, newer, target.id, "70.00")

    in_uow(url, _pay)
    credit_before = in_uow(
        url, lambda s: billing.account_credit(s, target.billing_account_id)
    )

    _withdraw(url, client, g["group"], g["student"], tl.at(tl.monday, "18:00"))

    released = run_sql(
        url,
        "SELECT r.allocation_id FROM allocation_releases r"
        " JOIN payment_allocations a ON a.id = r.allocation_id WHERE a.charge_id = :c",
        {"c": target.id},
    )
    assert len(released) == 2
    credit_after = in_uow(
        url, lambda s: billing.account_credit(s, target.billing_account_id)
    )
    assert credit_after - credit_before == Money("120.00")


def test_flat_and_course_fees_are_never_adjusted_on_withdrawal(
    module_seeded_url, client, subject
):
    url = module_seeded_url
    tl = timeline()
    ec.set_floor(url, ec.floor_of(tl))
    a, b = new_student(url), new_student(url)
    ec.add_family(url, a)
    ec.add_family(url, b)
    ec.group_fee(url, subject, "800.00")
    ec.cohort_fee(url, subject, "900.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [a])
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        enrolled = ec.enroll_cohort(client, cookie, cohort["id"], b)
    [group_fee] = ec.charges_of(url, a, "group_enrollment")
    [course_fee] = ec.charges_of(url, b, "cohort_enrollment")

    at = tl.at(tl.monday, "18:00")
    _withdraw(url, client, group, a, at)
    with frozen_clock(at):
        cookie = owner_cookie(url, client)
        ec.withdraw_cohort(url, client, cookie, cohort["id"], enrolled["id"])

    assert ec.adjustments_of(url, group_fee.id) == []
    assert ec.adjustments_of(url, course_fee.id) == []
