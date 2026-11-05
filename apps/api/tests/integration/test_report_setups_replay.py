"""Running a saved setup = configuring it by hand (#125 C1).

Per dataset, a body with a non-default column order, a grouping and filters
is saved; its stored ``parameters`` and the original body are both posted to
``POST /api/v1/exports`` and the task builds both. The CSV files are
byte-identical; the XLSX workbooks have the same cell values and types
(their metadata hold a timestamp).
"""

import io
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.families import persist_student
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration.catalog_support import unique_year
from tests.integration.expense_support import new_vendor
from tests.integration.export_support import (
    Actor,
    actor,
    clear_queue,
    download,
    expenses_body,
    file_of,
    job_row,
    new_expense,
    requested,
    run_job,
    sessions_body,
    students_body,
    unique,
)
from tests.integration.lifecycle_support import in_uow
from tests.integration.report_setup_support import saved

ZONE = ZoneInfo("America/Mexico_City")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


@pytest.fixture(scope="module")
def level(module_seeded_url: str) -> str:
    """A level of its own with four students, one inactive."""
    family = unique("Rep")

    async def make(session: AsyncSession) -> str:
        subject = await persist_subject(session)
        for given, extra in (
            ("Ana", {}),
            ("Beto", {"school_grade": "3º"}),
            ("Carla", {"is_active": False}),
            ("Dani", {"school_grade": "1º"}),
        ):
            await persist_student(
                session,
                given_names=given,
                family_names=family,
                education_level_id=subject.education_level_id,
                **extra,
            )
        return str(subject.education_level_id)

    return in_uow(module_seeded_url, make)


@pytest.fixture(scope="module")
def first_day(module_seeded_url: str) -> date:
    """A month of its own with four sessions, one cancelled."""
    first = date(unique_year(), 4, 6)

    def local(day: date, hour: int) -> datetime:
        return datetime.combine(day, time(hour), tzinfo=ZONE)

    async def make(session: AsyncSession) -> None:
        for offset, hour, extra in (
            (0, 16, {"students": 2}),
            (2, 9, {"classroom": True, "students": 1}),
            (4, 10, {"state": "unassigned"}),
            (5, 11, {"state": "cancelled"}),
        ):
            students = extra.pop("students", 0)
            day = first + timedelta(days=offset)
            row = await persist_class_session(
                session,
                starts_at=local(day, hour),
                ends_at=local(day, hour + 1),
                **extra,
            )
            for _ in range(students):
                await add_participant(session, row)

    in_uow(module_seeded_url, make)
    return first


def bodies(url: str, client: TestClient, who: Actor, dataset: str, fmt: str, **k):
    if dataset == "students":
        return students_body(
            fmt,
            columns=["school_grade", "given_names", "family_names", "is_active"],
            group_by="is_active",
            filters={"status": "all", "education_level_id": k["level"]},
        )
    if dataset == "class_sessions":
        first = k["first_day"]
        return sessions_body(
            first.isoformat(),
            (first + timedelta(days=9)).isoformat(),
            fmt,
            columns=["state", "date", "start_time", "students", "kind"],
            group_by="state",
            filters={
                "from": first.isoformat(),
                "to": (first + timedelta(days=9)).isoformat(),
                "include_cancelled": True,
            },
        )
    vendor = new_vendor(url)
    for index in range(3):
        new_expense(
            client,
            who,
            url,
            vendor,
            description=f"Gasto {index}",
            amount=f"{200 + index}.25",
        )
    return expenses_body(
        fmt,
        columns=["amount", "status", "description", "incurred_on"],
        group_by="status",
        filters={"vendor_id": vendor, "status": ["unpaid", "overdue"]},
    )


def cells(data: bytes) -> list[list[tuple[str, Any]]]:
    sheet = load_workbook(io.BytesIO(data)).active
    assert sheet is not None
    return [
        [(type(cell.value).__name__, cell.value) for cell in row]
        for row in sheet.iter_rows()
    ]


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
@pytest.mark.parametrize("dataset", ["students", "class_sessions", "expenses"])
def test_a_saved_setup_exports_what_the_same_body_by_hand_does(
    module_seeded_url, client, level, first_day, dataset, fmt
):
    url = module_seeded_url
    who = actor(url, client, "finance_staff", reauth=True)
    body = bodies(url, client, who, dataset, fmt, level=level, first_day=first_day)
    setup = saved(client, who, unique("Replay"), body)
    assert setup["parameters"] == body

    from_setup = requested(client, who, setup["parameters"])
    by_hand = requested(client, who, body)
    run_job(url)

    rows = []
    for job in (from_setup, by_hand):
        row = job_row(url, job["id"])
        assert row.status == "ready", row.error_code
        rows.append(row)
    assert rows[0].parameters == rows[1].parameters
    assert rows[0].row_count == rows[1].row_count > 0
    first, second = (file_of(url, job["id"]) for job in (from_setup, by_hand))
    assert first is not None and second is not None
    if fmt == "csv":
        assert first == second
    else:
        assert cells(first) == cells(second)
    # The file the runner downloads is the same one.
    assert download(client, who, from_setup["id"]).content == first
