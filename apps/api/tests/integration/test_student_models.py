"""The students tables in raw SQL (#33 A4): the wrapper function, the CHECKs,
the five-contact cap, the deferred position uniqueness and the foreign keys.
The EXPLAIN test is in ``test_students_search.py``."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.catalog_support import insert_level, unique_name
from tests.integration.conftest import run_sql, short_lived_engine

STUDENT = (
    "INSERT INTO students (given_names, family_names, birthdate{extra_columns})"
    " VALUES (:given, :family, CAST(:birthdate AS text)::date{extra_values})"
    " RETURNING id"
)


def insert_student(
    url: str,
    *,
    given: str = "Ana",
    family: str = "Ruiz",
    birthdate: str = "2012-01-01",
    **extra: object,
) -> str:
    columns = "".join(f", {name}" for name in extra)
    values = "".join(f", :{name}" for name in extra)
    [row] = run_sql(
        url,
        STUDENT.format(extra_columns=columns, extra_values=values),
        {"given": given, "family": family, "birthdate": birthdate, **extra},
    )
    return str(row.id)


def insert_contact(url: str, student_id: str, position: int, **extra: object) -> None:
    run_sql(
        url,
        "INSERT INTO student_emergency_contacts"
        " (student_id, name, relationship, phone, position)"
        " VALUES (:student_id, :name, :relationship, :phone, :position)",
        {
            "student_id": student_id,
            "name": "Ana",
            "relationship": "Madre",
            "phone": "55 1234 5678",
            "position": position,
            **extra,
        },
    )


def test_f_unaccent_folds_accents(module_seeded_url):
    [row] = run_sql(
        module_seeded_url, "SELECT public.f_unaccent(lower('ÁÉÍÓÚ Ñúñez')) AS folded"
    )

    assert row.folded == "aeiou nunez"


@pytest.mark.parametrize(
    "values",
    [
        {"given": "   "},
        {"given": " Ana"},
        {"family": ""},
        {"birthdate": "1899-12-31"},
        {"email": "a b@c.d"},
        {"phone": "12"},
        {"school_name": " Escuela"},
        {"school_grade": "2.º "},
    ],
    ids=str,
)
def test_invalid_students_are_rejected(module_seeded_url, values):
    with pytest.raises(IntegrityError):
        insert_student(module_seeded_url, **values)


def test_a_valid_student_with_the_oldest_birthdate(module_seeded_url):
    insert_student(
        module_seeded_url,
        birthdate="1900-01-01",
        email="a@b.co",
        phone="+52 (55) 1234-5678",
    )


@pytest.mark.parametrize(
    "values",
    [
        {"position": 6},
        {"position": 0},
        {"name": " "},
        {"relationship": ""},
        {"phone": "phone-ext"},
    ],
    ids=str,
)
def test_invalid_contacts_are_rejected(module_seeded_url, values):
    student_id = insert_student(module_seeded_url)
    position = values.pop("position", 1)

    with pytest.raises(IntegrityError):
        insert_contact(module_seeded_url, student_id, position, **values)


def test_a_sixth_contact_is_rejected(module_seeded_url):
    student_id = insert_student(module_seeded_url)
    for position in range(1, 6):
        insert_contact(module_seeded_url, student_id, position)

    with pytest.raises(IntegrityError):
        insert_contact(module_seeded_url, student_id, 6)


def test_a_duplicate_position_is_rejected_at_commit_not_before(module_seeded_url):
    student_id = insert_student(module_seeded_url)
    insert_contact(module_seeded_url, student_id, 1, name="Ana")
    insert_contact(module_seeded_url, student_id, 2, name="Beto")
    move = text(
        "UPDATE student_emergency_contacts SET position = :position"
        " WHERE student_id = :student AND name = :name"
    )

    async def run() -> None:
        engine = short_lived_engine(module_seeded_url)
        try:
            # A swap passes through a duplicate position: allowed inside the
            # transaction (the constraint is checked at commit).
            async with engine.begin() as conn:
                await conn.execute(
                    move, {"position": 2, "student": student_id, "name": "Ana"}
                )
                await conn.execute(
                    move, {"position": 1, "student": student_id, "name": "Beto"}
                )
            # A duplicate still there at commit is refused.
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        move, {"position": 2, "student": student_id, "name": "Beto"}
                    )
        finally:
            await engine.dispose()

    asyncio.run(run())

    rows = run_sql(
        module_seeded_url,
        "SELECT name, position FROM student_emergency_contacts"
        " WHERE student_id = :id ORDER BY position",
        {"id": student_id},
    )
    assert [(r.name, r.position) for r in rows] == [("Beto", 1), ("Ana", 2)]


def test_deleting_a_student_with_contacts_is_rejected(module_seeded_url):
    student_id = insert_student(module_seeded_url)
    insert_contact(module_seeded_url, student_id, 1)

    with pytest.raises(IntegrityError):
        run_sql(
            module_seeded_url, "DELETE FROM students WHERE id = :id", {"id": student_id}
        )


def test_deleting_a_level_used_by_a_student_is_rejected(module_seeded_url):
    level_id = asyncio.run(insert_level(module_seeded_url, unique_name(), 9000))
    insert_student(module_seeded_url, education_level_id=level_id)

    with pytest.raises(IntegrityError):
        run_sql(
            module_seeded_url,
            "DELETE FROM education_levels WHERE id = :id",
            {"id": level_id},
        )
