"""Concurrent progress report writes (#83 D6), with two connections (``race``
from ``test_assignment_offer_concurrency``): transaction A writes and keeps
its transaction open for a moment; B starts once A's change is flushed, so
B waits on the report's row lock (or on the partial unique index) and then
sees A's change.

- approve vs approve (one version): one ``ok``, one ``version_conflict``;
- approve vs request changes, approve vs retract (both orders): exactly one
  decision wins, the other gets ``version_conflict``;
- the author's ``PATCH`` vs submit (one version): one wins;
- two creates for one weekly period: one ``ok``, one
  ``progress_report_exists`` (never a ``500``);
- a resubmit vs a reviewer's approve of the version before it: the
  reviewer never approves content they didn't see (``version_conflict``).

Each case runs 5 times, on new reports.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ROLE_PERMISSIONS,
    TUTOR,
)
from app.modules.scheduling.models import ClassSession
from app.workflows import progress_reports as workflow
from app.workflows.progress_report_schemas import (
    ProgressReportCreate,
    ProgressReportReason,
    ProgressReportUpdate,
    ProgressReportView,
)
from tests.conftest import frozen_clock
from tests.factories.identity import persist_user
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.conftest import run_sql_async
from tests.integration.test_assignment_offer_concurrency import (
    REPEATS,
    _in_uow,
    race,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
MONDAY = date(2031, 3, 3)

Change = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


def _principal(user_id: uuid.UUID, role: str) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


def _body(student: uuid.UUID, kind: str = "on_demand") -> ProgressReportCreate:
    return ProgressReportCreate.model_validate(
        {
            "student_id": str(student),
            "kind": kind,
            "period_start": MONDAY.isoformat(),
            "period_end": (MONDAY + timedelta(days=6)).isoformat(),
            "summary": "Avanza bien",
        }
    )


async def _setup(url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A tutor teaching a new student and a coordinator: ``(tutor id,
    student id, coordinator id)``."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, ...]:
        tutor = await persist_user(session, roles=(TUTOR,))
        coordinator = await persist_user(session, roles=(ACADEMIC_COORDINATOR,))
        made: ClassSession = await persist_class_session(
            session,
            state="confirmed",
            tutor_user_id=tutor.id,
            starts_at=NOW - timedelta(minutes=30),
            ends_at=NOW + timedelta(minutes=30),
            scheduled_minutes=60,
        )
        student = (await add_participant(session, made)).student_id
        return tutor.id, student, coordinator.id

    tutor, student, coordinator = await _in_uow(url, _make)
    return tutor, student, coordinator


async def _report(
    url: str, *, submitted: bool = True
) -> tuple[ProgressReportView, uuid.UUID, uuid.UUID]:
    """A report (``submitted``: version 2): ``(view, tutor id, coordinator
    id)``."""
    tutor, student, coordinator = await _setup(url)

    async def _make(session: AsyncSession) -> ProgressReportView:
        view = await workflow.create_report(
            session, principal=_principal(tutor, TUTOR), body=_body(student)
        )
        if submitted:
            view = await workflow.submit_report(
                session,
                principal=_principal(tutor, TUTOR),
                report_id=view.id,
                version=view.version,
            )
        return view

    return await _in_uow(url, _make), tutor, coordinator


def _approve(report_id: uuid.UUID, coordinator: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.approve_report(
            db,
            principal=_principal(coordinator, ACADEMIC_COORDINATOR),
            report_id=report_id,
            version=version,
        )

    return _run


def _decide(
    action: str, report_id: uuid.UUID, coordinator: uuid.UUID, version: int
) -> Change:
    run = (
        workflow.request_changes
        if action == "request_changes"
        else (workflow.retract_report)
    )

    async def _run(db: AsyncSession) -> None:
        await run(
            db,
            principal=_principal(coordinator, ACADEMIC_COORDINATOR),
            report_id=report_id,
            body=ProgressReportReason(version=version, reason="Revisar"),
        )

    return _run


def _submit(report_id: uuid.UUID, tutor: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.submit_report(
            db, principal=_principal(tutor, TUTOR), report_id=report_id, version=version
        )

    return _run


def _patch(report_id: uuid.UUID, tutor: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.update_report(
            db,
            principal=_principal(tutor, TUTOR),
            report_id=report_id,
            body=ProgressReportUpdate(version=version, summary="Cambiado"),
        )

    return _run


async def _row(url: str, report_id: uuid.UUID) -> tuple[str, int, str | None]:
    [row] = await run_sql_async(
        url,
        "SELECT status, version, summary FROM progress_reports WHERE id = :id",
        {"id": report_id},
    )
    return str(row.status), int(row.version), row.summary


async def _request_status(url: str, report_id: uuid.UUID) -> str:
    [row] = await run_sql_async(
        url,
        "SELECT status FROM approval_requests"
        " WHERE request_type = 'academics.progress_report' AND subject_id = :id",
        {"id": report_id},
    )
    return str(row.status)


async def test_two_approvals_with_one_version(seeded_url):
    for _ in range(REPEATS):
        view, _, coordinator = await _report(seeded_url)

        outcomes = await race(
            seeded_url,
            _approve(view.id, coordinator, 2),
            _approve(view.id, coordinator, 2),
        )

        assert outcomes == ["ok", "version_conflict"]
        assert (await _row(seeded_url, view.id))[:2] == ("approved_for_family", 3)
        assert await _request_status(seeded_url, view.id) == "approved"


@pytest.mark.parametrize("other", ["request_changes", "retract"])
@pytest.mark.parametrize("approve_first", [True, False])
async def test_an_approval_racing_another_decision(seeded_url, other, approve_first):
    for _ in range(REPEATS):
        view, _, coordinator = await _report(seeded_url)
        approve = _approve(view.id, coordinator, 2)
        decide = _decide(other, view.id, coordinator, 2)
        changes = (approve, decide) if approve_first else (decide, approve)

        outcomes = await race(seeded_url, *changes)

        assert outcomes == ["ok", "version_conflict"]
        status, version, _ = await _row(seeded_url, view.id)
        winner = (
            "approved_for_family"
            if approve_first
            else ("changes_requested" if other == "request_changes" else "retracted")
        )
        assert (status, version) == (winner, 3)
        expected_request = {
            "approved_for_family": "approved",
            "changes_requested": "changes_requested",
            "retracted": "rejected",
        }[winner]
        assert await _request_status(seeded_url, view.id) == expected_request


@pytest.mark.parametrize("patch_first", [True, False])
async def test_an_edit_racing_a_submit(seeded_url, patch_first):
    for _ in range(REPEATS):
        view, tutor, _ = await _report(seeded_url, submitted=False)
        edit, submit = _patch(view.id, tutor, 1), _submit(view.id, tutor, 1)
        changes = (edit, submit) if patch_first else (submit, edit)

        outcomes = await race(seeded_url, *changes)

        assert outcomes == ["ok", "version_conflict"]
        status, version, summary = await _row(seeded_url, view.id)
        assert version == 2
        assert (status, summary) == (
            ("draft", "Cambiado") if patch_first else ("submitted", "Avanza bien")
        )


def _create_weekly(tutor: uuid.UUID, student: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.create_report(
            db, principal=_principal(tutor, TUTOR), body=_body(student, kind="weekly")
        )

    return _run


def _send_back(report_id: uuid.UUID, coordinator: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.request_changes(
            db,
            principal=_principal(coordinator, ACADEMIC_COORDINATOR),
            report_id=report_id,
            body=ProgressReportReason(version=version, reason="Más detalle"),
        )

    return _run


def _edit_and_resubmit(report_id: uuid.UUID, tutor: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.update_report(
            db,
            principal=_principal(tutor, TUTOR),
            report_id=report_id,
            body=ProgressReportUpdate(version=version, summary="Con detalle"),
        )
        await workflow.submit_report(
            db,
            principal=_principal(tutor, TUTOR),
            report_id=report_id,
            version=version + 1,
        )

    return _run


async def test_two_creates_for_one_weekly_period(seeded_url):
    for _ in range(REPEATS):
        tutor, student, _ = await _setup(seeded_url)

        outcomes = await race(
            seeded_url, _create_weekly(tutor, student), _create_weekly(tutor, student)
        )

        assert outcomes == ["ok", "progress_report_exists"]
        rows = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM progress_reports WHERE student_id = :s",
            {"s": student},
        )
        assert rows[0].n == 1


async def test_a_resubmit_racing_a_stale_approval(seeded_url):
    """The reviewer read version 3 (sent back); the author edits (4) and
    resubmits (5) while the reviewer approves version 3: never approved."""
    for _ in range(REPEATS):
        view, tutor, coordinator = await _report(seeded_url)
        await _in_uow(seeded_url, _send_back(view.id, coordinator, 2))

        outcomes = await race(
            seeded_url,
            _edit_and_resubmit(view.id, tutor, 3),
            _approve(view.id, coordinator, 3),
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _row(seeded_url, view.id) == ("submitted", 5, "Con detalle")
        assert await _request_status(seeded_url, view.id) == "pending"
