"""The custom price routes (#95 E, H6/H5 tests) through the real app: the
shape (the catalog item with its current version, the target, status,
``in_use``), the create codes (no backdating, references in one response,
``409 custom_price_overlap``), the list filters, order, pagination and
statement bound, ``PATCH`` (end, re-open, ``field_immutable``,
``custom_price_expiry_invalid`` against today and a charged service date,
``version_conflict``), ``DELETE`` (upcoming only; ``409
custom_price_in_use`` for a row a future session's charge uses), ``404``s
and the audit events."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import OWNER
from app.workflows import custom_prices
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.billing import persist_service_price
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.scheduling import persist_subject
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.session_charge_support import (
    FLOOR,
    charges_of,
    item,
    local,
    reset,
    run,
    set_floor,
)

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZONE)
TODAY = date(2031, 3, 10)
CUSTOM = "/api/v1/billing/custom-prices"
INVALID = (422, "validation_error")
ITEM_KEYS = {
    "id",
    "service_price",
    "student",
    "family",
    "effective_from",
    "expires_on",
    "amount",
    "status",
    "in_use",
    "version",
    "created_at",
}


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
class World:
    subject: uuid.UUID
    item: uuid.UUID
    level_item: uuid.UUID
    period_item: uuid.UUID
    student: uuid.UUID
    student_name: str
    family: uuid.UUID
    family_name: str


@pytest.fixture
def world(module_seeded_url: str) -> World:
    async def _make(session: AsyncSession) -> World:
        subject = await persist_subject(session)
        any_item = await persist_service_price(
            session, subject_id=subject.id, versions=[(date(2030, 1, 1), "300.00")]
        )
        level_item = await persist_service_price(
            session,
            subject_id=subject.id,
            education_level_id=subject.education_level_id,
            versions=[(date(2030, 1, 1), "320.00")],
        )
        period_item = await persist_service_price(
            session, subject_id=subject.id, pricing_model="per_period"
        )
        student = await persist_student(
            session, contacts=0, given_names="Zoe", family_names="Zúñiga"
        )
        family = FamilyFactory.build(name=f"Familia A {uuid.uuid4().hex[:6]}")
        session.add(family)
        await session.flush()
        await add_to_family(session, family, student)
        return World(
            subject=subject.id,
            item=any_item.id,
            level_item=level_item.id,
            period_item=period_item.id,
            student=student.id,
            student_name=f"{student.given_names} {student.family_names}",
            family=family.id,
            family_name=family.name,
        )

    return in_uow(module_seeded_url, _make)


def body(world: World, **values: Any) -> dict[str, Any]:
    return {
        "service_price_id": str(world.item),
        "student_id": str(world.student),
        "effective_from": str(TODAY),
        "amount": "250.00",
        **values,
    }


def created(client: TestClient, cookie: SetCookie, payload: dict[str, Any]) -> Any:
    response = post(client, cookie, CUSTOM, payload)
    assert response.status_code == 201, response.text
    return response.json()


def codes(response: Response) -> set[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return {(str(e["path"][-1]), e["code"]) for e in response.json()["errors"]}


# --- Create and read (E1, E3, E4) ----------------------------------------------------


def test_create_returns_the_item(client, staff, world, module_seeded_url):
    found = created(client, staff, body(world))

    assert set(found) == ITEM_KEYS
    assert found["student"] == {"id": str(world.student), "name": world.student_name}
    assert found["family"] is None
    assert (found["status"], found["in_use"], found["amount"], found["expires_on"]) == (
        "current",
        False,
        "250.00",
        None,
    )
    item = found["service_price"]
    assert item["id"] == str(world.item)
    assert item["current"]["amount"] == "300.00"
    assert "next" not in item and "versions" not in item
    [event] = events(
        module_seeded_url, record_type="custom_prices", record_id=uuid.UUID(found["id"])
    )
    assert event.action == "create" and event.changes["amount"]["new"] == "250.00"
    assert get(client, staff, f"{CUSTOM}/{found['id']}").json() == found


def test_no_backdating(client, staff, world):
    response = post(
        client,
        staff,
        CUSTOM,
        body(world, effective_from=str(TODAY - timedelta(days=1))),
    )

    assert code_of(response) == (422, "custom_price_effective_from_invalid")


def test_reference_codes_in_one_response(client, staff, world):
    response = post(
        client,
        staff,
        CUSTOM,
        body(world, service_price_id=str(uuid.uuid4()), student_id=str(uuid.uuid4())),
    )
    assert codes(response) == {
        ("service_price_id", "service_price_unknown"),
        ("student_id", "student_unknown"),
    }
    response = post(
        client,
        staff,
        CUSTOM,
        body(
            world,
            service_price_id=str(world.period_item),
            student_id=None,
            family_id=str(uuid.uuid4()),
        ),
    )
    assert codes(response) == {
        ("service_price_id", "pricing_model_not_allowed"),
        ("family_id", "family_unknown"),
    }


def test_body_codes(client, staff, world):
    response = post(
        client,
        staff,
        CUSTOM,
        body(
            world,
            family_id=str(world.family),
            amount="-1.00",
            expires_on=str(TODAY - timedelta(days=1)),
        ),
    )

    assert codes(response) == {
        ("family_id", "target_conflict"),
        ("expires_on", "dates_order"),
        ("amount", "amount_negative"),
    }


def test_overlapping_dates_are_409(client, staff, world):
    created(client, staff, body(world, expires_on=str(TODAY + timedelta(days=9))))

    clash = post(
        client,
        staff,
        CUSTOM,
        body(world, effective_from=str(TODAY + timedelta(days=9))),
    )
    assert code_of(clash) == (409, "custom_price_overlap")
    # Another item, the family, or the day after are fine.
    created(client, staff, body(world, service_price_id=str(world.level_item)))
    created(client, staff, body(world, student_id=None, family_id=str(world.family)))
    created(client, staff, body(world, effective_from=str(TODAY + timedelta(days=10))))


def test_unknown_ids_are_404(client, staff):
    missing = f"{CUSTOM}/{uuid.uuid4()}"
    assert code_of(get(client, staff, missing)) == (404, "not_found")
    assert code_of(
        patch(client, staff, missing, {"version": 1, "expires_on": None})
    ) == (
        404,
        "not_found",
    )
    assert code_of(delete(client, staff, missing)) == (404, "not_found")


# --- List (E2) -----------------------------------------------------------------------


def test_list_filters_order_and_pagination(client, staff, world, module_seeded_url):
    upcoming = created(
        client, staff, body(world, effective_from=str(TODAY + timedelta(days=30)))
    )
    current = created(
        client, staff, body(world, expires_on=str(TODAY + timedelta(days=29)))
    )
    level = created(client, staff, body(world, service_price_id=str(world.level_item)))
    family = created(
        client, staff, body(world, student_id=None, family_id=str(world.family))
    )

    def ids(**params: Any) -> list[str]:
        response = get(client, staff, CUSTOM, **params)
        assert response.status_code == 200, response.text
        return [c["id"] for c in response.json()["items"]]

    # "Familia A …" sorts before the student "… Zúñiga"; then the item
    # order (any level first), then the newest start.
    assert ids(service_price_id=str(world.item)) == [
        family["id"],
        upcoming["id"],
        current["id"],
    ]
    assert ids(student_id=str(world.student)) == [
        upcoming["id"],
        current["id"],
        level["id"],
    ]
    assert ids(family_id=str(world.family)) == [family["id"]]
    assert ids(student_id=str(world.student), status="upcoming") == [upcoming["id"]]
    page = get(
        client, staff, CUSTOM, student_id=str(world.student), limit=1, offset=1
    ).json()
    assert (page["total"], [c["id"] for c in page["items"]]) == (3, [current["id"]])
    assert (
        get(client, staff, CUSTOM, student_id=str(world.student), offset=9).json()[
            "total"
        ]
        == 3
    )

    count, listed = count_statements(
        module_seeded_url,
        lambda s: custom_prices.list_custom_prices(
            s,
            student_id=world.student,
            family_id=None,
            service_price_id=None,
            statuses=(),
            page=OffsetParams(50, 0),
        ),
    )
    assert listed.total == 3
    assert count <= 3, count


# --- PATCH (E5) ----------------------------------------------------------------------


def test_end_and_reopen(client, staff, world, module_seeded_url):
    found = created(client, staff, body(world))
    path = f"{CUSTOM}/{found['id']}"

    ended = patch(client, staff, path, {"version": 1, "expires_on": str(TODAY)})
    assert ended.status_code == 200, ended.text
    assert (ended.json()["expires_on"], ended.json()["version"]) == (str(TODAY), 2)
    same = patch(client, staff, path, {"version": 2, "expires_on": str(TODAY)})
    assert same.json()["version"] == 2
    later = created(
        client, staff, body(world, effective_from=str(TODAY + timedelta(days=1)))
    )

    assert code_of(patch(client, staff, path, {"version": 2, "expires_on": None})) == (
        409,
        "custom_price_overlap",
    )
    assert code_of(delete(client, staff, f"{CUSTOM}/{later['id']}")) == (204, None)
    reopened = patch(client, staff, path, {"version": 2, "expires_on": None})
    assert reopened.json()["expires_on"] is None
    updates = events(
        module_seeded_url,
        record_type="custom_prices",
        record_id=uuid.UUID(found["id"]),
        action="update",
    )
    assert len(updates) == 2
    assert set(updates[0].changes) == {"expires_on"}


def test_patch_rules(client, staff, world):
    found = created(
        client, staff, body(world, effective_from=str(TODAY + timedelta(days=5)))
    )
    path = f"{CUSTOM}/{found['id']}"

    assert codes(
        patch(client, staff, path, {"version": 1, "expires_on": None, "amount": "1.00"})
    ) == {("amount", "field_immutable")}
    assert code_of(
        patch(
            client,
            staff,
            path,
            {"version": 1, "expires_on": str(TODAY + timedelta(days=4))},
        )
    ) == (422, "custom_price_expiry_invalid")
    assert code_of(
        patch(
            client,
            staff,
            path,
            {"version": 3, "expires_on": str(TODAY + timedelta(days=9))},
        )
    ) == (409, "version_conflict")


def test_a_charged_custom_price_ends_after_its_last_service_date_and_stays(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    reset(url)
    session_day = TODAY + timedelta(days=15)
    it = item(url, starts_at=local(session_day), price="300.00")
    assert it.price is not None
    found = created(
        client,
        staff,
        {
            "service_price_id": str(it.price),
            "student_id": str(it.student),
            "effective_from": str(TODAY + timedelta(days=10)),
            "amount": "180.00",
        },
    )
    set_floor(url, FLOOR)
    run(url)
    [charge] = charges_of(url, it.session)
    assert str(charge.custom_price_id) == found["id"]
    path = f"{CUSTOM}/{found['id']}"

    assert get(client, staff, path).json()["in_use"] is True
    assert code_of(delete(client, staff, path)) == (409, "custom_price_in_use")
    assert code_of(
        patch(
            client,
            staff,
            path,
            {"version": 1, "expires_on": str(session_day - timedelta(days=1))},
        )
    ) == (422, "custom_price_expiry_invalid")
    ended = patch(client, staff, path, {"version": 1, "expires_on": str(session_day)})
    assert ended.status_code == 200, ended.text
    reset(url)


# --- DELETE (E6) ---------------------------------------------------------------------


def test_only_an_upcoming_custom_price_is_deleted(
    client, staff, world, module_seeded_url
):
    current = created(client, staff, body(world, expires_on=str(TODAY)))
    upcoming = created(
        client, staff, body(world, effective_from=str(TODAY + timedelta(days=1)))
    )

    assert code_of(delete(client, staff, f"{CUSTOM}/{current['id']}")) == (
        409,
        "custom_price_in_effect",
    )
    assert delete(client, staff, f"{CUSTOM}/{upcoming['id']}").status_code == 204
    assert code_of(get(client, staff, f"{CUSTOM}/{upcoming['id']}")) == (
        404,
        "not_found",
    )
    [event] = events(
        module_seeded_url,
        record_type="custom_prices",
        record_id=uuid.UUID(upcoming["id"]),
        action="delete",
    )
    assert event.changes["amount"] == {"old": "250.00", "new": None}
