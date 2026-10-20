"""Business settings over HTTP and through the service (#37 B, C, D, E, F).

The settings record is a singleton: the tests of this module share one
seeded database, and an autouse fixture puts the record back to the
migration's defaults (raw SQL, no audit event) and removes any test-registered
time-zone check after each test. Audit assertions count only the events a
test adds.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditEvent
from app.core.authz import Principal
from app.core.reauth import RecentAuthGuard
from app.modules.administration import service as administration
from app.modules.administration.schemas import BusinessSettingsUpdate
from app.modules.families import service as families
from app.modules.families.assignments import get_tutor_assignment_source
from app.modules.families.errors import BirthdateInFutureError
from app.modules.families.schemas import StudentCreate
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
)
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.student_support import STUDENTS, student_body, student_path

SETTINGS = "/api/v1/business-settings"
ME = "/api/v1/auth/me"
DEFAULT_ZONE = "America/Mexico_City"
RECORD_TYPES = ("business_settings", "operating_hours")


def entry(weekday: int, opens_at: str, closes_at: str) -> dict[str, Any]:
    return {"weekday": weekday, "opens_at": opens_at, "closes_at": closes_at}


DEFAULT_HOURS = [
    *(entry(day, "09:00", "20:00") for day in range(1, 6)),
    entry(6, "09:00", "14:00"),
]
DEFAULTS = {
    "center_name": "Classora",
    "contact_phone": None,
    "contact_email": None,
    "address": None,
    "time_zone": DEFAULT_ZONE,
    "operating_hours": DEFAULT_HOURS,
    "version": 1,
}


def without_updated_at(body: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(body.pop("updated_at"), str)
    return body


def restore_defaults(url: str) -> None:
    run_sql(
        url,
        "UPDATE business_settings SET center_name = 'Classora',"
        " contact_phone = NULL, contact_email = NULL, address = NULL,"
        " time_zone = :zone, version = 1",
        {"zone": DEFAULT_ZONE},
    )
    run_sql(url, "DELETE FROM operating_hours")
    run_sql(
        url,
        "INSERT INTO operating_hours (weekday, opens_at, closes_at) VALUES"
        " (1, '09:00', '20:00'), (2, '09:00', '20:00'), (3, '09:00', '20:00'),"
        " (4, '09:00', '20:00'), (5, '09:00', '20:00'), (6, '09:00', '14:00')",
    )


def stored(url: str) -> dict[str, Any]:
    [row] = run_sql(
        url,
        "SELECT center_name, contact_phone, contact_email, address, time_zone,"
        " version FROM business_settings",
    )
    hours = run_sql(
        url,
        "SELECT weekday, to_char(opens_at, 'HH24:MI') AS opens_at,"
        " to_char(closes_at, 'HH24:MI') AS closes_at"
        " FROM operating_hours ORDER BY weekday",
    )
    return {
        **row._asdict(),
        "operating_hours": [entry(h.weekday, h.opens_at, h.closes_at) for h in hours],
    }


def settings_events(url: str) -> list[AuditEvent]:
    return [
        event
        for record_type in RECORD_TYPES
        for event in events(url, record_type=record_type)
    ]


class EventCounter:
    """The settings events written since it was created."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.before = {event.id for event in settings_events(url)}

    def new(self) -> list[AuditEvent]:
        return sorted(
            (e for e in settings_events(self.url) if e.id not in self.before),
            key=lambda e: (e.occurred_at, e.id),
        )


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(autouse=True)
def clean_settings(module_seeded_url: str) -> Iterator[None]:
    yield
    for name in list(administration.time_zone_dependencies()):
        administration.unregister_time_zone_dependency(name)
    restore_defaults(module_seeded_url)


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture
def counter(module_seeded_url: str) -> EventCounter:
    return EventCounter(module_seeded_url)


class Check:
    """A test-registered time-zone dependency check."""

    def __init__(self, name: str, result: bool) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    async def __call__(self, session: AsyncSession) -> bool:
        self.calls += 1
        return self.result


def register(name: str, result: bool) -> Check:
    check = Check(f"tests.{name}", result)
    administration.register_time_zone_dependency(check.name, check)
    return check


# --- B: reading -----------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLE_CODES)
def test_every_role_reads_the_defaults(module_seeded_url, client, role):
    person = create_user(module_seeded_url, roles=(role,))
    response = get(client, sign_in(client, person.username), SETTINGS)

    assert response.status_code == 200, response.text
    assert without_updated_at(response.json()) == DEFAULTS


