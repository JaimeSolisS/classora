"""The three datasets' rows equal their pages' items (#124 A3, D5), the
45-day windows of the calendar read and the row limit."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import message
from app.core.money import Money
from app.modules.identity import service as identity
from app.modules.reporting.exports import datasets
from app.modules.reporting.exports.datasets import (
    CLASS_SESSIONS_DATASET,
    EXPENSES_DATASET,
    STUDENTS_DATASET,
    ClassSessionsExportFilters,
    ExpensesExportFilters,
    StudentsExportFilters,
)
from app.modules.reporting.exports.registry import ExportDataset, RowLimitExceeded
from app.modules.scheduling import calendar_read
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
    center_today,
    new_expense,
    read,
    requested,
    run_job,
    unique,
)
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


def rows_of(
    url: str, dataset: ExportDataset, who: Actor, filters: Any, *, limit: int = 10_000
) -> list[dict[str, Any]]:
    """The dataset's rows for ``who`` in one committed Unit of Work."""

    async def call(session: AsyncSession) -> list[dict[str, Any]]:
        principal = await identity.load_principal(session, who.user.id)
        assert principal is not None
        return await dataset.rows(
            session, principal, filters, limit=limit, language="es"
        )

    return in_uow(url, call)


def all_items(client: TestClient, who: Actor, path: str, **params: Any) -> list:
    response = read(client, who, path, limit=100, **params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("total", 0) <= 100
    return list(body["items"])


# --- students -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def level(module_seeded_url: str) -> tuple[str, str]:
    """A level of its own with five students: one inactive, one with a
    grade."""
    family = unique("Est")

    async def make(session: AsyncSession) -> tuple[str, str]:
        subject = await persist_subject(session)
        level_id = subject.education_level_id
        for given, extra in (
            ("Ana", {}),
            ("Beto", {"school_grade": "3º"}),
            ("Carla", {"is_active": False}),
            ("Dani", {"birthdate": date(1990, 1, 1)}),
            ("Eva", {}),
        ):
            await persist_student(
                session,
                given_names=given,
                family_names=family,
                education_level_id=level_id,
                **extra,
            )
        return str(level_id), family

    return in_uow(module_seeded_url, make)


def flat_student(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "family_names": item["family_names"],
        "given_names": item["given_names"],
        "is_active": item["is_active"],
        "is_minor": item["is_minor"],
        "school_grade": item["school_grade"],
        "education_level": (item["education_level"] or {}).get("name"),
    }


@pytest.mark.parametrize("role", ["owner", "finance_staff"])
@pytest.mark.parametrize("status", ["active", "all"])
def test_student_rows_equal_the_list_page(
    module_seeded_url, client, level, monkeypatch, role, status
):
    level_id, _ = level
    who = actor(module_seeded_url, client, role)
    # Pages of two, so the export reads three pages.
    monkeypatch.setattr(datasets, "PAGE_SIZE", 2)

    rows = rows_of(
        module_seeded_url,
        STUDENTS_DATASET,
        who,
        StudentsExportFilters(status=status, education_level_id=uuid.UUID(level_id)),
    )

    items = all_items(
        client,
        who,
        "/api/v1/students",
        status=status,
        education_level_id=level_id,
    )
    assert rows == [flat_student(item) for item in items]
    assert len(rows) == (5 if status == "all" else 4)
    assert {r["is_minor"] for r in rows} == {True, False}


def test_too_many_students_raise_the_row_limit(module_seeded_url, client, level):
    level_id, _ = level
    who = actor(module_seeded_url, client, "owner")

    with pytest.raises(RowLimitExceeded):
        rows_of(
            module_seeded_url,
            STUDENTS_DATASET,
            who,
            StudentsExportFilters(education_level_id=uuid.UUID(level_id)),
            limit=3,
        )


def test_a_students_export_end_to_end(module_seeded_url, client, level):
    from tests.integration.export_support import csv_table, file_of

    level_id, family = level
    who = actor(module_seeded_url, client, "academic_coordinator", reauth=True)

    job = requested(
        client,
        who,
        {
            "dataset": "students",
            "format": "csv",
            "columns": ["given_names", "is_active", "school_grade"],
            "group_by": "school_grade",
            "filters": {"status": "all", "education_level_id": level_id},
        },
    )
    run_job(module_seeded_url)

    assert csv_table(file_of(module_seeded_url, job["id"]) or b"") == [
        ["Nombres", "Activo", "Grado escolar"],
        ["Beto", "Sí", "3º"],
        ["Ana", "Sí", ""],
        ["Carla", "No", ""],
        ["Dani", "Sí", ""],
        ["Eva", "Sí", ""],
    ]
    assert family


# --- class_sessions -------------------------------------------------------------------


@pytest.fixture(scope="module")
def year() -> int:
    return unique_year()


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE)


