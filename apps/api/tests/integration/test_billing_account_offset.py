"""The family due-date offset (#97 C1-C3, B3; I3; N6 (a)).

``PATCH /api/v1/families/{family_id}/billing-account``: the check order,
the ``version | null`` rule (``null`` creates the account), the same value
writes nothing, the audit event; forward-only for session, package and
manual charges (offsets 0 and 7); a concurrent ``post_charge`` waits for
the PATCH's account lock and gets the new offset (5 repeats).

The other posting rules (#91–#94) keep their own offset tests:
``test_session_charges_sweep.py`` (offsets 0 and 7),
``test_cohort_enrollment_charges.py`` (J6 base + 5),
``test_tuition_charges.py`` (period start + plan offset + account offset)
and ``test_package_purchase.py`` (purchase date + 7).
"""

import asyncio
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.identity.permissions import ADMINISTRATOR, FINANCE_STAFF, OWNER
from tests.conftest import frozen_clock
from tests.factories import billing as factories
from tests.integration import due_date_support as dd
from tests.integration import package_support as ps
from tests.integration import session_charge_support as scs
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get, patch
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import events, in_uow

TODAY = dd.TODAY
NOON = dd.local(TODAY, 12)
HOLD_SECONDS = 0.5
REPEATS = 5


def path(family: object) -> str:
    return f"/api/v1/families/{family}/billing-account"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


def send(client: TestClient, user: CreatedUser, family: object, body: Any) -> Any:
    with frozen_clock(NOON):
        return patch(client, sign_in(client, user.username), path(family), body)


def summary(client: TestClient, user: CreatedUser, family: object) -> Any:
    with frozen_clock(NOON):
        response = get(client, sign_in(client, user.username), path(family))
    assert response.status_code == 200, response.text
    return response.json()


def bare_family(url: str) -> uuid.UUID:
    family_id, _ = in_uow(url, lambda s: factories.family_with_students(s, 1))
    return family_id


# --- C1: the PATCH -------------------------------------------------------------------


def test_null_version_creates_the_account(module_seeded_url, client, staff):
    url = module_seeded_url
    family = bare_family(url)
    assert summary(client, staff, family)["version"] is None

    response = send(client, staff, family, {"version": None, "due_offset_days": 7})

    assert response.status_code == 200, response.text
    settings = response.json()
    assert settings["family_id"] == str(family)
    assert settings["due_offset_days"] == 7
    after = summary(client, staff, family)
    assert after["account_id"] == settings["account_id"]
    assert after["due_offset_days"] == 7
    assert after["version"] == settings["version"]


