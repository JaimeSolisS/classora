"""Progress reports (#83 M5-16; spec §2 "progress reports prepared by
tutors", §4 "progress reports … require admin approval before family
visibility", §9, §16, W5.2, W5.3, W5.4; architecture §4.1, §4.2).

A tutor who currently teaches a student writes a weekly, monthly or
on-demand **progress report** about them: a narrative plus **facts** the
server compiles from attendance, final evaluations, homework and learning
plans (``academics.progress_report_facts``), frozen at each submit. A
reviewer (``progress_report.review``: Owner, Administrator, Academic
Coordinator) **approves** exactly the submitted version, **requests
changes** with a reason or **retracts** it. Only approved reports reach the
student and the guardians with academic access, through one closed
family-safe shape; drafts, reports under review or sent back, retracted
reports and every reason stay internal (``tests/integration/
test_session_notes_leak.py``, the shared family leak walk).

The routes are in :mod:`app.workflows.progress_reports_router`; the bodies
and views in :mod:`app.workflows.progress_report_schemas`; the approval
type in :mod:`app.workflows.progress_report_approval`.

**Who** (E4, E5; the pure rules in ``academics.progress_report_rules``):

- a ``tutor``-role user with the student in their current assignments
  (``scheduling.service.subject_ids_for_tutor_student`` not empty) creates
  reports (a subject report needs a subject they teach the student) and is
  the only one who edits, submits and deletes them;
- once the student is no longer theirs, the author reads their reports
  read-only (``403 progress_report_read_only``);
- ``progress_report.review`` holders read every non-draft report and decide
  it, never their own unless they hold the Owner role (``403
  progress_report_self_review``); they don't author (``403
  progress_report_tutor_only``, ``progress_report_author_only``);
- approved reports (R10, R11): holders, the student's current tutors, a
  former author (their own), the student and the guardians with academic
  access (#67 rule: ``403 student_consent_required`` for a linked guardian
  of an adult without the consent);
- anyone else, and an unknown id, the ``404`` of an unknown id.

**Orders:** create C2 (``404`` → ``403 progress_report_tutor_only`` →
``422 subject_unknown`` → ``403 report_subject_not_assigned`` → ``422
period_invalid`` → ``422 period_in_future`` → the draft, ``409
progress_report_exists``); edit C3, delete C4 and the workflow D1 (``404``
→ ``403`` → the report ``FOR UPDATE`` → ``409 version_conflict`` → ``409``
state → ``422`` content → the write → the approval request (report, then
request) → the audit event and notifications).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.progress_report_approval import PROGRESS_REPORT_TYPE
from app.workflows.progress_report_schemas import (
    ProgressReportApproved,
    ProgressReportChangesRequested,
    ProgressReportCreate,
    ProgressReportFacts,
    ProgressReportFamilyListItem,
    ProgressReportFamilyPage,
    ProgressReportFamilyView,
    ProgressReportListItem,
    ProgressReportPage,
    ProgressReportPerson,
    ProgressReportReason,
    ProgressReportRetracted,
    ProgressReportStudent,
    ProgressReportSubject,
    ProgressReportUpdate,
    ProgressReportView,
    ReportKind,
    ReportStatus,
)

rules = academics.progress_report_rules
Relation = rules.ReportRelation

RECORD_TYPE: Final = "progress_reports"
SUBMITTED_EVENT: Final = "academics.progress_report_submitted"
APPROVED_EVENT: Final = "academics.progress_report_approved"
CHANGES_REQUESTED_EVENT: Final = "academics.progress_report_changes_requested"
RETRACTED_EVENT: Final = "academics.progress_report_retracted"

APPROVED_NOTIFICATION: Final = "academics.progress_report_approved"
CHANGES_REQUESTED_NOTIFICATION: Final = "academics.progress_report_changes_requested"
RETRACTED_NOTIFICATION: Final = "academics.progress_report_retracted"


class ProgressReportApprovedParams(BaseModel):
    """``academics.progress_report_approved`` (E7, AM3): ids, the kind and
    the period only; ``audience`` is ``author`` for the report's author and
    ``family`` for the student and the guardians. Never a narrative, a
    number or a reason."""

    student_id: str
    report_id: str
    kind: str
    period_start: str
    period_end: str
    audience: Literal["author", "family"]


class ProgressReportDecisionParams(BaseModel):
    """``academics.progress_report_changes_requested`` / ``_retracted`` (E7):
    ids only, never the reason."""

    student_id: str
    report_id: str


def register_progress_report_notifications() -> None:
    """Register the three types (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        APPROVED_NOTIFICATION, ProgressReportApprovedParams
    )
    administration.register_notification_type(
        CHANGES_REQUESTED_NOTIFICATION, ProgressReportDecisionParams
    )
    administration.register_notification_type(
        RETRACTED_NOTIFICATION, ProgressReportDecisionParams
    )