def test_reading_needs_a_session(client):
    client.cookies.clear()

    assert code_of(client.get(SETTINGS)) == (401, "authentication_required")


def test_reading_takes_two_statements(module_seeded_url):
    count, detail = count_statements(
        module_seeded_url, administration.get_business_settings
    )

    assert count <= 2
    assert detail.center_name == "Classora"


def test_the_zone_is_read_once_per_unit_of_work(module_seeded_url):
    async def twice(session: AsyncSession) -> list[str]:
        return [
            await administration.get_center_time_zone(session),
            str(await administration.center_today(session)),
        ]

    count, (zone, _today) = count_statements(module_seeded_url, twice)

    assert zone == DEFAULT_ZONE
    assert count == 1


# --- C: changing ----------------------------------------------------------------------


def test_each_field_is_changed_and_cleared(module_seeded_url, client, cookie):
    changed = patch(
        client,
        cookie,
        SETTINGS,
        {
            "version": 1,
            "center_name": "  Centro Norte ",
            "contact_phone": "+52 55 1234 5678",
            "contact_email": "hola@centro.mx",
            "address": "Av. Reforma 1, CDMX",
        },
    )

    assert changed.status_code == 200, changed.text
    assert without_updated_at(changed.json()) == {
        **DEFAULTS,
        "center_name": "Centro Norte",
        "contact_phone": "+52 55 1234 5678",
        "contact_email": "hola@centro.mx",
        "address": "Av. Reforma 1, CDMX",
        "version": 2,
    }
    cleared = patch(
        client,
        cookie,
        SETTINGS,
        {"version": 2, "contact_phone": None, "contact_email": "", "address": None},
    )

    assert cleared.status_code == 200, cleared.text
    assert without_updated_at(cleared.json()) == {
        **DEFAULTS,
        "center_name": "Centro Norte",
        "version": 3,
    }
    assert stored(module_seeded_url) == {
        **DEFAULTS,
        "center_name": "Centro Norte",
        "version": 3,
    }


def test_hours_replace_the_whole_week_with_one_version_bump(
    module_seeded_url, client, cookie, counter
):
    week = [
        entry(1, "08:00", "21:00"),
        *(entry(day, "09:00", "20:00") for day in range(2, 6)),
        entry(7, "10:00", "13:00"),
    ]

    response = patch(client, cookie, SETTINGS, {"version": 1, "operating_hours": week})

    assert response.status_code == 200, response.text
    assert response.json()["operating_hours"] == week
    assert response.json()["version"] == 2
    assert stored(module_seeded_url)["operating_hours"] == week
    new = counter.new()
    # F1: Monday's change, Sunday added, Saturday removed; the settings row
    # changed only updated_at and version, which the audit leaves out.
    assert sorted((e.record_type, e.action) for e in new) == [
        ("operating_hours", "create"),
        ("operating_hours", "delete"),
        ("operating_hours", "update"),
    ]
    by_action = {e.action: e for e in new}
    assert by_action["update"].changes == {
        "opens_at": {"old": "09:00:00", "new": "08:00:00"},
        "closes_at": {"old": "20:00:00", "new": "21:00:00"},
    }
    assert by_action["create"].changes is not None
    assert by_action["create"].changes["weekday"] == {"old": None, "new": 7}
    assert by_action["delete"].changes is not None
    assert by_action["delete"].changes["weekday"] == {"old": 6, "new": None}


def test_an_empty_week_closes_every_day(module_seeded_url, client, cookie):
    response = patch(client, cookie, SETTINGS, {"version": 1, "operating_hours": []})

    assert response.status_code == 200, response.text
    assert response.json()["operating_hours"] == []
    assert stored(module_seeded_url)["operating_hours"] == []
    assert stored(module_seeded_url)["version"] == 2


