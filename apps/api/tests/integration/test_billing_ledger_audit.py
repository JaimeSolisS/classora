"""Audit events and one transaction (#90 E7; C5; spec §9; #22).

Every row ``post_charge`` and ``post_adjustment`` insert (the account, the
charge, the adjustment, each release and a re-inserted allocation) writes
one automatic ``create`` event with the Unit of Work's actor, in the same
transaction. A Unit of Work that posts a charge and an adjustment and then
raises leaves no ledger row and no event. No audit row carries a password.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from tests.factories import billing as factories
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import events, in_uow, no_secret_in_audit


class _Abort(Exception):
    pass


def _people(url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        user = await factories.staff_user_id(session)
        family, [student] = await factories.family_with_students(session)
        return user, family, student

    return in_uow(url, _make)


def test_each_posted_row_writes_one_create_event_with_the_actor(module_seeded_url):
    url = module_seeded_url
    user, family, student = _people(url)

    posted = in_uow(
        url, lambda s: factories.charge(s, student, "500.00"), actor_id=user
    )

    async def _paid(session: AsyncSession) -> uuid.UUID:
        paid = await factories.payment(
            session, posted.billing_account_id, "500.00", recorded_by=user
        )
        await factories.allocation(session, paid, posted.charge_id, "500.00")
        return paid.id

    in_uow(url, _paid)
    draft = AdjustmentDraft(
        charge_id=posted.charge_id,
        kind="closure_credit",
        amount=Money("-100.00"),
        source_key=f"closure_credit:{posted.charge_id}",
    )
    adjusted = in_uow(url, lambda s: billing.post_adjustment(s, draft), actor_id=user)

    [account_event] = events(
        url, record_type="billing_accounts", record_id=posted.billing_account_id
    )
    [charge_event] = events(url, record_type="charges", record_id=posted.charge_id)
    [adjustment_event] = events(
        url, record_type="charge_adjustments", record_id=adjusted.adjustment_id
    )
    [release_id] = [
        row.id
        for row in run_sql(
            url,
            "SELECT id FROM allocation_releases WHERE charge_adjustment_id = :a",
            {"a": adjusted.adjustment_id},
        )
    ]
    [release_event] = events(
        url, record_type="allocation_releases", record_id=release_id
    )
    assert adjusted.reinserted_allocation_id is not None
    [reinsert_event] = events(
        url,
        record_type="payment_allocations",
        record_id=adjusted.reinserted_allocation_id,
    )
    for event in (
        account_event,
        charge_event,
        adjustment_event,
        release_event,
        reinsert_event,
    ):
        assert (event.action, event.actor_user_id) == ("create", user)
    assert charge_event.changes is not None
    assert charge_event.changes["amount"] == {"old": None, "new": "500.00"}
    assert charge_event.changes["posted_by_user_id"] == {"old": None, "new": str(user)}
    assert adjustment_event.changes is not None
    assert adjustment_event.changes["amount"] == {"old": None, "new": "-100.00"}
    assert release_event.changes is not None
    assert release_event.changes["released_by_user_id"] == {
        "old": None,
        "new": str(user),
    }
    assert reinsert_event.changes is not None
    assert reinsert_event.changes["amount"] == {"old": None, "new": "400.00"}
    assert reinsert_event.changes["created_by_user_id"] == {"old": None, "new": None}
    assert family  # the account belongs to the student's family


def test_a_failed_unit_of_work_leaves_no_row_and_no_event(module_seeded_url):
    url = module_seeded_url
    _, family, student = _people(url)
    written: dict[str, uuid.UUID] = {}

    async def _post_then_fail(session: AsyncSession) -> None:
        posted = await factories.charge(session, student, "200.00")
        adjusted = await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=posted.charge_id,
                kind="manual",
                amount=Money("-50.00"),
                reason="Descuento",
            ),
        )
        written.update(
            account=posted.billing_account_id,
            charge=posted.charge_id,
            adjustment=adjusted.adjustment_id,
        )
        raise _Abort()

    with pytest.raises(_Abort):
        in_uow(url, _post_then_fail)

    for table, key in (
        ("billing_accounts", "account"),
        ("charges", "charge"),
        ("charge_adjustments", "adjustment"),
    ):
        assert (
            run_sql(
                url,
                f"SELECT count(*) AS n FROM {table} WHERE id = :id",  # noqa: S608
                {"id": written[key]},
            )[0].n
            == 0
        )
        assert events(url, record_type=table, record_id=written[key]) == []
    assert in_uow(url, lambda s: billing.family_balance(s, family)) is None


def test_no_audit_row_carries_a_password(module_seeded_url):
    url = module_seeded_url
    user, _, student = _people(url)
    in_uow(url, lambda s: factories.charge(s, student), actor_id=user)

    no_secret_in_audit(url, TEST_USER_PASSWORD)
