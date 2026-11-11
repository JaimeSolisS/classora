"""The ``catalog.education_levels`` (#32 B5) and ``catalog.general_subject``
(HF-12, #597 AC2) reference seeds.

Insert-only through the ORM: four levels, then the "General" level and
subject, with a ``create`` audit event each on the first run; nothing on
later runs; administrators' changes survive. An existing "General" level or
subject without a key is claimed instead of duplicated.
"""

import asyncio
import uuid

from app.seeds import runner
from app.seeds.upsert import UpsertResult
from tests.authz.csrf import origin_for
from tests.integration.catalog_support import (
    catalog_event_count,
    insert_level,
    insert_subject,
    level_row,
)
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import events

GENERAL_LEVEL = ("general", "General", 0)
SEEDED = [
    ("elementary", "Primaria", 10),
    ("middle_school", "Secundaria", 20),
    ("high_school", "Preparatoria", 30),
    ("university", "Universidad", 40),
]


def seed(dsn: str) -> list[tuple[str, UpsertResult]]:
    """Every registered reference seed, as ``make seed`` applies them."""

    async def _run() -> list[tuple[str, UpsertResult]]:
        engine = runner.create_seed_engine(settings_for(dsn))
        try:
            return await runner.run_reference_seeds(engine)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def levels(dsn: str) -> list[tuple[object, ...]]:
    rows = run_sql(
        dsn,
        "SELECT seed_key, name, sort_order, is_active, version"
        " FROM education_levels ORDER BY sort_order, name",
    )
    return [tuple(row) for row in rows]


def subjects(dsn: str) -> list[tuple[object, ...]]:
    rows = run_sql(
        dsn,
        "SELECT s.seed_key, s.name, l.seed_key AS level_key, l.name AS level,"
        " s.is_active, s.version FROM subjects s"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " ORDER BY s.name, s.id",
    )
    return [tuple(row) for row in rows]


def result_of(
    results: list[tuple[str, UpsertResult]], seed_name: str = "catalog.education_levels"
) -> UpsertResult:
    [result] = [r for name, r in results if name == seed_name]
    return result


def general_result(results: list[tuple[str, UpsertResult]]) -> UpsertResult:
    return result_of(results, "catalog.general_subject")


def test_the_first_run_inserts_the_four_levels_with_audit_events(head_url):
    results = seed(head_url)

    assert result_of(results) == UpsertResult(inserted=4)
    assert general_result(results) == UpsertResult(inserted=2)
    assert levels(head_url) == [
        (key, name, order, True, 1) for key, name, order in [GENERAL_LEVEL, *SEEDED]
    ]
    assert subjects(head_url) == [("general", "General", "general", "General", True, 1)]
    created = events(head_url, record_type="education_levels")
    assert [e.action for e in created] == ["create"] * 5
    assert {e.actor_user_id for e in created} == {None}
    assert {e.request_id for e in created} == {None}
    assert [e.changes["name"]["new"] for e in created] == [
        *(n for _, n, _ in SEEDED),
        "General",
    ]
    assert set(created[0].changes) == {"name", "sort_order", "is_active", "seed_key"}
    [subject_created] = events(head_url, record_type="subjects")
    assert subject_created.action == "create"
    assert subject_created.actor_user_id is None
    assert subject_created.changes["seed_key"]["new"] == "general"
    # The identity seeds write no audit row (Core upserts of unaudited tables).
    assert catalog_event_count(head_url) == 6
    [total] = run_sql(head_url, "SELECT count(*) AS n FROM audit_events")
    assert total.n == 6


def test_a_second_run_changes_nothing_and_writes_no_audit_row(head_url):
    seed(head_url)
    before = run_sql(
        head_url, "SELECT id, updated_at FROM education_levels ORDER BY id"
    )
    subjects_before = subjects(head_url)

    results = seed(head_url)

    assert result_of(results) == UpsertResult(inserted=0, updated=0)
    assert general_result(results) == UpsertResult(inserted=0, updated=0)
    after = run_sql(head_url, "SELECT id, updated_at FROM education_levels ORDER BY id")
    assert after == before
    assert subjects(head_url) == subjects_before
    [total] = run_sql(head_url, "SELECT count(*) AS n FROM audit_events")
    assert total.n == 6


