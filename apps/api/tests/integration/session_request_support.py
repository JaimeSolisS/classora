"""Helpers for the session request tests (#59) with real logins through the
real app.

The clock is frozen at :data:`NOW` (Monday 2030-12-30 12:00 in the default
center zone, America/Mexico_City, UTC-6, no DST) for the whole module by the
``frozen`` fixtures of the test modules, so the #48 fixed week of Monday
2031-01-06 is inside the slot range (today .. +60 days) and beyond the 24 h
booking notice. Default operating hours: Monday-Friday 09:00-20:00, Saturday
09:00-14:00, Sunday closed.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.families.models import Guardian, Student
from app.modules.scheduling.models import SessionRequest
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.integration.auth_support import CreatedUser, SetCookie, create_user
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow
from tests.integration.test_class_session_create import MONDAY, MX, at

SLOTS = "/api/v1/available-slots"
REQUESTS = "/api/v1/session-requests"
NOW = datetime(2030, 12, 30, 12, 0, tzinfo=MX)
MINOR_BIRTHDATE = date(2016, 5, 1)
TUESDAY = MONDAY + timedelta(days=1)
MONDAY_WINDOW = [(1, time(10, 0), time(12, 0))]
"""A tutor available Mondays 10:00-12:00: the 60-minute slots are 10:00,
10:30 and 11:00."""


@dataclass(frozen=True)
class Family:
    """A guardian account with an active profile linked to one student."""

    user: CreatedUser
    guardian_id: uuid.UUID
    student_id: uuid.UUID


def guardian_with_child(
    url: str,
    *,
    adult: bool = False,
    consent: bool = False,
    student_active: bool = True,
    **user: Any,
) -> Family:
    """A ``guardian`` user, their active profile and one linked child (a
    minor unless ``adult``; with ``consent``, an active
    ``guardian_academic_access`` consent naming the profile)."""
    account = create_user(url, roles=("guardian",), **user)

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        student: Student = await persist_student(
            session,
            contacts=0,
            birthdate=ADULT_BIRTHDATE if adult else MINOR_BIRTHDATE,
            is_active=student_active,
        )
        guardian: Guardian = await persist(session, GuardianFactory, user_id=account.id)
        await link_guardian(session, student, guardian)
        if consent:
            await login_consent(
                session,
                student,
                guardian,
                recorded_by=account.id,
                scope="guardian_academic_access",
            )
        return guardian.id, student.id

    guardian_id, student_id = in_uow(url, _make)
    return Family(user=account, guardian_id=guardian_id, student_id=student_id)


def link_child(url: str, family: Family, *, adult: bool = False) -> uuid.UUID:
    """Another student linked to the family's guardian profile."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        student: Student = await persist_student(
            session, contacts=0, birthdate=ADULT_BIRTHDATE if adult else MINOR_BIRTHDATE
        )
        guardian = await session.get(Guardian, family.guardian_id)
        assert guardian is not None
        await link_guardian(session, student, guardian)
        return student.id

    return in_uow(url, _make)


def student_account(url: str, **user: Any) -> tuple[CreatedUser, uuid.UUID]:
    """A ``student`` user with their own (adult) profile, so the login gate
    lets them in."""
    account = create_user(url, roles=("student",), **user)

    async def _make(session: AsyncSession) -> uuid.UUID:
        student: Student = await persist_student(
            session, contacts=0, birthdate=ADULT_BIRTHDATE
        )
        await student_login(session, student, account.id)
        return student.id

    return account, in_uow(url, _make)


def deactivate_guardian(url: str, guardian_id: uuid.UUID) -> None:
    async def _change(session: AsyncSession) -> None:
        guardian = await session.get(Guardian, guardian_id)
        assert guardian is not None
        guardian.is_active = False
        await session.flush()

    in_uow(url, _change)


def unlink(url: str, family: Family) -> None:
    from sqlalchemy import select

    from app.modules.families.models import StudentGuardian

    async def _delete(session: AsyncSession) -> None:
        link = await session.scalar(
            select(StudentGuardian).where(
                StudentGuardian.student_id == family.student_id,
                StudentGuardian.guardian_id == family.guardian_id,
            )
        )
        assert link is not None
        await session.delete(link)
        await session.flush()

    in_uow(url, _delete)


