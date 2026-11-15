"""The service price catalog routes (#89 B, E3, E4) through the real app.

Every B3 field code and reference code; the 409 for an existing key; the
response shapes (names, ``current``/``next``/``status``, version order);
the E3 date rules with the frozen clock and the center zone (a first
version in the past, later versions from today and after the latest,
deleting upcoming versions only, the local date at 23:30); ``404``s;
``package``/``per_period`` rows hidden; the audit events; the guard's
``409 price_version_in_use`` with the predicate swapped to ``true``. The
list order, filters and statement budget are in
``test_price_catalog_list.py``; constraints, the SQL guard and the race in
``test_price_catalog_rules.py``; the access matrix in
``tests/authz/matrix.toml``.
"""

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

from app.modules.billing import service as billing
from app.modules.billing.schemas import PriceVersionCreate
from app.modules.catalog.models import EducationLevel, Subject
from app.modules.identity.permissions import FINANCE_STAFF, OWNER
from app.workflows import price_catalog
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.billing import persist_service_price
from tests.factories.courses import persist_course_cohort
from tests.factories.scheduling import persist_class_group, persist_subject
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.price_catalog_support import (
    every_version_referenced as referenced,
)

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZONE)
TODAY = date(2031, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
PRICES = "/api/v1/billing/service-prices"

CREATED = (201, None)
INVALID = (422, "validation_error")
EXISTS = (409, "service_price_exists")
DATE_INVALID = (422, "price_effective_from_invalid")
IN_EFFECT = (409, "price_version_in_effect")
IN_USE = (409, "price_version_in_use")
NOT_FOUND = (404, "not_found")

ITEM_KEYS = {
    "id",
    "class_type",
    "pricing_model",
    "subject",
    "education_level",
    "duration_minutes",
    "cohort",
    "group",
    "period_frequency",
    "current",
    "next",
    "created_at",
}
VERSION_KEYS = {"id", "effective_from", "amount", "status", "created_at"}


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[FrozenClock]:
    frozen.set(NOW)
    yield frozen
    frozen.set(NOW)


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
    subject_name: str
    subject_level: uuid.UUID
    subject_level_name: str
    level: uuid.UUID
    level_name: str
    other_subject: uuid.UUID
    cohort: uuid.UUID
    cohort_name: str
    group: uuid.UUID
    group_name: str
    other_cohort: uuid.UUID
    other_group: uuid.UUID


@pytest.fixture
def world(module_seeded_url: str) -> World:
    async def _make(session: AsyncSession) -> World:
        subject = await persist_subject(session)
        subject_level = await session.get(EducationLevel, subject.education_level_id)
        assert subject_level is not None
        other = await persist_subject(session)
        level = EducationLevel(name=f"Primaria {uuid.uuid4().hex[:8]}", sort_order=40)
        session.add(level)
        await session.flush()
        cohort = await persist_course_cohort(session, subject_id=subject.id)
        group = await persist_class_group(session, subject_id=subject.id)
        other_cohort = await persist_course_cohort(session, subject_id=other.id)
        other_group = await persist_class_group(session, subject_id=other.id)
        return World(
            subject=subject.id,
            subject_name=subject.name,
            subject_level=subject_level.id,
            subject_level_name=subject_level.name,
            level=level.id,
            level_name=level.name,
            other_subject=other.id,
            cohort=cohort.id,
            cohort_name=cohort.name,
            group=group.id,
            group_name=group.name,
            other_cohort=other_cohort.id,
            other_group=other_group.id,
        )

    return in_uow(module_seeded_url, _make)


@dataclass(frozen=True)
class Ref:
    """A parametrized body value naming a :class:`World` field."""

    name: str


def resolved(world: World, value: Any) -> Any:
    return getattr(world, value.name) if isinstance(value, Ref) else value


def _json(values: dict[str, Any]) -> dict[str, Any]:
    return {
        k: str(v) if isinstance(v, uuid.UUID | date) else v
        for k, v in values.items()
        if v is not ...
    }


def create(
    client: TestClient, cookie: SetCookie, world: World, **body: Any
) -> Response:
    values = {
        "class_type": "individual",
        "pricing_model": "per_session",
        "subject_id": world.subject,
        "effective_from": TODAY,
        "amount": "300.00",
        **body,
    }
    return post(client, cookie, PRICES, _json(values))


def created(client: TestClient, cookie: SetCookie, world: World, **body: Any) -> Any:
    response = create(client, cookie, world, **body)
    assert code_of(response) == CREATED, response.text
    return response.json()


def add_version(
    client: TestClient,
    cookie: SetCookie,
    item_id: Any,
    effective_from: Any,
    amount: Any,
) -> Response:
    return post(
        client,
        cookie,
        f"{PRICES}/{item_id}/versions",
        _json({"effective_from": effective_from, "amount": amount}),
    )


def remove(
    client: TestClient, cookie: SetCookie, item_id: Any, version_id: Any
) -> Response:
    return delete(client, cookie, f"{PRICES}/{item_id}/versions/{version_id}")


def field_codes(response: Response) -> set[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return {(str(e["path"][-1]), e["code"]) for e in response.json()["errors"]}


def price_rows(url: str, subject: uuid.UUID) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM service_prices WHERE subject_id = :s",
        {"s": subject},
    )
    return int(row.n)


def version_dates(url: str, item_id: Any) -> list[date]:
    rows = run_sql(
        url,
        "SELECT effective_from FROM price_versions WHERE service_price_id = :p"
        " ORDER BY effective_from",
        {"p": item_id},
    )
    return [row.effective_from for row in rows]


# --- Create (B3) ---------------------------------------------------------------------


def test_create_returns_the_detail_with_names(client, staff, world):
    body = created(
        client,
        staff,
        world,
        education_level_id=world.level,
        duration_minutes=60,
        effective_from=YESTERDAY,
        amount="350.5",
    )

    assert set(body) == ITEM_KEYS | {"versions"}
    assert body["class_type"] == "individual"
    assert body["pricing_model"] == "per_session"
    assert body["subject"] == {
        "id": str(world.subject),
        "name": world.subject_name,
        "education_level_id": str(world.subject_level),
        "education_level_name": world.subject_level_name,
    }
    assert body["education_level"] == {"id": str(world.level), "name": world.level_name}
    assert body["duration_minutes"] == 60
    assert body["cohort"] is None and body["group"] is None
    [version] = body["versions"]
    assert set(version) == VERSION_KEYS
    assert version["effective_from"] == str(YESTERDAY)
    assert version["amount"] == "350.50"
    assert version["status"] == "current"
    assert body["current"] == version
    assert body["next"] is None


def test_finance_staff_create_and_scoped_items_name_their_scope(
    client, module_seeded_url, world
):
    finance = sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )

    cohort = created(
        client,
        finance,
        world,
        class_type="course",
        pricing_model="full_course",
        cohort_id=world.cohort,
        amount="4500.00",
    )
    group = created(
        client,
        finance,
        world,
        class_type="group",
        pricing_model="full_course",
        group_id=world.group,
        effective_from=TOMORROW,
    )

    assert cohort["cohort"] == {"id": str(world.cohort), "name": world.cohort_name}
    assert cohort["group"] is None and cohort["education_level"] is None
    assert group["group"] == {"id": str(world.group), "name": world.group_name}
    assert group["current"] is None
    assert group["next"]["status"] == "upcoming"


