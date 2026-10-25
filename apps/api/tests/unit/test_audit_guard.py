"""The sensitive-column guard for audited models (M2-04 C4).

Every mapped :class:`~app.core.audit.Audited` class (the application's, through
``app.core.model_registry``, and the test-only ones) must list each column whose
name matches ``password|token|secret|hash`` and each binary column in
``__audit_exclude__`` or ``__audit_mask__``. This stops a later issue from
adding, for example, ``reset_token_hash`` to an audited model and leaking it.
"""

from typing import Any

from sqlalchemy import LargeBinary, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import app.core.model_registry  # noqa: F401  (maps every application model)
from app.core.audit import Audited, audited_table_names, sensitive_column_problems
from app.core.models import Base, StandardColumns
from app.modules.academics.models import SessionCloseout, SessionNote
from app.modules.administration.models import ApprovalRequest
from app.modules.administration.settings_models import (
    BusinessSettings,
    OperatingHours,
    SchedulingPolicy,
)
from app.modules.catalog.models import (
    AcademicPeriod,
    Classroom,
    ClassroomAvailabilityWindow,
    Closure,
    EducationLevel,
    Subject,
)
from app.modules.courses.models import (
    CohortEnrollment,
    CourseCohort,
    CourseTemplate,
    CourseTemplateAssessment,
    CourseTemplateModule,
    CourseTemplateSnapshot,
    CourseTemplateSubject,
)
from app.modules.families.models import (
    ConsentRecord,
    Family,
    FamilyGuardian,
    FamilyStudent,
    Guardian,
    Student,
    StudentEmergencyContact,
    StudentGuardian,
)
from app.modules.identity.models import (
    PrivacyNoticeAcceptance,
    RegistrationRequest,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassGroup,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
    ClassSessionReassignmentFlag,
    ClassSessionSubstitution,
    GroupEnrollment,
    SessionChangeRequest,
    SessionRequest,
)
from app.modules.staff.models import (
    TutorAvailabilityChangeRequest,
    TutorAvailabilityException,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
    TutorPreferredClassroom,
    TutorProfile,
    TutorQualification,
)
from tests.integration.models import AuditedThing, TestBase


def audited_classes(*bases: type[DeclarativeBase]) -> list[type[Any]]:
    return sorted(
        (
            mapper.class_
            for base in bases
            for mapper in base.registry.mappers
            if issubclass(mapper.class_, Audited)
        ),
        key=lambda cls: cls.__name__,
    )


def test_every_audited_model_lists_its_sensitive_columns():
    classes = audited_classes(Base, TestBase)

    problems = [p for cls in classes for p in sensitive_column_problems(cls)]

    assert not problems, (
        "Audited models with unlisted sensitive columns (add them to"
        " __audit_exclude__ or __audit_mask__):\n" + "\n".join(problems)
    )