type FieldIssue = tuple[list[str | int], str, str]

NARRATIVE: Final = ("summary", "strengths", "areas_for_improvement", "next_steps")


# --- Access ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Caller:
    """The caller's facts about one student: ``subjects`` are the subjects
    in which they currently teach the student (empty without the ``tutor``
    role, or when the student isn't one of their current assignments)."""

    principal: Principal
    subjects: frozenset[uuid.UUID]

    @property
    def reviewer(self) -> bool:
        return self.principal.has(rules.PROGRESS_REPORT_REVIEW)

    @property
    def tutor(self) -> bool:
        return rules.TUTOR_ROLE in self.principal.roles

    @property
    def owner(self) -> bool:
        return rules.OWNER_ROLE in self.principal.roles

    @property
    def current(self) -> bool:
        """A tutor-role user currently teaching the student."""
        return self.tutor and bool(self.subjects)

    def relation(self, report: academics.ProgressReport) -> Relation:
        return rules.report_relation(
            is_author=report.author_user_id == self.principal.user_id,
            tutor=self.tutor,
            student_current=self.current,
            reviewer=self.reviewer,
        )

    def readable(self, report: academics.ProgressReport) -> bool:
        return rules.can_read(
            self.relation(report),
            status=report.status,
            deleted=report.deleted_at is not None,
            reviewer=self.reviewer,
        )


