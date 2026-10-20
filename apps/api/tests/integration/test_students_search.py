"""The student list: search, filters, order and pagination (#33 E1-E3, E5).

The module's own database holds exactly the students of :func:`dataset`, so
results are compared exactly. The statement-count test uses a fresh database.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.authz import Principal
from app.core.pagination import OffsetParams
from app.modules.families import service
from app.modules.families.assignments import NoTutorAssignments
from app.modules.families.models import Student
from app.modules.families.service import search_condition
from app.modules.identity.permissions import OWNER, ROLE_PERMISSIONS
from tests.factories.families import persist_student
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import LEVELS, count_statements, get, patch
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.student_support import (
    STUDENTS,
    contact,
    create_student,
    student_path,
)

LIST_ITEM_FIELDS = {
    "id",
    "given_names",
    "family_names",
    "is_active",
    "is_minor",
    "school_grade",
    "education_level",
}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture(scope="module")
def levels(client, owner) -> dict[str, str]:
    cookie = sign_in(client, owner.username)
    items = get(client, cookie, LEVELS, limit=100).json()["items"]
    return {level["name"]: level["id"] for level in items}


@pytest.fixture(scope="module")
def dataset(client, owner, levels) -> dict[str, dict[str, Any]]:
    """Every student of this module's database, by key."""
    cookie = sign_in(client, owner.username)
    rows = {
        "A": ("José María", "García López", levels["Secundaria"], "2012-01-01"),
        "B": ("José", "Ñúñez Pérez", levels["Primaria"], "2015-06-30"),
        "C": ("Ana", "González", None, "2001-03-03"),
        "D": ("Álvaro", "Álvarez Ruiz", levels["Secundaria"], "2010-10-10"),
        "E": ("Beatriz", "Ruiz 50%", None, "2013-01-01"),
        "F": ("Mateo", "Zamora", levels["Preparatoria"], "2009-09-09"),
        "H": ("Rita", "Aperez", None, "2014-04-04"),
    }
    created = {
        key: create_student(
            client,
            cookie,
            given_names=given,
            family_names=family,
            education_level_id=level,
            birthdate=birthdate,
            school_grade="2.º" if key == "A" else None,
            emergency_contacts=[contact()],
        )
        for key, (given, family, level, birthdate) in rows.items()
    }
    response = patch(
        client,
        cookie,
        student_path(created["D"]["id"]),
        {"version": 1, "is_active": False},
    )
    assert response.status_code == 200, response.text
    return created


def keys(dataset, response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    by_id = {student["id"]: key for key, student in dataset.items()}
    return [by_id[item["id"]] for item in response.json()["items"]]


def search(client, cookie, **params) -> Any:
    return get(client, cookie, STUDENTS, **params)


# --- E1: the list -------------------------------------------------------------------


def test_the_default_list_is_active_students_by_family_then_given_names(
    client, cookie, dataset
):
    response = search(client, cookie)

    assert keys(dataset, response) == ["H", "A", "C", "B", "E", "F"]
    assert response.json()["total"] == 6


def test_list_items_carry_no_birthdate_or_contact_data(client, cookie, dataset, levels):
    items = {i["id"]: i for i in search(client, cookie).json()["items"]}

    a = items[dataset["A"]["id"]]
    assert set(a) == LIST_ITEM_FIELDS
    assert a == {
        "id": dataset["A"]["id"],
        "given_names": "José María",
        "family_names": "García López",
        "is_active": True,
        "is_minor": True,
        "school_grade": "2.º",
        "education_level": {
            "id": levels["Secundaria"],
            "name": "Secundaria",
            "is_active": True,
        },
    }
    assert items[dataset["C"]["id"]]["is_minor"] is False
    assert items[dataset["C"]["id"]]["education_level"] is None


def test_status_filter(client, cookie, dataset):
    assert keys(dataset, search(client, cookie, status="inactive")) == ["D"]
    # Álvarez sorts with the A's (accents are folded in the order too).
    assert keys(dataset, search(client, cookie, status="all")) == [
        "D",
        "H",
        "A",
        "C",
        "B",
        "E",
        "F",
    ]
    assert code_of(search(client, cookie, status="archived")) == (
        422,
        "validation_error",
    )


def test_level_filter(client, cookie, dataset, levels):
    secundaria = levels["Secundaria"]

    assert keys(dataset, search(client, cookie, education_level_id=secundaria)) == ["A"]
    assert keys(
        dataset, search(client, cookie, education_level_id=secundaria, status="all")
    ) == ["D", "A"]
    unknown = search(
        client, cookie, education_level_id="00000000-0000-4000-8000-000000000999"
    )
    assert keys(dataset, unknown) == []
    assert unknown.json()["total"] == 0


def test_limit_offset_and_total(client, cookie, dataset):
    page = search(client, cookie, limit=2, offset=2)

    assert keys(dataset, page) == ["C", "B"]
    assert page.json()["total"] == 6
    assert (page.json()["limit"], page.json()["offset"]) == (2, 2)


# --- E2, E5: search -------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("jose", ["A", "B"]),
        ("JOSÉ GARCÍA", ["A"]),
        ("nunez", ["B"]),
        ("ÑÚÑEZ", ["B"]),
        ("garcia jose", ["A"]),
        ("lopez maria", ["A"]),
        ("Gonzales", ["C"]),
        ("xyz", []),
        ("%", ["E"]),
        ("50%", ["E"]),
        ("_", []),
        ("\\", []),
    ],
)
def test_search(client, cookie, dataset, q, found):
    response = search(client, cookie, q=q)

    assert keys(dataset, response) == found
    assert response.json()["total"] == len(found)