def test_an_administrators_rename_and_deactivation_survive(head_url):
    seed(head_url)
    run_sql(
        head_url,
        "UPDATE education_levels SET name = 'Bachillerato', version = version + 1"
        " WHERE seed_key = 'high_school'",
    )
    run_sql(
        head_url,
        "UPDATE education_levels SET is_active = false, version = version + 1"
        " WHERE seed_key = 'university'",
    )

    results = seed(head_url)

    assert result_of(results) == UpsertResult(inserted=0)
    assert levels(head_url) == [
        ("general", "General", 0, True, 1),
        ("elementary", "Primaria", 10, True, 1),
        ("middle_school", "Secundaria", 20, True, 1),
        ("high_school", "Bachillerato", 30, True, 2),
        ("university", "Universidad", 40, False, 2),
    ]


def test_a_level_with_a_seeded_name_in_another_case_is_kept(head_url):
    asyncio.run(insert_level(head_url, "primaria", 5))

    results = seed(head_url)

    assert result_of(results) == UpsertResult(inserted=3)
    assert levels(head_url) == [
        ("general", "General", 0, True, 1),
        (None, "primaria", 5, True, 1),
        ("middle_school", "Secundaria", 20, True, 1),
        ("high_school", "Preparatoria", 30, True, 1),
        ("university", "Universidad", 40, True, 1),
    ]


def test_the_command_prints_the_counts(head_url, monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "prod")  # reference seeds run everywhere
    monkeypatch.setenv("APP_ORIGIN", origin_for("prod"))
    monkeypatch.setenv("DATABASE_URL", head_url)
    monkeypatch.setenv("DATABASE_DIRECT_URL", head_url)

    assert runner.main() == 0
    assert runner.main() == 0

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("catalog.")
    ]
    assert lines == [
        "catalog.education_levels: 4 inserted, 0 updated",
        "catalog.general_subject: 2 inserted, 0 updated",
        "catalog.education_levels: 0 inserted, 0 updated",
        "catalog.general_subject: 0 inserted, 0 updated",
    ]


# --- HF-12 (#597 AC2): the built-in General level and subject ------------------------


def test_a_renamed_general_subject_and_level_are_not_recreated(head_url):
    seed(head_url)
    run_sql(
        head_url,
        "UPDATE subjects SET name = 'Tutoría general', version = version + 1"
        " WHERE seed_key = 'general'",
    )
    run_sql(
        head_url,
        "UPDATE education_levels SET name = 'Todas', version = version + 1"
        " WHERE seed_key = 'general'",
    )

    results = seed(head_url)

    assert general_result(results) == UpsertResult(inserted=0, updated=0)
    assert subjects(head_url) == [
        ("general", "Tutoría general", "general", "Todas", True, 2)
    ]
    assert levels(head_url)[0] == ("general", "Todas", 0, True, 2)


def test_an_existing_general_level_and_subject_are_claimed(head_url):
    level = asyncio.run(insert_level(head_url, "general", 50))
    subject = asyncio.run(insert_subject(head_url, level, "GENERAL"))
    # A "General" under another level is not the built-in one: left alone.
    other_level = asyncio.run(insert_level(head_url, "Talleres", 60))
    other = asyncio.run(insert_subject(head_url, other_level, "General"))

    results = seed(head_url)

    assert general_result(results) == UpsertResult(inserted=0, updated=2)
    assert level_row(head_url, level).seed_key == "general"
    assert level_row(head_url, level).sort_order == 50
    assert [r for r in subjects(head_url) if r[0] == "general"] == [
        ("general", "GENERAL", "general", "general", True, 2)
    ]
    [row] = run_sql(
        head_url, "SELECT seed_key FROM subjects WHERE id = :id", {"id": other}
    )
    assert row.seed_key is None
    [count] = run_sql(head_url, "SELECT count(*) AS n FROM subjects")
    assert count.n == 2
    claimed = events(head_url, record_type="subjects", record_id=uuid.UUID(subject))
    assert [e.action for e in claimed] == ["update"]
    assert claimed[0].changes["seed_key"] == {"old": None, "new": "general"}

    rerun = seed(head_url)

    assert general_result(rerun) == UpsertResult(inserted=0, updated=0)


def test_an_existing_general_level_gets_the_subject(head_url):
    level = asyncio.run(insert_level(head_url, "General", 70, is_active=True))

    results = seed(head_url)

    assert general_result(results) == UpsertResult(inserted=1, updated=1)
    assert subjects(head_url) == [("general", "General", "general", "General", True, 1)]
    assert level_row(head_url, level).seed_key == "general"