async def _caller(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> _Caller:
    """One statement for a tutor-role user; none otherwise."""
    subjects: frozenset[uuid.UUID] = frozenset()
    if rules.TUTOR_ROLE in principal.roles:
        subjects = await scheduling.subject_ids_for_tutor_student(
            session, principal.user_id, student_id, now=clock.now()
        )
    return _Caller(principal, subjects)


async def _readable_report(
    session: AsyncSession, principal: Principal, report_id: uuid.UUID
) -> tuple[_Caller, academics.ReportRow]:
    """The ``404`` step of every working route on one report (the matrix)."""
    row = await academics.report_with_subject(
        session, report_id, subjects=catalog.subject_name_columns()
    )
    if row is None:
        raise RecordNotFoundError()
    caller = await _caller(session, principal, row.report.student_id)
    if not caller.readable(row.report):
        raise RecordNotFoundError()
    return caller, row


_ACCESS_ERRORS: Final[Mapping[str, type[Exception]]] = {
    rules.NOT_FOUND: RecordNotFoundError,
    rules.PERMISSION_DENIED: PermissionDeniedError,
    rules.AUTHOR_ONLY: academics.ProgressReportAuthorOnlyError,
    rules.READ_ONLY: academics.ProgressReportReadOnlyError,
    rules.SELF_REVIEW: academics.ProgressReportSelfReviewError,
}
_STATE_ERRORS: Final[Mapping[str, type[Exception]]] = {
    rules.UNDER_REVIEW: academics.ProgressReportUnderReviewError,
    rules.NOT_SUBMITTED: academics.ProgressReportNotSubmittedError,
    rules.REPORT_CLOSED: academics.ProgressReportClosedError,
}


async def _locked(
    session: AsyncSession,
    principal: Principal,
    report_id: uuid.UUID,
    action: rules.ReportAction,
    version: int,
) -> tuple[_Caller, academics.ReportRow]:
    """C3 / C4 / D1 steps 3-7: readable, allowed, the report ``FOR UPDATE``,
    the version, the state. The row carries the locked report."""
    caller, row = await _readable_report(session, principal, report_id)
    blocked = rules.access_block(
        caller.relation(row.report),
        action,
        reviewer=caller.reviewer,
        is_owner_role=caller.owner,
    )
    if blocked is not None:
        raise _ACCESS_ERRORS[blocked]()
    report = await academics.get_report_for_update(session, report_id)
    if report is None:
        raise RecordNotFoundError()
    ensure_version(report, version)
    state = rules.state_block(report.status, action)
    if state is not None:
        raise _STATE_ERRORS[state]()
    return caller, academics.ReportRow(report, row.subject_name)


async def _request_id(session: AsyncSession, report_id: uuid.UUID) -> uuid.UUID:
    request_id = await administration.approval_request_id_for_subject(
        session, request_type=PROGRESS_REPORT_TYPE, subject_id=report_id
    )
    if request_id is None:  # pragma: no cover - the first submit creates it
        raise RecordNotFoundError()
    return request_id


async def _author_current(
    session: AsyncSession, report: academics.ProgressReport
) -> bool:
    """Whether the author still teaches the report's student. One
    statement."""
    return bool(
        await scheduling.subject_ids_for_tutor_student(
            session, report.author_user_id, report.student_id, now=clock.now()
        )
    )


# --- Facts ----------------------------------------------------------------------------


async def compile_facts(
    session: AsyncSession, row: academics.ReportRow
) -> dict[str, Any]:
    """B2: the facts of the report's student, period and subject, now. At
    most six statements (the zone, attendance, four academic reads)."""
    report = row.report
    zone = await administration.get_center_time_zone(session)
    now = clock.now()
    attendance = await scheduling.student_attendance_summary(
        session,
        report.student_id,
        report.period_start,
        report.period_end,
        subject_id=report.subject_id,
        time_zone=zone,
        now=now,
    )
    academic = await academics.academic_facts(
        session,
        report.student_id,
        report.period_start,
        report.period_end,
        report.subject_id,
        subjects=catalog.subject_name_columns(),
        zone=zone,
        now=now,
    )
    return academics.facts_document(
        start=report.period_start,
        end=report.period_end,
        subject=(
            None
            if report.subject_id is None
            else (report.subject_id, row.subject_name or "")
        ),
        attendance={
            "sessions": attendance.sessions,
            "present": attendance.present,
            "late": attendance.late,
            "absent": attendance.absent,
            "not_recorded": attendance.not_recorded,
        },
        academic=academic,
    )


def _facts(data: Mapping[str, Any] | None) -> ProgressReportFacts | None:
    return None if data is None else ProgressReportFacts.model_validate(data)


# --- Views ----------------------------------------------------------------------------


def _subject(row: academics.ReportRow) -> ProgressReportSubject | None:
    report = row.report
    if report.subject_id is None:
        return None
    return ProgressReportSubject(id=report.subject_id, name=row.subject_name or "")


def _person(
    people: Mapping[uuid.UUID, Any], user_id: uuid.UUID
) -> ProgressReportPerson:
    account = people.get(user_id)
    return ProgressReportPerson(
        display_name="" if account is None else account.display_name
    )


def _student(
    students: Mapping[uuid.UUID, Any], student_id: uuid.UUID
) -> ProgressReportStudent:
    found = students.get(student_id)
    return ProgressReportStudent(
        id=student_id, display_name="" if found is None else found.display_name
    )


async def _view(
    session: AsyncSession, caller: _Caller, row: academics.ReportRow
) -> ProgressReportView:
    """E1: at most 6 statements without the preview (the report, the
    caller's subjects, the student, the people, the author's assignment),
    at most 14 with it."""
    report = row.report
    relation = caller.relation(report)
    students = await families.student_references(session, [report.student_id])
    people = await identity.get_account_references(
        session,
        {
            user_id
            for user_id in (
                report.author_user_id,
                report.changes_requested_by_user_id,
                report.approved_by_user_id,
                report.retracted_by_user_id,
            )
            if user_id is not None
        },
    )
    if relation is Relation.AUTHOR:
        author_current = True
    elif caller.reviewer and report.status == rules.SUBMITTED:
        author_current = await _author_current(session, report)
    else:
        author_current = False
    flags = rules.can_flags(
        relation,
        report.status,
        reviewer=caller.reviewer,
        is_owner_role=caller.owner,
        author_current=author_current,
    )
    preview = None
    if relation is Relation.AUTHOR and report.status in rules.EDITABLE:
        preview = await compile_facts(session, row)
    changes = None
    if report.changes_requested_at is not None:
        assert report.changes_requested_by_user_id is not None  # noqa: S101
        assert report.changes_requested_reason is not None  # noqa: S101
        changes = ProgressReportChangesRequested(
            at=report.changes_requested_at,
            by=_person(people, report.changes_requested_by_user_id),
            reason=report.changes_requested_reason,
        )
    approved = None
    if report.approved_at is not None:
        assert report.approved_by_user_id is not None  # noqa: S101
        approved = ProgressReportApproved(
            at=report.approved_at, by=_person(people, report.approved_by_user_id)
        )
    retracted = None
    if report.retracted_at is not None:
        assert report.retracted_by_user_id is not None  # noqa: S101
        assert report.retract_reason is not None  # noqa: S101
        retracted = ProgressReportRetracted(
            at=report.retracted_at,
            by=_person(people, report.retracted_by_user_id),
            reason=report.retract_reason,
        )
    return ProgressReportView(
        id=report.id,
        version=report.version,
        status=cast(ReportStatus, report.status),
        student=_student(students, report.student_id),
        author=_person(people, report.author_user_id),
        is_own=report.author_user_id == caller.principal.user_id,
        kind=cast(ReportKind, report.kind),
        period_start=report.period_start,
        period_end=report.period_end,
        subject=_subject(row),
        summary=report.summary,
        strengths=report.strengths,
        areas_for_improvement=report.areas_for_improvement,
        next_steps=report.next_steps,
        facts=_facts(report.facts),
        facts_computed_at=report.facts_computed_at,
        facts_preview=_facts(preview),
        submitted_at=report.submitted_at,
        changes_requested=changes,
        approved=approved,
        retracted=retracted,
        created_at=report.created_at,
        updated_at=report.updated_at,
        can_edit=flags.can_edit,
        can_submit=flags.can_submit,
        can_delete=flags.can_delete,
        can_approve=flags.can_approve,
        can_request_changes=flags.can_request_changes,
        can_retract=flags.can_retract,
    )


# --- C: create, edit, delete ----------------------------------------------------------


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


def _period_issue(body: ProgressReportCreate) -> FieldIssue:
    """Where ``period_invalid`` points: the start when a weekly report
    doesn't start on a Monday or a monthly one on the 1st; else the end."""
    start = body.period_start
    wrong_start = (body.kind == rules.WEEKLY and start.isoweekday() != 1) or (
        body.kind == rules.MONTHLY and start.day != 1
    )
    return (
        ["body", "period_start" if wrong_start else "period_end"],
        rules.PERIOD_INVALID,
        "Choose a Monday for a weekly report, the 1st for a monthly one, or"
        " an on-demand period of at most 366 days that ends after it starts.",
    )


async def create_report(
    session: AsyncSession, *, principal: Principal, body: ProgressReportCreate
) -> ProgressReportView:
    """``POST /api/v1/progress-reports`` (C2). Nobody is notified."""
    if not await families.student_references(session, [body.student_id]):
        raise RecordNotFoundError()
    caller = await _caller(session, principal, body.student_id)
    if not caller.current:
        if caller.reviewer:
            raise academics.ProgressReportTutorOnlyError()
        raise RecordNotFoundError()
    subject_name: str | None = None
    if body.subject_id is not None:
        subject = await _active_subject(session, body.subject_id)
        if subject is None:
            raise academics.ProgressReportFieldError(
                [
                    (
                        ["body", "subject_id"],
                        "subject_unknown",
                        "Choose an active subject.",
                    )
                ]
            )
        if body.subject_id not in caller.subjects:
            raise academics.ReportSubjectNotAssignedError()
        subject_name = subject.name
    period = rules.period_for(body.kind, body.period_start, body.period_end)
    if isinstance(period, str):
        raise academics.ProgressReportFieldError([_period_issue(body)])
    start, end = period
    if rules.period_in_future(start, await administration.center_today(session)):
        raise academics.ProgressReportFieldError(
            [
                (
                    ["body", "period_start"],
                    "period_in_future",
                    "A report can't start after today.",
                )
            ]
        )
    report = await academics.create_report(
        session,
        student_id=body.student_id,
        author_user_id=principal.user_id,
        subject_id=body.subject_id,
        kind=body.kind,
        period_start=start,
        period_end=end,
        narrative={field: getattr(body, field) for field in NARRATIVE},
    )
    return await _view(session, caller, academics.ReportRow(report, subject_name))


async def update_report(
    session: AsyncSession,
    *,
    principal: Principal,
    report_id: uuid.UUID,
    body: ProgressReportUpdate,
) -> ProgressReportView:
    """``PATCH /api/v1/progress-reports/{id}`` (C3): the narrative fields
    sent; an unchanged body writes nothing."""
    caller, row = await _locked(session, principal, report_id, "edit", body.version)
    await academics.change_narrative(
        session,
        row.report,
        {
            field: getattr(body, field)
            for field in NARRATIVE
            if field in body.model_fields_set
        },
    )
    return await _view(session, caller, row)


async def delete_report(
    session: AsyncSession, *, principal: Principal, report_id: uuid.UUID, version: int
) -> None:
    """``DELETE /api/v1/progress-reports/{id}?version=`` (C4): a draft or a
    report sent back; its open inbox item is cancelled."""
    _, row = await _locked(session, principal, report_id, "delete", version)
    report = row.report
    if report.status == rules.CHANGES_REQUESTED:
        await administration.cancel_approval_request(
            session,
            request_id=await _request_id(session, report.id),
            by_user_id=principal.user_id,
        )
    await academics.soft_delete_report(
        session, report, by_user_id=principal.user_id, now=clock.now()
    )


# --- D: the workflow ------------------------------------------------------------------


def _reason_issues(reason: str) -> tuple[str, list[FieldIssue]]:
    cleaned = reason.strip()
    if not cleaned:
        return cleaned, [(["body", "reason"], "reason_required", "Give a reason.")]
    if len(cleaned) > rules.REASON_MAX:
        return cleaned, [
            (["body", "reason"], "too_long", f"At most {rules.REASON_MAX} characters.")
        ]
    return cleaned, []


async def _notify(
    session: AsyncSession,
    *,
    actor: Principal,
    recipients: Mapping[uuid.UUID, BaseModel],
    notification_type: str,
) -> None:
    """One notification per recipient, never to the actor themself."""
    for user_id, params in recipients.items():
        if user_id == actor.user_id:
            continue
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=notification_type,
            params=params,
        )


