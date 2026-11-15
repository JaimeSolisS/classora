"""Recently deleted records: the list, the restore and the purge rule (#129
B1-B3, C3; spec §9 "soft delete and recovery").

The routes are in :mod:`app.workflows.deleted_records_router`. The rows are
``academics.deleted_records``; this module adds the kinds' record policies
(``scheduling`` for the tutor's current students and subjects), the
center's time zone (``administration``) and the student names
(``families``).

**Restorable by the caller** (E3): they deleted it (``deleted_by_user_id``),
``now < deleted_at + 30 days``, and the kind's **existing delete check**
passes on the row as if it were live (:func:`_delete_block`, the same pure
rules as the five ``DELETE`` routes): the note's author while the student is
a current assignment; the evaluation's author likewise; a
``learning_plan.manage`` holder or a tutor teaching the student in the
plan's subject; the assignment's creator while any recipient is a current
student; the report's author while they teach the student.

**List** (B1, :func:`list_deleted`): every restorable record of the five
kinds, newest ``deleted_at`` first, at most 100 with ``has_more``; at most
seven statements (the zone, the tutor's students and subjects, one per
kind). Anyone else (students, guardians, Finance) gets an empty list.

**Restore** (B2, :func:`restore`): the row ``FOR UPDATE`` → ``404`` (unknown,
live, someone else's, past the window) → ``409 version_conflict`` → the
kind's ``403`` (or ``404``) → ``409 deleted_record_restore_conflict`` → both
columns cleared, the version + 1 and the automatic ``restore`` audit event.
Nothing is notified (E3).

**Purge rule** (C3, :func:`purge_deleted_records`): registered as
``academics.deleted_records`` on the retention registry by ``app.wiring``;
batches of 100 per kind, each in its own Unit of Work with no actor.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.core.uow import UnitOfWork
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.workflows.deleted_record_schemas import (
    DeletedRecordItem,
    DeletedRecordList,
    DeletedRecordStudent,
    RestoredRecord,
)

records = academics.deleted_records
Kind = records.DeletedRecordKind

note_rules = academics.note_rules
evaluation_rules = academics.evaluation_rules
plan_rules = academics.learning_plan_rules
assignment_rules = academics.assignment_rules
report_rules = academics.progress_report_rules

RULE_CODE: Final = "academics.deleted_records"
"""The retention rule's code (C1)."""


# --- The record policies (E3) ---------------------------------------------------------


@dataclass(frozen=True)
class _Caller:
    """The caller and the subjects in which they currently teach each of
    their current students (empty without the ``tutor`` role)."""

    principal: Principal
    subjects: Mapping[uuid.UUID, frozenset[uuid.UUID]]

    @property
    def tutor(self) -> bool:
        return note_rules.TUTOR_ROLE in self.principal.roles

    def assigned(self, student_id: uuid.UUID) -> bool:
        return self.tutor and student_id in self.subjects

    def subjects_of(self, student_id: uuid.UUID) -> frozenset[uuid.UUID]:
        return self.subjects.get(student_id, frozenset()) if self.tutor else frozenset()


async def _caller(session: AsyncSession, principal: Principal) -> _Caller:
    """One statement for a ``tutor``-role user; none otherwise."""
    subjects: Mapping[uuid.UUID, frozenset[uuid.UUID]] = {}
    if note_rules.TUTOR_ROLE in principal.roles:
        subjects = await scheduling.subjects_by_student_for_tutor(
            session, principal.user_id, now=clock.now()
        )
    return _Caller(principal, subjects)


type Block = type[Exception] | None

_NOTE_ERRORS: Final[Mapping[str, type[Exception]]] = {
    note_rules.NOTE_AUTHOR_ONLY: academics.NoteAuthorOnlyError,
    note_rules.NOTE_READ_ONLY: academics.NoteReadOnlyError,
}
_EVALUATION_ERRORS: Final[Mapping[str, type[Exception]]] = {
    evaluation_rules.EVALUATION_AUTHOR_ONLY: academics.EvaluationAuthorOnlyError,
    evaluation_rules.EVALUATION_READ_ONLY: academics.EvaluationReadOnlyError,
    evaluation_rules.PERMISSION_DENIED: PermissionDeniedError,
}
_PLAN_ERRORS: Final[Mapping[str, type[Exception]]] = {
    plan_rules.NOT_FOUND: RecordNotFoundError,
    plan_rules.PLAN_SUBJECT_NOT_ASSIGNED: academics.PlanSubjectNotAssignedError,
    plan_rules.PLAN_READ_ONLY: academics.PlanReadOnlyError,
}
_ASSIGNMENT_ERRORS: Final[Mapping[str, type[Exception]]] = {
    assignment_rules.NOT_FOUND: RecordNotFoundError,
    assignment_rules.ASSIGNMENT_CREATOR_ONLY: academics.AssignmentCreatorOnlyError,
    assignment_rules.ASSIGNMENT_READ_ONLY: academics.AssignmentReadOnlyError,
}
_REPORT_ERRORS: Final[Mapping[str, type[Exception]]] = {
    report_rules.NOT_FOUND: RecordNotFoundError,
    report_rules.PERMISSION_DENIED: PermissionDeniedError,
    report_rules.AUTHOR_ONLY: academics.ProgressReportAuthorOnlyError,
    report_rules.READ_ONLY: academics.ProgressReportReadOnlyError,
    report_rules.SELF_REVIEW: academics.ProgressReportSelfReviewError,
}


