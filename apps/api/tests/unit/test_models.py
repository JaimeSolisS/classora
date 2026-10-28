"""Naming convention (C1), version helper (C4 b) and the 409 rendering."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.conventions import install_conventions
from app.core.model_registry import target_metadata
from app.core.models import (
    NAMING_CONVENTION,
    Base,
    StaleVersionError,
    VersionedMixin,
    ensure_version,
)


def test_base_metadata_uses_the_naming_convention():
    assert dict(Base.metadata.naming_convention) == {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


def test_app_metadata_holds_only_the_registered_domain_tables():
    # Domain tables arrive with their own migrations (C9); the first module is
    # identity (M2-02, auth_sessions M2-05, account_tokens M2-11,
    # registration_requests M2-11b), then catalog (education_levels and
    # subjects, M2-14), then families (students and their emergency
    # contacts, M2-15; guardians and student_guardians, M2-16; families,
    # family_students and family_guardians, M2-16b; privacy_notice_versions
    # and privacy_notice_acceptances, M2-17b; consent_records, M2-17;
    # user_preferences, M2-18), then administration (business_settings and
    # operating_hours, M3-01; academic_periods and closures in catalog, M3-02;
    # classrooms and classroom_availability_windows in catalog, M3-03;
    # approval_requests, M3-04; notifications, M3-05; tutor_profiles in
    # staff, M3-07; tutor_qualifications in staff, M3-08;
    # tutor_availability_settings, _windows and _exceptions in staff, M3-09;
    # tutor_preferred_classrooms in staff, M3-09b), then scheduling
    # (class_sessions and class_session_participants, M4-02;
    # scheduling_policies in administration, M4-16; class_session_overrides,
    # M4-04; assignment_offers and assignment_offer_sessions, M4-07;
    # class_series and class_series_participants, M4-08;
    # class_session_substitutions, M4-10; class_groups and group_enrollments,
    # M4-11; session_requests, M4-14; session_change_requests, M4-17;
    # class_session_reassignment_flags and, in staff,
    # tutor_availability_change_requests, M4-18; class_session_attendance and
    # class_session_attendance_corrections, M5-09), academics
    # (session_closeouts, M5-10; session_closeout_approvals and
    # session_closeout_corrections, M5-11; session_notes, M5-12;
    # evaluation_templates, evaluation_template_criteria, evaluations and
    # evaluation_scores, M5-13; learning_plans, learning_plan_objectives and
    # learning_plan_milestones, M5-14; assignments and
    # assignment_submissions, M5-15; progress_reports, M5-16;
    # progress_report_schedules, M5-16c), then
    # courses (course_templates, _subjects, _modules, _assessments and
    # _snapshots, M4-19; course_cohorts, M4-20; cohort_enrollments, M4-21);
    # audit_events
    # (M2-04) and
    # rate_limit_counters (M2-09) and idempotency_keys (M6-03) are shared
    # infrastructure; billing (service_prices and price_versions, M6-04;
    # billing_accounts, payment_methods, charges, charge_adjustments,
    # payments, payment_reversals, payment_allocations, allocation_releases
    # and refunds, M6-05).
    assert target_metadata is Base.metadata
    assert set(Base.metadata.tables) == {
        "audit_events",
        "approval_requests",
        "notifications",
        "academic_periods",
        "closures",
        "classrooms",
        "classroom_availability_windows",
        "tutor_profiles",
        "tutor_qualifications",
        "tutor_availability_settings",
        "tutor_availability_windows",
        "tutor_availability_exceptions",
        "tutor_preferred_classrooms",
        "tutor_availability_change_requests",
        "class_sessions",
        "class_session_participants",
        "class_session_overrides",
        "assignment_offers",
        "assignment_offer_sessions",
        "class_series",
        "class_series_participants",
        "class_session_substitutions",
        "class_groups",
        "group_enrollments",
        "session_requests",
        "session_change_requests",
        "class_session_reassignment_flags",
        "class_session_attendance",
        "class_session_attendance_corrections",
        "session_closeouts",
        "session_closeout_approvals",
        "session_closeout_corrections",
        "session_notes",
        "evaluation_templates",
        "evaluation_template_criteria",
        "evaluations",
        "evaluation_scores",
        "learning_plans",
        "learning_plan_objectives",
        "learning_plan_milestones",
        "assignments",
        "assignment_submissions",
        "progress_reports",
        "progress_report_schedules",
        "users",
        "roles",
        "permissions",
        "role_permissions",
        "user_roles",
        "user_delegations",
        "auth_sessions",
        "rate_limit_counters",
        "idempotency_keys",
        "service_prices",
        "price_versions",
        "billing_accounts",
        "payment_methods",
        "charges",
        "charge_adjustments",
        "payments",
        "payment_reversals",
        "payment_allocations",
        "allocation_releases",
        "refunds",
        "account_tokens",
        "registration_requests",
        "education_levels",
        "subjects",
        "students",
        "student_emergency_contacts",
        "guardians",
        "student_guardians",
        "families",
        "family_students",
        "family_guardians",
        "privacy_notice_versions",
        "privacy_notice_acceptances",
        "consent_records",
        "user_preferences",
        "business_settings",
        "operating_hours",
        "scheduling_policies",
        "course_templates",
        "course_template_subjects",
        "course_template_modules",
        "course_template_assessments",
        "course_template_snapshots",
        "course_cohorts",
        "cohort_enrollments",
    }


def test_generated_constraint_and_index_names_are_deterministic():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id")),
        Column("email", String(100)),
        Column("age", Integer),
        UniqueConstraint("email"),
        CheckConstraint("age >= 0", name="age_not_negative"),
    )
    index = Index(None, child.c.parent_id)

    names = {constraint.name for constraint in child.constraints}
    assert names == {
        "pk_child",
        "uq_child_email",
        "ck_child_age_not_negative",
        "fk_child_parent_id_parent",
    }
    ddl = str(CreateTable(child).compile(dialect=postgresql.dialect()))
    for name in names:
        assert f"CONSTRAINT {name} " in ddl
    index_ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert index_ddl.startswith("CREATE INDEX ix_child_parent_id ON child")


class _Loaded(VersionedMixin):
    def __init__(self, version: int) -> None:
        self.version = version


def test_ensure_version_accepts_the_current_version():
    ensure_version(_Loaded(3), 3)


@pytest.mark.parametrize("expected", [2, 4])
def test_ensure_version_rejects_another_version(expected):
    with pytest.raises(StaleVersionError):
        ensure_version(_Loaded(3), expected)


def test_stale_version_error_renders_as_409_version_conflict():
    app = FastAPI()
    install_conventions(app)

    @app.get("/stale")
    async def stale() -> None:
        raise StaleVersionError()

    with TestClient(app) as client:
        response = client.get("/stale", headers={"X-Request-ID": "req-409"})

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "code": "version_conflict",
        "request_id": "req-409",
        "detail": (
            "The resource was changed by another request. Reload it and try again."
        ),
    }