def test_the_version_must_match_whether_an_account_exists(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    without = bare_family(url)
    w = dd.world(url)

    for family, version in ((without, 1), (w.family, None), (w.family, 9)):
        response = send(
            client, staff, family, {"version": version, "due_offset_days": 3}
        )
        assert response.status_code == 409, (family, version)
        assert response.json()["code"] == "version_conflict"
    assert summary(client, staff, without)["account_id"] is None
    assert summary(client, staff, w.family)["due_offset_days"] == 0


def test_a_change_bumps_the_version_and_is_audited(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    version = summary(client, staff, w.family)["version"]

    response = send(
        client, staff, w.family, {"version": version, "due_offset_days": 15}
    )

    assert response.status_code == 200
    assert response.json()["version"] == version + 1
    updates = [
        e
        for e in events(url, record_type="billing_accounts", record_id=w.account)
        if e.action == "update"
    ]
    assert [e.changes["due_offset_days"] for e in updates] == [{"old": 0, "new": 15}]
    assert updates[0].actor_user_id == staff.id


def test_the_same_value_writes_nothing(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url)
    version = summary(client, staff, w.family)["version"]

    response = send(client, staff, w.family, {"version": version, "due_offset_days": 0})

    assert response.status_code == 200
    assert response.json()["version"] == version
    assert [
        e.action
        for e in events(url, record_type="billing_accounts", record_id=w.account)
    ] == ["create"]


def test_an_inactive_family_may_change(module_seeded_url, client, staff):
    url = module_seeded_url
    w = dd.world(url, family_active=False)
    version = summary(client, staff, w.family)["version"]

    response = send(client, staff, w.family, {"version": version, "due_offset_days": 2})

    assert response.status_code == 200


def test_unknown_family_is_404_after_the_body(client, staff):
    unknown = uuid.uuid4()

    assert (
        send(
            client, staff, unknown, {"version": None, "due_offset_days": 61}
        ).status_code
        == 422
    )
    assert send(client, staff, unknown, {"due_offset_days": 1}).status_code == 422
    response = send(client, staff, unknown, {"version": None, "due_offset_days": 1})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
def test_other_billing_manage_roles_may_change(module_seeded_url, client, role):
    url = module_seeded_url
    user = create_user(url, roles=(role,))
    family = bare_family(url)

    response = send(client, user, family, {"version": None, "due_offset_days": 1})

    assert response.status_code == 200


# --- C3 / B3: forward only -----------------------------------------------------------


def _session_due(url: str, it: scs.Item) -> date:
    [row] = scs.charges_of(url, it.session)
    due: date = row.due_date
    return due


@pytest.mark.parametrize("offset", [0, 7])
def test_session_charges_use_the_offset_at_posting(
    module_seeded_url, client, staff, offset
):
    url = module_seeded_url
    scs.reset(url)
    day = scs.FLOOR + timedelta(days=9)
    before = scs.item(url, starts_at=scs.local(day, 10))
    assert before.family is not None
    scs.set_floor(url, scs.FLOOR)
    with frozen_clock(datetime(2031, 3, 20, 18, 0, tzinfo=UTC)):
        scs.run(url)
    version = summary(client, staff, before.family)["version"]
    send(client, staff, before.family, {"version": version, "due_offset_days": offset})
    after = scs.item(
        url,
        starts_at=scs.local(day, 12),
        student=before.student,
        subject=before.subject,
        family=False,
        price=None,
    )
    with frozen_clock(datetime(2031, 3, 20, 18, 0, tzinfo=UTC)):
        scs.run(url)

    assert _session_due(url, before) == day  # posted before: unchanged
    assert _session_due(url, after) == day + timedelta(days=offset)


@pytest.mark.parametrize("offset", [0, 7])
def test_package_charges_use_the_offset_at_posting(
    module_seeded_url, client, staff, offset
):
    url = module_seeded_url
    w = ps.world(url)
    product = ps.product(url)
    sold_on = date(2031, 3, 2)
    first = ps.sell(url, w.student, product, sold_on)
    version = summary(client, staff, w.family)["version"]
    send(client, staff, w.family, {"version": version, "due_offset_days": offset})
    second = ps.sell(url, w.student, product, sold_on + timedelta(days=1))

    rows = {
        row.package_id: row.due_date
        for row in run_sql(
            url,
            "SELECT package_id, due_date FROM charges WHERE package_id = ANY(:p)",
            {"p": [first, second]},
        )
    }
    assert rows[first] == sold_on
    assert rows[second] == sold_on + timedelta(days=1 + offset)


@pytest.mark.parametrize("offset", [0, 7])
def test_manual_charges_follow_apply_family_offset(module_seeded_url, offset):
    url = module_seeded_url
    w = dd.world(url)
    run_sql(
        url,
        "UPDATE billing_accounts SET due_offset_days = :d WHERE id = :id",
        {"d": offset, "id": w.account},
    )

    async def _post(session: AsyncSession) -> list[date]:
        found = []
        for apply in (False, True):
            posted = await factories.charge(
                session, w.student, due=TODAY, apply_family_offset=apply
            )
            [row] = await billing.charge_balances(
                session, w.account, charge_id=posted.charge_id, today=TODAY
            )
            found.append(row.effective_due_date)
        return found

    assert in_uow(url, _post) == [TODAY, TODAY + timedelta(days=offset)]


# --- C3: a racing posting ------------------------------------------------------------


async def _patch_then_post(url: str, w: dd.World) -> tuple[float, float, uuid.UUID]:
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()
    ended: dict[str, float] = {}

    async def run_a() -> None:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a), w.actor) as uow:
                version = (
                    await billing.account_of_family(uow.session, w.family)
                ).version  # type: ignore[union-attr]
                await billing.update_due_offset(
                    uow.session, w.family, version=version, due_offset_days=9
                )
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
            ended["a"] = time.monotonic()
        finally:
            holding.set()

    async def run_b() -> uuid.UUID:
        await holding.wait()
        async with UnitOfWork(create_sessionmaker(engine_b)) as uow:
            posted = await factories.charge(
                uow.session, w.student, due=TODAY, apply_family_offset=True
            )
        ended["b"] = time.monotonic()
        return posted.charge_id

    try:
        _, charge = await asyncio.gather(run_a(), run_b())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return ended["a"], ended["b"], charge


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_a_posting_waits_for_the_patch_and_gets_the_new_offset(
    module_seeded_url, repeat
):
    url = module_seeded_url
    w = dd.world(url)

    ended_a, ended_b, charge = asyncio.run(_patch_then_post(url, w))

    assert ended_b >= ended_a
    [row] = run_sql(url, "SELECT due_date FROM charges WHERE id = :id", {"id": charge})
    assert row.due_date == TODAY + timedelta(days=9)