def test_a_first_version_may_start_in_the_past(client, staff, world):
    body = created(client, staff, world, effective_from=date(2000, 1, 1))

    assert body["current"]["effective_from"] == "2000-01-01"


def test_the_same_key_is_409_with_the_existing_id(
    client, staff, world, module_seeded_url
):
    first = created(client, staff, world)

    again = create(client, staff, world, amount="999.00", effective_from=TOMORROW)

    assert code_of(again) == EXISTS
    assert again.json()["details"] == {"service_price_id": first["id"]}
    assert price_rows(module_seeded_url, world.subject) == 1
    # Differing only in the level, the duration or the scope is another key.
    created(client, staff, world, education_level_id=world.level)
    created(client, staff, world, duration_minutes=45)
    created(client, staff, world, class_type="group")
    created(client, staff, world, class_type="group", group_id=world.group)
    assert price_rows(module_seeded_url, world.subject) == 5


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            {"pricing_model": "per_period"},
            {("period_frequency", "period_frequency_required")},
            id="per-period-without-frequency",
        ),
        pytest.param(
            {"period_frequency": "weekly"},
            {("period_frequency", "period_frequency_not_allowed")},
            id="frequency-on-per-session",
        ),
        pytest.param(
            {"pricing_model": "per_period", "period_frequency": "daily"},
            {("period_frequency", "literal_error")},
            id="unknown-frequency",
        ),
        pytest.param(
            {
                "class_type": "course",
                "pricing_model": "per_period",
                "period_frequency": "monthly",
            },
            {("pricing_model", "pricing_model_not_allowed")},
            id="course-per-period",
        ),
        pytest.param(
            {
                "pricing_model": "per_period",
                "period_frequency": "monthly",
                "duration_minutes": 60,
            },
            {("duration_minutes", "duration_not_allowed")},
            id="duration-on-per-period",
        ),
        pytest.param(
            {"pricing_model": "package"},
            {("pricing_model", "literal_error")},
            id="package",
        ),
        pytest.param(
            {"class_type": "course"},
            {("pricing_model", "pricing_model_not_allowed")},
            id="course-per-session",
        ),
        pytest.param(
            {"pricing_model": "full_course"},
            {("pricing_model", "pricing_model_not_allowed")},
            id="individual-full-course",
        ),
        pytest.param(
            {"class_type": "group", "cohort_id": Ref("cohort")},
            {("cohort_id", "scope_not_allowed")},
            id="cohort-on-group",
        ),
        pytest.param(
            {"group_id": Ref("group")},
            {("group_id", "scope_not_allowed")},
            id="group-on-individual",
        ),
        pytest.param(
            {
                "class_type": "course",
                "pricing_model": "full_course",
                "cohort_id": Ref("cohort"),
                "group_id": Ref("group"),
            },
            {("group_id", "scope_conflict")},
            id="both-scopes",
        ),
        pytest.param(
            {
                "class_type": "group",
                "group_id": Ref("group"),
                "education_level_id": Ref("level"),
            },
            {("education_level_id", "level_not_allowed")},
            id="level-on-scoped",
        ),
        pytest.param(
            {
                "class_type": "group",
                "pricing_model": "full_course",
                "duration_minutes": 60,
            },
            {("duration_minutes", "duration_not_allowed")},
            id="duration-on-full-course",
        ),
        pytest.param(
            {"class_type": "group", "group_id": Ref("group"), "duration_minutes": 60},
            {("duration_minutes", "duration_not_allowed")},
            id="duration-on-scoped",
        ),
        pytest.param(
            {"duration_minutes": 14},
            {("duration_minutes", "duration_out_of_range")},
            id="duration-14",
        ),
        pytest.param(
            {"duration_minutes": 481},
            {("duration_minutes", "duration_out_of_range")},
            id="duration-481",
        ),
        pytest.param(
            {"duration_minutes": 10**30},
            {("duration_minutes", "duration_out_of_range")},
            id="duration-huge",
        ),
        pytest.param(
            {"effective_from": "1999-12-31"},
            {("effective_from", "date_out_of_range")},
            id="date-1999",
        ),
        pytest.param(
            {"effective_from": "2100-01-01"},
            {("effective_from", "date_out_of_range")},
            id="date-2100",
        ),
        pytest.param(
            {"amount": "-0.01"}, {("amount", "amount_negative")}, id="negative"
        ),
        pytest.param({"amount": 12.5}, {("amount", "money_invalid")}, id="number"),
        pytest.param({"amount": None}, {("amount", "money_invalid")}, id="null"),
        pytest.param(
            {"amount": "1.234"}, {("amount", "money_precision")}, id="precision"
        ),
        pytest.param(
            {"amount": "1234567890123"},
            {("amount", "money_out_of_range")},
            id="out-of-range",
        ),
        pytest.param({"amount": ...}, {("amount", "missing")}, id="missing-amount"),
        pytest.param({"extra": 1}, {("extra", "extra_forbidden")}, id="extra-field"),
    ],
)
def test_body_errors(client, staff, world, module_seeded_url, body, expected):
    values = {k: resolved(world, v) for k, v in body.items()}

    response = create(client, staff, world, **values)

    assert field_codes(response) == expected
    assert price_rows(module_seeded_url, world.subject) == 0


