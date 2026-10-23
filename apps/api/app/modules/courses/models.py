"""Course template tables (#64; spec §3 structured course).

- ``course_templates``: the template itself (name unique ignoring case,
  inactive rows count; a duration of ``session_count`` sessions of
  ``session_minutes`` minutes; an optional minimum attendance percentage).
  Versioned: the template row is the version anchor of all its children, so
  any change to a subject link, module or assessment bumps its ``version``
  exactly once. Templates are deactivated, never deleted.
- ``course_template_subjects``: the template's subjects (1 to 10).
- ``course_template_modules``: the ordered syllabus modules (1 to 50).
  ``position`` is unique per template, ``DEFERRABLE INITIALLY DEFERRED`` so a
  reorder in one transaction never meets a transient duplicate.
- ``course_template_assessments``: the assessment definitions (0 to 30,
  owner decision #64 E2 (a)), optionally tied to one module (``NULL`` = the
  whole course); same deferrable position rule.
- ``course_template_snapshots``: the immutable content of a template at one
  version (owner decision #64 E1 (a)). The trigger
  ``course_template_snapshots_immutable`` rejects ``UPDATE`` and ``DELETE``.
  Cohorts (#65) point at a snapshot, so later template edits never change
  them.
- ``course_cohorts`` (#65 A2): one cohort of a template: a name (unique
  ignoring case), its snapshot (and the snapshot's template), one subject
  and a maximum size. Its sessions are the occurrences of a ``course``
  series in the scheduling module (``class_series.cohort_id``), which keeps
  the tutor, the room and the timing.

Every table is :class:`~app.core.audit.Audited`; the snapshot's ``content``
is left out of its audit event (the row itself is the immutable record).
Removed modules and assessments are deleted (cohorts keep their syllabus in
the snapshot); every foreign key is ``ON DELETE RESTRICT``.
"""

import uuid
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

NAME_MAX_LENGTH = 100
TITLE_MAX_LENGTH = 200
TEXT_MAX_LENGTH = 2000
SESSION_COUNT_MAX = 200
"""ADR-0005 (#46 D4): at most 200 occurrences."""
SESSION_MINUTES_MIN = 15
SESSION_MINUTES_MAX = 480
PERCENT_MIN = 1
PERCENT_MAX = 100
SUBJECTS_MIN = 1
SUBJECTS_MAX = 10
MODULES_MIN = 1
MODULES_MAX = 50
ASSESSMENTS_MAX = 30
SNAPSHOT_SCHEMA_VERSION = 1

TEMPLATE_NAME_INDEX = "uq_course_templates_name_ci"
MODULE_POSITION_UNIQUE = "uq_course_template_modules_template_position"
ASSESSMENT_POSITION_UNIQUE = "uq_course_template_assessments_template_position"
SUBJECT_LINK_UNIQUE = "uq_course_template_subjects_template_subject"
SNAPSHOT_VERSION_UNIQUE = "uq_course_template_snapshots_template_version"
SNAPSHOT_IMMUTABLE_TRIGGER = "course_template_snapshots_immutable"
ASSESSMENT_MODULE_FK = "fk_course_template_assessments_module_id_modules"
COHORT_NAME_MAX_LENGTH = 120
COHORT_MAX_STUDENTS_MAX = 500
"""The #39 / #56 range: a cohort has 1 to 500 students at most."""
COHORT_NAME_INDEX = "uq_course_cohorts_name_ci"
COHORT_SNAPSHOT_FK = "fk_course_cohorts_template_snapshot_id_snapshots"


def _text_checks(column: str) -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint(f"{column} = btrim({column})", name=f"{column}_trimmed"),
        CheckConstraint(f"{column} ~ '[^[:space:]]'", name=f"{column}_not_blank"),
    )


def _range(column: str, low: int, high: int) -> CheckConstraint:
    return CheckConstraint(f"{column} BETWEEN {low} AND {high}", name=f"{column}_range")


def _template_fk() -> Any:
    return ForeignKey("course_templates.id", ondelete="RESTRICT")