INVALID_BODIES: dict[str, dict[str, Any]] = {
    "no_version": {"center_name": "X"},
    "version_zero": {"version": 0},
    "unknown_field": {"version": 1, "logo": "x"},
    "null_name": {"version": 1, "center_name": None},
    "blank_name": {"version": 1, "center_name": "  "},
    "long_name": {"version": 1, "center_name": "x" * 101},
    "bad_email": {"version": 1, "contact_email": "nope"},
    "bad_phone": {"version": 1, "contact_phone": "123"},
    "long_address": {"version": 1, "address": "x" * 301},
    "null_zone": {"version": 1, "time_zone": None},
    "unknown_zone": {"version": 1, "time_zone": "Mars/Olympus"},
    "null_hours": {"version": 1, "operating_hours": None},
    "weekday_zero": {"version": 1, "operating_hours": [entry(0, "09:00", "10:00")]},
    "weekday_eight": {"version": 1, "operating_hours": [entry(8, "09:00", "10:00")]},
    "same_weekday": {
        "version": 1,
        "operating_hours": [entry(1, "09:00", "10:00"), entry(1, "11:00", "12:00")],
    },
    "closes_first": {"version": 1, "operating_hours": [entry(1, "10:00", "09:00")]},
    "empty_interval": {"version": 1, "operating_hours": [entry(1, "10:00", "10:00")]},
    "seconds": {"version": 1, "operating_hours": [entry(1, "10:00:30", "11:00")]},
    "bad_time": {"version": 1, "operating_hours": [entry(1, "25:00", "26:00")]},
    "eight_entries": {
        "version": 1,
        "operating_hours": [entry(1 + i % 7, "09:00", "10:00") for i in range(8)],
    },
}


