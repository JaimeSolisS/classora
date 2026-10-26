"""Academics tables (#77 A2, A3; #78 A1, A2).

- ``session_closeouts``: one tutor closeout per class session
  (``uq_session_closeouts_class_session_id``, the race backstop): the
  submitting tutor (``tutor_user_id``, a snapshot for payroll attribution,
  M7-04), the review ``status`` (a CHECK constraint, not an enum type:
  ``submitted``, ``changes_requested``, ``approved``, #78), the structured
  summary (``topics``, ``performance``, ``homework``, ``next_steps``:
  stored trimmed, an empty field stored as ``NULL``, 1–2000 characters),
  ``submitted_at`` (the first submission), ``last_submitted_at`` (the
  latest submission, resubmission or correction request), the latest
  request-changes reason (``review_reason``, required while
  ``changes_requested``), the tutor's note on a resubmission or correction
  request (``tutor_note``), the current approval revision
  (``current_approval_id``, required while ``approved``), the reviewer
  who made the latest submission on the tutor's behalf with the reason
  (``on_behalf_by_user_id``, ``on_behalf_reason``: both or neither, never
  the tutor; #301) and a ``version``. The actual minutes live on
  ``class_sessions.actual_minutes`` (ISSUE-F2), not here.
- ``session_closeout_approvals`` (#78 E1): append-only approval revisions.
  A re-approval never edits a revision: it inserts the next one, naming the
  one it supersedes (``supersedes_approval_id``, unique: never two
  revisions superseding the same one). Each revision snapshots what payroll
  reads (M7-04): the tutor, the session start and kind, the scheduled and
  approved minutes and the per-student attendance statuses.
- ``session_closeout_corrections`` (#78 E5): append-only staff corrections
  (the fields changed, the minutes before and after, the reason, the
  revision it created when the closeout was approved).

- ``session_notes`` (#79 A1): a tutor's note about one participating
  student in one class session (``class_session_id`` + ``student_id``,
  E1), ``internal`` by default and ``family`` only after an explicit
  publish (``published_at`` / ``published_by_user_id``, set together with
  the visibility; the last unpublish keeps ``unpublished_at``,
  ``unpublished_by_user_id`` and an optional ``unpublish_reason`` until the
  next publish). The text is stored trimmed (1–4000); ``body_updated_at``
  marks an edit. A soft-deleted note (``deleted_at`` /
  ``deleted_by_user_id``, together) is always ``internal``.

- ``evaluation_templates`` and ``evaluation_template_criteria`` (#80 A1):
  a named template (``diagnostic`` or ``ongoing``, an optional subject)
  with 1–20 ordered criteria, each with a ``max_score`` (0.5–1000, two
  places). The template row is the version anchor of its criteria.
- ``evaluations`` and ``evaluation_scores`` (#80 A1): one tutor's
  evaluation of one student from a template whose name, kind, version and
  criteria are **copied** at create (editing the template never changes
  it). ``draft`` → ``final`` (the totals and ``score_percent`` stored),
  amendments (``amended_at``, the internal ``last_change_reason``), a staff
  void (``voided_*``, final only) and a soft delete (drafts only). An
  ``evaluated_on`` local date, no period column (E3). The optional course
  link is ``cohort_id`` + a snapshot ``course_assessment_id`` (no foreign
  key) + its title. The evaluation row is the version anchor of its scores.

- ``learning_plans``, ``learning_plan_objectives`` and
  ``learning_plan_milestones`` (#81 A1): one student's plan in one subject
  (title, goal description, ``starts_on`` / ``target_on`` local dates),
  ``draft`` → ``active`` → ``completed`` | ``discontinued``
  (``activated_at``, ``closed_at``; at most one live ``active`` plan per
  student and subject: a partial unique index), a soft delete (drafts
  only); 1–10 ordered objectives; 1–10 ordered milestones per objective
  (title, activities, ``due_on``, progress ``status`` with
  ``completed_at`` / ``completed_by_user_id`` exactly when ``done``). The
  plan row is the version anchor of its objectives and milestone
  definitions; each milestone has its own ``version`` for progress.

- ``assignments`` and ``assignment_submissions`` (#82 A1): homework a
  tutor sets (a subject, title, instructions, a center-local ``due_on`` +
  optional ``due_time``, an optional ``max_score`` and class session; soft
  delete) and one row per recipient with the submission (text and/or a URL,
  by the student or recorded by the tutor) and the grade (score and
  percentage or feedback only; a re-grade keeps an internal
  ``last_change_reason``).

- ``progress_reports`` (#83 A1): a tutor's report about one student for a
  weekly, monthly or on-demand period (optionally one subject): the
  narrative, the facts snapshot frozen at each submit, and the review
  (``draft`` → ``submitted`` → ``changes_requested`` |
  ``approved_for_family`` → ``retracted``). A trigger freezes an approved
  report's content (42501) and refuses every ``DELETE``.

``session_closeouts``, ``session_notes``, the four evaluation tables, the
three learning plan tables, the two homework tables and ``progress_reports``
are audited (the
create and update diffs are their record). The two append-only tables are not: a trigger
rejects ``UPDATE``, ``DELETE`` and ``TRUNCATE`` (42501), and their named
events
(``academics.closeout_approved``, ``academics.closeout_corrected``) are
their audit record. Every foreign key is ``ON DELETE RESTRICT``.
"""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, SoftDeleteMixin, VersionedMixin