class CourseTemplate(Audited, VersionedMixin, Model):
    """A reusable course template. ``min_attendance_percent`` ``NULL`` means
    no attendance criterion."""

    __tablename__ = "course_templates"
    __table_args__ = (
        *_text_checks("name"),
        _range("session_count", 1, SESSION_COUNT_MAX),
        _range("session_minutes", SESSION_MINUTES_MIN, SESSION_MINUTES_MAX),
        _range("min_attendance_percent", PERCENT_MIN, PERCENT_MAX),
        Index(TEMPLATE_NAME_INDEX, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(
        String(TEXT_MAX_LENGTH), nullable=True
    )
    session_count: Mapped[int] = mapped_column(SmallInteger)
    session_minutes: Mapped[int] = mapped_column(SmallInteger)
    min_attendance_percent: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class CourseTemplateSubject(Audited, Model):
    """One subject of a template (a catalog subject, kept by id)."""

    __tablename__ = "course_template_subjects"
    __table_args__ = (
        UniqueConstraint("template_id", "subject_id", name=SUBJECT_LINK_UNIQUE),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), _template_fk())
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        index=True,
    )


class CourseTemplateModule(Audited, Model):
    """One syllabus module, at ``position`` (1-based) in its template.
    ``materials`` is free-text materials references (no URLs, no files)."""

    __tablename__ = "course_template_modules"
    __table_args__ = (
        _range("position", 1, MODULES_MAX),
        *_text_checks("title"),
        _range("planned_sessions", 1, SESSION_COUNT_MAX),
        UniqueConstraint(
            "template_id",
            "position",
            name=MODULE_POSITION_UNIQUE,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _template_fk(), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(
        String(TEXT_MAX_LENGTH), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    planned_sessions: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    materials: Mapped[str | None] = mapped_column(
        String(TEXT_MAX_LENGTH), nullable=True
    )


class CourseTemplateAssessment(Audited, Model):
    """One assessment definition, at ``position`` (1-based) in its template;
    ``module_id`` ``NULL`` is the whole course, ``min_score_percent``
    ``NULL`` no minimum score."""

    __tablename__ = "course_template_assessments"
    __table_args__ = (
        _range("position", 1, ASSESSMENTS_MAX),
        *_text_checks("title"),
        _range("min_score_percent", PERCENT_MIN, PERCENT_MAX),
        UniqueConstraint(
            "template_id",
            "position",
            name=ASSESSMENT_POSITION_UNIQUE,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _template_fk(), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH))
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # An explicit name: the convention's would pass PostgreSQL's
        # 63-character limit.
        ForeignKey(
            "course_template_modules.id",
            ondelete="RESTRICT",
            name=ASSESSMENT_MODULE_FK,
        ),
        nullable=True,
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    min_score_percent: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class CourseTemplateSnapshot(Audited, Model):
    """The content of a template at ``template_version``, never updated or
    deleted (trigger ``course_template_snapshots_immutable``). ``content``
    follows ``schema_version`` (1: ``courses.templates.build_snapshot``)."""

    __tablename__ = "course_template_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "template_version", name=SNAPSHOT_VERSION_UNIQUE
        ),
    )
    __audit_exclude__: ClassVar[frozenset[str]] = frozenset({"content"})

    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), _template_fk())
    template_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger,
        default=SNAPSHOT_SCHEMA_VERSION,
        server_default=text(str(SNAPSHOT_SCHEMA_VERSION)),
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)


class CourseCohort(Audited, VersionedMixin, Model):
    """One cohort of a course template (#65 A2; spec §3 "each cohort has its
    own dates, tutors, enrolled students, pricing and attendance").

    ``template_snapshot_id`` is set once and never updated: the cohort's
    syllabus is that immutable snapshot. ``template_id`` is copied from the
    snapshot (the list filters by template). ``subject_id`` is the one
    subject of every session (one of the snapshot's subjects, #65 E4).
    The tutor, the room and the timing live on the cohort's ``course``
    series (``class_series.cohort_id``). No price column (#65 E6: M6-04 /
    M6-07 add the price link) and no enrollments (#66)."""

    __tablename__ = "course_cohorts"
    __table_args__ = (
        *_text_checks("name"),
        _range("max_students", 1, COHORT_MAX_STUDENTS_MAX),
        Index(COHORT_NAME_INDEX, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(COHORT_NAME_MAX_LENGTH))
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _template_fk(), index=True
    )
    template_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # An explicit name: the convention's would pass PostgreSQL's
        # 63-character limit.
        ForeignKey(
            "course_template_snapshots.id",
            ondelete="RESTRICT",
            name=COHORT_SNAPSHOT_FK,
        ),
        index=True,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    max_students: Mapped[int] = mapped_column(SmallInteger)