def revoke_academic_consent(url: str, family: Family) -> None:
    from sqlalchemy import select

    from app.modules.families.models import ConsentRecord

    async def _revoke(session: AsyncSession) -> None:
        rows = await session.scalars(
            select(ConsentRecord).where(
                ConsentRecord.student_id == family.student_id,
                ConsentRecord.guardian_id == family.guardian_id,
                ConsentRecord.scope == "guardian_academic_access",
                ConsentRecord.revoked_at.is_(None),
            )
        )
        for row in rows:
            row.revoked_at = NOW
            row.revoked_by_user_id = family.user.id
            row.revocation_cause = "revoked"
        await session.flush()

    in_uow(url, _revoke)


def give_academic_consent(url: str, family: Family) -> None:
    async def _give(session: AsyncSession) -> None:
        student = await session.get(Student, family.student_id)
        guardian = await session.get(Guardian, family.guardian_id)
        assert student is not None and guardian is not None
        await login_consent(
            session,
            student,
            guardian,
            recorded_by=family.user.id,
            scope="guardian_academic_access",
        )

    in_uow(url, _give)


# --- Requests ------------------------------------------------------------------------


def slots_params(
    student: uuid.UUID,
    subject: uuid.UUID,
    first: date = MONDAY,
    last: date | None = None,
    duration: int | str = 60,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "student_id": str(student),
        "subject_id": str(subject),
        "duration_minutes": duration,
        "from": first.isoformat(),
        "to": (last or first).isoformat(),
        **extra,
    }


def get_slots(
    client: TestClient, cookie: SetCookie, params: dict[str, Any]
) -> Response:
    return get(client, cookie, SLOTS, **params)


def slot_starts(response: Response, day: date = MONDAY) -> list[str]:
    """The local ``HH:MM`` starts of ``day``'s slots in a ``200``."""
    assert response.status_code == 200, response.text
    for item in response.json()["days"]:
        if item["date"] == day.isoformat():
            return [
                datetime.fromisoformat(slot["starts_at"])
                .astimezone(MX)
                .strftime("%H:%M")
                for slot in item["slots"]
            ]
    raise AssertionError(f"{day} not in the response")


def request_body(
    student: uuid.UUID,
    subject: uuid.UUID,
    starts_at: datetime | str | None = None,
    duration: int = 60,
    **extra: Any,
) -> dict[str, Any]:
    start = at(MONDAY, "10:00") if starts_at is None else starts_at
    return {
        "student_id": str(student),
        "subject_id": str(subject),
        "starts_at": start if isinstance(start, str) else start.isoformat(),
        "duration_minutes": duration,
        **extra,
    }


def create_request(
    client: TestClient, cookie: SetCookie, body: dict[str, Any]
) -> Response:
    return post(client, cookie, REQUESTS, body)


def created(response: Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def withdraw(
    client: TestClient, cookie: SetCookie, request_id: Any, version: Any
) -> Response:
    return post(
        client, cookie, f"{REQUESTS}/{request_id}/withdraw", {"version": version}
    )


def request_rows(url: str, student: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, status, resolved_at, version, note FROM session_requests"
        " WHERE student_id = :s ORDER BY created_at",
        {"s": student},
    )


def approval_of(url: str, request_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, status, request_type, requester_user_id, resolved_by_user_id"
        " FROM approval_requests WHERE subject_id = :s",
        {"s": request_id},
    )
    return row


async def insert_request_row(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    requested_by: uuid.UUID,
    starts_at: datetime,
    minutes: int = 60,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """A ``pending`` request and its inbox item, written directly (no slot
    check and no rate limit; #60 tests). ``created_at`` defaults to the
    database's now."""
    row = SessionRequest(
        id=uuid.uuid7(),
        student_id=student_id,
        subject_id=subject_id,
        requested_by_user_id=requested_by,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
        duration_minutes=minutes,
        status="pending",
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    await session.flush()
    await administration.create_approval_request(
        session,
        request_type="scheduling.session_request",
        subject_id=row.id,
        requester_user_id=requested_by,
    )
    return row.id


def insert_request(
    url: str,
    *,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    requested_by: uuid.UUID,
    starts_at: datetime,
    minutes: int = 60,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """:func:`insert_request_row` in its own committed Unit of Work."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        return await insert_request_row(
            session,
            student_id=student_id,
            subject_id=subject_id,
            requested_by=requested_by,
            starts_at=starts_at,
            minutes=minutes,
            created_at=created_at,
        )

    return in_uow(url, _make, actor_id=requested_by)
