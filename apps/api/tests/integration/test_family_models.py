"""The family tables in raw SQL (#172 A2): one family per student and per
guardian, a non-blank name, ``RESTRICT`` foreign keys and the trigram index;
and the G4 factories."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.modules.families.models import Family, FamilyGuardian, FamilyStudent
from app.modules.families.search import text_search_condition
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    persist_student,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow
from tests.integration.test_guardian_models import insert_guardian
from tests.integration.test_student_models import insert_student


def insert_family(url: str, name: str = "Familia García López") -> str:
    [row] = run_sql(
        url, "INSERT INTO families (name) VALUES (:name) RETURNING id", {"name": name}
    )
    return str(row.id)


def insert_member(url: str, table: str, family_id: str, member_id: str) -> None:
    column = "student_id" if table == "family_students" else "guardian_id"
    run_sql(
        url,
        f"INSERT INTO {table} (family_id, {column})"  # noqa: S608 - test constants
        " VALUES (:family_id, :member_id)",
        {"family_id": family_id, "member_id": member_id},
    )


def new_member(url: str, table: str) -> str:
    return insert_student(url) if table == "family_students" else insert_guardian(url)


MEMBERSHIP = [
    ("family_students", "uq_family_students_student_id"),
    ("family_guardians", "uq_family_guardians_guardian_id"),
]


@pytest.mark.parametrize(("table", "unique"), MEMBERSHIP)
def test_a_member_in_two_families_is_rejected(module_seeded_url, table, unique):
    member = new_member(module_seeded_url, table)
    insert_member(module_seeded_url, table, insert_family(module_seeded_url), member)

    with pytest.raises(IntegrityError, match=unique):
        insert_member(
            module_seeded_url, table, insert_family(module_seeded_url), member
        )


@pytest.mark.parametrize(("table", "unique"), MEMBERSHIP)
def test_the_same_member_twice_in_one_family_is_rejected(
    module_seeded_url, table, unique
):
    member = new_member(module_seeded_url, table)
    family = insert_family(module_seeded_url)
    insert_member(module_seeded_url, table, family, member)

    with pytest.raises(IntegrityError, match=unique):
        insert_member(module_seeded_url, table, family, member)


def test_a_family_has_many_students_and_guardians(module_seeded_url):
    family = insert_family(module_seeded_url)
    for table in ("family_students", "family_guardians"):
        for _ in range(2):
            insert_member(
                module_seeded_url, table, family, new_member(module_seeded_url, table)
            )


@pytest.mark.parametrize("name", ["", "   ", "\t", " Familia", "Familia "])
def test_a_blank_or_untrimmed_name_is_rejected(module_seeded_url, name):
    with pytest.raises(IntegrityError, match="ck_families_name_"):
        insert_family(module_seeded_url, name)


def test_a_201_character_name_is_rejected(module_seeded_url):
    with pytest.raises(Exception, match="too long"):
        insert_family(module_seeded_url, "F" * 201)


def test_duplicate_names_are_allowed(module_seeded_url):
    assert insert_family(module_seeded_url, "Familia Igual") != insert_family(
        module_seeded_url, "Familia Igual"
    )


@pytest.mark.parametrize(("table", "_unique"), MEMBERSHIP)
def test_deleting_a_family_with_members_is_rejected(module_seeded_url, table, _unique):
    family = insert_family(module_seeded_url)
    insert_member(
        module_seeded_url, table, family, new_member(module_seeded_url, table)
    )

    with pytest.raises(IntegrityError, match=f"fk_{table}_family_id_families"):
        run_sql(
            module_seeded_url, "DELETE FROM families WHERE id = :id", {"id": family}
        )


@pytest.mark.parametrize(
    ("table", "target"),
    [("family_students", "students"), ("family_guardians", "guardians")],
)
def test_deleting_a_member_student_or_guardian_is_rejected(
    module_seeded_url, table, target
):
    member = new_member(module_seeded_url, table)
    insert_member(module_seeded_url, table, insert_family(module_seeded_url), member)

    with pytest.raises(IntegrityError, match=f"fk_{table}_"):
        run_sql(
            module_seeded_url,
            f"DELETE FROM {target} WHERE id = :id",  # noqa: S608 - test constant
            {"id": member},
        )


def test_an_empty_family_can_be_deleted_in_sql(module_seeded_url):
    # Only the RESTRICT keys stop a delete; the application never deletes.
    family = insert_family(module_seeded_url)
    run_sql(module_seeded_url, "DELETE FROM families WHERE id = :id", {"id": family})


def test_an_explain_of_the_family_search_uses_the_trigram_index(seeded_url):
    async def many(session) -> None:
        for n in range(2000):
            session.add(Family(name=f"Familia Apellido {n:04d}"))
        await session.flush()

    in_uow(seeded_url, many)
    run_sql(seeded_url, "ANALYZE families", autocommit=True)

    async def explain(session) -> str:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        query = select(Family.id).where(
            text_search_condition("apellido 0042", Family.name)
        )
        compiled = query.compile(
            dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
        )
        rows = await session.execute(text(f"EXPLAIN {compiled}"))
        return "\n".join(row[0] for row in rows)

    plan = in_uow(seeded_url, explain)

    assert "ix_families_search_name_trgm" in plan, plan


def test_the_factories_persist_a_family_with_members(module_seeded_url):
    async def build(session):
        family = await persist(session, FamilyFactory)
        first = await persist_student(session)
        second = await persist_student(session)
        guardian = await persist(session, GuardianFactory)
        rows = await add_to_family(session, family, first, second, guardian)
        return family.id, rows

    family_id, rows = in_uow(module_seeded_url, build)

    assert [type(row) for row in rows] == [FamilyStudent, FamilyStudent, FamilyGuardian]
    [counts] = run_sql(
        module_seeded_url,
        "SELECT (SELECT count(*) FROM family_students WHERE family_id = :id) AS s,"
        " (SELECT count(*) FROM family_guardians WHERE family_id = :id) AS g",
        {"id": family_id},
    )
    assert (counts.s, counts.g) == (2, 1)