def test_body_errors_come_together(client, staff, world):
    response = create(
        client,
        staff,
        world,
        class_type="course",
        pricing_model="per_session",
        education_level_id=world.level,
        duration_minutes=5,
        effective_from="1999-01-01",
        amount="-5.00",
    )

    assert field_codes(response) == {
        ("pricing_model", "pricing_model_not_allowed"),
        ("duration_minutes", "duration_out_of_range"),
        ("effective_from", "date_out_of_range"),
        ("amount", "amount_negative"),
    }


def _inactive(url: str, model: type, row_id: uuid.UUID) -> None:
    table = model.__tablename__  # type: ignore[attr-defined]
    run_sql(url, f"UPDATE {table} SET is_active = false WHERE id = :id", {"id": row_id})  # noqa: S608


@pytest.mark.parametrize(
    ("change", "body", "expected"),
    [
        pytest.param(
            None,
            {"subject_id": uuid.UUID(int=1)},
            {("subject_id", "subject_unknown")},
            id="subject-unknown",
        ),
        pytest.param(
            "subject", {}, {("subject_id", "subject_inactive")}, id="subject-inactive"
        ),
        pytest.param(
            "subject_level",
            {},
            {("subject_id", "subject_inactive")},
            id="subject-level-inactive",
        ),
        pytest.param(
            None,
            {"education_level_id": uuid.UUID(int=2)},
            {("education_level_id", "education_level_unknown")},
            id="level-unknown",
        ),
        pytest.param(
            "level",
            {"education_level_id": Ref("level")},
            {("education_level_id", "education_level_inactive")},
            id="level-inactive",
        ),
        pytest.param(
            None,
            {
                "class_type": "course",
                "pricing_model": "full_course",
                "cohort_id": uuid.UUID(int=3),
            },
            {("cohort_id", "cohort_unknown")},
            id="cohort-unknown",
        ),
        pytest.param(
            None,
            {"class_type": "group", "group_id": uuid.UUID(int=4)},
            {("group_id", "group_unknown")},
            id="group-unknown",
        ),
        pytest.param(
            None,
            {
                "class_type": "course",
                "pricing_model": "full_course",
                "cohort_id": Ref("other_cohort"),
            },
            {("cohort_id", "scope_subject_mismatch")},
            id="cohort-of-another-subject",
        ),
        pytest.param(
            None,
            {"class_type": "group", "group_id": Ref("other_group")},
            {("group_id", "scope_subject_mismatch")},
            id="group-of-another-subject",
        ),
    ],
)
def test_reference_errors(
    client, staff, world, module_seeded_url, change, body, expected
):
    url = module_seeded_url
    if change == "subject":
        _inactive(url, Subject, world.subject)
    elif change == "subject_level":
        _inactive(url, EducationLevel, world.subject_level)
    elif change == "level":
        _inactive(url, EducationLevel, world.level)
    values = {k: resolved(world, v) for k, v in body.items()}

    response = create(client, staff, world, **values)

    assert field_codes(response) == expected
    assert price_rows(url, world.subject) == 0


