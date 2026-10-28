"""Billing accounts: one per family, created on first use (#90 E3; C1, C2;
#86 E4).

- ``ensure_billing_account`` creates the row once (``due_offset_days = 0``,
  one ``create`` audit event) and returns the same id afterwards; a
  deactivated family gets one too; an unknown family is a ``ValueError``.
- **Concurrency** (two connections, the #47 D3 pattern): A creates the
  account and holds its transaction open; B starts once A has flushed. B's
  insert waits on the unique index until A commits, its savepoint rolls
  back on the violation, and it reads A's row: both return the same id,
  one row and one audit event exist. Repeated 5 times.
"""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.models import BillingAccount
from app.modules.families.models import Family
from tests.factories import billing as factories
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import events, in_uow

HOLD_SECONDS = 0.5
REPEATS = 5


def _family(url: str, *, active: bool = True) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        family_id, _ = await factories.family_with_students(session, 0)
        if not active:
            family = await session.get(Family, family_id)
            assert family is not None
            family.is_active = False
            await session.flush()
        return family_id

    return in_uow(url, _make)


def _accounts_of(url: str, family_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, due_offset_days, version FROM billing_accounts"
        " WHERE family_id = :f",
        {"f": family_id},
    )


def test_the_account_is_created_once_and_reused(module_seeded_url):
    url = module_seeded_url
    family_id = _family(url)

    first = in_uow(url, lambda s: billing.ensure_billing_account(s, family_id))
    again = in_uow(url, lambda s: billing.ensure_billing_account(s, family_id))

    assert again == first
    [row] = _accounts_of(url, family_id)
    assert (row.id, row.due_offset_days, row.version) == (first, 0, 1)
    created = events(url, record_type="billing_accounts", record_id=first)
    assert [e.action for e in created] == ["create"]
    assert created[0].changes == {
        "family_id": {"old": None, "new": str(family_id)},
        "due_offset_days": {"old": None, "new": 0},
    }


def test_a_deactivated_family_still_gets_an_account(module_seeded_url):
    url = module_seeded_url
    family_id = _family(url, active=False)

    account_id = in_uow(url, lambda s: billing.ensure_billing_account(s, family_id))

    assert [row.id for row in _accounts_of(url, family_id)] == [account_id]


def test_an_unknown_family_is_a_programming_error(module_seeded_url):
    with pytest.raises(ValueError, match="unknown family"):
        in_uow(
            module_seeded_url,
            lambda s: billing.ensure_billing_account(s, uuid.uuid4()),
        )


def test_lock_billing_account_locks_the_row_and_refuses_unknown_ids(
    module_seeded_url,
):
    url = module_seeded_url
    family_id = _family(url)
    account_id = in_uow(url, lambda s: billing.ensure_billing_account(s, family_id))

    locked = in_uow(url, lambda s: billing.lock_billing_account(s, account_id))
    assert isinstance(locked, BillingAccount)
    assert (locked.id, locked.family_id) == (account_id, family_id)
    with pytest.raises(ValueError, match="unknown billing account"):
        in_uow(url, lambda s: billing.lock_billing_account(s, uuid.uuid4()))


async def _race(url: str, family_id: uuid.UUID) -> list[uuid.UUID]:
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    flushed = asyncio.Event()

    async def run_a() -> uuid.UUID:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a)) as uow:
                account_id = await billing.ensure_billing_account(
                    uow.session, family_id
                )
                flushed.set()
                await asyncio.sleep(HOLD_SECONDS)
                return account_id
        finally:
            flushed.set()

    async def run_b() -> uuid.UUID:
        await flushed.wait()
        async with UnitOfWork(create_sessionmaker(engine_b)) as uow:
            return await billing.ensure_billing_account(uow.session, family_id)

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_connections_creating_the_account_get_the_same_row(
    module_seeded_url, attempt
):
    url = module_seeded_url
    family_id = _family(url)

    first, second = asyncio.run(_race(url, family_id))

    assert first == second
    assert [row.id for row in _accounts_of(url, family_id)] == [first]
    assert len(events(url, record_type="billing_accounts", record_id=first)) == 1
