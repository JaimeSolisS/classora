"""The ``families.demo_families`` demo generator (#195 B, C2-C4, E5-E8)."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.families.errors import GuardianConsentRequiredError
from app.modules.families.logins import valid_login_consent_ids
from tests.factories import demo, families
from tests.factories.base import GeneratorSkipped
from tests.factories.families import (
    ADULT_BIRTHDATE,
    DEMO_STUDENT_BIRTHDATE,
    DemoFamiliesGenerator,
    DemoFamilyMissingLoginError,
    GuardianFactory,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_GENERATORS,
    DEMO_GUARDIAN_USERNAME,
    DEMO_STUDENT_USERNAME,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    DEMO_FAMILIES_ROWS,
    FAMILIES_GENERATOR,
    FIRST_RUN_LINES,
    RERUN_LINES,
    UntouchedSession,
    count,
    demo_env,
    migrate,
)

TABLES = (
    "families",
    "students",
    "student_emergency_contacts",
    "guardians",
    "student_guardians",
    "family_students",
    "family_guardians",
    "consent_records",
)
SEEDED_COUNTS = {
    "families": 3,
    "students": 5,
    "student_emergency_contacts": 5,
    "guardians": 4,
    "student_guardians": 7,
    "family_students": 5,
    "family_guardians": 4,
    "consent_records": 1,
}
EMPTY_COUNTS = dict.fromkeys(TABLES, 0)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


@pytest.fixture
def other_migrated_url(database_url: str) -> Iterator[str]:
    """A second fresh database at head (C4 compares two databases)."""
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    try:
        yield migrate(
            make_url(database_url)
            .set(database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


def table_counts(url: str) -> dict[str, int]:
    return {table: count(url, table) for table in TABLES}


def families_snapshot(url: str) -> dict[str, Any]:
    """C4: everything but ids, timestamps, hashes and contact phones."""
    users = run_sql(
        url,
        "SELECT u.username, u.display_name, u.state, r.code, p.language, p.theme"
        " FROM users u JOIN user_roles ur ON ur.user_id = u.id"
        " JOIN roles r ON r.id = ur.role_id"
        " LEFT JOIN user_preferences p ON p.user_id = u.id ORDER BY u.username",
    )
    students = run_sql(
        url,
        "SELECT f.name AS family, s.given_names, s.family_names, s.birthdate,"
        " u.username, c.name AS contact, c.relationship AS contact_relationship"
        " FROM students s"
        " JOIN family_students fs ON fs.student_id = s.id"
        " JOIN families f ON f.id = fs.family_id"
        " LEFT JOIN users u ON u.id = s.user_id"
        " JOIN student_emergency_contacts c ON c.student_id = s.id"
        " ORDER BY f.name, s.given_names, s.family_names",
    )
    guardians = run_sql(
        url,
        "SELECT f.name AS family, g.given_names, g.family_names, u.username"
        " FROM guardians g"
        " JOIN family_guardians fg ON fg.guardian_id = g.id"
        " JOIN families f ON f.id = fg.family_id"
        " LEFT JOIN users u ON u.id = g.user_id"
        " ORDER BY f.name, g.given_names, g.family_names",
    )
    links = run_sql(
        url,
        "SELECT s.given_names AS student, g.given_names AS guardian,"
        " sg.relationship FROM student_guardians sg"
        " JOIN students s ON s.id = sg.student_id"
        " JOIN guardians g ON g.id = sg.guardian_id"
        " ORDER BY s.given_names, g.given_names, sg.relationship",
    )
    consents = run_sql(
        url,
        "SELECT s.given_names AS student, g.given_names AS guardian, c.scope,"
        " c.method, u.username AS recorded_by FROM consent_records c"
        " JOIN students s ON s.id = c.student_id"
        " JOIN guardians g ON g.id = c.guardian_id"
        " JOIN users u ON u.id = c.recorded_by_user_id",
    )
    return {
        name: [tuple(row) for row in rows]
        for name, rows in {
            "users": users,
            "students": students,
            "guardians": guardians,
            "links": links,
            "consents": consents,
            "families": run_sql(url, "SELECT name FROM families ORDER BY name"),
        }.items()
    }


def family_shape(url: str) -> list[tuple[Any, ...]]:
    """Per family: its students (birthdate, login) and its guardians (login,
    relationships to the family's students)."""
    rows = run_sql(
        url,
        "SELECT f.id, f.name,"
        " (SELECT array_agg(s.family_names) FROM family_students fs"
        "  JOIN students s ON s.id = fs.student_id WHERE fs.family_id = f.id)"
        "  AS student_surnames,"
        " (SELECT array_agg(coalesce(u.username, '-') ORDER BY u.username)"
        "  FROM family_students fs JOIN students s ON s.id = fs.student_id"
        "  LEFT JOIN users u ON u.id = s.user_id WHERE fs.family_id = f.id)"
        "  AS student_logins,"
        " (SELECT array_agg(s.birthdate ORDER BY s.birthdate)"
        "  FROM family_students fs JOIN students s ON s.id = fs.student_id"
        "  WHERE fs.family_id = f.id) AS birthdates,"
        " (SELECT array_agg(coalesce(u.username, '-') ORDER BY u.username)"
        "  FROM family_guardians fg JOIN guardians g ON g.id = fg.guardian_id"
        "  LEFT JOIN users u ON u.id = g.user_id WHERE fg.family_id = f.id)"
        "  AS guardian_logins,"
        " (SELECT array_agg(sg.relationship ORDER BY sg.relationship)"
        "  FROM student_guardians sg"
        "  JOIN family_students fs ON fs.student_id = sg.student_id"
        "  JOIN family_guardians fg ON fg.guardian_id = sg.guardian_id"
        "  WHERE fs.family_id = f.id AND fg.family_id = f.id) AS relationships"
        " FROM families f ORDER BY f.created_at, f.id",
    )
    return [tuple(row) for row in rows]


def demo_student_profile_id(url: str) -> uuid.UUID:
    [row] = run_sql(
        url,
        "SELECT s.id FROM students s JOIN users u ON u.id = s.user_id"
        " WHERE u.username = :username",
        {"username": DEMO_STUDENT_USERNAME},
    )
    return uuid.UUID(str(row.id))


async def _valid_consents(url: str, student_id: uuid.UUID) -> list[uuid.UUID]:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await valid_login_consent_ids(uow.session, student_id)
    finally:
        await engine.dispose()


def test_local_creates_the_three_demo_families(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out == FIRST_RUN_LINES
    assert out[-1] == f"{FAMILIES_GENERATOR}: {DEMO_FAMILIES_ROWS} rows"
    assert table_counts(migrated_url) == SEEDED_COUNTS

    first, second, third = family_shape(migrated_url)
    for _, name, surnames, *_ in (first, second, third):
        # B3: the students share the surname pair the family is named after.
        assert set(surnames) == {name.removeprefix("Familia ")}
    assert first[3:] == (
        [DEMO_STUDENT_USERNAME, "-"],
        first[4],
        [DEMO_GUARDIAN_USERNAME],
        ["mother", "mother"],
    )
    assert DEMO_STUDENT_BIRTHDATE in first[4]
    assert all(born > date(2010, 1, 1) for born in first[4])
    assert second[3:] == (
        ["-", "-"],
        second[4],
        ["-", "-"],
        ["father", "father", "mother", "mother"],
    )
    assert all(born > date(2010, 1, 1) for born in second[4])
    assert third[3:] == (["-"], [ADULT_BIRTHDATE], ["-"], ["grandparent"])

    # B4: one in-app student_login consent from demo.guardian, recorded by it.
    [consent] = run_sql(
        migrated_url,
        "SELECT c.scope, c.method, su.username AS student, gu.username AS guardian,"
        " ru.username AS recorded_by, v.version = (SELECT max(version)"
        "  FROM privacy_notice_versions) AS current_notice"
        " FROM consent_records c"
        " JOIN students s ON s.id = c.student_id JOIN users su ON su.id = s.user_id"
        " JOIN guardians g ON g.id = c.guardian_id"
        " JOIN users gu ON gu.id = g.user_id"
        " JOIN users ru ON ru.id = c.recorded_by_user_id"
        " JOIN privacy_notice_versions v ON v.id = c.notice_version_id",
    )
    assert tuple(consent) == (
        "student_login",
        "in_app",
        DEMO_STUDENT_USERNAME,
        DEMO_GUARDIAN_USERNAME,
        DEMO_GUARDIAN_USERNAME,
        True,
    )
    student_id = demo_student_profile_id(migrated_url)
    assert len(asyncio.run(_valid_consents(migrated_url, student_id))) == 1
    # No guardian contact data, no education level, no academic-access consent.
    assert (
        run_sql(
            migrated_url,
            "SELECT count(*) FROM guardians"
            " WHERE email IS NOT NULL OR phone IS NOT NULL",
        )[0][0]
        == 0
    )
    assert (
        run_sql(
            migrated_url,
            "SELECT count(*) FROM students WHERE education_level_id IS NOT NULL",
        )[0][0]
        == 0
    )


def test_a_second_run_writes_no_family(migrated_url, env_for, capsys):
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert table_counts(migrated_url) == SEEDED_COUNTS
    assert count(migrated_url, "users") == 7


def test_dev_writes_no_family(migrated_url, env_for, capsys):
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert (
        f"{FAMILIES_GENERATOR}: skipped (APP_ENV=dev; local only)"
        in capsys.readouterr().out.splitlines()
    )
    assert table_counts(migrated_url) == EMPTY_COUNTS
    assert count(migrated_url, "users") == 0


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
def test_the_generator_writes_nothing_outside_local(app_env):
    generator = DemoFamiliesGenerator(app_env=lambda: app_env)

    result = asyncio.run(generator.generate(UntouchedSession()))  # type: ignore[arg-type]

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


async def _run_generators(url: str, *steps: Any) -> list[int | GeneratorSkipped]:
    """Run ``steps`` (generators or ``async (session) -> None`` callables) in
    one Unit of Work."""
    engine = short_lived_engine(url)
    results: list[int | GeneratorSkipped] = []
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            for step in steps:
                if hasattr(step, "generate"):
                    results.append(await step.generate(uow.session))
                else:
                    await step(uow.session)
    finally:
        await engine.dispose()
    return results


def test_a_guardian_profile_already_linked_to_demo_guardian_means_0_rows(
    migrated_url, env_for, capsys
):
    env_for("local", migrated_url)
    assert demo.main([], generators=DEMO_ACCOUNT_GENERATORS) == 0
    [guardian_user] = run_sql(
        migrated_url,
        "SELECT id FROM users WHERE username = :username",
        {"username": DEMO_GUARDIAN_USERNAME},
    )

    async def link_a_profile(session: Any) -> None:
        await families.persist(session, GuardianFactory, user_id=guardian_user.id)

    local = DemoFamiliesGenerator(app_env=lambda: "local")
    assert asyncio.run(_run_generators(migrated_url, link_a_profile, local)) == [0]

    assert table_counts(migrated_url) == {**EMPTY_COUNTS, "guardians": 1}


def test_missing_demo_logins_fail_the_run(migrated_url, env_for):
    env_for("local", migrated_url)

    with pytest.raises(DemoFamilyMissingLoginError):
        demo.main([], generators=[DemoFamiliesGenerator()])

    assert table_counts(migrated_url) == EMPTY_COUNTS


def test_a_refused_login_basis_rolls_the_whole_run_back(
    migrated_url, env_for, monkeypatch
):
    # B5 / E7: without the consent, the gate refuses the minor's login and
    # nothing at all is written (not even the accounts).
    async def no_consent(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(families, "login_consent", no_consent)
    env_for("local", migrated_url)

    with pytest.raises(GuardianConsentRequiredError):
        demo.main([])

    assert table_counts(migrated_url) == EMPTY_COUNTS
    assert count(migrated_url, "users") == 0


def test_the_same_seed_gives_the_same_demo_data(
    migrated_url, other_migrated_url, env_for
):
    # C4: two fresh databases, --seed 1.
    env_for("local", migrated_url)
    assert demo.main(["--seed", "1"]) == 0
    env_for("local", other_migrated_url)
    get_settings.cache_clear()
    assert demo.main(["--seed", "1"]) == 0

    first = families_snapshot(migrated_url)
    assert first == families_snapshot(other_migrated_url)
    assert len(first["users"]) == 7
    assert len(first["students"]) == 5
    assert len(first["guardians"]) == 4
    assert len(first["links"]) == 7
    assert len(first["consents"]) == 1
