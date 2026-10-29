"""Shared helpers of the enrollment charge tests (#92 G2-G8).

Groups and cohorts are created and enrolled **through the API** (so the M4
hooks run) on the series timeline (:mod:`tests.integration.series_support`):
each test takes its own decade, "now" is a Sunday 12:00 (Mexico City) and a
Monday/Wednesday 16:00 schedule starts the next day.

**Isolation.** The sweepers read every candidate of the module database.
Each test sets the billing floor inside its own decade (:func:`set_floor`),
and decades only grow within a run, so earlier tests' periods and sessions
(earlier decades) are below the floor and never candidates.
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.workflows import enrollment_charges, session_charges
from tests.factories.billing import persist_service_price
from tests.factories.scheduling import persist_tutor_user
from tests.integration import cohort_support, group_support
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import Timeline
from tests.integration.session_charge_support import add_family, set_floor
from tests.integration.test_class_session_create import CREATED

__all__ = [
    "add_family",
    "set_floor",
]

ZONE = "America/Mexico_City"


def price(
    url: str,
    subject: uuid.UUID,
    amount: str | None,
    *,
    class_type: str,
    pricing_model: str = "full_course",
    effective_from: date = date(2000, 1, 1),
    **key: Any,
) -> uuid.UUID:
    """A catalog item with one version; returns the item id."""

    async def _add(session: AsyncSession) -> uuid.UUID:
        return (
            await persist_service_price(
                session,
                subject_id=subject,
                class_type=class_type,
                pricing_model=pricing_model,
                versions=[(effective_from, amount)],
                **key,
            )
        ).id

    return in_uow(url, _add)


def cohort_fee(url: str, subject: uuid.UUID, amount: str, **key: Any) -> uuid.UUID:
    return price(url, subject, amount, class_type="course", **key)


def group_fee(url: str, subject: uuid.UUID, amount: str, **key: Any) -> uuid.UUID:
    return price(url, subject, amount, class_type="group", **key)


def group_session_price(
    url: str, subject: uuid.UUID, amount: str, **key: Any
) -> uuid.UUID:
    return price(
        url, subject, amount, class_type="group", pricing_model="per_session", **key
    )


def run_enrollments(url: str) -> enrollment_charges.EnrollmentChargeRunResult:
    return asyncio.run(
        enrollment_charges.run_post_enrollment_charges(settings_for(url))
    )


def run_sessions(url: str) -> session_charges.SessionChargeRunResult:
    return asyncio.run(session_charges.run_post_session_charges(settings_for(url)))


def make_cohort(
    client: TestClient,
    cookie: Any,
    subject: uuid.UUID,
    starts_on: date,
    *,
    session_count: int = 10,
    weekdays: list[int] | None = None,
) -> dict[str, Any]:
    template = cohort_support.new_template(
        client, cookie, [subject], session_count=session_count
    )
    response = cohort_support.create_cohort(
        client,
        cookie,
        cohort_support.cohort_body(
            template=template["id"],
            subject=subject,
            starts_on=starts_on,
            weekdays=weekdays,
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def enroll_cohort(
    client: TestClient, cookie: Any, cohort_id: Any, student: uuid.UUID
) -> dict[str, Any]:
    response = cohort_support.enroll(client, cookie, cohort_id, student)
    assert code_of(response) == CREATED, response.text
    body: dict[str, Any] = response.json()
    return body


def withdraw_cohort(
    url: str, client: TestClient, cookie: Any, cohort_id: Any, enrollment_id: Any
) -> dict[str, Any]:
    [row] = run_sql(
        url,
        "SELECT version FROM cohort_enrollments WHERE id = :id",
        {"id": str(enrollment_id)},
    )
    response = cohort_support.withdraw(
        client, cookie, cohort_id, enrollment_id, row.version
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def make_group(
    client: TestClient,
    cookie: Any,
    subject: uuid.UUID,
    starts_on: date,
    students: Sequence[uuid.UUID] = (),
    *,
    count: int | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    response = group_support.create_group(
        client,
        cookie,
        group_support.group_body(
            subject=subject,
            starts_on=starts_on,
            students=list(students),
            count=count,
            until=until,
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def enroll_group(
    client: TestClient, cookie: Any, group_id: Any, student: uuid.UUID
) -> dict[str, Any]:
    response = group_support.enroll(client, cookie, group_id, student)
    assert code_of(response) == CREATED, response.text
    body: dict[str, Any] = response.json()
    return body


def withdraw_group(
    url: str, client: TestClient, cookie: Any, group_id: Any, enrollment_id: Any
) -> dict[str, Any]:
    [row] = run_sql(
        url,
        "SELECT version FROM group_enrollments WHERE id = :id",
        {"id": str(enrollment_id)},
    )
    response = group_support.withdraw(
        client, cookie, group_id, enrollment_id, row.version
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def group_enrollment_id(url: str, group_id: Any, student: uuid.UUID) -> uuid.UUID:
    rows = run_sql(
        url,
        "SELECT id FROM group_enrollments WHERE group_id = :g AND student_id = :s"
        " ORDER BY enrolled_at DESC, id DESC",
        {"g": str(group_id), "s": student},
    )
    found: uuid.UUID = rows[0].id
    return found


def sessions_of(url: str, *, group_id: Any = None, cohort_id: Any = None) -> list[Any]:
    column, value = ("group_id", group_id) if group_id else ("cohort_id", cohort_id)
    return run_sql(
        url,
        f"SELECT id, state, starts_at FROM class_sessions WHERE {column} = :o"  # noqa: S608
        " ORDER BY starts_at",
        {"o": str(value)},
    )


def confirm_sessions(url: str, session_ids: Sequence[Any]) -> None:
    """Make these sessions ``confirmed`` with a fresh tutor (raw SQL, as a
    fixture may)."""
    tutor = in_uow(url, persist_tutor_user)
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'confirmed', tutor_user_id = :t,"
        " first_confirmed_at = starts_at - interval '7 days'"
        " WHERE id = ANY(:ids)",
        {"t": tutor, "ids": [uuid.UUID(str(i)) for i in session_ids]},
    )


def cancel_session(url: str, session_id: Any) -> None:
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
        " cancellation_reason = 'Cancelada por el centro', tutor_user_id = NULL"
        " WHERE id = :id",
        {"id": str(session_id)},
    )


def skip_session(url: str, session_id: Any) -> None:
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'skipped_closure' WHERE id = :id",
        {"id": str(session_id)},
    )


def fees(url: str, *, cohort_enrollment: Any = None, group_enrollment: Any = None):
    if cohort_enrollment is not None:
        where, value = "cohort_enrollment_id", cohort_enrollment
    else:
        where, value = "group_enrollment_id", group_enrollment
    return run_sql(
        url,
        f"SELECT * FROM charges WHERE {where} = :id ORDER BY posted_at",  # noqa: S608
        {"id": str(value)},
    )


def charges_of(url: str, student: uuid.UUID, kind: str | None = None) -> list[Any]:
    sql = "SELECT * FROM charges WHERE student_id = :s"
    params: dict[str, Any] = {"s": student}
    if kind is not None:
        sql += " AND kind = :k"
        params["k"] = kind
    return run_sql(url, sql + " ORDER BY service_date, posted_at", params)


def adjustments_of(url: str, charge_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charge_adjustments WHERE charge_id = :c ORDER BY posted_at, id",
        {"c": str(charge_id)},
    )


def net_of(url: str, charge_id: Any) -> str:
    [row] = run_sql(
        url,
        "SELECT c.amount + coalesce((SELECT sum(a.amount) FROM charge_adjustments a"
        " WHERE a.charge_id = c.id), 0) AS net FROM charges c WHERE c.id = :c",
        {"c": str(charge_id)},
    )
    return f"{row.net:.2f}"


def floor_of(tl: Timeline) -> date:
    """A floor inside the test's decade, before its schedule."""
    return tl.now.date() - timedelta(days=1)
