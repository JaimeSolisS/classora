"""Homework assignments, submissions and grading (#82 M5-15; spec §2, §4
"tutors provide instructions/deadlines, students upload work, tutors grade
and provide feedback, guardians track completion" as rescoped on
2026-10-21: text and/or a URL, no uploads; §9, §16, W5.3, W5.4;
architecture §4.1, §4.2).

A tutor sets homework for one or more of their current students (each has
their own submission row), optionally linked to one of their classes. The
student submits text and/or a link from their own login, or the tutor
records work handed in on paper. The tutor grades it (a score of the
optional ``max_score`` and feedback, or feedback only); re-grades keep an
internal reason. Late and missing are computed at read time from the
center-local deadline (E3). The student and the guardians with academic
access read the homework with **that student's** row only, through one
closed shape (:class:`~app.workflows.assignment_schemas.StudentAssignment`)
and are notified (ids only). ``tests/integration/test_session_notes_leak.py``
(the shared family leak walk) proves no ``GET`` route carries another
student's submission, grade or name, a change reason or a deleted
assignment to a family.

The routes are in :mod:`app.workflows.assignments_router`; the bodies and
views in :mod:`app.workflows.assignment_schemas`.

**Who** (E6; the pure rules in ``academics.assignment_rules``):

- a ``tutor``-role user sets homework for students among their current
  students (``scheduling.service.student_ids_for_tutor``); only the creator
  edits, deletes, grades and records handed-in work, while the student
  (for an assignment-wide write: any recipient) is still one of their
  current students (``403 assignment_read_only`` otherwise);
- ``assignment.review`` holders read everything, write nothing (``403
  assignment_creator_only``);
- a tutor currently teaching the student reads that student's homework
  through the per-student routes;
- the student reads and submits their own; the linked guardians with
  academic access read (``403 submission_student_only`` on a submit; ``403
  student_consent_required`` for a guardian of an adult without the
  consent);
- anyone else, Finance included, the ``404`` of an unknown id.

**Orders:** create C2 (:func:`create_assignment`); writes on one assignment
C3 (``404`` → ``403`` → the assignment ``FOR UPDATE`` then the submission
row → ``409 version_conflict`` → ``409`` state → ``422`` content → write).
"""

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.assignment_schemas import (
    AssignmentCounts,
    AssignmentCreate,
    AssignmentList,
    AssignmentPatch,
    AssignmentPerson,
    AssignmentSession,
    AssignmentStateSummary,
    AssignmentStudent,
    AssignmentSubject,
    AssignmentSummary,
    AssignmentView,
    Due,
    GradePut,
    RosterEntry,
    StudentAssignment,
    StudentAssignmentList,
    StudentAssignmentSession,
    StudentSubmission,
    SubmissionPut,
    SubmissionState,
    SubmittedVia,
)

rules = academics.assignment_rules
Relation = rules.AssignmentRelation

SET_NOTIFICATION: Final = "academics.assignment_set"
SUBMITTED_NOTIFICATION: Final = "academics.assignment_submitted"
GRADED_NOTIFICATION: Final = "academics.assignment_graded"
STUDENT_ROLE: Final = "student"


class AssignmentSetParams(BaseModel):
    """``academics.assignment_set`` (E8): ids and the deadline's date only,
    never a title, instructions or tutor name."""

    student_id: str
    assignment_id: str
    subject_id: str
    due_on: str


class AssignmentSubmittedParams(BaseModel):
    """``academics.assignment_submitted`` (E8): no text or link."""

    student_id: str
    assignment_id: str


class AssignmentGradedParams(BaseModel):
    """``academics.assignment_graded`` (E8): no score or feedback."""

    student_id: str
    assignment_id: str
    subject_id: str


def register_assignment_notifications() -> None:
    """Register the three types (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(SET_NOTIFICATION, AssignmentSetParams)
    administration.register_notification_type(
        SUBMITTED_NOTIFICATION, AssignmentSubmittedParams
    )
    administration.register_notification_type(
        GRADED_NOTIFICATION, AssignmentGradedParams
    )


type FieldIssue = tuple[list[str | int], str, str]


def _invalid(
    path: Sequence[str | int],
    code: str,
    message: str,
    *,
    details: dict[str, str | list[str]] | None = None,
) -> academics.AssignmentFieldError:
    return academics.AssignmentFieldError.one(path, code, message, details=details)


# --- The caller and the clock ---------------------------------------------------------


@dataclass(frozen=True)
class _Caller:
    """The caller and their current students (empty without the ``tutor``
    role)."""

    principal: Principal
    assigned: frozenset[uuid.UUID]

    @property
    def tutor(self) -> bool:
        return rules.TUTOR_ROLE in self.principal.roles

    @property
    def reviewer(self) -> bool:
        return self.principal.has(rules.ASSIGNMENT_REVIEW)

    def is_creator(self, assignment: academics.Assignment) -> bool:
        return self.tutor and assignment.creator_user_id == self.principal.user_id

    def staff_relation(
        self, assignment: academics.Assignment, student_ids: Iterable[uuid.UUID]
    ) -> rules.AssignmentRelation:
        """The relation without the student or family branches: for an
        assignment-wide write, ``student_ids`` are every recipient (the
        creator is current while any is a current student); for one row,
        that student."""
        return rules.assignment_relation(
            tutor=self.tutor,
            is_creator=self.is_creator(assignment),
            student_assigned=any(s in self.assigned for s in student_ids),
            reviewer=self.reviewer,
        )


async def _caller(session: AsyncSession, principal: Principal) -> _Caller:
    """One statement for a ``tutor``-role user; none otherwise."""
    assigned: frozenset[uuid.UUID] = frozenset()
    if rules.TUTOR_ROLE in principal.roles:
        assigned = await scheduling.student_ids_for_tutor(
            session, principal.user_id, now=clock.now()
        )
    return _Caller(principal, assigned)


@dataclass(frozen=True)
class _Clock:
    zone: str
    now: datetime
    today: date

    def deadline(self, assignment: academics.Assignment) -> datetime:
        return rules.deadline_instant(assignment.due_on, assignment.due_time, self.zone)


async def _clock(session: AsyncSession) -> _Clock:
    """The center's zone (cached for the Unit of Work), now and today."""
    zone = await administration.get_center_time_zone(session)
    now = clock.now()
    return _Clock(zone, now, clock.today_in(zone))