async def submit_report(
    session: AsyncSession, *, principal: Principal, report_id: uuid.UUID, version: int
) -> ProgressReportView:
    """``POST …/submit`` (D2): the facts compiled and frozen; the inbox item
    opened (first submit) or sent back to pending (resubmit). Nobody is
    notified (the inbox is the reviewers' queue)."""
    caller, row = await _locked(session, principal, report_id, "submit", version)
    report = row.report
    if report.summary is None:
        raise academics.ProgressReportSummaryRequiredError()
    previous = report.status
    facts = await compile_facts(session, row)
    await academics.submit_report(session, report, facts=facts, now=clock.now())
    if previous == rules.DRAFT:
        await administration.open_approval_request(
            session,
            request_type=PROGRESS_REPORT_TYPE,
            subject_id=report.id,
            requester_user_id=report.author_user_id,
        )
    else:
        await administration.resubmit_approval_request(
            session,
            request_id=await _request_id(session, report.id),
            by_user_id=principal.user_id,
        )
    record_audit_event(
        session,
        action=SUBMITTED_EVENT,
        record_type=RECORD_TYPE,
        record_id=report.id,
        details={"report_id": report.id, "previous_status": previous},
    )
    return await _view(session, caller, row)


async def approve_report(
    session: AsyncSession, *, principal: Principal, report_id: uuid.UUID, version: int
) -> ProgressReportView:
    """``POST …/approve`` (D3): ``approved_for_family``; the author, the
    student and the guardians with academic access are notified."""
    caller, row = await _locked(session, principal, report_id, "approve", version)
    report = row.report
    await academics.approve_report(
        session, report, by_user_id=principal.user_id, now=clock.now()
    )
    await administration.decide_approval_request(
        session,
        principal=principal,
        request_id=await _request_id(session, report.id),
        action="approve",
    )
    record_audit_event(
        session,
        action=APPROVED_EVENT,
        record_type=RECORD_TYPE,
        record_id=report.id,
        details={"report_id": report.id, "approver_user_id": principal.user_id},
    )
    family = await families.academic_notification_recipients(
        session, report.student_id, today=await administration.center_today(session)
    )

    def params(audience: Literal["author", "family"]) -> ProgressReportApprovedParams:
        return ProgressReportApprovedParams(
            student_id=str(report.student_id),
            report_id=str(report.id),
            kind=report.kind,
            period_start=report.period_start.isoformat(),
            period_end=report.period_end.isoformat(),
            audience=audience,
        )

    recipients: dict[uuid.UUID, BaseModel] = {
        user_id: params("family") for user_id in family
    }
    recipients[report.author_user_id] = params("author")
    await _notify(
        session,
        actor=principal,
        recipients=recipients,
        notification_type=APPROVED_NOTIFICATION,
    )
    return await _view(session, caller, row)


