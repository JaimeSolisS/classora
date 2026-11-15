"""Helpers for the student-login and consent tests (#35).

- :func:`adult_profile_for` / :func:`minor_profile_for`: a committed student
  profile linked to a login (the ``student_login`` factory helper), so an
  account holding ``student`` passes the gate (adult) or needs a consent
  (minor). Used by the #29/#169/#31/#170 tests that activate a student (H5).
- HTTP helpers for ``PUT …/account`` and the three consent routes (the cookie,
  CSRF token and ``Origin`` sent by hand, as ``catalog_support`` does).
"""

import uuid
from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.modules.families.models import Guardian, Student
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow
from tests.integration.student_support import STUDENTS

MINOR_BIRTHDATE = date(2014, 3, 1)


def adult_profile_for(url: str, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    """A committed adult student profile linked to ``user_id``; its id."""

    async def _create(session: Any) -> uuid.UUID:
        student = await persist_student(
            session, birthdate=overrides.pop("birthdate", ADULT_BIRTHDATE), **overrides
        )
        await student_login(session, student, user_id)
        return student.id

    return in_uow(url, _create)


def minor_profile_for(
    url: str,
    user_id: uuid.UUID,
    *,
    consenting: bool = False,
    recorded_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """A committed minor's profile linked to ``user_id``; with ``consenting``
    also a linked active guardian with a ``paper`` login consent (recorded by
    ``recorded_by``, default the student's own account)."""

    async def _create(session: Any) -> uuid.UUID:
        student: Student = await persist_student(session, birthdate=MINOR_BIRTHDATE)
        await student_login(session, student, user_id)
        if consenting:
            guardian: Guardian = await persist(session, GuardianFactory)
            await link_guardian(session, student, guardian)
            await login_consent(
                session, student, guardian, recorded_by=recorded_by or user_id
            )
        return student.id

    return in_uow(url, _create)


def account_path(student_id: Any) -> str:
    return f"{STUDENTS}/{student_id}/account"


def consents_path(student_id: Any) -> str:
    return f"{STUDENTS}/{student_id}/consents"


def revoke_path(student_id: Any, consent_id: Any) -> str:
    return f"{consents_path(student_id)}/{consent_id}/revoke"


def put(client: TestClient, cookie: SetCookie, path: str, body: Any) -> Response:
    client.cookies.clear()
    return client.put(path, json=body, headers=signed_headers(cookie))


def set_account(
    client: TestClient, cookie: SetCookie, student: dict[str, Any], user_id: Any
) -> Response:
    """``PUT …/account`` at the student's current version."""
    return put(
        client,
        cookie,
        account_path(student["id"]),
        {
            "user_id": None if user_id is None else str(user_id),
            "version": student["version"],
        },
    )


def consents_of(client: TestClient, cookie: SetCookie, student_id: Any) -> Response:
    return get(client, cookie, consents_path(student_id))


def give(
    client: TestClient,
    cookie: SetCookie,
    student_id: Any,
    guardian_id: Any,
    *,
    scope: str = "student_login",
    method: str = "paper",
) -> Response:
    return post(
        client,
        cookie,
        consents_path(student_id),
        {"scope": scope, "guardian_id": str(guardian_id), "method": method},
    )


def given(
    client: TestClient, cookie: SetCookie, student_id: Any, guardian_id: Any, **kw: Any
) -> dict[str, Any]:
    """A consent that must be recorded; returns the record."""
    response = give(client, cookie, student_id, guardian_id, **kw)
    assert response.status_code == 201, response.text
    record: dict[str, Any] = response.json()
    return record


def revoke(
    client: TestClient, cookie: SetCookie, student_id: Any, consent_id: Any
) -> Response:
    client.cookies.clear()
    return client.post(
        revoke_path(student_id, consent_id), headers=signed_headers(cookie)
    )


def user_state(url: str, user_id: Any) -> str:
    [row] = run_sql(url, "SELECT state FROM users WHERE id = :id", {"id": user_id})
    return str(row.state)


def consent_rows(url: str, student_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, guardian_id, scope, method, revoked_at, revoked_by_user_id,"
        " revocation_cause FROM consent_records WHERE student_id = :id"
        " ORDER BY granted_at, id",
        {"id": student_id},
    )


def consent_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM consent_records")
    return int(row.n)
