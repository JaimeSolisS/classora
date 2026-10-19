"""The ``catalog.education_levels`` reference seed (#32 B5).

Insert-only through the ORM: four levels with a ``create`` audit event each
on the first run; nothing on later runs; administrators' changes survive.
"""

import asyncio

from app.seeds import runner
from app.seeds.upsert import UpsertResult
from tests.authz.csrf import origin_for
from tests.integration.catalog_support import catalog_event_count, insert_level
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import events

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


def result_of(results: list[tuple[str, UpsertResult]]) -> UpsertResult:
    [result] = [r for name, r in results if name == "catalog.education_levels"]
    return result


def test_the_first_run_inserts_the_four_levels_with_audit_events(head_url):
    results = seed(head_url)

    assert result_of(results) == UpsertResult(inserted=4)
    assert levels(head_url) == [
        (key, name, order, True, 1) for key, name, order in SEEDED
    ]
    created = events(head_url, record_type="education_levels")
    assert [e.action for e in created] == ["create"] * 4
    assert {e.actor_user_id for e in created} == {None}
    assert {e.request_id for e in created} == {None}
    assert [e.changes["name"]["new"] for e in created] == [n for _, n, _ in SEEDED]
    assert set(created[0].changes) == {"name", "sort_order", "is_active", "seed_key"}
    # The identity seeds write no audit row (Core upserts of unaudited tables).
    assert catalog_event_count(head_url) == 4
    [total] = run_sql(head_url, "SELECT count(*) AS n FROM audit_events")
    assert total.n == 4


def test_a_second_run_changes_nothing_and_writes_no_audit_row(head_url):
    seed(head_url)
    before = run_sql(
        head_url, "SELECT id, updated_at FROM education_levels ORDER BY id"
    )

    results = seed(head_url)

    assert result_of(results) == UpsertResult(inserted=0, updated=0)
    after = run_sql(head_url, "SELECT id, updated_at FROM education_levels ORDER BY id")
    assert after == before
    [total] = run_sql(head_url, "SELECT count(*) AS n FROM audit_events")
    assert total.n == 4


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
        "catalog.education_levels: 0 inserted, 0 updated",
    ]