async def request_changes(
    session: AsyncSession,
    *,
    principal: Principal,
    report_id: uuid.UUID,
    body: ProgressReportReason,
) -> ProgressReportView:
    """``POST …/request-changes`` (D4): back to the author, who still
    teaches the student (``409 progress_report_author_not_current``
    otherwise), with the reason; the author is notified."""
    caller, row = await _locked(
        session, principal, report_id, "request_changes", body.version
    )
    report = row.report
    if not await _author_current(session, report):
        raise academics.ProgressReportAuthorNotCurrentError()
    reason, issues = _reason_issues(body.reason)
    if issues:
        raise academics.ProgressReportFieldError(issues)
    await academics.request_report_changes(
        session, report, by_user_id=principal.user_id, reason=reason, now=clock.now()
    )
    await administration.decide_approval_request(
        session,
        principal=principal,
        request_id=await _request_id(session, report.id),
        action="request_changes",
        reason=reason,
    )
    record_audit_event(
        session,
        action=CHANGES_REQUESTED_EVENT,
        record_type=RECORD_TYPE,
        record_id=report.id,
        details={"report_id": report.id, "reason": reason},
    )
    await _notify(
        session,
        actor=principal,
        recipients={
            report.author_user_id: ProgressReportDecisionParams(
                student_id=str(report.student_id), report_id=str(report.id)
            )
        },
        notification_type=CHANGES_REQUESTED_NOTIFICATION,
    )
    return await _view(session, caller, row)