def test_reference_errors_come_together(client, staff, world):
    response = create(
        client,
        staff,
        world,
        subject_id=uuid.UUID(int=5),
        class_type="group",
        group_id=world.group,
    )
    assert field_codes(response) == {
        ("subject_id", "subject_unknown"),
        ("group_id", "scope_subject_mismatch"),
    }

    level = create(
        client,
        staff,
        world,
        subject_id=uuid.UUID(int=6),
        education_level_id=uuid.UUID(int=7),
    )
    assert field_codes(level) == {
        ("subject_id", "subject_unknown"),
        ("education_level_id", "education_level_unknown"),
    }


def test_existing_items_keep_working_after_a_deactivation(
    client, staff, world, module_seeded_url
):
    item = created(client, staff, world, effective_from=YESTERDAY)
    _inactive(module_seeded_url, Subject, world.subject)

    assert code_of(get(client, staff, f"{PRICES}/{item['id']}")) == (200, None)
    assert (
        code_of(add_version(client, staff, item["id"], TOMORROW, "310.00")) == CREATED
    )
    assert code_of(create(client, staff, world, duration_minutes=60)) == INVALID


# --- Versions (B5, B6; E3) -----------------------------------------------------------


def test_a_later_version_can_not_start_in_the_past(
    client, staff, world, module_seeded_url
):
    item = created(client, staff, world, effective_from=date(2031, 1, 1))

    response = add_version(client, staff, item["id"], YESTERDAY, "320.00")

    assert code_of(response) == DATE_INVALID
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", "effective_from"], "price_effective_from_invalid")
    ]
    assert version_dates(module_seeded_url, item["id"]) == [date(2031, 1, 1)]