def make_sessions(url: str, specs: list[dict[str, Any]]) -> list[str]:
    async def make(session: AsyncSession) -> list[str]:
        made = []
        for spec in specs:
            students = spec.pop("students", 0)
            row = await persist_class_session(session, **spec)
            for _ in range(students):
                await add_participant(session, row)
            made.append(str(row.id))
        return made

    return in_uow(url, make)


def flat_session(item: dict[str, Any]) -> dict[str, Any]:
    starts = datetime.fromisoformat(item["starts_at"]).astimezone(ZONE)
    ends = datetime.fromisoformat(item["ends_at"]).astimezone(ZONE)
    return {
        "date": starts.date(),
        "start_time": starts.time(),
        "end_time": ends.time(),
        "duration_minutes": int((ends - starts).total_seconds() // 60),
        "kind": item["kind"],
        "state": item["state"],
        "subject": item["subject"]["name"],
        "education_level": item["subject"]["education_level_name"],
        "tutor": (item["tutor"] or {}).get("display_name"),
        "classroom": (item["classroom"] or {}).get("name"),
        "students": [p["display_name"] for p in item["participants"]],
        "series": item["series_id"] is not None,
    }


def test_session_rows_equal_the_calendar(module_seeded_url, client, year):
    first = date(year, 3, 2)
    make_sessions(
        module_seeded_url,
        [
            {
                "starts_at": local(first, 16),
                "ends_at": local(first, 17, 30),
                "students": 2,
            },
            {
                "starts_at": local(first + timedelta(days=3), 9),
                "ends_at": local(first + timedelta(days=3), 10),
                "classroom": True,
                "students": 1,
            },
            {
                "starts_at": local(first + timedelta(days=5), 9),
                "ends_at": local(first + timedelta(days=5), 10),
                "state": "unassigned",
            },
            {
                "starts_at": local(first + timedelta(days=6), 9),
                "ends_at": local(first + timedelta(days=6), 10),
                "state": "cancelled",
            },
        ],
    )
    who = actor(module_seeded_url, client, "finance_staff")
    last = first + timedelta(days=29)

    rows = rows_of(
        module_seeded_url,
        CLASS_SESSIONS_DATASET,
        who,
        ClassSessionsExportFilters.model_validate({"from": first, "to": last}),
    )

    start = local(first, 0).astimezone(UTC)
    end = local(last + timedelta(days=1), 0).astimezone(UTC)
    items = read(
        client,
        who,
        "/api/v1/class-sessions",
        **{"from": start.isoformat(), "to": end.isoformat()},
    ).json()["items"]
    assert rows == [flat_session(item) for item in items]
    assert len(rows) == 3
    assert rows[0]["duration_minutes"] == 90
    assert len(rows[0]["students"]) == 2


def test_a_session_on_a_window_edge_is_exported_once(module_seeded_url, client, year):
    first = date(year + 1, 1, 5)
    edge = local(first, 0).astimezone(UTC) + datetime_window()
    [crossing] = make_sessions(
        module_seeded_url,
        [
            {
                "starts_at": edge - timedelta(minutes=30),
                "ends_at": edge + timedelta(minutes=30),
            }
        ],
    )
    who = actor(module_seeded_url, client, "owner")

    rows = rows_of(
        module_seeded_url,
        CLASS_SESSIONS_DATASET,
        who,
        ClassSessionsExportFilters.model_validate(
            {"from": first, "to": first + timedelta(days=99)}
        ),
    )

    assert len(rows) == 1
    assert crossing


def datetime_window() -> timedelta:
    return datasets.CALENDAR_WINDOW


def test_a_truncated_window_raises_the_row_limit(
    module_seeded_url, client, year, monkeypatch
):
    first = date(year + 2, 5, 4)
    make_sessions(
        module_seeded_url,
        [
            {"starts_at": local(first, 9), "ends_at": local(first, 10)},
            {"starts_at": local(first, 11), "ends_at": local(first, 12)},
        ],
    )
    who = actor(module_seeded_url, client, "owner")
    monkeypatch.setattr(calendar_read, "ITEM_LIMIT", 1)

    with pytest.raises(RowLimitExceeded):
        rows_of(
            module_seeded_url,
            CLASS_SESSIONS_DATASET,
            who,
            ClassSessionsExportFilters.model_validate({"from": first, "to": first}),
        )


def test_the_session_filters_reach_the_calendar(module_seeded_url, client, year):
    first = date(year + 3, 6, 1)
    make_sessions(
        module_seeded_url,
        [
            {"starts_at": local(first, 9), "ends_at": local(first, 10)},
            {
                "starts_at": local(first, 11),
                "ends_at": local(first, 12),
                "state": "cancelled",
            },
        ],
    )
    who = actor(module_seeded_url, client, "owner")

    def count(**filters: Any) -> int:
        return len(
            rows_of(
                module_seeded_url,
                CLASS_SESSIONS_DATASET,
                who,
                ClassSessionsExportFilters.model_validate(
                    {"from": first, "to": first, **filters}
                ),
            )
        )

    assert count() == 1
    assert count(include_cancelled=True) == 2
    assert count(kind="course") == 0


# --- expenses -------------------------------------------------------------------------


def money(value: str) -> Money:
    return Money(Decimal(value))


def flat_expense(item: dict[str, Any]) -> dict[str, Any]:
    category = item["category"]
    return {
        "incurred_on": date.fromisoformat(item["incurred_on"]),
        "due_on": date.fromisoformat(item["due_on"]),
        "category": (
            message("es", f"finance.categories.system.{category['system_key']}")
            if category["system_key"]
            else category["name"]
        ),
        "vendor": (item["vendor"] or {}).get("name"),
        "description": item["description"],
        "amount": money(item["amount"]),
        "adjusted_amount": money(item["adjusted_amount"]),
        "paid_amount": money(item["paid_amount"]),
        "open_amount": money(item["open_amount"]),
        "status": item["status"],
        "receipt_reference": item["receipt_reference"],
        "template": (item["template"] or {}).get("name"),
    }


def test_expense_rows_equal_the_list_page(module_seeded_url, client, monkeypatch):
    who = actor(module_seeded_url, client, "owner")
    vendor = new_vendor(module_seeded_url)
    for index in range(3):
        new_expense(
            client,
            who,
            module_seeded_url,
            vendor,
            description=f"Gasto {index}",
            amount=f"{100 + index}.50",
            receipt_reference=f"F-{index}" if index else None,
        )
    monkeypatch.setattr(datasets, "PAGE_SIZE", 2)

    rows = rows_of(
        module_seeded_url,
        EXPENSES_DATASET,
        who,
        ExpensesExportFilters(vendor_id=uuid.UUID(vendor)),
    )

    items = all_items(
        client, who, "/api/v1/finance/expenses", vendor_id=vendor, sort="-incurred_on"
    )
    assert rows == [flat_expense(item) for item in items]
    assert len(rows) == 3
    assert rows[0]["category"] == "Renta"


def test_expense_filters_reach_the_list(module_seeded_url, client):
    who = actor(module_seeded_url, client, "finance_staff")
    vendor = new_vendor(module_seeded_url)
    new_expense(client, who, module_seeded_url, vendor)
    today = center_today(module_seeded_url)
    yesterday = today - timedelta(days=1)

    def count(**filters: Any) -> int:
        return len(
            rows_of(
                module_seeded_url,
                EXPENSES_DATASET,
                who,
                ExpensesExportFilters(vendor_id=uuid.UUID(vendor), **filters),
            )
        )

    assert count() == 1
    assert count(status=["paid"]) == 0
    assert count(incurred_from=yesterday, incurred_to=yesterday) == 1
    assert count(incurred_from=today) == 0


def test_too_many_expenses_raise_the_row_limit(module_seeded_url, client):
    who = actor(module_seeded_url, client, "owner")
    vendor = new_vendor(module_seeded_url)
    for _ in range(2):
        new_expense(client, who, module_seeded_url, vendor)

    with pytest.raises(RowLimitExceeded):
        rows_of(
            module_seeded_url,
            EXPENSES_DATASET,
            who,
            ExpensesExportFilters(vendor_id=uuid.UUID(vendor)),
            limit=1,
        )