async def retract_report(
    session: AsyncSession,
    *,
    principal: Principal,
    report_id: uuid.UUID,
    body: ProgressReportReason,
) -> ProgressReportView:
    """``POST …/retract`` (D5): a submitted or approved report becomes
    ``retracted`` (final, never family-visible); a submitted one's inbox
    item is rejected with the reason. The author is notified (families are
    not)."""
    caller, row = await _locked(session, principal, report_id, "retract", body.version)
    report = row.report
    reason, issues = _reason_issues(body.reason)
    if issues:
        raise academics.ProgressReportFieldError(issues)
    previous = report.status
    await academics.retract_report(
        session, report, by_user_id=principal.user_id, reason=reason, now=clock.now()
    )
    if previous == rules.SUBMITTED:
        await administration.decide_approval_request(
            session,
            principal=principal,
            request_id=await _request_id(session, report.id),
            action="reject",
            reason=reason,
        )
    record_audit_event(
        session,
        action=RETRACTED_EVENT,
        record_type=RECORD_TYPE,
        record_id=report.id,
        details={"report_id": report.id, "reason": reason, "previous_status": previous},
    )
    await _notify(
        session,
        actor=principal,
        recipients={
            report.author_user_id: ProgressReportDecisionParams(
                student_id=str(report.student_id), report_id=str(report.id)
            )
        },
        notification_type=RETRACTED_NOTIFICATION,
    )
    return await _view(session, caller, row)


