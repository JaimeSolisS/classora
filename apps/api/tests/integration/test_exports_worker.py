"""The export run (#124 D1-D4, E4, E7, F2, F3, G1), calling the task
function in-process."""

import asyncio
import io
import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Any, ClassVar, Literal

import openpyxl
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.modules.reporting.exports import render, service
from app.modules.reporting.exports.registry import (
    ExportColumn,
    ExportDataset,
    ExportParametersBase,
    register_export_dataset,
    unregister_export_dataset,
)
from app.modules.reporting.tasks import run_exports
from tests.conftest import frozen_clock
from tests.factories.families import persist_student
from tests.integration.conftest import run_sql, settings_for
from tests.integration.expense_support import new_vendor
from tests.integration.export_support import (
    actor,
    audit_of,
    clear_queue,
    csv_table,
    expenses_body,
    file_of,
    insert_job,
    job_row,
    new_expense,
    notifications_of,
    requested,
    run_job,
    students_body,
    unique,
)
from tests.integration.lifecycle_support import in_uow

TASK_LOGGER = "app.modules.reporting.tasks"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)
    # A test's leftover queued job never reaches the next test's run.
    run_sql(module_seeded_url, "DELETE FROM export_jobs WHERE status = 'queued'")


# --- A fake dataset whose rows a test controls ----------------------------------------


class ProbeFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeParameters(ExportParametersBase):
    COLUMN_CODES: ClassVar[tuple[str, ...]] = ("name",)

    dataset: Literal["probe_rows"]
    format: Literal["csv", "xlsx"]
    columns: list[Literal["name"]] | None = Field(default=None, min_length=1)
    group_by: None = None
    filters: ProbeFilters = Field(default_factory=ProbeFilters)


class Probe:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: BaseException | None = None
        self.delay = 0.0

    async def rows(
        self, session: Any, principal: Any, filters: Any, *, limit: int, language: str
    ) -> list[dict[str, Any]]:
        level = await session.scalar(text("SHOW transaction_isolation"))
        self.calls.append(str(level))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return [{"name": "uno"}]


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[Probe]:
    made = Probe()
    # The catalog has no labels for a test dataset: its header is the code.
    monkeypatch.setattr(render, "header_label", lambda _l, _d, code: code)
    dataset = ExportDataset(
        code="probe_rows",
        permission="expense.view",
        personal=False,
        columns=(ExportColumn("name", "text"),),
        filters_model=ProbeFilters,
        parameters_model=ProbeParameters,
        rows=made.rows,
    )
    register_export_dataset(dataset)
    yield made
    unregister_export_dataset("probe_rows")


# --- D1: a run builds the queued job --------------------------------------------------


def test_a_run_builds_the_file_and_notifies_the_requester(module_seeded_url, client):
    who = actor(module_seeded_url, client, "finance_staff")
    vendor = new_vendor(module_seeded_url)
    new_expense(client, who, module_seeded_url, vendor, description="Luz")
    new_expense(client, who, module_seeded_url, vendor, description="Agua")
    job = requested(
        client,
        who,
        expenses_body(columns=["description", "amount"], filters={"vendor_id": vendor}),
    )

    result = run_job(module_seeded_url)

    assert (result.ready, result.failed) == (1, 0)
    row = job_row(module_seeded_url, job["id"])
    content = file_of(module_seeded_url, job["id"])
    assert content is not None
    assert row.status == "ready"
    assert row.row_count == 2
    assert row.byte_size == len(content)
    assert row.error_code is None
    assert row.expires_at - row.finished_at == timedelta(hours=24)
    assert sorted(csv_table(content)[1:]) == [["Agua", "1000.00"], ["Luz", "1000.00"]]
    [notice] = notifications_of(module_seeded_url, who.user, "reporting.export_ready")
    assert notice.params == {
        "export_id": job["id"],
        "dataset": "expenses",
        "format": "csv",
        "row_count": 2,
    }
    # F3: the worker's update is the requester's.
    create, update = audit_of(module_seeded_url, job["id"])
    assert (create.action, update.action) == ("create", "update")
    assert update.actor_user_id == who.user.id
    assert update.changes["status"] == {"old": "queued", "new": "ready"}


