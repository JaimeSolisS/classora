"""Concurrent evaluation writes (#80 D9), with two connections (``race``
from ``test_assignment_offer_concurrency``): transaction A writes and keeps
its transaction open for a moment; B starts once A's change is flushed, so
B waits on the row lock and then sees A's version.

- two finalizes with the same version: one ``ok``, one
  ``version_conflict``;
- a finalize racing a delete (both orders): exactly one succeeds;
- an amendment racing a staff void (both orders): serialized by the row
  lock, the loser gets ``version_conflict``;
- a template ``PUT`` racing an evaluation create (both orders): the create
  copies the old or the new criteria in full, never a mix (the template
  ``FOR SHARE``).

Each case runs 5 times, on new evaluations.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.academics import service as academics
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ROLE_PERMISSIONS,
    TUTOR,
)
from app.modules.scheduling.models import ClassSession
from app.workflows import evaluations as workflow
from app.workflows.evaluation_schemas import (
    EvaluationCreate,
    EvaluationPatch,
    EvaluationScoreInput,
    EvaluationTemplateCreate,
    EvaluationTemplateReplace,
    EvaluationVoid,
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


def _principal(user_id: uuid.UUID, role: str) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


def _template_body(*titles: str) -> EvaluationTemplateCreate:
    return EvaluationTemplateCreate.model_validate(
        {
            "name": f"Plantilla {uuid.uuid4().hex[:8]}",
            "kind": "ongoing",
            "criteria": [{"title": title, "max_score": "10"} for title in titles],
        }
    )


async def _setup(url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, Any]:
    """A tutor teaching a new student, and a template on the session's
    subject: ``(tutor id, student id, subject id, template view)``."""

    async def _make(session: AsyncSession) -> tuple[Any, ...]:
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
        template = await workflow.create_template(
            session, body=_template_body("Lectura", "Escritura")
        )
        return tutor.id, student, made.subject_id, template

    tutor, student, subject, template = await _in_uow(url, _make)
    return tutor, student, subject, template


async def _evaluation(url: str, *, final: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    """A scored draft (``final``: finalized, version 2): ``(evaluation id,
    tutor id)``."""
    tutor, student, subject, template = await _setup(url)

    async def _make(session: AsyncSession) -> uuid.UUID:
        view = await workflow.create_evaluation(
            session,
            principal=_principal(tutor, TUTOR),
            body=EvaluationCreate(
                student_id=student,
                template_id=template.id,
                subject_id=subject,
                scores=[
                    EvaluationScoreInput(position=1, score=Decimal(5)),
                    EvaluationScoreInput(position=2, score=Decimal(6)),
                ],
            ),
        )
        if final:
            await workflow.finalize_evaluation(
                session,
                principal=_principal(tutor, TUTOR),
                evaluation_id=view.id,
                version=1,
            )
        return view.id

    return await _in_uow(url, _make), tutor


def _finalize(evaluation_id: uuid.UUID, tutor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.finalize_evaluation(
            db,
            principal=_principal(tutor, TUTOR),
            evaluation_id=evaluation_id,
            version=1,
        )

    return _run


def _delete(evaluation_id: uuid.UUID, tutor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.delete_evaluation(
            db,
            principal=_principal(tutor, TUTOR),
            evaluation_id=evaluation_id,
            version=1,
        )

    return _run


def _amend(evaluation_id: uuid.UUID, tutor: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.edit_evaluation(
            db,
            principal=_principal(tutor, TUTOR),
            evaluation_id=evaluation_id,
            body=EvaluationPatch(
                version=2,
                change_reason="Recalificado",
                scores=[EvaluationScoreInput(position=1, score=Decimal(9))],
            ),
        )

    return _run


def _void(evaluation_id: uuid.UUID, staff: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.void_evaluation(
            db,
            principal=_principal(staff, ACADEMIC_COORDINATOR),
            evaluation_id=evaluation_id,
            body=EvaluationVoid(version=2, reason="Alumno equivocado"),
        )

    return _run


async def _row(url: str, evaluation_id: uuid.UUID) -> tuple[str, int, bool, bool]:
    [row] = await run_sql_async(
        url,
        "SELECT status, version, deleted_at IS NOT NULL AS deleted,"
        " voided_at IS NOT NULL AS voided FROM evaluations WHERE id = :id",
        {"id": evaluation_id},
    )
    return str(row.status), int(row.version), bool(row.deleted), bool(row.voided)


async def test_two_finalizes_with_one_version(seeded_url):
    for _ in range(REPEATS):
        evaluation_id, tutor = await _evaluation(seeded_url)

        outcomes = await race(
            seeded_url, _finalize(evaluation_id, tutor), _finalize(evaluation_id, tutor)
        )

        assert outcomes == ["ok", "version_conflict"]
        assert await _row(seeded_url, evaluation_id) == ("final", 2, False, False)


@pytest.mark.parametrize("finalize_first", [True, False])
async def test_a_finalize_racing_a_delete(seeded_url, finalize_first):
    for _ in range(REPEATS):
        evaluation_id, tutor = await _evaluation(seeded_url)
        changes = (
            (_finalize(evaluation_id, tutor), _delete(evaluation_id, tutor))
            if finalize_first
            else (_delete(evaluation_id, tutor), _finalize(evaluation_id, tutor))
        )

        outcomes = await race(seeded_url, *changes)

        assert outcomes[0] == "ok"
        assert outcomes[1] in {"version_conflict", "not_found"}
        assert await _row(seeded_url, evaluation_id) == (
            ("final", 2, False, False) if finalize_first else ("draft", 2, True, False)
        )


@pytest.mark.parametrize("void_first", [True, False])
async def test_an_amendment_racing_a_void(seeded_url, void_first):
    staff = await _in_uow(
        seeded_url, lambda db: persist_user(db, roles=(ACADEMIC_COORDINATOR,))
    )
    for _ in range(REPEATS):
        evaluation_id, tutor = await _evaluation(seeded_url, final=True)
        amend, void = _amend(evaluation_id, tutor), _void(evaluation_id, staff.id)
        changes = (void, amend) if void_first else (amend, void)

        outcomes = await race(seeded_url, *changes)

        assert outcomes == ["ok", "version_conflict"]
        status, version, deleted, voided = await _row(seeded_url, evaluation_id)
        assert (status, version, deleted, voided) == ("final", 3, False, void_first)


@pytest.mark.parametrize("replace_first", [True, False])
async def test_a_template_replace_racing_a_create(seeded_url, replace_first):
    for _ in range(REPEATS):
        tutor, student, subject, template = await _setup(seeded_url)

        async def _replace(db: AsyncSession, template: Any = template) -> None:
            await workflow.replace_template(
                db,
                template_id=template.id,
                body=EvaluationTemplateReplace.model_validate(
                    {
                        "version": template.version,
                        "name": template.name,
                        "kind": template.kind,
                        "is_active": True,
                        "criteria": [
                            {"title": "Comprensión", "max_score": "20"},
                            {"title": "Oral", "max_score": "5"},
                            {"title": "Cálculo", "max_score": "7"},
                        ],
                    }
                ),
            )

        created: list[uuid.UUID] = []

        async def _create(
            db: AsyncSession,
            template: Any = template,
            student: uuid.UUID = student,
            subject: uuid.UUID = subject,
            tutor: uuid.UUID = tutor,
            created: list[uuid.UUID] = created,
        ) -> None:
            view = await workflow.create_evaluation(
                db,
                principal=_principal(tutor, TUTOR),
                body=EvaluationCreate(
                    student_id=student, template_id=template.id, subject_id=subject
                ),
            )
            created.append(view.id)

        changes = (_replace, _create) if replace_first else (_create, _replace)

        assert await race(seeded_url, *changes) == ["ok", "ok"]
        [evaluation_id] = created

        async def _copied(
            db: AsyncSession, evaluation_id: uuid.UUID = evaluation_id
        ) -> list[tuple[str, Decimal]]:
            rows = await academics.scores_of(db, [evaluation_id])
            return [(r.criterion_title, r.max_score) for r in rows[evaluation_id]]

        copied = await _in_uow(seeded_url, _copied)
        expected = (
            [
                ("Comprensión", Decimal(20)),
                ("Oral", Decimal(5)),
                ("Cálculo", Decimal(7)),
            ]
            if replace_first
            else [("Lectura", Decimal(10)), ("Escritura", Decimal(10))]
        )
        assert copied == expected