# --- E: working reads -----------------------------------------------------------------


async def get_report(
    session: AsyncSession, principal: Principal, report_id: uuid.UUID
) -> ProgressReportView:
    """``GET /api/v1/progress-reports/{id}`` (E1)."""
    caller, row = await _readable_report(session, principal, report_id)
    return await _view(session, caller, row)


async def list_reports(
    session: AsyncSession,
    principal: Principal,
    *,
    status: str | None,
    student_id: uuid.UUID | None,
    kind: str | None,
    mine: bool,
    cursor: str | None,
    limit: int,
) -> ProgressReportPage:
    """``GET /api/v1/progress-reports`` (E2): a tutor's own reports; a
    reviewer every non-draft report plus their own drafts (``mine`` narrows
    it to their own). Anyone else the ``404`` of an unknown id. At most 5
    statements."""
    reviewer = principal.has(rules.PROGRESS_REPORT_REVIEW)
    if not reviewer and rules.TUTOR_ROLE not in principal.roles:
        raise RecordNotFoundError()
    page = await academics.progress_report_working_page(
        session,
        subjects=catalog.subject_name_columns(),
        viewer_user_id=principal.user_id,
        reviewer=reviewer,
        mine=mine,
        status=status,
        student_id=student_id,
        kind=kind,
        cursor=cursor,
        limit=limit,
    )
    students = await families.student_references(
        session, {row.report.student_id for row in page.rows}
    )
    people = await identity.get_account_references(
        session, {row.report.author_user_id for row in page.rows}
    )
    return ProgressReportPage(
        items=[
            ProgressReportListItem(
                id=row.report.id,
                version=row.report.version,
                status=cast(ReportStatus, row.report.status),
                student=_student(students, row.report.student_id),
                author=_person(people, row.report.author_user_id),
                kind=cast(ReportKind, row.report.kind),
                period_start=row.report.period_start,
                period_end=row.report.period_end,
                subject=_subject(row),
                submitted_at=row.report.submitted_at,
                approved_at=row.report.approved_at,
            )
            for row in page.rows
        ],
        next_cursor=page.next_cursor,
    )


# --- F: approved reports (all audiences) ----------------------------------------------


@dataclass(frozen=True)
class _ApprovedScope:
    author_user_id: uuid.UUID | None = None
    """Set for a former author: only their own approved reports."""


