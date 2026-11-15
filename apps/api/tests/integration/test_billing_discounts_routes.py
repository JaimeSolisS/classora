"""The discount routes (#95 D, H6 tests) through the real app: the shapes
(eligibility names, percent with four places, statuses), the create field
and reference codes, ``409 discount_name_exists``, the list filters, order,
pagination and statement bound, ``PATCH`` (switch percent/amount,
``field_immutable``, the resulting-row rules, ``version_conflict``, a no-op
with no event), the assignments (``audience_not_assigned``,
``student_unknown``/``family_unknown``, ``409``, delete), ``404``s, the
audit events and the access matrix (Coordinator 403, Finance Staff 200;
the full matrix is ``tests/authz/matrix.toml``)."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import ACADEMIC_COORDINATOR, FINANCE_STAFF, OWNER
from app.workflows import billing_discounts
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.families import FamilyFactory, persist_student
from tests.factories.scheduling import persist_subject
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZONE)
TODAY = date(2031, 3, 10)
DISCOUNTS = "/api/v1/billing/discounts"
INVALID = (422, "validation_error")
ITEM_KEYS = {
    "id",
    "kind",
    "name",
    "audience",
    "percent",
    "amount",
    "eligibility",
    "starts_on",
    "expires_on",
    "is_active",
    "status",
    "assignment_count",
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
    token: str
    subject: uuid.UUID
    subject_name: str
    level: uuid.UUID
    level_name: str
    inactive_subject: uuid.UUID
    student: uuid.UUID
    student_name: str
    family: uuid.UUID
    family_name: str


@pytest.fixture
def world(module_seeded_url: str) -> World:
    async def _make(session: AsyncSession) -> World:
        from app.modules.catalog.models import EducationLevel

        subject = await persist_subject(session)
        level = await session.get(EducationLevel, subject.education_level_id)
        assert level is not None
        inactive = await persist_subject(session)
        inactive.is_active = False
        student = await persist_student(session, contacts=0)
        family = FamilyFactory.build()
        session.add(family)
        await session.flush()
        return World(
            token=uuid.uuid4().hex[:10],
            subject=subject.id,
            subject_name=subject.name,
            level=level.id,
            level_name=level.name,
            inactive_subject=inactive.id,
            student=student.id,
            student_name=f"{student.given_names} {student.family_names}",
            family=family.id,
            family_name=family.name,
        )

    return in_uow(module_seeded_url, _make)


def body(world: World, **values: Any) -> dict[str, Any]:
    return {
        "kind": "discount",
        "audience": "all",
        "name": f"Descuento {world.token} {uuid.uuid4().hex[:6]}",
        "percent": "10",
        **values,
    }


def created(client: TestClient, cookie: SetCookie, payload: dict[str, Any]) -> Any:
    response = post(client, cookie, DISCOUNTS, payload)
    assert response.status_code == 201, response.text
    return response.json()


def codes(response: Response) -> set[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return {
        ("/".join(str(p) for p in e["path"][1:]), e["code"])
        for e in response.json()["errors"]
    }


# --- Create and read (D1, D3, D4) ----------------------------------------------------


def test_create_returns_the_detail_with_names(client, staff, world, module_seeded_url):
    detail = created(
        client,
        staff,
        body(
            world,
            percent="12.5",
            eligibility={
                "charge_kinds": ["session"],
                "subject_ids": [str(world.subject)],
                "education_level_ids": [str(world.level)],
            },
            starts_on="2031-03-01",
        ),
    )

    assert set(detail) == ITEM_KEYS | {"assignments"}
    assert detail["percent"] == "12.5000" and detail["amount"] is None
    assert detail["eligibility"] == {
        "charge_kinds": ["session"],
        "class_types": None,
        "subjects": [{"id": str(world.subject), "name": world.subject_name}],
        "education_levels": [{"id": str(world.level), "name": world.level_name}],
        "min_family_students": None,
    }
    assert (detail["status"], detail["assignment_count"], detail["version"]) == (
        "current",
        0,
        1,
    )
    assert detail["assignments"] == []
    [event] = events(
        module_seeded_url, record_type="discounts", record_id=uuid.UUID(detail["id"])
    )
    assert event.action == "create"
    assert event.changes["percent"] == {"old": None, "new": "12.5000"}
    assert get(client, staff, f"{DISCOUNTS}/{detail['id']}").json() == detail


def test_the_statuses(client, staff, world):
    upcoming = created(client, staff, body(world, starts_on="2031-03-11"))
    expired = created(client, staff, body(world, expires_on="2031-03-09"))
    inactive = created(client, staff, body(world, is_active=False))
    today = created(
        client, staff, body(world, starts_on="2031-03-10", expires_on="2031-03-10")
    )

    assert [d["status"] for d in (upcoming, expired, inactive, today)] == [
        "upcoming",
        "expired",
        "inactive",
        "current",
    ]


def test_create_field_codes_in_one_response(client, staff, world):
    response = post(
        client,
        staff,
        DISCOUNTS,
        {
            "kind": "scholarship",
            "audience": "all",
            "name": "  ",
            "percent": "10",
            "amount": "5.00",
            "starts_on": "2031-02-01",
            "expires_on": "2031-01-01",
            "eligibility": {"subject_ids": [], "min_family_students": 3},
        },
    )

    assert codes(response) == {
        ("audience", "audience_not_allowed"),
        ("name", "string_too_short"),
        ("amount", "value_conflict"),
        ("expires_on", "dates_order"),
        ("eligibility/subject_ids", "criteria_empty_list"),
    }


def test_create_reference_codes_with_the_index(client, staff, world):
    response = post(
        client,
        staff,
        DISCOUNTS,
        body(
            world,
            eligibility={
                "subject_ids": [str(world.subject), str(world.inactive_subject)],
                "education_level_ids": [str(uuid.uuid4())],
            },
        ),
    )

    errors = codes(response)
    ordered = sorted([world.subject, world.inactive_subject])
    index = ordered.index(world.inactive_subject)
    assert errors == {
        (f"eligibility/subject_ids/{index}", "subject_inactive"),
        ("eligibility/education_level_ids/0", "education_level_unknown"),
    }


def test_a_name_is_unique_ignoring_case(client, staff, world):
    first = created(client, staff, body(world))

    response = post(client, staff, DISCOUNTS, body(world, name=first["name"].upper()))

    assert code_of(response) == (409, "discount_name_exists")


def test_unknown_ids_are_404(client, staff):
    missing = uuid.uuid4()
    assert code_of(get(client, staff, f"{DISCOUNTS}/{missing}")) == (404, "not_found")
    assert code_of(patch(client, staff, f"{DISCOUNTS}/{missing}", {"version": 1})) == (
        404,
        "not_found",
    )
    assert code_of(
        post(
            client,
            staff,
            f"{DISCOUNTS}/{missing}/assignments",
            {"family_id": str(missing)},
        )
    ) == (404, "not_found")


# --- List (D2) -----------------------------------------------------------------------


def test_list_filters_order_and_pagination(client, staff, world):
    promotion = created(
        client,
        staff,
        body(
            world,
            kind="promotion",
            name=f"A {world.token}",
            starts_on="2031-03-01",
            expires_on="2031-03-31",
        ),
    )
    scholarship = created(
        client,
        staff,
        body(world, kind="scholarship", audience="assigned", name=f"Z {world.token}"),
    )
    general = created(client, staff, body(world, name=f"B {world.token}"))
    retired = created(
        client, staff, body(world, name=f"C {world.token}", is_active=False)
    )

    def ids(**params: Any) -> list[str]:
        response = get(client, staff, DISCOUNTS, q=world.token.upper(), **params)
        assert response.status_code == 200, response.text
        return [d["id"] for d in response.json()["items"]]

    assert ids() == [scholarship["id"], general["id"], promotion["id"], retired["id"]]
    assert ids(kind=["promotion", "scholarship"]) == [
        scholarship["id"],
        promotion["id"],
    ]
    assert ids(status="inactive") == [retired["id"]]
    page = get(client, staff, DISCOUNTS, q=world.token, limit=2, offset=2).json()
    assert (page["total"], [d["id"] for d in page["items"]]) == (
        4,
        [promotion["id"], retired["id"]],
    )
    assert get(client, staff, DISCOUNTS, q=world.token, offset=10).json()["total"] == 4
    assert code_of(get(client, staff, DISCOUNTS, status="old")) == INVALID


def test_the_list_statement_bound(client, staff, world, module_seeded_url):
    for n in range(5):
        created(
            client,
            staff,
            body(
                world,
                name=f"{world.token} {n}",
                eligibility={
                    "subject_ids": [str(world.subject)],
                    "education_level_ids": [str(world.level)],
                },
            ),
        )

    count, page = count_statements(
        module_seeded_url,
        lambda s: billing_discounts.list_discounts(
            s, kinds=(), statuses=(), q=world.token, page=OffsetParams(50, 0)
        ),
    )

    assert page.total == 5
    assert count <= 4, count


# --- PATCH (D5) ----------------------------------------------------------------------


def test_patch_switches_the_value_and_audits_once(
    client, staff, world, module_seeded_url
):
    detail = created(client, staff, body(world))
    path = f"{DISCOUNTS}/{detail['id']}"

    response = patch(
        client,
        staff,
        path,
        {
            "version": 1,
            "percent": None,
            "amount": "50.00",
            "name": f"Nuevo {world.token}",
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert (updated["percent"], updated["amount"], updated["version"]) == (
        None,
        "50.00",
        2,
    )
    [_, event] = events(
        module_seeded_url, record_type="discounts", record_id=uuid.UUID(detail["id"])
    )
    assert event.action == "update"
    assert set(event.changes) == {"percent", "amount", "name"}


def test_a_no_op_patch_writes_nothing(client, staff, world, module_seeded_url):
    detail = created(client, staff, body(world))
    path = f"{DISCOUNTS}/{detail['id']}"

    response = patch(
        client, staff, path, {"version": 1, "percent": "10.0", "name": detail["name"]}
    )

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert len(events(module_seeded_url, record_id=uuid.UUID(detail["id"]))) == 1


def test_patch_rules(client, staff, world):
    detail = created(client, staff, body(world))
    path = f"{DISCOUNTS}/{detail['id']}"
    other = created(client, staff, body(world))

    assert codes(patch(client, staff, path, {"version": 1, "kind": "promotion"})) == {
        ("kind", "field_immutable")
    }
    assert codes(patch(client, staff, path, {"version": 1, "percent": None})) == {
        ("amount", "value_required")
    }
    assert codes(patch(client, staff, path, {"version": 1, "amount": "5.00"})) == {
        ("amount", "value_conflict")
    }
    assert codes(
        patch(
            client,
            staff,
            path,
            {"version": 1, "eligibility": {"min_family_students": 2}},
        )
    ) == {("eligibility", "min_family_students_not_allowed")}
    assert codes(
        patch(
            client,
            staff,
            path,
            {
                "version": 1,
                "eligibility": {"subject_ids": [str(world.inactive_subject)]},
            },
        )
    ) == {("eligibility/subject_ids/0", "subject_inactive")}
    assert code_of(
        patch(client, staff, path, {"version": 1, "name": other["name"]})
    ) == (409, "discount_name_exists")
    assert code_of(patch(client, staff, path, {"version": 7, "name": "x"})) == (
        409,
        "version_conflict",
    )


# --- Assignments (D6, D7) ------------------------------------------------------------


def test_assignments(client, staff, world, module_seeded_url):
    detail = created(
        client, staff, body(world, kind="scholarship", audience="assigned")
    )
    path = f"{DISCOUNTS}/{detail['id']}/assignments"

    response = post(client, staff, path, {"student_id": str(world.student)})
    assert response.status_code == 201, response.text
    response = post(client, staff, path, {"family_id": str(world.family)})
    assert response.status_code == 201, response.text
    after = response.json()
    assert after["assignment_count"] == 2
    targets = sorted(
        [
            (
                world.student_name,
                {"id": str(world.student), "name": world.student_name},
                None,
            ),
            (
                world.family_name,
                None,
                {"id": str(world.family), "name": world.family_name},
            ),
        ],
        key=lambda t: t[0].lower(),
    )
    assert [(a["student"], a["family"]) for a in after["assignments"]] == [
        (s, f) for _, s, f in targets
    ]
    assert code_of(post(client, staff, path, {"student_id": str(world.student)})) == (
        409,
        "discount_assignment_exists",
    )
    assert codes(post(client, staff, path, {"student_id": str(uuid.uuid4())})) == {
        ("student_id", "student_unknown")
    }
    assert codes(post(client, staff, path, {"family_id": str(uuid.uuid4())})) == {
        ("family_id", "family_unknown")
    }
    assert codes(post(client, staff, path, {})) == {("family_id", "target_required")}

    assignment = after["assignments"][0]["id"]
    general = created(client, staff, body(world))
    assert code_of(
        delete(client, staff, f"{DISCOUNTS}/{general['id']}/assignments/{assignment}")
    ) == (404, "not_found")
    assert delete(client, staff, f"{path}/{assignment}").status_code == 204
    assert (
        get(client, staff, f"{DISCOUNTS}/{detail['id']}").json()["assignment_count"]
        == 1
    )
    [event] = events(
        module_seeded_url,
        record_type="discount_assignments",
        record_id=uuid.UUID(assignment),
        action="delete",
    )
    assert event.changes is not None


def test_an_all_discount_has_no_assignments(client, staff, world):
    detail = created(client, staff, body(world))

    response = post(
        client,
        staff,
        f"{DISCOUNTS}/{detail['id']}/assignments",
        {"student_id": str(world.student)},
    )

    assert code_of(response) == (422, "audience_not_assigned")


def test_there_is_no_discount_delete(client, staff, world):
    detail = created(client, staff, body(world))

    response = delete(client, staff, f"{DISCOUNTS}/{detail['id']}")

    assert response.status_code == 405


# --- Access (H10) --------------------------------------------------------------------


def test_finance_staff_manage_and_a_coordinator_is_refused(
    client, world, module_seeded_url
):
    finance = sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )
    coordinator = sign_in(
        client, create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,)).username
    )

    assert post(client, finance, DISCOUNTS, body(world)).status_code == 201
    assert code_of(get(client, coordinator, DISCOUNTS)) == (403, "permission_denied")
    assert code_of(post(client, coordinator, DISCOUNTS, body(world))) == (
        403,
        "permission_denied",
    )