def test_running_again_changes_nothing(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    job = requested(client, who, expenses_body())
    run_job(module_seeded_url)
    before = job_row(module_seeded_url, job["id"])

    result = run_job(module_seeded_url)

    assert result.jobs == 0
    after = job_row(module_seeded_url, job["id"])
    assert after.updated_at == before.updated_at
    assert len(audit_of(module_seeded_url, job["id"])) == 2
    assert (
        len(notifications_of(module_seeded_url, who.user, "reporting.export_ready"))
        == 1
    )


def test_the_job_runs_in_one_repeatable_read_transaction(
    module_seeded_url, client, probe
):
    who = actor(module_seeded_url, client, "finance_staff")
    job = insert_job(module_seeded_url, who.user, "probe_rows")

    run_job(module_seeded_url)

    assert probe.calls == ["repeatable read"]
    assert job_row(module_seeded_url, job).status == "ready"
    assert csv_table(file_of(module_seeded_url, job) or b"") == [["name"], ["uno"]]


def test_an_xlsx_export_is_read_back_without_formulas(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    vendor = new_vendor(module_seeded_url)
    new_expense(
        client, who, module_seeded_url, vendor, description='=HYPERLINK("http://x")'
    )
    job = requested(
        client,
        who,
        expenses_body(
            "xlsx", columns=["description", "amount"], filters={"vendor_id": vendor}
        ),
    )

    run_job(module_seeded_url)

    ws = openpyxl.load_workbook(
        io.BytesIO(file_of(module_seeded_url, job["id"]) or b"")
    ).active
    cells = [c for row in ws.iter_rows() for c in row]
    assert [c.value for c in cells] == [
        "Descripción",
        "Monto",
        '\'=HYPERLINK("http://x")',
        1000,
    ]
    assert not [c for c in cells if c.data_type == "f"]


def test_a_formula_like_description_is_escaped_in_csv(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    vendor = new_vendor(module_seeded_url)
    new_expense(
        client, who, module_seeded_url, vendor, description='=HYPERLINK("http://x")'
    )
    job = requested(
        client,
        who,
        expenses_body(columns=["description"], filters={"vendor_id": vendor}),
    )

    run_job(module_seeded_url)

    assert csv_table(file_of(module_seeded_url, job["id"]) or b"")[1] == [
        '\'=HYPERLINK("http://x")'
    ]


def test_an_export_with_no_rows_is_ready_with_the_header(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    vendor = new_vendor(module_seeded_url)
    job = requested(
        client, who, expenses_body(columns=["vendor"], filters={"vendor_id": vendor})
    )

    run_job(module_seeded_url)

    assert job_row(module_seeded_url, job["id"]).row_count == 0
    assert csv_table(file_of(module_seeded_url, job["id"]) or b"") == [["Proveedor"]]


def test_the_export_uses_the_language_stored_at_request(module_seeded_url, client):
    from tests.integration.export_support import set_language

    who = actor(module_seeded_url, client, "owner")
    set_language(module_seeded_url, who.user, "en")
    vendor = new_vendor(module_seeded_url)
    new_expense(client, who, module_seeded_url, vendor)
    job = requested(
        client,
        who,
        expenses_body(columns=["category", "status"], filters={"vendor_id": vendor}),
    )
    set_language(module_seeded_url, who.user, "es")

    run_job(module_seeded_url)

    assert job["language"] == "en"
    assert csv_table(file_of(module_seeded_url, job["id"]) or b"") == [
        ["Category", "Status"],
        ["Rent", "Overdue"],
    ]


# --- D2: failures ---------------------------------------------------------------------


def test_too_many_rows_fail_the_job_without_a_file(
    module_seeded_url, client, monkeypatch
):
    who = actor(module_seeded_url, client, "owner")
    vendor = new_vendor(module_seeded_url)
    for _ in range(2):
        new_expense(client, who, module_seeded_url, vendor)
    job = requested(client, who, expenses_body(filters={"vendor_id": vendor}))
    monkeypatch.setattr(service, "MAX_ROWS", 1)

    run_job(module_seeded_url)

    row = job_row(module_seeded_url, job["id"])
    assert (row.status, row.error_code) == ("failed", "row_limit_exceeded")
    assert row.row_count is None and row.expires_at is None
    assert file_of(module_seeded_url, job["id"]) is None
    [notice] = notifications_of(module_seeded_url, who.user, "reporting.export_failed")
    assert notice.params == {
        "export_id": job["id"],
        "dataset": "expenses",
        "error_code": "row_limit_exceeded",
    }


def test_a_file_over_the_size_limit_fails_the_job(
    module_seeded_url, client, monkeypatch
):
    who = actor(module_seeded_url, client, "owner")
    job = requested(client, who, expenses_body())
    monkeypatch.setattr(service, "MAX_FILE_BYTES", 10)

    run_job(module_seeded_url)

    row = job_row(module_seeded_url, job["id"])
    assert (row.status, row.error_code) == ("failed", "size_limit_exceeded")
    assert file_of(module_seeded_url, job["id"]) is None


@pytest.mark.parametrize("change", ["suspend", "role"])
def test_a_requester_who_lost_access_gets_permission_lost(
    module_seeded_url, client, change
):
    from tests.integration.export_support import set_roles

    who = actor(module_seeded_url, client, "finance_staff")
    job = requested(client, who, expenses_body())
    if change == "suspend":
        run_sql(
            module_seeded_url,
            "UPDATE users SET state = 'suspended' WHERE id = :u",
            {"u": who.user.id},
        )
    else:
        set_roles(module_seeded_url, who.user, "academic_coordinator")

    run_job(module_seeded_url)

    row = job_row(module_seeded_url, job["id"])
    assert (row.status, row.error_code) == ("failed", "permission_lost")
    assert file_of(module_seeded_url, job["id"]) is None
    # The notification waits until they can sign in.
    assert (
        len(notifications_of(module_seeded_url, who.user, "reporting.export_failed"))
        == 1
    )
    [_, update] = audit_of(module_seeded_url, job["id"])
    assert update.actor_user_id == who.user.id


def test_an_exception_fails_the_job_once_and_never_loops(
    module_seeded_url, client, probe, caplog
):
    who = actor(module_seeded_url, client, "finance_staff")
    job = insert_job(module_seeded_url, who.user, "probe_rows")
    probe.error = RuntimeError("boom Zyxw")

    with caplog.at_level(logging.ERROR, logger=TASK_LOGGER):
        result = run_job(module_seeded_url)

    assert (result.failed, result.ready) == (1, 0)
    row = job_row(module_seeded_url, job)
    assert (row.status, row.error_code) == ("failed", "error")
    assert file_of(module_seeded_url, job) is None
    [record] = [r for r in caplog.records if r.message == "Building an export failed"]
    assert record.error == "RuntimeError"
    assert "Zyxw" not in caplog.text
    assert (
        len(notifications_of(module_seeded_url, who.user, "reporting.export_failed"))
        == 1
    )

    again = run_job(module_seeded_url)

    assert again.jobs == 0
    assert probe.calls == ["repeatable read"]
    assert job_row(module_seeded_url, job).updated_at == row.updated_at


def test_a_crash_leaves_the_job_queued_for_the_next_run(
    module_seeded_url, client, probe
):
    who = actor(module_seeded_url, client, "finance_staff")
    job = insert_job(module_seeded_url, who.user, "probe_rows")
    # Not an Exception: no handler runs, as when the worker process dies.
    probe.error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run_job(module_seeded_url)

    assert job_row(module_seeded_url, job).status == "queued"
    assert file_of(module_seeded_url, job) is None
    probe.error = None

    run_job(module_seeded_url)

    assert job_row(module_seeded_url, job).status == "ready"


def test_a_job_of_an_unknown_dataset_fails_with_error(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    job = insert_job(module_seeded_url, who.user, "gone_rows")

    run_job(module_seeded_url)

    row = job_row(module_seeded_url, job)
    assert (row.status, row.error_code) == ("failed", "error")


# --- D3: concurrency ------------------------------------------------------------------


def test_a_locked_job_is_skipped(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    job = requested(client, who, expenses_body())

    async def scenario() -> Any:
        from tests.integration.conftest import short_lived_engine

        engine = short_lived_engine(module_seeded_url)
        try:
            async with engine.connect() as conn, conn.begin():
                await conn.execute(
                    text("SELECT id FROM export_jobs WHERE id = :i FOR UPDATE"),
                    {"i": job["id"]},
                )
                return await run_exports(settings_for(module_seeded_url))
        finally:
            await engine.dispose()

    result = asyncio.run(scenario())

    assert (result.jobs, result.skipped, result.ready) == (1, 1, 0)
    assert job_row(module_seeded_url, job["id"]).status == "queued"
    run_job(module_seeded_url)
    assert job_row(module_seeded_url, job["id"]).status == "ready"


def test_two_concurrent_runs_build_a_job_once(module_seeded_url, client, probe):
    who = actor(module_seeded_url, client, "finance_staff")
    job = insert_job(module_seeded_url, who.user, "probe_rows")
    probe.delay = 0.5

    async def both() -> Any:
        settings = settings_for(module_seeded_url)
        return await asyncio.gather(run_exports(settings), run_exports(settings))

    first, second = asyncio.run(both())

    assert first.ready + second.ready == 1
    assert first.skipped + second.skipped == 1
    assert probe.calls == ["repeatable read"]
    assert job_row(module_seeded_url, job).status == "ready"
    assert (
        len(notifications_of(module_seeded_url, who.user, "reporting.export_ready"))
        == 1
    )


# --- D4: the purge --------------------------------------------------------------------


def test_the_purge_expires_ready_jobs_after_24_hours(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    job = requested(client, who, expenses_body())
    run_job(module_seeded_url)
    expires_at = job_row(module_seeded_url, job["id"]).expires_at

    with frozen_clock(expires_at - timedelta(seconds=1)):
        run_job(module_seeded_url)
    assert job_row(module_seeded_url, job["id"]).status == "ready"
    assert file_of(module_seeded_url, job["id"]) is not None

    with frozen_clock(expires_at):
        result = run_job(module_seeded_url)

    assert result.expired >= 1
    row = job_row(module_seeded_url, job["id"])
    assert row.status == "expired"
    assert row.row_count is not None and row.expires_at == expires_at
    assert file_of(module_seeded_url, job["id"]) is None
    # F3: the purge is a system job (no actor).
    *_, expired = audit_of(module_seeded_url, job["id"])
    assert expired.changes["status"] == {"old": "ready", "new": "expired"}
    assert expired.actor_user_id is None


def test_the_purge_runs_in_batches(module_seeded_url, client, monkeypatch):
    who = actor(module_seeded_url, client, "owner")
    jobs = [requested(client, who, expenses_body()) for _ in range(3)]
    run_job(module_seeded_url)
    monkeypatch.setattr(service, "PURGE_BATCH", 2)
    latest = max(job_row(module_seeded_url, j["id"]).expires_at for j in jobs)

    with frozen_clock(latest):
        result = run_job(module_seeded_url)

    assert result.expired >= 3
    assert {job_row(module_seeded_url, j["id"]).status for j in jobs} == {"expired"}


# --- F2: personal data stays in the file ----------------------------------------------


def test_a_students_name_reaches_only_the_file(module_seeded_url, client, caplog):
    sentinel = unique("Zyxw")

    async def make(session: Any) -> None:
        await persist_student(session, family_names=sentinel, given_names="Ana")

    in_uow(module_seeded_url, make)
    who = actor(module_seeded_url, client, "owner", reauth=True)

    with caplog.at_level(logging.DEBUG):
        job = requested(client, who, students_body())
        run_job(module_seeded_url)

    assert sentinel.encode() in (file_of(module_seeded_url, job["id"]) or b"")
    for sql in (
        # The student's own audit trail names them (spec §9); the export adds
        # no event that does.
        "SELECT count(*) AS n FROM audit_events WHERE (CAST(changes AS text)"
        " LIKE :s OR CAST(details AS text) LIKE :s) AND record_type <> 'students'",
        "SELECT count(*) AS n FROM export_jobs WHERE CAST(parameters AS text) LIKE :s"
        " OR file_name LIKE :s",
        "SELECT count(*) AS n FROM notifications WHERE CAST(params AS text) LIKE :s",
    ):
        assert run_sql(module_seeded_url, sql, {"s": f"%{sentinel}%"})[0].n == 0, sql
    assert sentinel not in caplog.text
    assert not [r for r in caplog.records if sentinel in str(r.__dict__)]