def test_a_version_from_today_replaces_a_past_one(client, staff, world):
    item = created(client, staff, world, effective_from=date(2031, 1, 1))

    response = add_version(client, staff, item["id"], TODAY, "320.00")

    assert code_of(response) == CREATED, response.text
    body = response.json()
    assert [
        (v["effective_from"], v["amount"], v["status"]) for v in body["versions"]
    ] == [
        (str(TODAY), "320.00", "current"),
        ("2031-01-01", "300.00", "past"),
    ]
    assert body["current"]["amount"] == "320.00"


def test_a_version_must_follow_the_latest(client, staff, world, module_seeded_url):
    later = TODAY + timedelta(days=30)
    item = created(client, staff, world, effective_from=YESTERDAY)
    assert code_of(add_version(client, staff, item["id"], later, "320.00")) == CREATED

    assert (
        code_of(add_version(client, staff, item["id"], later, "330.00")) == DATE_INVALID
    )
    assert (
        code_of(
            add_version(client, staff, item["id"], later - timedelta(days=1), "330.00")
        )
        == DATE_INVALID
    )
    response = add_version(
        client, staff, item["id"], later + timedelta(days=1), "320.00"
    )
    # The same amount again is an explicit re-publish (accepted).
    assert code_of(response) == CREATED
    body = response.json()
    assert body["current"]["effective_from"] == str(YESTERDAY)
    assert body["next"]["effective_from"] == str(later)
    assert [v["status"] for v in body["versions"]] == [
        "upcoming",
        "upcoming",
        "current",
    ]
    assert version_dates(module_seeded_url, item["id"]) == [
        YESTERDAY,
        later,
        later + timedelta(days=1),
    ]


def test_a_null_version_means_not_offered(client, staff, world, module_seeded_url):
    item = created(client, staff, world, effective_from=YESTERDAY)

    response = add_version(client, staff, item["id"], TOMORROW, None)

    assert code_of(response) == CREATED, response.text
    assert response.json()["next"]["amount"] is None
    again = add_version(client, staff, item["id"], TOMORROW + timedelta(days=1), None)
    assert code_of(again) == CREATED
    missing = post(
        client,
        staff,
        f"{PRICES}/{item['id']}/versions",
        {"effective_from": str(TOMORROW + timedelta(days=9))},
    )
    assert field_codes(missing) == {("amount", "missing")}
    negative = add_version(
        client, staff, item["id"], TOMORROW + timedelta(days=9), "-1.00"
    )
    assert field_codes(negative) == {("amount", "amount_negative")}


def test_deleting_an_upcoming_version_restores_the_lookup(
    client, staff, world, module_seeded_url
):
    url = module_seeded_url
    item = created(client, staff, world, effective_from=YESTERDAY)
    body = add_version(client, staff, item["id"], TOMORROW, "400.00").json()
    upcoming = body["next"]["id"]

    def lookup_on(on: date) -> str | None:
        found = in_uow(
            url,
            lambda s: billing.lookup_price(
                s,
                class_type="individual",
                pricing_model="per_session",
                subject_id=world.subject,
                on=on,
            ),
        )
        return None if found is None else str(found.amount)

    assert lookup_on(TOMORROW) == "400.00"
    assert code_of(remove(client, staff, item["id"], upcoming)) == (204, None)
    assert lookup_on(TOMORROW) == "300.00"
    assert version_dates(url, item["id"]) == [YESTERDAY]


