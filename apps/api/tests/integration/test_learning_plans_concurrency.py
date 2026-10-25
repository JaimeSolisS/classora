"""Concurrent learning plan writes (#81 D4), with two connections (``race``
from ``test_assignment_offer_concurrency``): transaction A writes and keeps
its transaction open for a moment; B starts once A's change is flushed, so
B waits on a row lock (or on the partial unique index) and then sees A's
change.

- two activations of two drafts for one student and subject: one ``ok``,
  one ``active_plan_exists`` (the partial unique index; never a ``500``);
- a content ``PUT`` that deletes a milestone racing a ``PATCH`` of that
  milestone (both orders): serialized by the plan lock (``FOR UPDATE`` vs
  ``FOR SHARE``); the ``PATCH`` either sees the milestone or gets
  ``not_found``;
- two ``PATCH``es of one milestone with one version: one
  ``version_conflict``;
- a complete racing a ``PUT`` (both orders): the loser gets
  ``version_conflict``.

Each case runs 5 times, on new plans.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import ROLE_PERMISSIONS, TUTOR
from app.modules.scheduling.models import ClassSession
from app.workflows import learning_plans as workflow
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanMilestoneProgress,
    LearningPlanReplace,
    LearningPlanTransition,
    LearningPlanView,
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

Change = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


def _tutor(user_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({TUTOR}),
        permissions=frozenset(ROLE_PERMISSIONS[TUTOR]),
    )


def _body(subject_id: uuid.UUID) -> LearningPlanCreate:
    return LearningPlanCreate.model_validate(
        {
            "subject_id": str(subject_id),
            "title": "Plan",
            "objectives": [
                {"title": "Leer", "milestones": [{"title": "A"}, {"title": "B"}]}
            ],
        }
    )


async def _setup(url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A tutor teaching a new student: ``(tutor id, student id, subject
    id)``."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, ...]:
        tutor = await persist_user(session, roles=(TUTOR,))
        made: ClassSession = await persist_class_session(
            session,
            state="confirmed",
            tutor_user_id=tutor.id,
            starts_at=NOW - timedelta(minutes=30),
            ends_at=NOW + timedelta(minutes=30),
            scheduled_minutes=60,
        )
        student = (await add_participant(session, made)).student_id
        return tutor.id, student, made.subject_id

    tutor, student, subject = await _in_uow(url, _make)
    return tutor, student, subject


async def _plan(
    url: str, *, active: bool = False, setup: Any = None
) -> tuple[LearningPlanView, uuid.UUID]:
    """A draft (``active``: activated, version 2): ``(view, tutor id)``."""
    tutor, student, subject = setup or await _setup(url)

    async def _make(session: AsyncSession) -> LearningPlanView:
        view = await workflow.create_plan(
            session, principal=_tutor(tutor), student_id=student, body=_body(subject)
        )
        if active:
            view = await workflow.transition_plan(
                session,
                principal=_tutor(tutor),
                plan_id=view.id,
                body=LearningPlanTransition(version=1, action="activate"),
            )
        return view

    return await _in_uow(url, _make), tutor


def _transition(
    plan_id: uuid.UUID, tutor: uuid.UUID, version: int, action: str
) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.transition_plan(
            db,
            principal=_tutor(tutor),
            plan_id=plan_id,
            body=LearningPlanTransition.model_validate(
                {"version": version, "action": action}
            ),
        )

    return _run


def _progress(milestone_id: uuid.UUID, tutor: uuid.UUID, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.set_milestone_progress(
            db,
            principal=_tutor(tutor),
            milestone_id=milestone_id,
            body=LearningPlanMilestoneProgress(version=version, status="done"),
        )

    return _run


def _drop_first_milestone(view: LearningPlanView, tutor: uuid.UUID) -> Change:
    """A ``PUT`` that keeps only the second milestone."""
    [objective] = view.objectives
    kept = objective.milestones[1]

    async def _run(db: AsyncSession) -> None:
        await workflow.replace_plan(
            db,
            principal=_tutor(tutor),
            plan_id=view.id,
            body=LearningPlanReplace.model_validate(
                {
                    "version": view.version,
                    "title": view.title,
                    "starts_on": view.starts_on.isoformat(),
                    "objectives": [
                        {
                            "id": str(objective.id),
                            "title": objective.title,
                            "milestones": [{"id": str(kept.id), "title": kept.title}],
                        }
                    ],
                }
            ),
        )

    return _run


async def _plan_row(url: str, plan_id: uuid.UUID) -> tuple[str, int]:
    [row] = await run_sql_async(
        url,
        "SELECT status, version FROM learning_plans WHERE id = :id",
        {"id": plan_id},
    )
    return str(row.status), int(row.version)


async def _milestones(url: str, plan_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = await run_sql_async(
        url,
        "SELECT id, status FROM learning_plan_milestones WHERE plan_id = :id",
        {"id": plan_id},
    )
    return {row.id: str(row.status) for row in rows}


async def test_two_activations_for_one_subject(seeded_url):
    for _ in range(REPEATS):
        setup = await _setup(seeded_url)
        first, tutor = await _plan(seeded_url, setup=setup)
        second, _ = await _plan(seeded_url, setup=setup)

        outcomes = await race(
            seeded_url,
            _transition(first.id, tutor, 1, "activate"),
            _transition(second.id, tutor, 1, "activate"),
        )

        assert outcomes == ["ok", "active_plan_exists"]
        assert await _plan_row(seeded_url, first.id) == ("active", 2)
        assert await _plan_row(seeded_url, second.id) == ("draft", 1)


@pytest.mark.parametrize("put_first", [True, False])
async def test_a_put_racing_a_milestone_patch(seeded_url, put_first):
    for _ in range(REPEATS):
        view, tutor = await _plan(seeded_url, active=True)
        dropped = view.objectives[0].milestones[0]
        put, mark = _drop_first_milestone(view, tutor), _progress(dropped.id, tutor, 1)
        changes = (put, mark) if put_first else (mark, put)

        outcomes = await race(seeded_url, *changes)

        assert outcomes == (["ok", "not_found"] if put_first else ["ok", "ok"])
        remaining = await _milestones(seeded_url, view.id)
        assert dropped.id not in remaining
        assert set(remaining.values()) == {"pending"}
        assert await _plan_row(seeded_url, view.id) == ("active", 3)


async def test_two_patches_with_one_version(seeded_url):
    for _ in range(REPEATS):
        view, tutor = await _plan(seeded_url, active=True)
        target = view.objectives[0].milestones[0].id

        outcomes = await race(
            seeded_url, _progress(target, tutor, 1), _progress(target, tutor, 1)
        )

        assert outcomes == ["ok", "version_conflict"]
        assert (await _milestones(seeded_url, view.id))[target] == "done"


@pytest.mark.parametrize("complete_first", [True, False])
async def test_a_complete_racing_a_put(seeded_url, complete_first):
    for _ in range(REPEATS):
        view, tutor = await _plan(seeded_url, active=True)
        complete = _transition(view.id, tutor, 2, "complete")
        put = _drop_first_milestone(view, tutor)
        changes = (complete, put) if complete_first else (put, complete)

        outcomes = await race(seeded_url, *changes)

        assert outcomes == ["ok", "version_conflict"]
        status, version = await _plan_row(seeded_url, view.id)
        assert (status, version) == (
            ("completed", 3) if complete_first else ("active", 3)
        )
