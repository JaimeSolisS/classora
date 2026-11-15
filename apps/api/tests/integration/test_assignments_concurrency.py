"""Concurrent homework writes (#82 D3), with two connections (``race`` from
``test_assignment_offer_concurrency``): transaction A writes and keeps its
transaction open for a moment; B starts once A's change is flushed, so B
waits on the assignment's row lock (``FOR UPDATE``, then the submission
row) and then sees A's change.

- a student's replacement racing the creator's grade (both orders): one
  ``ok``, the other ``version_conflict`` (a submit never lands on a graded
  row);
- two student submits with one version: one ``version_conflict``;
- a ``PATCH`` removing a recipient racing that recipient's submit (both
  orders): either the removal wins and the submit is ``not_found``, or the
  submit wins and the removal is ``recipient_has_submission``;
- a ``DELETE`` racing a submit (both orders): exactly one succeeds.

Each case runs 5 times, on new assignments.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.families.models import Student
from app.modules.identity.permissions import ROLE_PERMISSIONS, STUDENT, TUTOR
from app.modules.scheduling.models import ClassSession
from app.workflows import assignments as workflow
from app.workflows.assignment_schemas import (
    AssignmentCreate,
    AssignmentPatch,
    GradePut,
    SubmissionPut,
)
from tests.conftest import frozen_clock
from tests.factories.families import ADULT_BIRTHDATE, persist_student, student_login
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


def _principal(user_id: uuid.UUID, role: str) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


class _World:
    def __init__(self, ids: tuple[uuid.UUID, ...]) -> None:
        (
            self.tutor,
            self.student_user,
            self.student,
            self.other,
            self.subject,
            self.assignment,
        ) = ids

    @property
    def creator(self) -> Principal:
        return _principal(self.tutor, TUTOR)

    @property
    def pupil(self) -> Principal:
        return _principal(self.student_user, STUDENT)


async def _world(url: str, *, submitted: bool) -> _World:
    """A tutor teaching two students (the first with a login) and homework
    for both (``submitted``: the first student already submitted, so their
    row is at version 2)."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, ...]:
        tutor = await persist_user(session, roles=(TUTOR,))
        login = await persist_user(session, roles=(STUDENT,))
        made: ClassSession = await persist_class_session(
            session,
            state="confirmed",
            tutor_user_id=tutor.id,
            starts_at=NOW - timedelta(minutes=30),
            ends_at=NOW + timedelta(minutes=30),
            scheduled_minutes=60,
        )
        student: Student = await persist_student(
            session, contacts=0, birthdate=ADULT_BIRTHDATE
        )
        await student_login(session, student, login.id)
        await add_participant(session, made, student.id)
        other = (await add_participant(session, made)).student_id
        view = await workflow.create_assignment(
            session,
            principal=_principal(tutor.id, TUTOR),
            body=AssignmentCreate.model_validate(
                {
                    "subject_id": str(made.subject_id),
                    "title": "Tarea",
                    "instructions": "Leer",
                    "due_on": "2031-03-12",
                    "max_score": "10",
                    "student_ids": [str(student.id), str(other)],
                }
            ),
        )
        if submitted:
            await workflow.submit(
                session,
                principal=_principal(login.id, STUDENT),
                assignment_id=view.id,
                student_id=student.id,
                body=SubmissionPut(text="Primera"),
            )
        return tutor.id, login.id, student.id, other, made.subject_id, view.id

    return _World(await _in_uow(url, _make))


def _submit(world: _World, version: int | None, text: str = "Respuesta") -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.submit(
            db,
            principal=world.pupil,
            assignment_id=world.assignment,
            student_id=world.student,
            body=SubmissionPut(version=version, text=text),
        )

    return _run


def _grade(world: _World, version: int) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.grade(
            db,
            principal=world.creator,
            assignment_id=world.assignment,
            student_id=world.student,
            body=GradePut.model_validate({"version": version, "score": "8"}),
        )

    return _run


def _remove(world: _World) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.edit_assignment(
            db,
            principal=world.creator,
            assignment_id=world.assignment,
            body=AssignmentPatch.model_validate(
                {"version": 1, "remove_student_ids": [str(world.student)]}
            ),
        )

    return _run


def _delete(world: _World) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.delete_assignment(
            db, principal=world.creator, assignment_id=world.assignment, version=1
        )

    return _run


async def _row(url: str, world: _World) -> Any:
    rows = await run_sql_async(
        url,
        "SELECT text, version, graded_at FROM assignment_submissions"
        " WHERE assignment_id = :a AND student_id = :s",
        {"a": world.assignment, "s": world.student},
    )
    return rows[0] if rows else None


async def _deleted(url: str, world: _World) -> bool:
    [row] = await run_sql_async(
        url,
        "SELECT deleted_at FROM assignments WHERE id = :a",
        {"a": world.assignment},
    )
    return row.deleted_at is not None


@pytest.mark.parametrize("grade_first", [True, False])
async def test_a_replacement_racing_the_grade(seeded_url, grade_first):
    for _ in range(REPEATS):
        world = await _world(seeded_url, submitted=True)
        changes = (_grade(world, 2), _submit(world, 2, "Segunda"))
        if not grade_first:
            changes = changes[::-1]

        outcomes = await race(seeded_url, *changes)

        assert outcomes == ["ok", "version_conflict"]
        row = await _row(seeded_url, world)
        assert row.version == 3
        if grade_first:
            assert (row.text, row.graded_at is not None) == ("Primera", True)
        else:
            assert (row.text, row.graded_at) == ("Segunda", None)


async def test_two_submits_with_one_version(seeded_url):
    for _ in range(REPEATS):
        world = await _world(seeded_url, submitted=True)

        outcomes = await race(
            seeded_url, _submit(world, 2, "A"), _submit(world, 2, "B")
        )

        assert outcomes == ["ok", "version_conflict"]
        row = await _row(seeded_url, world)
        assert (row.text, row.version) == ("A", 3)


@pytest.mark.parametrize("remove_first", [True, False])
async def test_a_removal_racing_a_submit(seeded_url, remove_first):
    for _ in range(REPEATS):
        world = await _world(seeded_url, submitted=False)
        changes = (_remove(world), _submit(world, None))
        if not remove_first:
            changes = changes[::-1]

        outcomes = await race(seeded_url, *changes)

        if remove_first:
            assert outcomes == ["ok", "not_found"]
            assert await _row(seeded_url, world) is None
        else:
            assert outcomes == ["ok", "recipient_has_submission"]
            assert (await _row(seeded_url, world)).text == "Respuesta"


@pytest.mark.parametrize("delete_first", [True, False])
async def test_a_delete_racing_a_submit(seeded_url, delete_first):
    for _ in range(REPEATS):
        world = await _world(seeded_url, submitted=False)
        changes = (_delete(world), _submit(world, None))
        if not delete_first:
            changes = changes[::-1]

        outcomes = await race(seeded_url, *changes)

        if delete_first:
            assert outcomes == ["ok", "not_found"]
            assert await _deleted(seeded_url, world)
            assert (await _row(seeded_url, world)).text is None
        else:
            assert outcomes == ["ok", "assignment_has_submissions"]
            assert not await _deleted(seeded_url, world)