def _note_block(caller: _Caller, note: Any) -> Block:
    """``DELETE /api/v1/notes/{id}`` steps 3-4 on a live copy."""
    principal = caller.principal
    assigned = caller.assigned(note.student_id)
    relation = note_rules.note_relation(
        principal, author_user_id=note.author_user_id, student_assigned=assigned
    )
    if not note_rules.can_read(relation):
        return RecordNotFoundError
    blocked = note_rules.write_block(
        "delete",
        relation,
        student_assigned=assigned,
        reviewer=note_rules.is_reviewer(principal),
    )
    return None if blocked is None else _NOTE_ERRORS[blocked]


def _evaluation_block(caller: _Caller, evaluation: Any) -> Block:
    """``DELETE /api/v1/evaluations/{id}`` steps 3-4 on a live copy."""
    principal = caller.principal
    relation = evaluation_rules.evaluation_relation(
        principal,
        author_user_id=evaluation.author_user_id,
        student_assigned=caller.assigned(evaluation.student_id),
    )
    if not evaluation_rules.can_read(
        relation, status=evaluation.status, voided=evaluation.voided_at is not None
    ):
        return RecordNotFoundError
    blocked = evaluation_rules.write_block(
        "delete", relation, reviewer=evaluation_rules.is_reviewer(principal)
    )
    return None if blocked is None else _EVALUATION_ERRORS[blocked]


def _plan_block(caller: _Caller, plan: Any) -> Block:
    """``DELETE /api/v1/learning-plans/{id}`` steps 3-4 on a live copy (the
    family rule isn't checked on a write)."""
    principal = caller.principal
    manager = principal.has(plan_rules.LEARNING_PLAN_MANAGE)
    subjects = frozenset() if manager else caller.subjects_of(plan.student_id)
    assigned = caller.tutor and bool(subjects)
    relation = plan_rules.plan_relation(
        manager=manager,
        tutor=caller.tutor,
        student_assigned=assigned,
        subject_assigned=plan.subject_id in subjects,
        is_creator=plan.created_by_user_id == principal.user_id,
        family=False,
    )
    if not plan_rules.can_read(
        relation, status=plan.status, activated_at=plan.activated_at, deleted=False
    ):
        return RecordNotFoundError
    blocked = plan_rules.access_block(relation)
    return None if blocked is None else _PLAN_ERRORS[blocked]


def _assignment_block(
    caller: _Caller, assignment: Any, recipients: list[uuid.UUID]
) -> Block:
    """``DELETE /api/v1/assignments/{id}`` steps 3-4 on a live copy: the
    creator is current while any recipient is a current student."""
    principal = caller.principal
    relation = assignment_rules.assignment_relation(
        tutor=caller.tutor,
        is_creator=caller.tutor and assignment.creator_user_id == principal.user_id,
        student_assigned=any(caller.assigned(s) for s in recipients),
        reviewer=principal.has(assignment_rules.ASSIGNMENT_REVIEW),
    )
    blocked = assignment_rules.access_block(relation, "delete")
    return None if blocked is None else _ASSIGNMENT_ERRORS[blocked]


def _report_block(caller: _Caller, report: Any) -> Block:
    """``DELETE /api/v1/progress-reports/{id}`` steps 3-4 on a live copy."""
    principal = caller.principal
    reviewer = principal.has(report_rules.PROGRESS_REPORT_REVIEW)
    relation = report_rules.report_relation(
        is_author=report.author_user_id == principal.user_id,
        tutor=caller.tutor,
        student_current=caller.tutor and bool(caller.subjects_of(report.student_id)),
        reviewer=reviewer,
    )
    if not report_rules.can_read(
        relation, status=report.status, deleted=False, reviewer=reviewer
    ):
        return RecordNotFoundError
    blocked = report_rules.access_block(
        relation,
        "delete",
        reviewer=reviewer,
        is_owner_role=report_rules.OWNER_ROLE in principal.roles,
    )
    return None if blocked is None else _REPORT_ERRORS[blocked]


def _delete_block(
    caller: _Caller, kind: Any, record: Any, recipients: list[uuid.UUID]
) -> Block:
    """The kind's delete check on ``record`` as if it were live: ``None``
    when it passes, else the error class its ``DELETE`` route raises."""
    if kind is Kind.SESSION_NOTE:
        return _note_block(caller, record)
    if kind is Kind.EVALUATION:
        return _evaluation_block(caller, record)
    if kind is Kind.LEARNING_PLAN:
        return _plan_block(caller, record)
    if kind is Kind.ASSIGNMENT:
        return _assignment_block(caller, record, recipients)
    return _report_block(caller, record)


# --- B1: the list ---------------------------------------------------------------------