async def _is_student_self(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> bool:
    """The caller's own student profile (one statement for a ``student``
    role, none otherwise)."""
    if STUDENT_ROLE not in principal.roles:
        return False
    return await families.own_student_id(session, principal.user_id) == student_id


async def _row_relation(
    session: AsyncSession,
    caller: _Caller,
    assignment: academics.Assignment,
    student_id: uuid.UUID,
    *,
    family: bool,
) -> rules.AssignmentRelation:
    """The caller's relation to one recipient's row. The student and family
    branches are checked only when no creator relation applies, and the
    family rule only with ``family`` (``403 student_consent_required``
    propagates for a linked guardian of an adult without the consent)."""
    if caller.is_creator(assignment):
        return caller.staff_relation(assignment, [student_id])
    if await _is_student_self(session, caller.principal, student_id):
        return Relation.STUDENT_SELF
    relation = caller.staff_relation(assignment, [student_id])
    if relation is not Relation.NONE or not family:
        return relation
    try:
        await scheduling.ensure_family_student_access(
            session, caller.principal, student_id
        )
    except RecordNotFoundError:
        return Relation.NONE
    return Relation.GUARDIAN


_ACCESS_ERRORS: Final[Mapping[str, type[Exception]]] = {
    rules.NOT_FOUND: RecordNotFoundError,
    rules.ASSIGNMENT_CREATOR_ONLY: academics.AssignmentCreatorOnlyError,
    rules.ASSIGNMENT_READ_ONLY: academics.AssignmentReadOnlyError,
    rules.SUBMISSION_STUDENT_ONLY: academics.SubmissionStudentOnlyError,
}
_STATE_ERRORS: Final[Mapping[str, type[Exception]]] = {
    rules.SUBMISSION_GRADED: academics.SubmissionGradedError,
    rules.SUBMISSION_LOCKED: academics.SubmissionLockedError,
    rules.SUBMISSION_REQUIRED: academics.SubmissionRequiredError,
    rules.ASSIGNMENT_HAS_SUBMISSIONS: academics.AssignmentHasSubmissionsError,
}


def _ensure_access(
    relation: rules.AssignmentRelation, action: rules.AssignmentAction
) -> None:
    blocked = rules.access_block(relation, action)
    if blocked is not None:
        raise _ACCESS_ERRORS[blocked]()


def _ensure_state(blocked: str | None) -> None:
    if blocked is not None:
        raise _STATE_ERRORS[blocked]()


async def _row(
    session: AsyncSession, assignment_id: uuid.UUID
) -> academics.AssignmentRow:
    """The live assignment with its subject's name and session start, or
    the ``404`` of an unknown id."""
    row = await academics.assignment_with_subject(
        session,
        assignment_id,
        subjects=catalog.subject_name_columns(),
        sessions=scheduling.note_session_columns(),
    )
    if row is None:
        raise RecordNotFoundError()
    return row


# --- Views ----------------------------------------------------------------------------


def _subject(row: academics.AssignmentRow) -> AssignmentSubject:
    return AssignmentSubject(id=row.assignment.subject_id, name=row.subject_name)


def _person(people: Mapping[uuid.UUID, Any], user_id: uuid.UUID) -> AssignmentPerson:
    account = people.get(user_id)
    return AssignmentPerson(
        display_name="" if account is None else account.display_name
    )


def _state(
    submission: academics.AssignmentSubmission | academics.SubmissionFact,
    deadline: datetime,
    now: datetime,
) -> SubmissionState:
    return rules.submission_state(
        submission.submitted_at, submission.graded_at, deadline, now
    )


def _counts(
    facts: Iterable[academics.AssignmentSubmission | academics.SubmissionFact],
    deadline: datetime,
    now: datetime,
) -> AssignmentCounts:
    states: Counter[str] = Counter()
    recipients = 0
    late = 0
    for fact in facts:
        recipients += 1
        states[_state(fact, deadline, now)] += 1
        if rules.is_late(fact.submitted_at, deadline):
            late += 1
    return AssignmentCounts(
        recipients=recipients,
        pending=states[rules.PENDING],
        missing=states[rules.MISSING],
        submitted=states[rules.SUBMITTED],
        late=late,
        graded=states[rules.GRADED],
    )


def _summary_fields(
    caller: _Caller,
    row: academics.AssignmentRow,
    facts: Sequence[academics.AssignmentSubmission | academics.SubmissionFact],
    people: Mapping[uuid.UUID, Any],
    times: _Clock,
) -> dict[str, Any]:
    assignment = row.assignment
    deadline = times.deadline(assignment)
    relation = caller.staff_relation(assignment, [f.student_id for f in facts])
    has_submissions = any(f.submitted_at is not None for f in facts)
    return {
        "id": assignment.id,
        "version": assignment.version,
        "subject": _subject(row),
        "title": assignment.title,
        "instructions": assignment.instructions,
        "due_on": assignment.due_on,
        "due_time": assignment.due_time,
        "deadline_at": deadline,
        "max_score": assignment.max_score,
        "class_session": (
            None
            if assignment.class_session_id is None or row.session_starts_at is None
            else AssignmentSession(
                id=assignment.class_session_id, starts_at=row.session_starts_at
            )
        ),
        "creator": _person(people, assignment.creator_user_id),
        "is_own": assignment.creator_user_id == caller.principal.user_id,
        "created_at": assignment.created_at,
        "updated_at": assignment.updated_at,
        "counts": _counts(facts, deadline, times.now),
        "can_edit": rules.write_block(relation, "edit") is None,
        "can_delete": rules.write_block(
            relation, "delete", has_submissions=has_submissions
        )
        is None,
    }


def _roster_entry(
    caller: _Caller,
    assignment: academics.Assignment,
    submission: academics.AssignmentSubmission,
    name: str,
    times: _Clock,
) -> RosterEntry:
    deadline = times.deadline(assignment)
    relation = caller.staff_relation(assignment, [submission.student_id])
    submitted = submission.submitted_at is not None
    graded = submission.graded_at is not None
    return RosterEntry(
        student=AssignmentStudent(id=submission.student_id, display_name=name),
        version=submission.version,
        state=_state(submission, deadline, times.now),
        is_late=rules.is_late(submission.submitted_at, deadline),
        submitted_at=submission.submitted_at,
        submitted_via=cast(SubmittedVia | None, submission.submitted_via),
        text=submission.text,
        url=submission.url,
        score=submission.score,
        score_percent=submission.score_percent,
        feedback=submission.feedback,
        graded_at=submission.graded_at,
        grade_amended_at=submission.grade_amended_at,
        last_change_reason=submission.last_change_reason,
        can_record_submission=rules.write_block(relation, "submit", graded=graded)
        is None,
        can_grade=rules.write_block(relation, "grade", submitted=submitted) is None,
    )


async def _assignment_view(
    session: AsyncSession,
    caller: _Caller,
    row: academics.AssignmentRow,
    times: _Clock,
) -> AssignmentView:
    """C6: the roster, the students' and the creator's names in three
    statements whatever the number of recipients."""
    assignment = row.assignment
    submissions = (await academics.submissions_of(session, [assignment.id]))[
        assignment.id
    ]
    students = await families.student_references(
        session, [s.student_id for s in submissions]
    )
    people = await identity.get_account_references(
        session, {assignment.creator_user_id}
    )

    def name(student_id: uuid.UUID) -> str:
        found = students.get(student_id)
        return "" if found is None else found.display_name

    roster = sorted(
        (
            _roster_entry(caller, assignment, s, name(s.student_id), times)
            for s in submissions
        ),
        key=lambda entry: (
            entry.student.display_name.casefold(),
            str(entry.student.id),
        ),
    )
    return AssignmentView(
        **_summary_fields(caller, row, submissions, people, times), roster=roster
    )


def _student_item(
    relation: rules.AssignmentRelation,
    item: academics.StudentRow,
    people: Mapping[uuid.UUID, Any],
    times: _Clock,
) -> StudentAssignment:
    """E2: the closed per-student shape (no user ids, no roster, no change
    reason, no session id)."""
    assignment = item.row.assignment
    submission = item.submission
    deadline = times.deadline(assignment)
    submitted = submission.submitted_at is not None
    graded = submission.graded_at is not None
    passed = times.now > deadline
    can_submit = relation is Relation.STUDENT_SELF and (
        rules.write_block(
            relation,
            "submit",
            submitted=submitted,
            graded=graded,
            deadline_passed=passed,
        )
        is None
    )
    can_grade = relation is Relation.CREATOR and (
        rules.write_block(relation, "grade", submitted=submitted) is None
    )
    return StudentAssignment(
        id=assignment.id,
        subject=_subject(item.row),
        title=assignment.title,
        instructions=assignment.instructions,
        due_on=assignment.due_on,
        due_time=assignment.due_time,
        deadline_at=deadline,
        max_score=assignment.max_score,
        creator=_person(people, assignment.creator_user_id),
        class_session=(
            None
            if item.row.session_starts_at is None
            else StudentAssignmentSession(starts_at=item.row.session_starts_at)
        ),
        submission=StudentSubmission(
            state=_state(submission, deadline, times.now),
            is_late=rules.is_late(submission.submitted_at, deadline),
            submitted_at=submission.submitted_at,
            submitted_via=cast(SubmittedVia | None, submission.submitted_via),
            text=submission.text,
            url=submission.url,
            score=submission.score,
            score_percent=submission.score_percent,
            feedback=submission.feedback,
            graded_at=submission.graded_at,
            updated=submission.grade_amended_at is not None,
            version=submission.version,
        ),
        can_submit=can_submit,
        can_grade=can_grade,
    )


# --- C: create ------------------------------------------------------------------------


async def _active_subject(session: AsyncSession, subject_id: uuid.UUID) -> Any | None:
    """The subject when it and its level are active (``FOR SHARE``), else
    ``None``."""
    subject = await catalog.get_subject_for_reference(session, subject_id, lock=True)
    if (
        subject is None
        or not subject.is_active
        or not subject.education_level_is_active
    ):
        return None
    return subject


def _due_issues(due_on: date, due_time: time | None, times: _Clock) -> list[FieldIssue]:
    """C2 step 7: not before the center's today (nor today at a time that
    has passed), at most 365 days ahead."""
    earliest, latest = rules.due_window(times.today)
    if due_on < earliest or (
        due_on == earliest
        and due_time is not None
        and rules.deadline_instant(due_on, due_time, times.zone) < times.now
    ):
        return [(["body", "due_on"], "due_in_past", "The deadline has already passed.")]
    if due_on > latest:
        return [
            (
                ["body", "due_on"],
                "due_too_far",
                f"At most {rules.DUE_MAX_DAYS} days ahead.",
            )
        ]
    return []


def _recipient_invalid(
    student_ids: Iterable[uuid.UUID],
) -> academics.AssignmentFieldError:
    return _invalid(
        ["body", "student_ids"],
        "recipient_invalid",
        "Choose students you currently teach (in this class, when linked).",
        details={"student_ids": sorted(str(s) for s in student_ids)},
    )


async def _notify_set(
    session: AsyncSession,
    assignment: academics.Assignment,
    student_ids: Iterable[uuid.UUID],
    times: _Clock,
) -> None:
    """E8: one ``academics.assignment_set`` per student, to the student's
    login and their academic recipients."""
    for student_id in sorted(set(student_ids)):
        recipients = await families.academic_notification_recipients(
            session, student_id, today=times.today
        )
        params = AssignmentSetParams(
            student_id=str(student_id),
            assignment_id=str(assignment.id),
            subject_id=str(assignment.subject_id),
            due_on=assignment.due_on.isoformat(),
        )
        for user_id in recipients:
            await administration.create_notification(
                session,
                recipient_user_id=user_id,
                notification_type=SET_NOTIFICATION,
                params=params,
            )


async def create_assignment(
    session: AsyncSession, *, principal: Principal, body: AssignmentCreate
) -> AssignmentView:
    """``POST /api/v1/assignments`` (C2): ``403 assignment_tutor_only`` →
    ``422 subject_unknown`` → the session link (``FOR SHARE``: ``422
    class_session_invalid`` / ``409 session_not_assignable`` / ``422
    subject_mismatch``) → the recipients (``422 recipients_required`` /
    ``recipient_invalid``) → the deadline (``422 due_in_past`` /
    ``due_too_far``) → insert, notify → ``201``."""
    if rules.TUTOR_ROLE not in principal.roles:
        raise academics.AssignmentTutorOnlyError()
    subject = await _active_subject(session, body.subject_id)
    if subject is None:
        raise _invalid(
            ["body", "subject_id"], "subject_unknown", "Choose an active subject."
        )
    caller = await _caller(session, principal)
    participants: frozenset[uuid.UUID] | None = None
    if body.class_session_id is not None:
        context = await scheduling.evaluation_session(
            session, body.class_session_id, share=True
        )
        if context is None or context.tutor_user_id != principal.user_id:
            raise _invalid(
                ["body", "class_session_id"],
                "class_session_invalid",
                "Choose one of your classes.",
            )
        if context.state not in rules.ASSIGNABLE_STATES:
            raise academics.SessionNotAssignableError()
        if context.subject_id != body.subject_id:
            raise _invalid(
                ["body", "subject_id"],
                "subject_mismatch",
                "The class is in another subject.",
            )
        participants = frozenset(context.participant_ids)
    if body.student_ids is None:
        recipients = (
            sorted(participants & caller.assigned) if participants is not None else []
        )
        if not recipients:
            raise _invalid(
                ["body", "student_ids"], "recipients_required", "Choose the students."
            )
    else:
        if not body.student_ids:
            raise _invalid(
                ["body", "student_ids"], "recipients_required", "Choose the students."
            )
        allowed = (
            caller.assigned if participants is None else caller.assigned & participants
        )
        invalid = [s for s in body.student_ids if s not in allowed]
        if invalid:
            raise _recipient_invalid(invalid)
        recipients = list(body.student_ids)
    times = await _clock(session)
    issues = _due_issues(body.due_on, body.due_time, times)
    if issues:
        raise academics.AssignmentFieldError(issues)
    assignment, _ = await academics.create_assignment(
        session,
        creator_user_id=principal.user_id,
        subject_id=body.subject_id,
        class_session_id=body.class_session_id,
        title=body.title,
        instructions=body.instructions,
        due_on=body.due_on,
        due_time=body.due_time,
        max_score=body.max_score,
        student_ids=recipients,
    )
    await _notify_set(session, assignment, recipients, times)
    return await _assignment_view(
        session, caller, await _row(session, assignment.id), times
    )


# --- C4, C5: edit and delete ----------------------------------------------------------


async def _locked_assignment(
    session: AsyncSession,
    principal: Principal,
    assignment_id: uuid.UUID,
    action: rules.AssignmentAction,
    version: int,
) -> tuple[_Caller, academics.Assignment, list[academics.AssignmentSubmission]]:
    """C3 for an assignment-wide write: ``404`` → ``403`` → the assignment
    ``FOR UPDATE`` and its rows (by ``student_id``) → ``409
    version_conflict``."""
    row = await _row(session, assignment_id)
    current = (await academics.submissions_of(session, [assignment_id]))[assignment_id]
    caller = await _caller(session, principal)
    relation = caller.staff_relation(row.assignment, [s.student_id for s in current])
    _ensure_access(relation, action)
    assignment = await academics.get_assignment_for_update(session, assignment_id)
    if assignment is None:
        raise RecordNotFoundError()
    rows = await academics.submissions_for_update(session, assignment_id)
    ensure_version(assignment, version)
    return caller, assignment, rows


async def edit_assignment(
    session: AsyncSession,
    *,
    principal: Principal,
    assignment_id: uuid.UUID,
    body: AssignmentPatch,
) -> AssignmentView:
    """``PATCH /api/v1/assignments/{id}`` (C4, AM1): the creator only. A key
    left out keeps its value (``model_fields_set``); ``due_time`` /
    ``max_score`` ``null`` clear them. ``409 assignment_has_grades`` /
    ``recipient_has_submission`` → ``422`` content → write; added students
    are notified. A body that changes nothing writes nothing."""
    caller, assignment, rows = await _locked_assignment(
        session, principal, assignment_id, "edit", body.version
    )
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    for key in ("title", "instructions", "due_on", "due_time", "max_score"):
        if key in sent and getattr(body, key) != getattr(assignment, key):
            changes[key] = getattr(body, key)
    by_student = {r.student_id: r for r in rows}
    removed = [by_student[s] for s in body.remove_student_ids or [] if s in by_student]
    added = [s for s in body.add_student_ids or [] if s not in by_student]
    # 409: the state of the stored rows.
    if "max_score" in changes and any(r.graded_at is not None for r in rows):
        raise academics.AssignmentHasGradesError()
    submitted = [r.student_id for r in removed if r.submitted_at is not None]
    if submitted:
        raise academics.RecipientHasSubmissionError(submitted)
    # 422: the content.
    if added:
        allowed = caller.assigned
        if assignment.class_session_id is not None:
            context = await scheduling.evaluation_session(
                session, assignment.class_session_id
            )
            allowed = allowed & frozenset(
                () if context is None else context.participant_ids
            )
        invalid = [s for s in added if s not in allowed]
        if invalid:
            raise _invalid(
                ["body", "add_student_ids"],
                "recipient_invalid",
                "Choose students you currently teach (in this class, when linked).",
                details={"student_ids": sorted(str(s) for s in invalid)},
            )
    total = len(rows) - len(removed) + len(added)
    if total == 0:
        raise _invalid(
            ["body", "remove_student_ids"],
            "recipients_required",
            "Homework needs at least one student.",
        )
    if total > rules.RECIPIENTS_MAX:
        raise _invalid(
            ["body", "add_student_ids"],
            "recipients_too_many",
            f"At most {rules.RECIPIENTS_MAX} students.",
        )
    times = await _clock(session)
    if "due_on" in changes or "due_time" in changes:
        issues = _due_issues(
            changes.get("due_on", assignment.due_on),
            changes.get("due_time", assignment.due_time),
            times,
        )
        if issues:
            raise academics.AssignmentFieldError(issues)
    if changes or removed or added:
        await academics.change_assignment(session, assignment, changes)
        if removed:
            await academics.remove_recipients(session, removed)
        if added:
            await academics.add_recipients(session, assignment.id, added)
            await _notify_set(session, assignment, added, times)
    return await _assignment_view(
        session, caller, await _row(session, assignment.id), times
    )


async def delete_assignment(
    session: AsyncSession,
    *,
    principal: Principal,
    assignment_id: uuid.UUID,
    version: int,
) -> None:
    """``DELETE /api/v1/assignments/{id}?version=`` (C5): only while nothing
    is submitted (``409 assignment_has_submissions``); every route then
    answers ``404`` for it."""
    _, assignment, rows = await _locked_assignment(
        session, principal, assignment_id, "delete", version
    )
    _ensure_state(
        rules.state_block(
            "delete", has_submissions=any(r.submitted_at is not None for r in rows)
        )
    )
    await academics.soft_delete_assignment(
        session, assignment, actor_id=principal.user_id, now=clock.now()
    )


# --- D: submit and grade --------------------------------------------------------------


async def _locked_row(
    session: AsyncSession,
    principal: Principal,
    assignment_id: uuid.UUID,
    student_id: uuid.UUID,
    action: rules.AssignmentAction,
) -> tuple[
    _Caller,
    rules.AssignmentRelation,
    academics.Assignment,
    academics.AssignmentSubmission,
]:
    """C3 for one recipient's row: ``404`` (unknown or deleted assignment,
    not a recipient, or not readable) → ``403`` → the assignment ``FOR
    UPDATE`` then the row ``FOR UPDATE``."""
    row = await _row(session, assignment_id)
    if await academics.submission_for(session, assignment_id, student_id) is None:
        raise RecordNotFoundError()
    caller = await _caller(session, principal)
    relation = await _row_relation(
        session, caller, row.assignment, student_id, family=action == "submit"
    )
    _ensure_access(relation, action)
    assignment = await academics.get_assignment_for_update(session, assignment_id)
    if assignment is None:
        raise RecordNotFoundError()
    submission = await academics.get_submission_for_update(
        session, assignment_id, student_id
    )
    if submission is None:
        raise RecordNotFoundError()
    return caller, relation, assignment, submission


async def _one_student_item(
    session: AsyncSession,
    caller: _Caller,
    relation: rules.AssignmentRelation,
    assignment_id: uuid.UUID,
    student_id: uuid.UUID,
    times: _Clock,
) -> StudentAssignment:
    found = await academics.student_rows(
        session,
        student_id,
        [assignment_id],
        subjects=catalog.subject_name_columns(),
        sessions=scheduling.note_session_columns(),
    )
    item = found[assignment_id]
    people = await identity.get_account_references(
        session, {item.row.assignment.creator_user_id}
    )
    return _student_item(relation, item, people, times)


async def submit(
    session: AsyncSession,
    *,
    principal: Principal,
    assignment_id: uuid.UUID,
    student_id: uuid.UUID,
    body: SubmissionPut,
) -> StudentAssignment:
    """``PUT …/students/{student_id}/submission`` (D1). The student: text
    and/or a link (``422 submission_empty``), replaceable until the
    deadline (``409 submission_locked``), a missing one late; the creator
    is notified. The creator (student still assigned): handed-in work, any
    time, content optional; nobody is notified. Graded: ``409
    submission_graded``."""
    caller, relation, assignment, row = await _locked_row(
        session, principal, assignment_id, student_id, "submit"
    )
    student_path = relation is Relation.STUDENT_SELF
    submitted = row.submitted_at is not None
    if submitted and body.version is None:
        raise _invalid(
            ["body", "version"],
            "version_required",
            "Send the submission's version to replace it.",
        )
    if body.version is not None:
        ensure_version(row, body.version)
    times = await _clock(session)
    _ensure_state(
        rules.state_block(
            "submit",
            student_path=student_path,
            submitted=submitted,
            graded=row.graded_at is not None,
            deadline_passed=times.now > times.deadline(assignment),
        )
    )
    issues: list[FieldIssue] = []
    if body.text is not None and len(body.text) > rules.TEXT_MAX:
        issues.append(
            (["body", "text"], "too_long", f"At most {rules.TEXT_MAX} characters.")
        )
    if body.url is not None and rules.validate_url(body.url) is not None:
        issues.append((["body", "url"], "url_invalid", "Write an http or https link."))
    if student_path and body.text is None and body.url is None:
        issues.append((["body"], "submission_empty", "Write an answer or add a link."))
    if issues:
        raise academics.AssignmentFieldError(issues)
    await academics.record_submission(
        session,
        row,
        text=body.text,
        url=body.url,
        via=rules.VIA_STUDENT if student_path else rules.VIA_TUTOR,
        actor_id=principal.user_id,
        now=times.now,
    )
    if student_path:
        await administration.create_notification(
            session,
            recipient_user_id=assignment.creator_user_id,
            notification_type=SUBMITTED_NOTIFICATION,
            params=AssignmentSubmittedParams(
                student_id=str(student_id), assignment_id=str(assignment.id)
            ),
        )
    return await _one_student_item(
        session, caller, relation, assignment.id, student_id, times
    )


def _grade_issues(assignment: academics.Assignment, body: GradePut) -> list[FieldIssue]:
    issues: list[FieldIssue] = []
    if assignment.max_score is None:
        if body.score is not None:
            issues.append(
                (
                    ["body", "score"],
                    "score_not_allowed",
                    "This homework is graded with feedback only.",
                )
            )
        if body.feedback is None:
            issues.append(
                (["body", "feedback"], "feedback_required", "Write feedback.")
            )
    elif body.score is None:
        issues.append((["body", "score"], "score_required", "Give a score."))
    elif not Decimal(0) <= body.score <= Decimal(assignment.max_score):
        issues.append(
            (
                ["body", "score"],
                "score_out_of_range",
                "The score is 0 to the maximum score.",
            )
        )
    if body.feedback is not None and len(body.feedback) > rules.FEEDBACK_MAX:
        issues.append(
            (
                ["body", "feedback"],
                "too_long",
                f"At most {rules.FEEDBACK_MAX} characters.",
            )
        )
    return issues


async def grade(
    session: AsyncSession,
    *,
    principal: Principal,
    assignment_id: uuid.UUID,
    student_id: uuid.UUID,
    body: GradePut,
) -> RosterEntry:
    """``PUT …/students/{student_id}/grade`` (D2): the creator only, student
    still assigned; a submitted row (``409 submission_required``). The first
    grade notifies the student and their academic recipients; a re-grade
    needs a change reason (internal) and notifies nobody; an unchanged
    re-grade writes nothing."""
    caller, _, assignment, row = await _locked_row(
        session, principal, assignment_id, student_id, "grade"
    )
    ensure_version(row, body.version)
    _ensure_state(rules.state_block("grade", submitted=row.submitted_at is not None))
    issues = _grade_issues(assignment, body)
    if issues:
        raise academics.AssignmentFieldError(issues)
    regrade = row.graded_at is not None
    unchanged = regrade and row.score == body.score and row.feedback == body.feedback
    times = await _clock(session)
    if not unchanged:
        reason = body.change_reason
        if regrade and (reason is None or len(reason) > rules.REASON_MAX):
            raise _invalid(
                ["body", "change_reason"],
                "change_reason_required",
                "Say why the grade changes (1-500 characters).",
            )
        percent = (
            None
            if body.score is None or assignment.max_score is None
            else rules.score_percent(body.score, Decimal(assignment.max_score))
        )
        await academics.grade_submission(
            session,
            row,
            score=body.score,
            score_percent=percent,
            feedback=body.feedback,
            actor_id=principal.user_id,
            now=times.now,
            today=times.today,
            change_reason=reason if regrade else None,
        )
        if not regrade:
            recipients = await families.academic_notification_recipients(
                session, student_id, today=times.today
            )
            params = AssignmentGradedParams(
                student_id=str(student_id),
                assignment_id=str(assignment.id),
                subject_id=str(assignment.subject_id),
            )
            for user_id in recipients:
                await administration.create_notification(
                    session,
                    recipient_user_id=user_id,
                    notification_type=GRADED_NOTIFICATION,
                    params=params,
                )
    students = await families.student_references(session, [student_id])
    found = students.get(student_id)
    return _roster_entry(
        caller, assignment, row, "" if found is None else found.display_name, times
    )


# --- F: the working routes ------------------------------------------------------------


async def get_assignment(
    session: AsyncSession, principal: Principal, assignment_id: uuid.UUID
) -> AssignmentView:
    """``GET /api/v1/assignments/{id}`` (F1): the creator (current or
    former) and ``assignment.review`` holders; anyone else the ``404``. At
    most 8 statements whatever the number of recipients."""
    row = await _row(session, assignment_id)
    reviewer = principal.has(rules.ASSIGNMENT_REVIEW)
    creator = (
        rules.TUTOR_ROLE in principal.roles
        and row.assignment.creator_user_id == principal.user_id
    )
    if not reviewer and not creator:
        raise RecordNotFoundError()
    caller = (
        await _caller(session, principal)
        if creator
        else _Caller(principal, frozenset())
    )
    return await _assignment_view(session, caller, row, await _clock(session))


async def list_assignments(
    session: AsyncSession,
    principal: Principal,
    *,
    student_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    class_session_id: uuid.UUID | None,
    due: Due | None,
    cursor: str | None,
    limit: int,
) -> AssignmentList:
    """``GET /api/v1/assignments`` (F1): a tutor the assignments they set
    (current and former students); ``assignment.review`` holders every one;
    anyone else ``403 permission_denied``. At most 8 statements."""
    reviewer = principal.has(rules.ASSIGNMENT_REVIEW)
    tutor = rules.TUTOR_ROLE in principal.roles
    if not reviewer and not tutor:
        raise PermissionDeniedError()
    times = await _clock(session)
    page = await academics.assignment_working_page(
        session,
        subjects=catalog.subject_name_columns(),
        sessions=scheduling.note_session_columns(),
        creator_user_id=None if reviewer else principal.user_id,
        student_id=student_id,
        subject_id=subject_id,
        class_session_id=class_session_id,
        due=due,
        today=times.today,
        cursor=cursor,
        limit=limit,
    )
    items: list[AssignmentSummary] = []
    if page.rows:
        caller = await _caller(session, principal)
        ids = [r.assignment.id for r in page.rows]
        facts: dict[uuid.UUID, list[academics.SubmissionFact]] = {i: [] for i in ids}
        for fact in await academics.submission_facts(session, ids):
            facts[fact.assignment_id].append(fact)
        people = await identity.get_account_references(
            session, {r.assignment.creator_user_id for r in page.rows}
        )
        items = [
            AssignmentSummary(
                **_summary_fields(caller, r, facts[r.assignment.id], people, times)
            )
            for r in page.rows
        ]
    return AssignmentList(items=items, next_cursor=page.next_cursor)


# --- E: the per-student reads ---------------------------------------------------------


@dataclass(frozen=True)
class _StudentScope:
    caller: _Caller
    creator_user_id: uuid.UUID | None
    """Only this creator's assignments (a former creator), else every one."""
    student_self: bool


async def _student_scope(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> _StudentScope:
    """E1: ``assignment.review`` holders, tutors currently teaching the
    student, the student and the linked guardians with academic access read
    every assignment of the student (``403 student_consent_required`` for a
    linked guardian of an adult without the consent); a former creator only
    theirs; anyone else, and an unknown id, the ``404``."""
    caller = await _caller(session, principal)
    student_self = await _is_student_self(session, principal, student_id)
    if caller.reviewer:
        if not await families.student_references(session, [student_id]):
            raise RecordNotFoundError()
        return _StudentScope(caller, None, student_self)
    if student_id in caller.assigned or student_self:
        return _StudentScope(caller, None, student_self)
    try:
        await scheduling.ensure_family_student_access(session, principal, student_id)
        return _StudentScope(caller, None, student_self)
    except RecordNotFoundError:
        if not caller.tutor:
            raise
    if await academics.has_own_assignment(session, student_id, principal.user_id):
        return _StudentScope(caller, principal.user_id, student_self)
    raise RecordNotFoundError()


def _item_relation(
    scope: _StudentScope, assignment: academics.Assignment, student_id: uuid.UUID
) -> rules.AssignmentRelation:
    caller = scope.caller
    if caller.is_creator(assignment):
        return caller.staff_relation(assignment, [student_id])
    if scope.student_self:
        return Relation.STUDENT_SELF
    return caller.staff_relation(assignment, [student_id])


def _order_key(fact: academics.StudentFact) -> tuple[int, int, int, str]:
    """E3: ``due_on`` desc, ``due_time`` desc nulls first, ``id``."""
    if fact.due_time is None:
        timed, seconds = 0, 0
    else:
        timed = 1
        seconds = (
            -(
                fact.due_time.hour * 3600
                + fact.due_time.minute * 60
                + fact.due_time.second
            )
            * 1_000_000
            - fact.due_time.microsecond
        )
    return (-fact.due_on.toordinal(), timed, seconds, str(fact.assignment_id))


def _cursor_of(fact: academics.StudentFact) -> str:
    return encode_cursor(
        [
            fact.due_on.isoformat(),
            None if fact.due_time is None else fact.due_time.isoformat(),
            str(fact.assignment_id),
        ]
    )


def _after(cursor: str) -> tuple[int, int, int, str]:
    due_on, due_time, assignment_id = decode_cursor(
        cursor, types=(str, (str, type(None)), str)
    )
    try:
        fact = academics.StudentFact(
            assignment_id=uuid.UUID(str(assignment_id)),
            subject_id=uuid.UUID(int=0),
            due_on=date.fromisoformat(str(due_on)),
            due_time=None if due_time is None else time.fromisoformat(str(due_time)),
            submitted_at=None,
            graded_at=None,
        )
    except ValueError:
        raise InvalidCursorError() from None
    return _order_key(fact)


def _check_range(from_: date | None, to: date | None) -> None:
    if from_ is not None and to is not None and from_ > to:
        raise _invalid(["query", "from"], "range_invalid", "'from' is after 'to'.")


async def list_student_assignments(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    state: SubmissionState | None,
    subject_id: uuid.UUID | None,
    from_: date | None,
    to: date | None,
    cursor: str | None,
    limit: int,
) -> StudentAssignmentList:
    """``GET /api/v1/students/{id}/assignments`` (E1-E3): the student's
    homework with their own row only; the ``summary`` counts the filtered
    set (ignoring ``state`` and the cursor). At most 7 statements, the same
    for 1 and 20 items."""
    _check_range(from_, to)
    scope = await _student_scope(session, principal, student_id)
    times = await _clock(session)
    facts = await academics.student_facts(
        session,
        student_id,
        creator_user_id=scope.creator_user_id,
        subject_id=subject_id,
        from_=from_,
        to=to,
    )
    states: dict[uuid.UUID, str] = {}
    summary: Counter[str] = Counter()
    for fact in facts:
        deadline = rules.deadline_instant(fact.due_on, fact.due_time, times.zone)
        found = rules.submission_state(
            fact.submitted_at, fact.graded_at, deadline, times.now
        )
        states[fact.assignment_id] = found
        summary[found] += 1
    chosen = sorted(
        (f for f in facts if state is None or states[f.assignment_id] == state),
        key=_order_key,
    )
    if cursor is not None:
        position = _after(cursor)
        chosen = [f for f in chosen if _order_key(f) > position]
    page = chosen[:limit]
    next_cursor = _cursor_of(page[-1]) if len(chosen) > limit and page else None
    items: list[StudentAssignment] = []
    if page:
        found_rows = await academics.student_rows(
            session,
            student_id,
            [f.assignment_id for f in page],
            subjects=catalog.subject_name_columns(),
            sessions=scheduling.note_session_columns(),
        )
        people = await identity.get_account_references(
            session, {r.row.assignment.creator_user_id for r in found_rows.values()}
        )
        for fact in page:
            item = found_rows.get(fact.assignment_id)
            if item is None:
                continue
            items.append(
                _student_item(
                    _item_relation(scope, item.row.assignment, student_id),
                    item,
                    people,
                    times,
                )
            )
    return StudentAssignmentList(
        items=items,
        summary=AssignmentStateSummary(
            pending=summary[rules.PENDING],
            missing=summary[rules.MISSING],
            submitted=summary[rules.SUBMITTED],
            graded=summary[rules.GRADED],
        ),
        next_cursor=next_cursor,
    )


async def get_student_assignment(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> StudentAssignment:
    """``GET /api/v1/students/{id}/assignments/{assignment_id}`` (E1, E2):
    the same access as the list; an assignment the student isn't a
    recipient of (or a former creator's other assignments) the ``404``."""
    scope = await _student_scope(session, principal, student_id)
    times = await _clock(session)
    found = await academics.student_rows(
        session,
        student_id,
        [assignment_id],
        subjects=catalog.subject_name_columns(),
        sessions=scheduling.note_session_columns(),
    )
    item = found.get(assignment_id)
    if item is None or (
        scope.creator_user_id is not None
        and item.row.assignment.creator_user_id != scope.creator_user_id
    ):
        raise RecordNotFoundError()
    people = await identity.get_account_references(
        session, {item.row.assignment.creator_user_id}
    )
    return _student_item(
        _item_relation(scope, item.row.assignment, student_id),
        item,
        people,
        times,
    )