def test_search_with_the_status_and_level_filters(client, cookie, dataset, levels):
    assert keys(dataset, search(client, cookie, q="alvarez")) == []
    assert keys(dataset, search(client, cookie, q="alvarez", status="inactive")) == [
        "D"
    ]
    assert keys(dataset, search(client, cookie, q="ruiz")) == ["E"]
    assert keys(dataset, search(client, cookie, q="ruiz", status="all")) == ["D", "E"]
    assert keys(
        dataset, search(client, cookie, q="jose", education_level_id=levels["Primaria"])
    ) == ["B"]


def test_relevance_comes_before_the_name_order(client, cookie, dataset):
    # "Pérez" is a whole word of B's name; "Aperez" only contains it, so it
    # is less similar although it sorts first by name.
    response = search(client, cookie, q="perez")

    assert keys(dataset, response) == ["B", "H"]
    assert keys(dataset, search(client, cookie, q="perez", limit=1, offset=1)) == ["H"]
    assert search(client, cookie, q="perez", limit=1).json()["total"] == 2


def test_the_search_text_is_trimmed(client, cookie, dataset):
    assert keys(dataset, search(client, cookie, q="  nunez  ")) == ["B"]


@pytest.mark.parametrize(
    "q",
    ["a b c d e f", "x" * 101, "", "   "],
    ids=["six terms", "101 characters", "empty", "blank"],
)
def test_invalid_searches_are_422(client, cookie, q):
    response = search(client, cookie, q=q)

    assert code_of(response) == (422, "validation_error")
    assert response.json()["errors"][0]["path"] == ["query", "q"]


def test_five_terms_are_allowed(client, cookie, dataset):
    assert keys(dataset, search(client, cookie, q="jose maria garcia lopez jose")) == [
        "A"
    ]


def test_the_access_log_line_has_no_search_text(client, cookie, dataset, caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    search(client, cookie, q="Gonzales")

    access = [r for r in caplog.records if r.name == "app.access"]
    assert access
    for record in access:
        assert "Gonzales" not in record.getMessage()
        assert "Gonzales" not in repr(record.__dict__)


# --- E3: statements ----------------------------------------------------------------


def staff_principal() -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({OWNER}),
        permissions=frozenset(ROLE_PERMISSIONS[OWNER]),
    )


def test_the_list_takes_four_statements_for_thirty_rows(seeded_url):
    level_ids = [
        row.id for row in run_sql(seeded_url, "SELECT id FROM education_levels")
    ]

    async def thirty(session) -> None:
        for n in range(30):
            await persist_student(
                session, contacts=2, education_level_id=level_ids[n % len(level_ids)]
            )

    in_uow(seeded_url, thirty)
    principal = staff_principal()

    for q in (None, "a"):
        statements, page = count_statements(
            seeded_url,
            lambda session, q=q: service.list_students(
                session,
                principal=principal,
                source=NoTutorAssignments(),
                q=q,
                status="all",
                education_level_id=None,
                page=OffsetParams(limit=30, offset=0),
            ),
        )
        # Three, plus the center time zone for "today" (#37: once per Unit
        # of Work).
        assert statements == 4, q
        if q is None:
            assert page.total == 30
            assert len(page.items) == 30
            assert all(item.education_level is not None for item in page.items)


def test_an_explain_of_the_search_uses_the_trigram_index(seeded_url):
    async def many(session) -> None:
        for n in range(2000):
            session.add(
                Student(
                    given_names="Nombre",
                    family_names=f"Apellido {n:04d}",
                    birthdate=date(2012, 1, 1),
                )
            )
        await session.flush()

    in_uow(seeded_url, many)
    run_sql(seeded_url, "ANALYZE students", autocommit=True)

    async def explain(session) -> str:

        await session.execute(text("SET LOCAL enable_seqscan = off"))
        query = select(Student.id).where(search_condition("apellido 0042"))
        compiled = query.compile(
            dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
        )
        rows = await session.execute(text(f"EXPLAIN {compiled}"))
        return "\n".join(row[0] for row in rows)

    plan = in_uow(seeded_url, explain)

    assert "ix_students_search_name_trgm" in plan, plan
