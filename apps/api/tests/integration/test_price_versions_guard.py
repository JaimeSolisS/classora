"""The price-version guard with a real charge (#90 E9, A5; #89 E2;
data-model §5.2).

Migration 0061 makes ``price_version_is_referenced`` an ``EXISTS`` on
``charges.price_version_id``. With a charge on a version: the predicate is
true, SQL ``UPDATE`` and ``DELETE`` of the version fail with 42501, and the
#89 route ``DELETE …/versions/{id}`` on that upcoming version answers ``409
price_version_in_use``; an unreferenced upcoming version of the same item
is still deleted (``204``).
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing.models import PriceVersion
from app.modules.identity.permissions import OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as factories
from tests.factories.billing import persist_service_price
from tests.factories.scheduling import persist_subject
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.billing_ledger_support import fails_with
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZONE)
TODAY = date(2031, 3, 10)
PRICES = "/api/v1/billing/service-prices"


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(client, create_user(module_seeded_url, roles=(OWNER,)).username)


@dataclass(frozen=True)
class Item:
    item: uuid.UUID
    current: uuid.UUID
    referenced: uuid.UUID
    unreferenced: uuid.UUID


@pytest.fixture
def item(module_seeded_url: str) -> Item:
    """An item with a current version and two upcoming ones; a charge uses
    the first upcoming version."""

    async def _make(session: AsyncSession) -> Item:
        subject = await persist_subject(session)
        price = await persist_service_price(
            session,
            subject_id=subject.id,
            versions=[
                (TODAY, "300.00"),
                (TODAY + timedelta(days=5), "320.00"),
                (TODAY + timedelta(days=9), "340.00"),
            ],
        )
        versions = list(
            await session.scalars(
                select(PriceVersion.id)
                .where(PriceVersion.service_price_id == price.id)
                .order_by(PriceVersion.effective_from)
            )
        )
        _, [student] = await factories.family_with_students(session)
        await factories.charge(session, student, "320.00", price_version_id=versions[1])
        return Item(price.id, versions[0], versions[1], versions[2])

    return in_uow(module_seeded_url, _make)


def _referenced(url: str, version_id: uuid.UUID) -> bool:
    [row] = run_sql(
        url,
        "SELECT price_version_is_referenced(CAST(:v AS uuid)) AS r",
        {"v": version_id},
    )
    return bool(row.r)


def test_the_predicate_sees_the_charge(module_seeded_url, item):
    url = module_seeded_url
    assert _referenced(url, item.referenced) is True
    assert _referenced(url, item.current) is False
    assert _referenced(url, item.unreferenced) is False


def test_sql_cannot_change_or_delete_a_referenced_version(module_seeded_url, item):
    url = module_seeded_url
    message = fails_with(
        url,
        "UPDATE price_versions SET amount = 1 WHERE id = :id",
        "42501",
        id=item.referenced,
    )
    assert "price_versions row is referenced" in message
    fails_with(
        url, "DELETE FROM price_versions WHERE id = :id", "42501", id=item.referenced
    )
    [row] = run_sql(
        url, "SELECT amount FROM price_versions WHERE id = :id", {"id": item.referenced}
    )
    assert Money(row.amount) == Money("320.00")


def test_the_route_refuses_a_referenced_version_and_deletes_an_unused_one(
    client, staff, module_seeded_url, item
):
    path = f"{PRICES}/{item.item}/versions"

    assert code_of(delete(client, staff, f"{path}/{item.referenced}")) == (
        409,
        "price_version_in_use",
    )
    assert code_of(delete(client, staff, f"{path}/{item.unreferenced}")) == (204, None)

    remaining = run_sql(
        module_seeded_url,
        "SELECT id FROM price_versions WHERE service_price_id = :p",
        {"p": item.item},
    )
    assert {row.id for row in remaining} == {item.current, item.referenced}
