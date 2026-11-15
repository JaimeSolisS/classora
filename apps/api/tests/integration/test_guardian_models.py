"""The guardian tables in raw SQL (#34 A4): one account per profile, one link
per student and guardian, the relationship codes, the foreign keys and the
trigram index."""

from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.modules.families.models import Guardian
from app.modules.families.search import name_search_condition
from tests.integration.auth_support import create_user
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow
from tests.integration.test_student_models import insert_student


def insert_guardian(url: str, **extra: object) -> str:
    values_by_column = {"given_names": "Laura", "family_names": "Ñúñez", **extra}
    columns = ", ".join(values_by_column)
    values = ", ".join(f":{name}" for name in values_by_column)
    [row] = run_sql(
        url,
        f"INSERT INTO guardians ({columns}) VALUES ({values}) RETURNING id",  # noqa: S608
        values_by_column,
    )
    return str(row.id)


def insert_link(
    url: str, student_id: str, guardian_id: str, relationship: str = "mother"
) -> None:
    run_sql(
        url,
        "INSERT INTO student_guardians (student_id, guardian_id, relationship)"
        " VALUES (:student_id, :guardian_id, :relationship)",
        {
            "student_id": student_id,
            "guardian_id": guardian_id,
            "relationship": relationship,
        },
    )


@pytest.mark.parametrize(
    "values",
    [
        {"given_names": "  "},
        {"family_names": " Ñúñez"},
        {"email": "a b@c.d"},
        {"phone": "12"},
    ],
    ids=str,
)
def test_invalid_guardians_are_rejected(module_seeded_url, values):
    with pytest.raises(IntegrityError):
        insert_guardian(module_seeded_url, **values)


def test_a_second_guardian_with_the_same_account_is_rejected(module_seeded_url):
    user = create_user(module_seeded_url, roles=("guardian",))
    insert_guardian(module_seeded_url, user_id=user.id)

    with pytest.raises(IntegrityError, match="uq_guardians_user_id"):
        insert_guardian(module_seeded_url, user_id=user.id)


def test_many_guardians_without_an_account_are_allowed(module_seeded_url):
    insert_guardian(module_seeded_url)
    insert_guardian(module_seeded_url)


def test_a_duplicate_link_is_rejected(module_seeded_url):
    student_id = insert_student(module_seeded_url)
    guardian_id = insert_guardian(module_seeded_url)
    insert_link(module_seeded_url, student_id, guardian_id)

    with pytest.raises(IntegrityError, match="uq_student_guardians_student_guardian"):
        insert_link(module_seeded_url, student_id, guardian_id, "father")


@pytest.mark.parametrize("relationship", ["aunt", "Mother", "", "mother "])
def test_an_unknown_relationship_is_rejected(module_seeded_url, relationship):
    student_id = insert_student(module_seeded_url)
    guardian_id = insert_guardian(module_seeded_url)

    with pytest.raises(IntegrityError, match="relationship_valid"):
        insert_link(module_seeded_url, student_id, guardian_id, relationship)


@pytest.mark.parametrize(
    "relationship",
    [
        "mother",
        "father",
        "grandparent",
        "stepparent",
        "sibling",
        "other_relative",
        "legal_guardian",
        "other",
    ],
)
def test_every_relationship_code_is_accepted(module_seeded_url, relationship):
    insert_link(
        module_seeded_url,
        insert_student(module_seeded_url),
        insert_guardian(module_seeded_url),
        relationship,
    )


@pytest.mark.parametrize("table", ["students", "guardians"])
def test_deleting_a_linked_student_or_guardian_is_rejected(module_seeded_url, table):
    student_id = insert_student(module_seeded_url)
    guardian_id = insert_guardian(module_seeded_url)
    insert_link(module_seeded_url, student_id, guardian_id)
    row_id = student_id if table == "students" else guardian_id

    with pytest.raises(IntegrityError):
        run_sql(
            module_seeded_url,
            f"DELETE FROM {table} WHERE id = :id",  # noqa: S608 - test constant
            {"id": row_id},
        )


def test_deleting_a_user_linked_to_a_guardian_is_rejected(module_seeded_url):
    user = create_user(module_seeded_url, roles=())
    insert_guardian(module_seeded_url, user_id=user.id)

    with pytest.raises(IntegrityError, match="fk_guardians_user_id_users"):
        run_sql(module_seeded_url, "DELETE FROM users WHERE id = :id", {"id": user.id})


def test_an_explain_of_the_guardian_search_uses_the_trigram_index(seeded_url):
    async def many(session) -> None:
        for n in range(2000):
            session.add(
                Guardian(given_names="Nombre", family_names=f"Apellido {n:04d}")
            )
        await session.flush()

    in_uow(seeded_url, many)
    run_sql(seeded_url, "ANALYZE guardians", autocommit=True)

    async def explain(session) -> str:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        query = select(Guardian.id).where(
            name_search_condition(
                "apellido 0042", Guardian.given_names, Guardian.family_names
            )
        )
        compiled = query.compile(
            dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
        )
        rows = await session.execute(text(f"EXPLAIN {compiled}"))
        return "\n".join(row[0] for row in rows)

    plan = in_uow(seeded_url, explain)

    assert "ix_guardians_search_name_trgm" in plan, plan


def test_insert_student_helper_still_makes_minors(module_seeded_url):
    # Guards the shared raw-SQL helper this module imports.
    student_id = insert_student(module_seeded_url)
    [row] = run_sql(
        module_seeded_url,
        "SELECT birthdate FROM students WHERE id = :id",
        {"id": student_id},
    )
    assert row.birthdate == date(2012, 1, 1)