async def _approved_scope(
    session: AsyncSession, caller: _Caller, student_id: uuid.UUID
) -> _ApprovedScope:
    """F1: holders and the student's current tutors read every approved
    report; the student and the guardians with academic access too (``403
    student_consent_required`` for a linked guardian of an adult without
    the consent); a former author their own (``404`` when they have none);
    anyone else ``404``."""
    if caller.reviewer:
        if not await families.student_references(session, [student_id]):
            raise RecordNotFoundError()
        return _ApprovedScope()
    if caller.current:
        return _ApprovedScope()
    try:
        await scheduling.ensure_family_student_access(
            session, caller.principal, student_id
        )
        return _ApprovedScope()
    except RecordNotFoundError:
        if not caller.tutor:
            raise
    if await academics.has_own_approved_report(
        session, student_id, caller.principal.user_id
    ):
        return _ApprovedScope(author_user_id=caller.principal.user_id)
    raise RecordNotFoundError()


async def _creatable(
    session: AsyncSession, caller: _Caller
) -> tuple[bool, list[ProgressReportSubject]]:
    """AM2: a tutor currently teaching the student may create; the active
    subjects they teach the student, by name. At most one statement."""
    if not caller.current:
        return False, []
    found = await catalog.subject_references(session, caller.subjects)
    subjects = sorted(
        (
            ProgressReportSubject(id=ref.id, name=ref.name)
            for ref in found.values()
            if ref.is_active and ref.education_level_is_active
        ),
        key=lambda s: (s.name.casefold(), str(s.id)),
    )
    return True, subjects


async def list_approved(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    kind: str | None,
    subject_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> ProgressReportFamilyPage:
    """``GET /api/v1/students/{id}/progress-reports`` (F3, AM2): at most 6
    statements, the same for 1 and 50 reports."""
    caller = await _caller(session, principal, student_id)
    scope = await _approved_scope(session, caller, student_id)
    page = await academics.approved_page(
        session,
        student_id,
        subjects=catalog.subject_name_columns(),
        author_user_id=scope.author_user_id,
        kind=kind,
        subject_id=subject_id,
        cursor=cursor,
        limit=limit,
    )
    people = await identity.get_account_references(
        session, {row.report.author_user_id for row in page.rows}
    )
    can_create, creatable = await _creatable(session, caller)
    items = []
    for row in page.rows:
        assert row.report.approved_at is not None  # noqa: S101 (approved rows)
        items.append(
            ProgressReportFamilyListItem(
                id=row.report.id,
                kind=cast(ReportKind, row.report.kind),
                period_start=row.report.period_start,
                period_end=row.report.period_end,
                subject=_subject(row),
                author=_person(people, row.report.author_user_id),
                approved_at=row.report.approved_at,
            )
        )
    return ProgressReportFamilyPage(
        items=items,
        next_cursor=page.next_cursor,
        can_create=can_create,
        creatable_subjects=creatable,
    )


async def get_approved(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    report_id: uuid.UUID,
) -> ProgressReportFamilyView:
    """``GET /api/v1/students/{id}/progress-reports/{report_id}`` (F1, F2):
    the frozen facts exactly as approved."""
    caller = await _caller(session, principal, student_id)
    scope = await _approved_scope(session, caller, student_id)
    row = await academics.approved_report(
        session,
        student_id,
        report_id,
        subjects=catalog.subject_name_columns(),
        author_user_id=scope.author_user_id,
    )
    if row is None:
        raise RecordNotFoundError()
    report = row.report
    assert report.approved_at is not None  # noqa: S101 (approved rows)
    facts = _facts(report.facts)
    assert facts is not None  # noqa: S101 (approved rows have facts)
    people = await identity.get_account_references(session, {report.author_user_id})
    return ProgressReportFamilyView(
        id=report.id,
        student_id=report.student_id,
        kind=cast(ReportKind, report.kind),
        period_start=report.period_start,
        period_end=report.period_end,
        subject=_subject(row),
        author=_person(people, report.author_user_id),
        summary=report.summary,
        strengths=report.strengths,
        areas_for_improvement=report.areas_for_improvement,
        next_steps=report.next_steps,
        facts=facts,
        approved_at=report.approved_at,
    )