def test_a_current_or_past_version_is_never_deleted(
    client, staff, world, module_seeded_url
):
    item = created(client, staff, world, effective_from=date(2031, 1, 1))
    body = add_version(client, staff, item["id"], TODAY, "320.00").json()
    current, past = body["versions"]

    assert code_of(remove(client, staff, item["id"], current["id"])) == IN_EFFECT
    assert code_of(remove(client, staff, item["id"], past["id"])) == IN_EFFECT
    assert len(version_dates(module_seeded_url, item["id"])) == 2


def test_deleting_the_only_upcoming_version_reopens_the_first_version_rule(
    client, staff, world, module_seeded_url
):
    item = created(client, staff, world, effective_from=TOMORROW)

    assert code_of(remove(client, staff, item["id"], item["versions"][0]["id"])) == (
        204,
        None,
    )
    detail = get(client, staff, f"{PRICES}/{item['id']}").json()
    assert detail["versions"] == [] and detail["current"] is None
    assert (
        code_of(add_version(client, staff, item["id"], YESTERDAY, "290.00")) == CREATED
    )
    assert version_dates(module_seeded_url, item["id"]) == [YESTERDAY]


def test_today_is_the_center_local_date(module_seeded_url, world, at_now):
    url = module_seeded_url
    # 23:30 on the 10th in Mexico City is 05:30 UTC on the 11th.
    late = datetime(2031, 3, 10, 23, 30, tzinfo=ZONE)
    at_now.set(late)
    item = in_uow(
        url,
        lambda s: persist_service_price(
            s, subject_id=world.subject, versions=[(date(2031, 1, 1), "300.00")]
        ),
    )

    def add(on: date) -> Any:
        return in_uow(
            url,
            lambda s: price_catalog.add_price_version(
                s, item.id, PriceVersionCreate(effective_from=on, amount=None)
            ),
        )

    detail = add(TODAY)
    assert [v.status for v in detail.versions] == ["current", "past"]
    with pytest.raises(billing.PriceEffectiveFromInvalidError):
        add(TODAY - timedelta(days=1))


# --- Reads and 404s (B4) -------------------------------------------------------------


def test_unknown_and_hidden_items_are_404(client, staff, world, module_seeded_url):
    hidden = [
        in_uow(
            module_seeded_url,
            lambda s, key=key: persist_service_price(
                s, versions=[(YESTERDAY, "100.00")], **key
            ),
        ).id
        for key in (
            {"subject_id": world.subject, "pricing_model": "package"},
            {"subject_id": None, "pricing_model": "package"},
        )
    ]
    item = created(client, staff, world, effective_from=TOMORROW)
    other = created(client, staff, world, duration_minutes=45, effective_from=TOMORROW)

    for item_id in [uuid.UUID(int=9), *hidden]:
        assert code_of(get(client, staff, f"{PRICES}/{item_id}")) == NOT_FOUND
        assert (
            code_of(add_version(client, staff, item_id, TOMORROW, "1.00")) == NOT_FOUND
        )
        assert code_of(remove(client, staff, item_id, uuid.UUID(int=9))) == NOT_FOUND
    # A version of another item, and an unknown version.
    assert (
        code_of(remove(client, staff, item["id"], other["versions"][0]["id"]))
        == NOT_FOUND
    )
    assert code_of(remove(client, staff, item["id"], uuid.UUID(int=10))) == NOT_FOUND
    assert len(version_dates(module_seeded_url, other["id"])) == 1


def test_the_body_is_checked_before_the_item(client, staff):
    response = post(client, staff, f"{PRICES}/{uuid.UUID(int=11)}/versions", {})

    assert field_codes(response) == {
        ("effective_from", "missing"),
        ("amount", "missing"),
    }


# --- Audit (B8) ----------------------------------------------------------------------


