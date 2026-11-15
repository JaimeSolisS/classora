"""Every kind of data (#554 HF-01f B8) and the English labels (C5), on one
``small`` English run with the real clock frozen at
:data:`~tests.integration.simulation_support.FROZEN_NOW`.

B8 is the owner's "every kind of data" check: every table of the models'
metadata has at least one row after the run, except :data:`NOT_SEEDED`."""

import io
import re
import uuid
import zipfile
from collections.abc import Iterator
from typing import Any, Final

import pytest
from sqlalchemy.engine import make_url

from app.core.model_registry import target_metadata
from tests.conftest import frozen_clock
from tests.factories.simulation.plan import plan_simulation
from tests.factories.simulation.texts import POOLS
from tests.integration.auth_support import auth_client, login
from tests.integration.conftest import run_sql
from tests.integration.simulation_support import FROZEN_NOW, migrate, run_command

NOT_SEEDED: Final = frozenset(
    {
        # Written by real HTTP requests only: a signed-in browser session,
        # an idempotent request's key and a rate-limit window.
        "auth_sessions",
        "idempotency_keys",
        "rate_limit_counters",
        # A failed background job (#128): none is seeded, the view stays
        # empty (HF-01f "Failed jobs").
        "job_failures",
    }
)
"""The tables the simulation leaves empty, each for its reason. The
Procrastinate tables are not in the models' metadata (and stay empty: E12)."""

PLAN = plan_simulation("en", "small", 1)
KPI_RANGE = {"from": "2026-09-01", "to": "2026-11-18"}
ACCENTED = frozenset("áéíóúñ¿¡ÁÉÍÓÚÑ")


def _spanish_reference_names() -> set[str]:
    """The Spanish names HF-01 renames in English (levels, expense
    categories) and the Spanish pool's subject and classroom names."""
    names: set[str] = set()
    for key in ("education_levels", "expense_categories", "subjects", "classrooms"):
        es, en = POOLS["es"][key], POOLS["en"][key]
        assert isinstance(es, dict) and isinstance(en, dict)
        names |= set(es.values()) - set(en.values())
    return names


@pytest.fixture(scope="module")
def en_url(database_url: str) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        migrate(url)
        code, _, err = run_command(url, "en")
        assert code == 0, err
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


def scalar(url: str, sql: str, **params: Any) -> Any:
    return run_sql(url, sql, params or None)[0][0]


def test_every_table_has_rows_except_the_listed_ones(en_url):
    empty = sorted(
        table
        for table in target_metadata.tables
        if scalar(en_url, f'SELECT count(*) FROM "{table}"') == 0  # noqa: S608
    )
    assert empty == sorted(NOT_SEEDED)
    assert NOT_SEEDED <= set(target_metadata.tables)


def _get(client: Any, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def _words(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _words(item)
    elif isinstance(value, list):
        for item in value:
            found |= _words(item)
    elif isinstance(value, str):
        found.add(value)
    return found


def test_english_kpi_report_and_export_labels(en_url):
    """C5: what the API returns for the seeded data holds no Spanish
    reference name and no accented text."""
    spanish = _spanish_reference_names()
    owner = PLAN.owner
    with frozen_clock(FROZEN_NOW), auth_client(en_url) as client:
        assert login(client, owner.username, owner.password).status_code == 200
        bodies = [
            _get(client, "/api/v1/reports/kpis/course-profitability", **KPI_RANGE),
            _get(client, "/api/v1/reports/kpis/room-utilization", **KPI_RANGE),
            _get(client, "/api/v1/reports/kpis/tutor-performance", **KPI_RANGE),
            _get(client, "/api/v1/exports/datasets"),
            _get(client, "/api/v1/exports"),
            _get(client, "/api/v1/report-setups"),
            _get(client, "/api/v1/imports"),
        ]
        [ready] = [j for j in bodies[4]["items"] if j["status"] == "ready"]
    # The file itself (its download expired 24 hours after 18 Nov 17:00).
    content = scalar(
        en_url,
        "SELECT content FROM export_files WHERE job_id = :j",
        j=uuid.UUID(ready["id"]),
    )
    texts = set().union(*(_words(b) for b in bodies))
    assert not texts & spanish, texts & spanish
    for value in texts:
        assert not ACCENTED & set(value), value
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        sheets = "".join(
            book.read(name).decode()
            for name in book.namelist()
            if name.startswith("xl/") and name.endswith(".xml")
        )
    cells = set(re.findall(r"<t[^>]*>([^<]*)</t>", sheets))
    assert cells
    assert not cells & spanish, cells & spanish
    for value in cells:
        assert not ACCENTED & set(value), value


def test_english_free_text_of_administration_and_reporting(en_url):
    en = POOLS["en"]
    setups = {r[0] for r in run_sql(en_url, "SELECT name FROM report_setups")}
    assert setups == set(en["report_setups"].values())  # type: ignore[union-attr]
    files = {r[0] for r in run_sql(en_url, "SELECT file_name FROM import_jobs")}
    assert files == set(en["import_files"].values())  # type: ignore[union-attr]
    balances = {
        r[0]
        for r in run_sql(
            en_url, "SELECT description FROM charges WHERE import_job_id IS NOT NULL"
        )
    }
    assert balances == set(en["opening_balance_descriptions"])  # type: ignore[arg-type]
    deleted = en["deleted_records"]
    assert isinstance(deleted, dict)
    assert (
        scalar(en_url, "SELECT title FROM learning_plans WHERE deleted_at IS NOT NULL")
        == (deleted["plan_title"])
    )
    assert (
        scalar(en_url, "SELECT body FROM session_notes WHERE deleted_at IS NOT NULL")
        == (deleted["note"])
    )
    assert (
        scalar(en_url, "SELECT contact_email FROM business_settings")
        == (en["contact_email_updated"])
    )
    languages = {r[0] for r in run_sql(en_url, "SELECT language FROM export_jobs")}
    assert languages == {"en"}