SUMMARY_MAX_LENGTH: Final = 2000
REASON_MAX_LENGTH: Final = 1000
SUMMARY_COLUMNS: Final = ("topics", "performance", "homework", "next_steps")
CLOSEOUT_SUBMITTED: Final = "submitted"
CLOSEOUT_CHANGES_REQUESTED: Final = "changes_requested"
CLOSEOUT_APPROVED: Final = "approved"
CLOSEOUT_STATUSES: Final = (
    CLOSEOUT_SUBMITTED,
    CLOSEOUT_CHANGES_REQUESTED,
    CLOSEOUT_APPROVED,
)
CLOSEOUT_SESSION_UNIQUE: Final = "uq_session_closeouts_class_session_id"

APPROVAL_SOURCES: Final = ("review", "staff_correction")
CORRECTABLE_FIELDS: Final = ("actual_minutes", *SUMMARY_COLUMNS)
APPROVED_MINUTES_MAX: Final = 720
CURRENT_APPROVAL_FK: Final = "fk_session_closeouts_current_approval_id"
SUPERSEDES_UNIQUE: Final = "uq_session_closeout_approvals_supersedes_approval_id"


def _summary_check(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {SUMMARY_MAX_LENGTH}",
        name=f"{column}_length",
    )


def _reason_check(column: str, *, nullable: bool) -> CheckConstraint:
    valid = (
        f"char_length(btrim({column})) BETWEEN 1 AND {REASON_MAX_LENGTH}"
        f" AND {column} = btrim({column})"
    )
    return CheckConstraint(
        f"{column} IS NULL OR ({valid})" if nullable else valid,
        name=f"{column}_valid",
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class SessionCloseout(Audited, VersionedMixin, Model):
    """A tutor's closeout of one class session (#77 A2, #78 A1)."""

    __tablename__ = "session_closeouts"
    __table_args__ = (
        CheckConstraint(_in("status", CLOSEOUT_STATUSES), name="status_valid"),
        *(_summary_check(column) for column in SUMMARY_COLUMNS),
        _reason_check("review_reason", nullable=True),
        _reason_check("tutor_note", nullable=True),
        CheckConstraint(
            "status <> 'changes_requested' OR review_reason IS NOT NULL",
            name="changes_requested_reason",
        ),
        CheckConstraint(
            "status <> 'approved' OR current_approval_id IS NOT NULL",
            name="approved_has_approval",
        ),
        # #301 A1: the latest submission made on the tutor's behalf.
        _reason_check("on_behalf_reason", nullable=True),
        CheckConstraint(
            "(on_behalf_by_user_id IS NULL) = (on_behalf_reason IS NULL)",
            name="on_behalf_together",
        ),
        CheckConstraint(
            "on_behalf_by_user_id IS NULL OR on_behalf_by_user_id <> tutor_user_id",
            name="on_behalf_not_tutor",
        ),
        # #78 and M7 list a tutor's closeouts by submission time.
        Index(
            "ix_session_closeouts_tutor_user_id_submitted_at",
            "tutor_user_id",
            "submitted_at",
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        unique=True,
    )
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(20))
    topics: Mapped[str | None] = mapped_column(Text, nullable=True)
    performance: Mapped[str | None] = mapped_column(Text, nullable=True)
    homework: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    tutor_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "session_closeout_approvals.id",
            ondelete="RESTRICT",
            name=CURRENT_APPROVAL_FK,
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    on_behalf_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """#301: the reviewer who made the latest submission on the tutor's
    behalf (``NULL`` when the tutor made it)."""
    on_behalf_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """#301: why (trimmed, 1–1000; with ``on_behalf_by_user_id``)."""


class SessionCloseoutApproval(Model):
    """One approval revision of a closeout (#78 E1). Append-only; revision
    1 supersedes nothing, revision ``n + 1`` supersedes revision ``n``."""

    __tablename__ = "session_closeout_approvals"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(revision = 1) = (supersedes_approval_id IS NULL)",
            name="revision_supersedes",
        ),
        CheckConstraint(_in("source", APPROVAL_SOURCES), name="source_valid"),
        CheckConstraint(
            f"approved_minutes BETWEEN 0 AND {APPROVED_MINUTES_MAX}",
            name="approved_minutes_range",
        ),
        UniqueConstraint(
            "closeout_id", "revision", name="uq_session_closeout_approvals_revision"
        ),
        # M7-04 reads a tutor's approved closeouts by service date.
        Index(
            "ix_session_closeout_approvals_tutor_user_id_session_starts_at",
            "tutor_user_id",
            "session_starts_at",
        ),
    )

    closeout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_closeouts.id", ondelete="RESTRICT"),
    )
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "session_closeout_approvals.id",
            ondelete="RESTRICT",
            name="fk_session_closeout_approvals_supersedes_approval_id",
        ),
        nullable=True,
        unique=True,
    )
    source: Mapped[str] = mapped_column(String(20))
    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
    )
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    session_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_kind: Mapped[str] = mapped_column(String(20))
    scheduled_minutes: Mapped[int] = mapped_column(Integer)
    approved_minutes: Mapped[int] = mapped_column(Integer)
    attendance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """``{student_id: status}`` when approved."""
    approved_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionCloseoutCorrection(Model):
    """One staff correction of a closeout (#78 E5). Append-only."""

    __tablename__ = "session_closeout_corrections"
    __table_args__ = (
        CheckConstraint(
            "fields <@ ARRAY[{}]::text[]".format(
                ", ".join(f"'{f}'" for f in CORRECTABLE_FIELDS)
            ),
            name="fields_valid",
        ),
        CheckConstraint(
            "cardinality(fields) > 0 OR approval_id IS NOT NULL",
            name="fields_or_reapproval",
        ),
        CheckConstraint(
            "(new_minutes IS NOT NULL) = ('actual_minutes' = ANY (fields))",
            name="minutes_with_field",
        ),
        CheckConstraint(
            f"new_minutes IS NULL OR new_minutes BETWEEN 0 AND {APPROVED_MINUTES_MAX}",
            name="new_minutes_range",
        ),
        _reason_check("reason", nullable=False),
        CheckConstraint(
            _in("closeout_status", (CLOSEOUT_SUBMITTED, CLOSEOUT_APPROVED)),
            name="closeout_status_valid",
        ),
        CheckConstraint(
            "(closeout_status = 'approved') = (approval_id IS NOT NULL)",
            name="approval_when_approved",
        ),
    )

    closeout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_closeouts.id", ondelete="RESTRICT"),
        index=True,
    )
    fields: Mapped[list[str]] = mapped_column(ARRAY(Text))
    previous_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    closeout_status: Mapped[str] = mapped_column(String(20))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "session_closeout_approvals.id",
            ondelete="RESTRICT",
            name="fk_session_closeout_corrections_approval_id",
        ),
        nullable=True,
    )
    corrected_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )


# --- Session notes (#79) ----------------------------------------------------------

NOTE_BODY_MAX: Final = 4000
NOTE_UNPUBLISH_REASON_MAX: Final = 500
NOTE_INTERNAL: Final = "internal"
NOTE_FAMILY: Final = "family"
NOTE_VISIBILITIES: Final = (NOTE_INTERNAL, NOTE_FAMILY)


class SessionNote(Audited, SoftDeleteMixin, VersionedMixin, Model):
    """A tutor's note about one student in one class session (#79 A1, A2).
    Its audit create and update diffs (text, visibility, deletion) are the
    note's history (E8)."""

    __tablename__ = "session_notes"
    __table_args__ = (
        CheckConstraint(
            f"char_length(body) BETWEEN 1 AND {NOTE_BODY_MAX} AND body = btrim(body)",
            name="body_valid",
        ),
        CheckConstraint(_in("visibility", NOTE_VISIBILITIES), name="visibility_valid"),
        CheckConstraint(
            "(visibility = 'family') = (published_at IS NOT NULL"
            " AND published_by_user_id IS NOT NULL)",
            name="published_together",
        ),
        CheckConstraint(
            "(unpublished_at IS NULL) = (unpublished_by_user_id IS NULL)",
            name="unpublished_together",
        ),
        CheckConstraint(
            "unpublish_reason IS NULL OR (unpublished_at IS NOT NULL"
            f" AND char_length(unpublish_reason) BETWEEN 1"
            f" AND {NOTE_UNPUBLISH_REASON_MAX}"
            " AND unpublish_reason = btrim(unpublish_reason))",
            name="unpublish_reason_valid",
        ),
        CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name="deleted_together",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR visibility = 'internal'",
            name="deleted_internal",
        ),
        # A student's history, newest first (E2, F2: read backwards), and a
        # session's notes (E1); deleted notes are never read.
        Index(
            "ix_session_notes_student_id_created_at",
            "student_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_session_notes_class_session_id",
            "class_session_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("class_sessions.id", ondelete="RESTRICT")
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        String(10), server_default=text("'internal'")
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    unpublished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unpublished_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    unpublish_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


# --- Evaluations (#80) ------------------------------------------------------------

EVALUATION_KINDS: Final = ("diagnostic", "ongoing")
EVALUATION_STATUSES: Final = ("draft", "final")
TEMPLATE_NAME_INDEX: Final = "uq_evaluation_templates_name_ci"
CRITERION_POSITION_UNIQUE: Final = "uq_evaluation_template_criteria_position"
SCORE_POSITION_UNIQUE: Final = "uq_evaluation_scores_position"


def _trimmed_text(column: str, maximum: int, *, nullable: bool) -> CheckConstraint:
    valid = (
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})"
    )
    return CheckConstraint(
        f"{column} IS NULL OR ({valid})" if nullable else valid,
        name=f"{column}_valid",
    )