def test_the_guard_scans_the_real_audited_models():
    # Guards against a guard that silently scans nothing.
    assert audited_classes(Base) == [
        AcademicPeriod,
        ApprovalRequest,
        AssignmentOffer,
        AssignmentOfferSession,
        BusinessSettings,
        ClassGroup,
        ClassSeries,
        ClassSeriesParticipant,
        ClassSession,
        ClassSessionAttendance,
        ClassSessionParticipant,
        ClassSessionReassignmentFlag,
        ClassSessionSubstitution,
        Classroom,
        ClassroomAvailabilityWindow,
        Closure,
        CohortEnrollment,
        ConsentRecord,
        CourseCohort,
        CourseTemplate,
        CourseTemplateAssessment,
        CourseTemplateModule,
        CourseTemplateSnapshot,
        CourseTemplateSubject,
        EducationLevel,
        Family,
        FamilyGuardian,
        FamilyStudent,
        GroupEnrollment,
        Guardian,
        OperatingHours,
        PrivacyNoticeAcceptance,
        RegistrationRequest,
        SchedulingPolicy,
        SessionChangeRequest,
        SessionCloseout,
        SessionNote,
        SessionRequest,
        Student,
        StudentEmergencyContact,
        StudentGuardian,
        Subject,
        TutorAvailabilityChangeRequest,
        TutorAvailabilityException,
        TutorAvailabilitySettings,
        TutorAvailabilityWindow,
        TutorPreferredClassroom,
        TutorProfile,
        TutorQualification,
        User,
        UserDelegation,
        UserRole,
    ]
    assert AuditedThing in audited_classes(TestBase)
    assert User.__audit_exclude__ == {"password_hash"}
    # The catalog (#32 A4) has no sensitive column: nothing excluded or masked.
    # Student profiles (#33 A5): no sensitive column either; personal fields
    # are audited unmasked (spec §9 prior and new values). Guardian profiles
    # and links (#34 A5), families and their membership (#172 A3): the same.
    # Academic periods and closures (#38 D1), classrooms and their windows
    # (#39 C1), approval requests (#40 E1), tutor profiles (#43 D1),
    # qualifications (#44 E1), availability (#45 E1) and preferred
    # classrooms (#197), class sessions and their participants (#47 D6): the
    # same.
    for unmasked in (
        ApprovalRequest,
        EducationLevel,
        Subject,
        AcademicPeriod,
        Closure,
        Classroom,
        ClassroomAvailabilityWindow,
        Student,
        StudentEmergencyContact,
        Guardian,
        StudentGuardian,
        Family,
        FamilyStudent,
        FamilyGuardian,
        TutorProfile,
        TutorQualification,
        TutorAvailabilitySettings,
        TutorAvailabilityWindow,
        TutorAvailabilityException,
        TutorPreferredClassroom,
        ClassSession,
        ClassSessionParticipant,
        ClassSeries,
        ClassSeriesParticipant,
        CourseTemplate,
        CourseTemplateSubject,
        CourseTemplateModule,
        CourseTemplateAssessment,
    ):
        assert unmasked.__audit_exclude__ == frozenset()
        assert unmasked.__audit_mask__ == frozenset()
    # #64: a snapshot's content is the immutable row itself, not repeated in
    # its create event.
    assert CourseTemplateSnapshot.__audit_exclude__ == {"content"}
    assert {"users", "user_roles", "user_delegations", "audited_things"} <= set(
        audited_table_names()
    )
    assert not {"roles", "permissions", "role_permissions"} & audited_table_names()
    # #36 A4: a UI preference is not an important record.
    assert "user_preferences" not in audited_table_names()
    # #78 A2: approval revisions and staff corrections are append-only; their
    # named events are their record (the #76 E3 pattern).
    assert (
        not {
            "session_closeout_approvals",
            "session_closeout_corrections",
        }
        & audited_table_names()
    )


# --- Self-test on synthetic classes -------------------------------------------


class GuardBase(DeclarativeBase):
    metadata = MetaData()


class Leaky(Audited, StandardColumns, GuardBase):
    __tablename__ = "guard_leaky"

    reset_token_hash: Mapped[str] = mapped_column(Text)
    ApiSecret: Mapped[str] = mapped_column(Text)
    user_password: Mapped[str] = mapped_column(String(10))
    photo: Mapped[bytes] = mapped_column(LargeBinary)
    note: Mapped[str] = mapped_column(Text)


class Listed(Audited, StandardColumns, GuardBase):
    __tablename__ = "guard_listed"
    __audit_exclude__ = frozenset({"reset_token_hash", "photo"})
    __audit_mask__ = frozenset({"ApiSecret", "user_password"})

    reset_token_hash: Mapped[str] = mapped_column(Text)
    ApiSecret: Mapped[str] = mapped_column(Text)
    user_password: Mapped[str] = mapped_column(String(10))
    photo: Mapped[bytes] = mapped_column(LargeBinary)
    note: Mapped[str] = mapped_column(Text)


def test_the_guard_reports_planted_violations():
    assert sensitive_column_problems(Leaky) == [
        "Leaky.reset_token_hash: sensitive name",
        "Leaky.ApiSecret: sensitive name",
        "Leaky.user_password: sensitive name",
        "Leaky.photo: binary column",
    ]


def test_the_guard_accepts_excluded_and_masked_columns():
    assert sensitive_column_problems(Listed) == []