def _local_date(value: datetime, zone: str) -> date:
    return value.astimezone(ZoneInfo(zone)).date()


def _title(row: Any) -> str | None:
    record = row.record
    if row.kind in (Kind.LEARNING_PLAN, Kind.ASSIGNMENT):
        return str(record.title)
    if row.kind is Kind.EVALUATION:
        return str(record.template_name)
    return None


def _reference_date(row: Any, zone: str) -> date:
    record = row.record
    if row.kind is Kind.SESSION_NOTE:
        starts_at = row.session_starts_at or record.created_at
        return _local_date(starts_at, zone)
    if row.kind is Kind.EVALUATION:
        return record.evaluated_on  # type: ignore[no-any-return]
    if row.kind is Kind.LEARNING_PLAN:
        return record.starts_on  # type: ignore[no-any-return]
    if row.kind is Kind.ASSIGNMENT:
        return record.due_on  # type: ignore[no-any-return]
    if record.kind == report_rules.ON_DEMAND:
        return _local_date(record.created_at, zone)
    return record.period_start  # type: ignore[no-any-return]


def _item(row: Any, zone: str) -> DeletedRecordItem:
    record = row.record
    student_id, name = row.students[0]
    return DeletedRecordItem(
        kind=row.kind,
        id=record.id,
        version=record.version,
        student=DeletedRecordStudent(id=student_id, display_name=name),
        title=_title(row),
        reference_date=_reference_date(row, zone),
        deleted_at=record.deleted_at,
        restorable_until=records.restorable_until(record.deleted_at),
    )


async def list_deleted(
    session: AsyncSession, principal: Principal
) -> DeletedRecordList:
    """``GET /api/v1/deleted-records`` (B1): at most seven statements."""
    now = clock.now()
    zone = await administration.get_center_time_zone(session)
    caller = await _caller(session, principal)
    rows = await records.deleted_rows(
        session,
        principal.user_id,
        since=now - records.RECOVERY_WINDOW,
        students=families.student_name_columns(),
        sessions=scheduling.note_session_columns(),
    )
    restorable = [
        row
        for row in rows
        if row.students
        and _delete_block(
            caller, row.kind, row.record, [student for student, _ in row.students]
        )
        is None
    ]
    restorable.sort(
        key=lambda row: (row.record.deleted_at, row.record.id), reverse=True
    )
    return DeletedRecordList(
        items=[_item(row, zone) for row in restorable[: records.LIST_MAX]],
        has_more=len(restorable) > records.LIST_MAX,
    )


# --- B2: the restore ------------------------------------------------------------------


async def restore(
    session: AsyncSession,
    *,
    principal: Principal,
    kind: Any,
    record_id: uuid.UUID,
    version: int,
) -> RestoredRecord:
    """``POST /api/v1/deleted-records/{kind}/{id}/restore`` (B2), in the
    route's Unit of Work."""
    record = await records.deleted_for_update(session, kind, record_id)
    if (
        record is None
        or record.deleted_by_user_id != principal.user_id
        or clock.now() >= records.restorable_until(record.deleted_at)
    ):
        raise RecordNotFoundError()
    ensure_version(record, version)
    recipients: list[uuid.UUID] = []
    if kind is Kind.ASSIGNMENT:
        recipients = await records.recipient_ids(session, record.id)
    blocked = _delete_block(await _caller(session, principal), kind, record, recipients)
    if blocked is not None:
        raise blocked()
    if kind is Kind.SESSION_NOTE:
        await records.restore_note(session, record)
    elif kind is Kind.EVALUATION:
        await records.restore_evaluation(session, record)
    elif kind is Kind.LEARNING_PLAN:
        await records.restore_plan(session, record)
    elif kind is Kind.ASSIGNMENT:
        await records.restore_assignment(session, record)
    else:
        await records.restore_report(session, record)
    return RestoredRecord(kind=kind, id=record.id, version=record.version)


# --- C3: the purge rule ---------------------------------------------------------------


async def purge_deleted_records(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime
) -> int:
    """The ``academics.deleted_records`` retention rule (C3): every kind's
    rows deleted at or before ``now - 30 days``, in batches of 100, each in
    its own Unit of Work with no actor (the ``delete`` audit events have a
    ``NULL`` actor). The #316 placeholder guard uses the center-local today
    of ``now``. Returns the number of records purged."""
    async with UnitOfWork(sessionmaker) as uow:
        zone = await administration.get_center_time_zone(uow.session)
    today = _local_date(now, zone)
    schedule_rules = academics.progress_report_schedule_rules
    before = {
        cadence: schedule_rules.latest_ended_period(cadence, today)[0]
        for cadence in (report_rules.WEEKLY, report_rules.MONTHLY)
    }
    cutoff = now - records.RECOVERY_WINDOW
    total = 0
    for kind in Kind:
        while True:
            async with UnitOfWork(sessionmaker) as uow:
                count = await records.purge_batch(
                    uow.session, kind, cutoff=cutoff, placeholder_before=before
                )
            total += count
            if count < records.PURGE_BATCH:
                break
    return total