class EvaluationTemplate(Audited, VersionedMixin, Model):
    """An evaluation template (#80 A1, C): the version anchor of its
    criteria (a criteria change bumps ``version`` once). ``kind`` never
    changes; ``subject_id`` ``NULL`` is "any subject"."""

    __tablename__ = "evaluation_templates"
    __table_args__ = (
        _trimmed_text("name", 120, nullable=False),
        CheckConstraint(_in("kind", EVALUATION_KINDS), name="kind_valid"),
        _trimmed_text("description", 2000, nullable=True),
        Index(TEMPLATE_NAME_INDEX, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class EvaluationTemplateCriterion(Audited, Model):
    """One criterion of a template, at ``position`` (1-based). Removed
    criteria are deleted (evaluations keep their copies)."""

    __tablename__ = "evaluation_template_criteria"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 20", name="position_range"),
        _trimmed_text("title", 120, nullable=False),
        _trimmed_text("description", 500, nullable=True),
        CheckConstraint(
            "max_score >= 0.5 AND max_score <= 1000", name="max_score_range"
        ),
        UniqueConstraint(
            "template_id",
            "position",
            name=CRITERION_POSITION_UNIQUE,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    max_score: Mapped[Decimal] = mapped_column(Numeric(6, 2))


class Evaluation(Audited, SoftDeleteMixin, VersionedMixin, Model):
    """One tutor's evaluation of one student (#80 A1, D). Its audit create
    and update diffs (scores via ``evaluation_scores``, feedback, date,
    status, amendments, void, deletion) are its edit history (spec §9)."""

    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(_in("kind", EVALUATION_KINDS), name="kind_valid"),
        CheckConstraint(_in("status", EVALUATION_STATUSES), name="status_valid"),
        _trimmed_text("template_name", 120, nullable=False),
        CheckConstraint(
            "(cohort_id IS NULL) = (course_assessment_id IS NULL)"
            " AND (cohort_id IS NULL) = (course_assessment_title IS NULL)",
            name="course_together",
        ),
        CheckConstraint(
            "cohort_id IS NULL OR class_session_id IS NOT NULL",
            name="course_needs_session",
        ),
        CheckConstraint(
            "course_assessment_title IS NULL"
            " OR char_length(course_assessment_title) BETWEEN 1 AND 200",
            name="course_assessment_title_valid",
        ),
        CheckConstraint(
            "feedback IS NULL OR char_length(feedback) BETWEEN 1 AND 4000",
            name="feedback_valid",
        ),
        CheckConstraint(
            "score_percent >= 0 AND score_percent <= 100", name="score_percent_range"
        ),
        CheckConstraint(
            "(status = 'final') = (finalized_at IS NOT NULL"
            " AND score_total IS NOT NULL AND max_total IS NOT NULL"
            " AND score_percent IS NOT NULL)",
            name="final_totals",
        ),
        CheckConstraint(
            "last_change_reason IS NULL"
            " OR char_length(last_change_reason) BETWEEN 1 AND 500",
            name="last_change_reason_valid",
        ),
        CheckConstraint(
            "(voided_at IS NULL) = (voided_by_user_id IS NULL)"
            " AND (voided_at IS NULL) = (void_reason IS NULL)",
            name="voided_together",
        ),
        CheckConstraint(
            "void_reason IS NULL OR char_length(void_reason) BETWEEN 1 AND 500",
            name="void_reason_valid",
        ),
        CheckConstraint("voided_at IS NULL OR status = 'final'", name="voided_final"),
        CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name="deleted_together",
        ),
        CheckConstraint("deleted_at IS NULL OR status = 'draft'", name="deleted_draft"),
        # The history (F2), newest first (read backwards).
        Index(
            "ix_evaluations_student_id_evaluated_on",
            "student_id",
            "evaluated_on",
            "id",
            postgresql_where=text("status = 'final' AND voided_at IS NULL"),
        ),
        # The working list (E2), newest first (read backwards).
        Index(
            "ix_evaluations_student_id_created_at",
            "student_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_evaluations_author_user_id_drafts",
            "author_user_id",
            postgresql_where=text("status = 'draft' AND deleted_at IS NULL"),
        ),
        # #85: the evaluations of one cohort assessment.
        Index(
            "ix_evaluations_cohort_id_course_assessment_id",
            "cohort_id",
            "course_assessment_id",
            postgresql_where=text("cohort_id IS NOT NULL"),
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT")
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_templates.id", ondelete="RESTRICT")
    )
    template_version: Mapped[int] = mapped_column(Integer)
    template_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    evaluated_on: Mapped[date] = mapped_column(Date)
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_cohorts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    course_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    """A ``course_template_snapshots`` assessment id (no foreign key, E7)."""
    course_assessment_title: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    status: Mapped[str] = mapped_column(String(10), server_default=text("'draft'"))
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_total: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    max_total: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    score_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Internal (E4): never returned to families."""
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class EvaluationScore(Audited, Model):
    """One criterion of an evaluation, copied from the template at create
    (title, description, max score), with its score (``NULL`` until
    scored) and an optional comment."""

    __tablename__ = "evaluation_scores"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 20", name="position_range"),
        _trimmed_text("criterion_title", 120, nullable=False),
        CheckConstraint(
            "criterion_description IS NULL"
            " OR char_length(criterion_description) BETWEEN 1 AND 500",
            name="criterion_description_valid",
        ),
        CheckConstraint(
            "max_score >= 0.5 AND max_score <= 1000", name="max_score_range"
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= max_score)",
            name="score_range",
        ),
        CheckConstraint(
            "comment IS NULL OR char_length(comment) BETWEEN 1 AND 1000",
            name="comment_valid",
        ),
        UniqueConstraint("evaluation_id", "position", name=SCORE_POSITION_UNIQUE),
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    criterion_title: Mapped[str] = mapped_column(String(120))
    criterion_description: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    max_score: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Learning plans (#81) ---------------------------------------------------------

PLAN_STATUSES: Final = ("draft", "active", "completed", "discontinued")
MILESTONE_STATUSES: Final = ("pending", "in_progress", "done")
ACTIVE_PLAN_UNIQUE: Final = "uq_learning_plans_student_id_subject_id_active"
OBJECTIVE_POSITION_UNIQUE: Final = "uq_learning_plan_objectives_position"
MILESTONE_POSITION_UNIQUE: Final = "uq_learning_plan_milestones_position"


class LearningPlan(Audited, SoftDeleteMixin, VersionedMixin, Model):
    """One student's learning plan in one subject (#81 A1, E2, E3): the
    version anchor of its objectives and milestone definitions (a change to
    them bumps ``version`` once). Its audit diffs are its edit history
    (spec §9)."""

    __tablename__ = "learning_plans"
    __table_args__ = (
        _trimmed_text("title", 120, nullable=False),
        CheckConstraint(
            "description IS NULL OR char_length(description) BETWEEN 1 AND 2000",
            name="description_valid",
        ),
        CheckConstraint(
            "target_on IS NULL OR target_on >= starts_on", name="target_after_start"
        ),
        CheckConstraint(_in("status", PLAN_STATUSES), name="status_valid"),
        CheckConstraint(
            "status <> 'draft' OR activated_at IS NULL", name="draft_not_activated"
        ),
        CheckConstraint(
            "status NOT IN ('active', 'completed') OR activated_at IS NOT NULL",
            name="active_activated",
        ),
        CheckConstraint(
            "(status IN ('completed', 'discontinued')) = (closed_at IS NOT NULL)",
            name="closed_together",
        ),
        CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name="deleted_together",
        ),
        CheckConstraint("deleted_at IS NULL OR status = 'draft'", name="deleted_draft"),
        # E2: at most one active plan per student and subject.
        Index(
            ACTIVE_PLAN_UNIQUE,
            "student_id",
            "subject_id",
            unique=True,
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
        # The list (E2), read by student.
        Index(
            "ix_learning_plans_student_id_created_at",
            "student_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_on: Mapped[date] = mapped_column(Date)
    target_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'draft'"))
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class LearningPlanObjective(Audited, Model):
    """One objective of a plan, at ``position`` (1-based, unique per plan,
    deferrable: the #64 pattern). Removed objectives are deleted (the audit
    keeps them)."""

    __tablename__ = "learning_plan_objectives"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 10", name="position_range"),
        _trimmed_text("title", 200, nullable=False),
        CheckConstraint(
            "description IS NULL OR char_length(description) BETWEEN 1 AND 1000",
            name="description_valid",
        ),
        UniqueConstraint(
            "plan_id",
            "position",
            name=OBJECTIVE_POSITION_UNIQUE,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class LearningPlanMilestone(Audited, VersionedMixin, Model):
    """One milestone of an objective, at ``position`` (1-based, unique per
    objective, deferrable), with its progress ``status``. ``done`` stores
    who and when; its own ``version`` guards progress writes (D2)."""

    __tablename__ = "learning_plan_milestones"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 10", name="position_range"),
        _trimmed_text("title", 200, nullable=False),
        CheckConstraint(
            "activities IS NULL OR char_length(activities) BETWEEN 1 AND 2000",
            name="activities_valid",
        ),
        CheckConstraint(_in("status", MILESTONE_STATUSES), name="status_valid"),
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL"
            " AND completed_by_user_id IS NOT NULL)",
            name="done_together",
        ),
        UniqueConstraint(
            "objective_id",
            "position",
            name=MILESTONE_POSITION_UNIQUE,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "learning_plan_objectives.id",
            ondelete="RESTRICT",
            # The convention's name would pass PostgreSQL's 63-character limit.
            name="fk_learning_plan_milestones_objective_id",
        ),
        index=True,
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(200))
    activities: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


# --- Homework assignments (#82) ---------------------------------------------------

SUBMISSION_UNIQUE: Final = "uq_assignment_submissions_assignment_id_student_id"


class Assignment(Audited, SoftDeleteMixin, VersionedMixin, Model):
    """A piece of homework a tutor sets (#82 A1, E2): the creator, a
    subject, the title and instructions, a center-local deadline
    (``due_on`` + optional ``due_time``; ``NULL`` = the end of the day, no
    time-zone dependency, E3), an optional ``max_score`` (E5) and an
    optional class session. Each recipient has an
    :class:`AssignmentSubmission` row. Its audit diffs are its edit history
    (spec §9)."""

    __tablename__ = "assignments"
    __table_args__ = (
        _trimmed_text("title", 200, nullable=False),
        _trimmed_text("instructions", 4000, nullable=False),
        CheckConstraint(
            "max_score IS NULL OR (max_score >= 0.5 AND max_score <= 1000)",
            name="max_score_range",
        ),
        CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name="deleted_together",
        ),
        # The working list (F1): a creator's live assignments.
        Index(
            "ix_assignments_creator_user_id_due_on",
            "creator_user_id",
            "due_on",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_assignments_class_session_id",
            "class_session_id",
            postgresql_where=text("class_session_id IS NOT NULL"),
        ),
    )

    creator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT")
    )
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str] = mapped_column(Text)
    due_on: Mapped[date] = mapped_column(Date)
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class AssignmentSubmission(Audited, VersionedMixin, Model):
    """One recipient of an assignment (#82 A1, E2, E4, E5): the student's
    submission (text and/or a URL, by the student or recorded by the tutor)
    and the grade (score and percentage when the assignment is scored,
    feedback, the first grade's instant and center-local date; a re-grade
    keeps an internal ``last_change_reason``). A removed recipient's row is
    deleted (the audit keeps it)."""

    __tablename__ = "assignment_submissions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name=SUBMISSION_UNIQUE),
        CheckConstraint(
            "text IS NULL OR char_length(text) BETWEEN 1 AND 10000",
            name="text_valid",
        ),
        CheckConstraint(
            "url IS NULL OR char_length(url) BETWEEN 1 AND 2000", name="url_valid"
        ),
        CheckConstraint(
            "submitted_via IS NULL OR submitted_via IN ('student', 'tutor')",
            name="submitted_via_valid",
        ),
        CheckConstraint(
            "(submitted_at IS NULL) = (submitted_by_user_id IS NULL)"
            " AND (submitted_at IS NULL) = (submitted_via IS NULL)",
            name="submitted_together",
        ),
        CheckConstraint(
            "submitted_at IS NOT NULL OR (text IS NULL AND url IS NULL)",
            name="content_needs_submission",
        ),
        CheckConstraint("score IS NULL OR score >= 0", name="score_range"),
        CheckConstraint(
            "score_percent IS NULL OR (score_percent >= 0 AND score_percent <= 100)",
            name="score_percent_range",
        ),
        CheckConstraint(
            "feedback IS NULL OR char_length(feedback) BETWEEN 1 AND 4000",
            name="feedback_valid",
        ),
        CheckConstraint(
            "graded_at IS NULL OR submitted_at IS NOT NULL",
            name="graded_needs_submission",
        ),
        CheckConstraint(
            "(graded_at IS NULL) = (graded_on IS NULL)"
            " AND (graded_at IS NULL) = (graded_by_user_id IS NULL)",
            name="graded_together",
        ),
        CheckConstraint(
            "(score IS NULL) = (score_percent IS NULL)", name="score_together"
        ),
        CheckConstraint(
            "graded_at IS NOT NULL OR (score IS NULL AND feedback IS NULL)",
            name="grade_needs_graded",
        ),
        CheckConstraint(
            "last_change_reason IS NULL"
            " OR (char_length(last_change_reason) BETWEEN 1 AND 500"
            " AND last_change_reason = btrim(last_change_reason))",
            name="last_change_reason_valid",
        ),
        CheckConstraint(
            "grade_amended_at IS NULL OR graded_at IS NOT NULL",
            name="amended_needs_graded",
        ),
        # A student's assignments (E3) and history (G1).
        Index(
            "ix_assignment_submissions_student_id_assignment_id",
            "student_id",
            "assignment_id",
        ),
        Index(
            "ix_assignment_submissions_student_id_graded_on",
            "student_id",
            "graded_on",
            postgresql_where=text("score IS NOT NULL"),
        ),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assignments.id", ondelete="RESTRICT"),
        index=True,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    submitted_via: Mapped[str | None] = mapped_column(String(10), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    score_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    graded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    graded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    grade_amended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Internal (E5): never returned to families."""


# --- Progress reports (#83) -------------------------------------------------------

PROGRESS_REPORT_KINDS: Final = ("weekly", "monthly", "on_demand")
PROGRESS_REPORT_STATUSES: Final = (
    "draft",
    "submitted",
    "changes_requested",
    "approved_for_family",
    "retracted",
)
PROGRESS_REPORT_PERIOD_UNIQUE: Final = "uq_progress_reports_period"
PROGRESS_REPORT_PROTECTED: Final = (
    "student_id",
    "author_user_id",
    "subject_id",
    "kind",
    "period_start",
    "period_end",
    "summary",
    "strengths",
    "areas_for_improvement",
    "next_steps",
    "facts",
    "facts_computed_at",
    "approved_at",
    "approved_by_user_id",
)
"""The columns the immutability trigger freezes once ``approved_at`` is set
(#83 A1, E3; migration 0057)."""


class ProgressReport(Audited, SoftDeleteMixin, VersionedMixin, Model):
    """A tutor's progress report about one student for one period (#83 A1,
    E2, E3): an optional subject, the narrative (``summary`` required to
    submit, ``strengths``, ``areas_for_improvement``, ``next_steps``; stored
    trimmed), the family-safe ``facts`` snapshot frozen at each submit, and
    the review: ``draft`` → ``submitted`` → ``changes_requested`` |
    ``approved_for_family`` (the only status families ever see) →
    ``retracted`` (final). Once ``approved_at`` is set a trigger refuses
    any change to the content and the approval (42501), and every
    ``DELETE``. A soft delete is allowed in ``draft`` and
    ``changes_requested``. Its audit diffs are its edit history (spec
    §9)."""

    __tablename__ = "progress_reports"
    __table_args__ = (
        CheckConstraint(_in("kind", PROGRESS_REPORT_KINDS), name="kind_valid"),
        CheckConstraint(
            "period_start <= period_end AND period_end - period_start <= 365",
            name="period_valid",
        ),
        CheckConstraint(
            "kind <> 'weekly' OR (extract(isodow from period_start) = 1"
            " AND period_end = period_start + 6)",
            name="weekly_period",
        ),
        CheckConstraint(
            "kind <> 'monthly' OR (extract(day from period_start) = 1"
            " AND period_end = (period_start + interval '1 month'"
            " - interval '1 day')::date)",
            name="monthly_period",
        ),
        CheckConstraint(_in("status", PROGRESS_REPORT_STATUSES), name="status_valid"),
        _trimmed_text("summary", 4000, nullable=True),
        _trimmed_text("strengths", 2000, nullable=True),
        _trimmed_text("areas_for_improvement", 2000, nullable=True),
        _trimmed_text("next_steps", 2000, nullable=True),
        CheckConstraint(
            "(facts IS NULL) = (facts_computed_at IS NULL)", name="facts_together"
        ),
        CheckConstraint("status = 'draft' OR facts IS NOT NULL", name="facts_frozen"),
        CheckConstraint(
            "status = 'draft' OR submitted_at IS NOT NULL", name="submitted_set"
        ),
        CheckConstraint(
            "(changes_requested_at IS NULL) = (changes_requested_by_user_id IS NULL)"
            " AND (changes_requested_at IS NULL) = (changes_requested_reason IS NULL)",
            name="changes_requested_together",
        ),
        _reason_check("changes_requested_reason", nullable=True),
        CheckConstraint(
            "status <> 'changes_requested' OR changes_requested_at IS NOT NULL",
            name="changes_requested_set",
        ),
        CheckConstraint(
            "(approved_at IS NULL) = (approved_by_user_id IS NULL)",
            name="approved_together",
        ),
        CheckConstraint(
            "(status = 'approved_for_family') <= (approved_at IS NOT NULL)",
            name="approved_set",
        ),
        CheckConstraint(
            "approved_at IS NULL OR status IN ('approved_for_family', 'retracted')",
            name="approved_status",
        ),
        CheckConstraint(
            "(retracted_at IS NULL) = (retracted_by_user_id IS NULL)"
            " AND (retracted_at IS NULL) = (retract_reason IS NULL)",
            name="retracted_together",
        ),
        _reason_check("retract_reason", nullable=True),
        CheckConstraint(
            "(status = 'retracted') = (retracted_at IS NOT NULL)",
            name="retracted_set",
        ),
        CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name="deleted_together",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR status IN ('draft', 'changes_requested')",
            name="deleted_editable",
        ),
        # R10: a student's approved reports.
        Index(
            "ix_progress_reports_student_id_approved",
            "student_id",
            "period_end",
            "id",
            postgresql_where=text("status = 'approved_for_family'"),
        ),
        # R2 (mine): an author's live reports.
        Index(
            "ix_progress_reports_author_user_id_created_at",
            "author_user_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The reviewer queue.
        Index(
            "ix_progress_reports_submitted_at",
            "submitted_at",
            "id",
            postgresql_where=text("status = 'submitted'"),
        ),
        # One live weekly/monthly report per student, author, period and
        # subject (#83c's placeholders rely on it).
        Index(
            PROGRESS_REPORT_PERIOD_UNIQUE,
            "student_id",
            "author_user_id",
            "kind",
            "period_start",
            text("COALESCE(subject_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text(
                "kind <> 'on_demand' AND deleted_at IS NULL AND status <> 'retracted'"
            ),
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(20))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'draft'"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[str | None] = mapped_column(Text, nullable=True)
    areas_for_improvement: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    facts_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    changes_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    changes_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    changes_requested_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Internal (E6): never in the family shape."""
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    retracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retracted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    retract_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Internal (E6): never in the family shape."""
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