def test_writes_are_audited_and_refusals_are_not(
    client, staff, world, module_seeded_url
):
    url = module_seeded_url
    item = created(client, staff, world, effective_from=YESTERDAY, amount="250.00")
    [first] = item["versions"]

    [item_event] = events(
        url, record_type="service_prices", record_id=uuid.UUID(item["id"])
    )
    assert item_event.action == "create"
    assert item_event.changes["subject_id"]["new"] == str(world.subject)
    [version_event] = events(
        url, record_type="price_versions", record_id=uuid.UUID(first["id"])
    )
    assert version_event.action == "create"
    assert version_event.changes["amount"]["new"] == "250.00"
    assert version_event.changes["effective_from"]["new"] == str(YESTERDAY)

    upcoming = add_version(client, staff, item["id"], TOMORROW, "260.00").json()["next"]
    assert code_of(remove(client, staff, item["id"], upcoming["id"])) == (204, None)
    actions = [
        e.action
        for e in events(
            url, record_type="price_versions", record_id=uuid.UUID(upcoming["id"])
        )
    ]
    assert actions == ["create", "delete"]

    before = len(events(url, record_type="price_versions")) + len(
        events(url, record_type="service_prices")
    )
    assert code_of(create(client, staff, world)) == EXISTS
    assert (
        code_of(add_version(client, staff, item["id"], YESTERDAY, "1.00"))
        == DATE_INVALID
    )
    assert code_of(remove(client, staff, item["id"], first["id"])) == IN_EFFECT
    after = len(events(url, record_type="price_versions")) + len(
        events(url, record_type="service_prices")
    )
    assert after == before


# --- The guard (B6; E2) --------------------------------------------------------------


@pytest.fixture
def every_version_referenced(module_seeded_url: str) -> Iterator[None]:
    with referenced(module_seeded_url):
        yield


def test_a_referenced_upcoming_version_is_409_in_use(
    client, staff, world, module_seeded_url, request
):
    url = module_seeded_url
    item = created(client, staff, world, effective_from=TOMORROW)
    [version] = item["versions"]
    request.getfixturevalue("every_version_referenced")
    before = len(events(url, record_type="price_versions"))

    response = remove(client, staff, item["id"], version["id"])

    assert code_of(response) == IN_USE
    assert version_dates(url, item["id"]) == [TOMORROW]
    assert len(events(url, record_type="price_versions")) == before


# --- #93 B1, B2, H8: per-period prices ---------------------------------------------


def test_per_period_prices_with_each_frequency_coexist(
    client, staff, world, module_seeded_url
):
    frequencies = ("weekly", "biweekly", "monthly")
    found = [
        created(
            client,
            staff,
            world,
            pricing_model="per_period",
            period_frequency=frequency,
            amount="1000.00",
        )
        for frequency in frequencies
    ]
    group_item = created(
        client,
        staff,
        world,
        class_type="group",
        group_id=world.group,
        pricing_model="per_period",
        period_frequency="monthly",
    )

    assert [f["period_frequency"] for f in found] == list(frequencies)
    assert all(f["pricing_model"] == "per_period" for f in found)
    assert group_item["group"]["id"] == str(world.group)
    duplicate = create(
        client, staff, world, pricing_model="per_period", period_frequency="weekly"
    )
    assert code_of(duplicate) == (409, "service_price_exists")
    assert duplicate.json()["details"] == {"service_price_id": found[0]["id"]}
    detail = get(client, staff, f"{PRICES}/{found[2]['id']}").json()
    assert detail["period_frequency"] == "monthly"
    # Versions work on per-period items exactly as #89 E3.
    versioned = add_version(client, staff, found[2]["id"], TOMORROW, "1100.00")
    assert versioned.status_code == 201, versioned.text
    assert [v["amount"] for v in versioned.json()["versions"]] == [
        "1100.00",
        "1000.00",
    ]
    removed = remove(
        client, staff, found[2]["id"], versioned.json()["versions"][0]["id"]
    )
    assert removed.status_code == 204, removed.text
    per_session = created(client, staff, world, amount="300.00")
    assert per_session["period_frequency"] is None