@pytest.mark.parametrize("body", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_invalid_bodies_are_422_and_change_nothing(
    module_seeded_url, client, cookie, counter, body
):
    response = patch(client, cookie, SETTINGS, body)

    assert code_of(response) == (422, "validation_error"), response.text
    assert stored(module_seeded_url) == DEFAULTS
    assert counter.new() == []


def test_a_field_error_names_the_field(client, cookie):
    response = patch(
        client,
        cookie,
        SETTINGS,
        {"version": 1, "operating_hours": [entry(3, "12:00", "11:00")]},
    )

    assert [e["path"] for e in response.json()["errors"]] == [
        ["body", "operating_hours", 0, "closes_at"]
    ]


def test_a_stale_version_is_409_and_changes_nothing(
    module_seeded_url, client, cookie, counter
):
    assert (
        patch(client, cookie, SETTINGS, {"version": 1, "center_name": "A"}).status_code
        == 200
    )
    before = stored(module_seeded_url)

    response = patch(client, cookie, SETTINGS, {"version": 1, "center_name": "B"})

    assert code_of(response) == (409, "version_conflict")
    assert stored(module_seeded_url) == before
    assert len(counter.new()) == 1


def test_a_body_that_changes_nothing_is_200_without_a_version_or_event(
    module_seeded_url, client, cookie, counter
):
    response = patch(
        client,
        cookie,
        SETTINGS,
        {
            "version": 1,
            "center_name": "Classora",
            "contact_phone": None,
            "time_zone": DEFAULT_ZONE,
            "operating_hours": list(reversed(DEFAULT_HOURS)),
        },
    )

    assert response.status_code == 200, response.text
    assert without_updated_at(response.json()) == DEFAULTS
    assert stored(module_seeded_url) == DEFAULTS
    assert counter.new() == []


def test_a_zone_change_without_reauthentication_is_403_and_changes_nothing(
    module_seeded_url, client, cookie, counter
):
    response = patch(
        client,
        cookie,
        SETTINGS,
        {"version": 1, "time_zone": "America/Cancun", "center_name": "Otro"},
    )

    assert code_of(response) == (403, "reauth_required")
    assert stored(module_seeded_url) == DEFAULTS
    assert counter.new() == []


def test_a_zone_change_with_a_recent_reauthentication_is_200_and_audited(
    module_seeded_url, client, owner, cookie, counter
):
    reauthenticate(client, cookie)

    response = patch(
        client, cookie, SETTINGS, {"version": 1, "time_zone": "America/Cancun"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["time_zone"] == "America/Cancun"
    assert response.json()["version"] == 2
    [event] = counter.new()
    assert (event.record_type, event.action) == ("business_settings", "update")
    assert event.changes == {
        "time_zone": {"old": DEFAULT_ZONE, "new": "America/Cancun"}
    }
    assert event.actor_user_id == owner.id
    assert event.request_id == response.headers["x-request-id"]
    # E5: the session reports the new zone.
    client.cookies.clear()
    me = client.get(ME, headers=cookie_header(cookie))
    assert me.json()["center_time_zone"] == "America/Cancun"


def test_sending_the_current_zone_needs_no_reauthentication(
    module_seeded_url, client, cookie
):
    response = patch(
        client,
        cookie,
        SETTINGS,
        {"version": 1, "time_zone": DEFAULT_ZONE, "center_name": "Centro Sur"},
    )

    assert response.status_code == 200, response.text
    assert stored(module_seeded_url)["center_name"] == "Centro Sur"


def test_reauthentication_comes_before_the_version_check(client, cookie):
    response = patch(
        client, cookie, SETTINGS, {"version": 7, "time_zone": "America/Cancun"}
    )

    assert code_of(response) == (403, "reauth_required")


def test_validation_comes_before_reauthentication(client, cookie):
    response = patch(client, cookie, SETTINGS, {"version": 1, "time_zone": "Nope/Nope"})

    assert code_of(response) == (422, "validation_error")


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_only_owners_and_administrators_change_the_settings(
    module_seeded_url, client, counter, role
):
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)
    reauthenticate(client, other)

    responses = [
        patch(client, other, SETTINGS, {"version": 1, "center_name": "X"}),
        patch(client, other, SETTINGS, {"version": 1, "time_zone": "America/Cancun"}),
        patch(client, other, SETTINGS, {"version": 1}),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 3
    assert stored(module_seeded_url) == DEFAULTS
    assert counter.new() == []


def test_an_administrator_changes_the_settings(module_seeded_url, client):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)

    response = patch(
        client, admin_cookie, SETTINGS, {"version": 1, "address": "Calle 1"}
    )

    assert response.status_code == 200, response.text
    assert stored(module_seeded_url)["address"] == "Calle 1"


# --- C8: concurrency ------------------------------------------------------------------

HOLD_SECONDS = 0.5


def test_two_concurrent_changes_with_the_same_version_one_wins(module_seeded_url):
    from app.core.db import create_sessionmaker
    from app.core.models import StaleVersionError
    from app.core.uow import UnitOfWork
    from tests.integration.conftest import short_lived_engine

    guard = RecentAuthGuard("settings.payment_methods_or_time_zone", None)

    async def change(engine: Any, name: str, hold: asyncio.Event | None) -> str:
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await administration.update_business_settings(
                    uow.session,
                    BusinessSettingsUpdate(version=1, center_name=name),
                    guard=guard,
                )
                if hold is not None:
                    hold.set()
                    await asyncio.sleep(HOLD_SECONDS)
        except StaleVersionError:
            return "version_conflict"
        finally:
            if hold is not None:
                hold.set()
        return "ok"

    async def run() -> list[str]:
        engine_a = short_lived_engine(module_seeded_url)
        engine_b = short_lived_engine(module_seeded_url)
        changed = asyncio.Event()

        async def second() -> str:
            await changed.wait()
            return await change(engine_b, "Segundo", None)

        try:
            return list(
                await asyncio.gather(change(engine_a, "Primero", changed), second())
            )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    assert asyncio.run(run()) == ["ok", "version_conflict"]
    assert stored(module_seeded_url)["center_name"] == "Primero"
    assert stored(module_seeded_url)["version"] == 2


# --- D: the time-zone guard -----------------------------------------------------------


def zone_change(client: TestClient, cookie: Any, version: int = 1) -> Any:
    reauthenticate(client, cookie)
    return patch(
        client, cookie, SETTINGS, {"version": version, "time_zone": "America/Cancun"}
    )


def test_scheduled_data_blocks_a_zone_change(
    module_seeded_url, client, cookie, counter
):
    check = register("sessions", True)

    response = zone_change(client, cookie)

    assert code_of(response) == (409, "tz_change_requires_migration")
    assert set(response.json()) == {
        "type",
        "title",
        "status",
        "detail",
        "code",
        "request_id",
    }
    assert check.calls == 1
    assert stored(module_seeded_url) == DEFAULTS
    assert counter.new() == []


def test_no_scheduled_data_lets_the_zone_change(module_seeded_url, client, cookie):
    check = register("sessions", False)

    response = zone_change(client, cookie)

    assert response.status_code == 200, response.text
    assert check.calls == 1
    assert stored(module_seeded_url)["time_zone"] == "America/Cancun"


def test_any_registered_check_blocks(module_seeded_url, client, cookie):
    first = register("first", False)
    second = register("second", True)

    response = zone_change(client, cookie)

    assert code_of(response) == (409, "tz_change_requires_migration")
    assert (first.calls, second.calls) == (1, 1)
    assert stored(module_seeded_url)["time_zone"] == DEFAULT_ZONE


def test_other_changes_succeed_while_scheduled_data_exists(
    module_seeded_url, client, cookie
):
    check = register("sessions", True)

    response = patch(
        client,
        cookie,
        SETTINGS,
        {
            "version": 1,
            "center_name": "Centro Este",
            "time_zone": DEFAULT_ZONE,
            "operating_hours": [entry(7, "10:00", "13:00")],
        },
    )

    assert response.status_code == 200, response.text
    assert check.calls == 0
    assert stored(module_seeded_url)["center_name"] == "Centro Este"


def test_the_version_is_checked_before_the_guard(client, cookie):
    check = register("sessions", True)

    response = zone_change(client, cookie, version=5)

    assert code_of(response) == (409, "version_conflict")
    assert check.calls == 0


# --- E: the setting replaces the constant ---------------------------------------------

KIRITIMATI = "Pacific/Kiritimati"  # UTC+14
INSTANT = datetime(2026, 10, 17, 10, 30, tzinfo=UTC)  # 2026-10-18 00:30 there


def set_zone(url: str, zone: str) -> None:
    run_sql(url, "UPDATE business_settings SET time_zone = :zone", {"zone": zone})


def owner_principal() -> Principal:
    return Principal(
        user_id=uuid.uuid7(),
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )


def create_in_service(url: str, birthdate: str) -> Any:
    body = StudentCreate.model_validate(student_body(birthdate=birthdate))

    async def call(session: AsyncSession) -> Any:
        return await families.create_student(
            session, principal=owner_principal(), body=body
        )

    return in_uow(url, call)


def is_minor_in_service(url: str, student_id: Any) -> bool:
    async def call(session: AsyncSession) -> bool:
        detail = await families.get_student(
            session,
            principal=owner_principal(),
            source=get_tutor_assignment_source(),
            student_id=student_id,
        )
        return detail.is_minor

    return in_uow(url, call)


def test_center_today_follows_the_setting(clock, module_seeded_url):
    clock.set(INSTANT)

    async def today(session: AsyncSession) -> str:
        return str(await administration.center_today(session))

    assert in_uow(module_seeded_url, today) == "2026-10-17"
    set_zone(module_seeded_url, KIRITIMATI)
    assert in_uow(module_seeded_url, today) == "2026-10-18"


def test_a_birthdate_is_in_the_future_by_the_center_zone(clock, module_seeded_url):
    clock.set(INSTANT)

    with pytest.raises(BirthdateInFutureError):
        create_in_service(module_seeded_url, "2026-10-18")
    set_zone(module_seeded_url, KIRITIMATI)
    created = create_in_service(module_seeded_url, "2026-10-18")
    assert created.personal.birthdate.isoformat() == "2026-10-18"


def test_the_18th_birthday_follows_the_center_zone(clock, module_seeded_url):
    clock.set(INSTANT)
    student = create_in_service(module_seeded_url, "2008-10-18")

    assert is_minor_in_service(module_seeded_url, student.id) is True
    set_zone(module_seeded_url, KIRITIMATI)
    assert is_minor_in_service(module_seeded_url, student.id) is False


def test_the_students_api_uses_the_center_zone(clock, module_seeded_url, client, owner):
    # Over HTTP, after the privacy notice's publication (2026-10-18): at
    # 2026-10-18T10:30Z it is the 19th in Kiritimati, the 18th in Mexico City.
    clock.set(datetime(2026, 10, 18, 10, 30, tzinfo=UTC))
    cookie = sign_in(client, owner.username)

    refused = post(client, cookie, STUDENTS, student_body(birthdate="2026-10-19"))
    adult = post(client, cookie, STUDENTS, student_body(birthdate="2008-10-19"))
    set_zone(module_seeded_url, KIRITIMATI)
    accepted = post(client, cookie, STUDENTS, student_body(birthdate="2026-10-19"))
    adult_now = get(client, cookie, student_path(adult.json()["id"]))

    assert code_of(refused) == (422, "validation_error")
    assert adult.json()["is_minor"] is True
    assert accepted.status_code == 201, accepted.text
    assert adult_now.json()["is_minor"] is False
    client.cookies.clear()
    assert (
        client.get(ME, headers=cookie_header(cookie)).json()["center_time_zone"]
        == KIRITIMATI
    )
