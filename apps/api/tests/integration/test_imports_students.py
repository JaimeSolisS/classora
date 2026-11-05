"""The ``students`` import kind end to end (#122 B4, B5, D2, D5; E6, E8)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.import_support import (
    action,
    clear_queue,
    codes,
    job_path,
    post_import,
    rows_of,
    run_job,
    student,
    students_csv,
    students_named,
    unique,
)
from tests.integration.invitation_support import sign_in


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


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


def level(url: str, *, active: bool = True) -> tuple[str, str]:
    name = unique("Nivel")
    [row] = run_sql(
        url,
        "INSERT INTO education_levels (name, sort_order, is_active)"
        " VALUES (:n, 950, :a) RETURNING id",
        {"n": name, "a": active},
    )
    return name, str(row.id)


def contacts_of(url: str, student_id: object) -> list[tuple[str, str, str, int]]:
    rows = run_sql(
        url,
        "SELECT name, relationship, phone, position FROM student_emergency_contacts"
        " WHERE student_id = :s ORDER BY position",
        {"s": student_id},
    )
    return [(r.name, r.relationship, r.phone, r.position) for r in rows]


def test_b5_d5_a_good_excel_file_imports(client, cookie, module_seeded_url):
    # Excel's "CSV UTF-8" in a Spanish locale: BOM, ";" and CRLF.
    family = unique()
    level_name, level_id = level(module_seeded_url)
    rows = [
        student(
            family,
            "Ana",
            birthdate="04/03/2012",
            email="ana@example.com",
            school_name="Colegio Sol",
            school_grade="2.º de secundaria",
            education_level=level_name.lower(),
        ),
        student(
            family,
            "Luis",
            contact2_name="Pedro Pérez",
            contact2_relationship="Padre",
            contact2_phone="+52 55 8765 4321",
        ),
        student(family, "Sofía", phone="55 1111 2222"),
    ]
    csv = students_csv(rows, delimiter=";", bom=True, newline="\r\n")

    staged = post_import(client, cookie, "students", csv, file_name=" alumnos.csv ")

    assert staged.status_code == 201, staged.text
    job = staged.json()
    assert job["file_name"] == "alumnos.csv"
    assert job["counts"]["ready"] == 3 and job["counts"]["total"] == 3
    assert action(client, cookie, job["id"], "commit").status_code == 202
    run_job(module_seeded_url)

    created = {
        row.given_names: row for row in students_named(module_seeded_url, family)
    }
    assert set(created) == {"Ana", "Luis", "Sofía"}
    ana = created["Ana"]
    assert ana.is_active is True
    assert ana.birthdate.isoformat() == "2012-03-04"
    assert (ana.email, ana.school_name, ana.school_grade) == (
        "ana@example.com",
        "Colegio Sol",
        "2.º de secundaria",
    )
    assert str(ana.education_level_id) == level_id
    assert contacts_of(module_seeded_url, created["Luis"].id) == [
        ("María López", "Madre", "55 1234 5678", 1),
        ("Pedro Pérez", "Padre", "+52 55 8765 4321", 2),
    ]
    done = rows_of(client, cookie, job["id"])["items"]
    assert {r["record_id"] for r in done} == {str(r.id) for r in created.values()}


def test_b4_every_row_code(client, cookie, module_seeded_url):
    family = unique()
    inactive, _ = level(module_seeded_url, active=False)
    rows = [
        student(family, ""),  # 2 required
        student(family, "x" * 101),  # 3 too_long
        student(family, "A", birthdate="31-12-2010"),  # 4 invalid_date
        student(family, "B", birthdate="2099-01-01"),  # 5 future_date
        student(family, "C", email="nope@"),  # 6 invalid_email
        student(family, "D", phone="12"),  # 7 invalid_phone
        student(family, "E", education_level=inactive),  # 8 level unknown
        student(family, "F", contact2_name="Solo"),  # 9 contact 2 incomplete
        student(family, "G", school_name="y" * 1001),  # 10 cell too long
    ]

    job = post_import(client, cookie, "students", students_csv(rows)).json()
    items = {r["row_number"]: r for r in rows_of(client, cookie, job["id"])["items"]}

    assert job["counts"]["error"] == 9 and job["counts"]["ready"] == 0
    assert {r["status"] for r in items.values()} == {"error"}
    expected = {
        2: ("given_names", "required"),
        3: ("given_names", "too_long"),
        4: ("birthdate", "invalid_date"),
        5: ("birthdate", "future_date"),
        6: ("email", "invalid_email"),
        7: ("phone", "invalid_phone"),
        8: ("education_level", "education_level_unknown"),
        9: ("contact2_relationship", "required"),
        10: ("school_name", "too_long"),
    }
    for row_number, issue in expected.items():
        assert issue in codes(items[row_number]["errors"]), row_number
    assert len(items[10]["values"]["school_name"]) == 1000
    assert action(client, cookie, job["id"], "commit").json()["code"] == (
        "import_nothing_to_commit"
    )


def test_e6_duplicates_in_file_and_existing_ignore_case_and_accents(
    client, cookie, module_seeded_url
):
    family = unique("Núñez")
    first = post_import(
        client, cookie, "students", students_csv([student(family, "José María")])
    ).json()
    action(client, cookie, first["id"], "commit")
    run_job(module_seeded_url)

    rows = [
        student(family.upper().replace("Ú", "U"), "jose maria"),  # existing
        student(family, "Lucía"),
        student(family, "LUCIA"),  # repeats the previous row
        student(family, "Lucía", birthdate="2013-01-01"),  # another birthdate
    ]
    job = post_import(client, cookie, "students", students_csv(rows)).json()
    items = rows_of(client, cookie, job["id"])["items"]

    assert [r["status"] for r in items] == ["duplicate", "ready", "duplicate", "ready"]
    assert codes(items[0]["warnings"]) == {(None, "duplicate_existing")}
    assert codes(items[2]["warnings"]) == {(None, "duplicate_in_file")}
    assert job["counts"]["duplicate"] == 2


def test_d2_a_level_deactivated_before_commit_fails_that_row(
    client, cookie, module_seeded_url
):
    family = unique()
    level_name, level_id = level(module_seeded_url)
    rows = [student(family, "Ana", education_level=level_name), student(family, "Luis")]
    job = post_import(client, cookie, "students", students_csv(rows)).json()
    assert job["counts"]["ready"] == 2
    run_sql(
        module_seeded_url,
        "UPDATE education_levels SET is_active = false WHERE id = :i",
        {"i": level_id},
    )
    action(client, cookie, job["id"], "commit")

    run_job(module_seeded_url)

    done = get(client, cookie, job_path(job["id"])).json()
    items = rows_of(client, cookie, job["id"])["items"]
    assert done["status"] == "committed"
    assert (done["counts"]["created"], done["counts"]["failed"]) == (1, 1)
    assert items[0]["status"] == "failed"
    assert codes(items[0]["errors"]) == {
        ("education_level", "education_level_unavailable")
    }
    assert [r.given_names for r in students_named(module_seeded_url, family)] == [
        "Luis"
    ]
