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
    # approval_requests, M3-04);
    # audit_events
    # (M2-04) and
    # rate_limit_counters (M2-09) are shared infrastructure.
    assert target_metadata is Base.metadata
    assert set(Base.metadata.tables) == {
        "audit_events",
        "approval_requests",
        "academic_periods",
        "closures",
        "classrooms",
        "classroom_availability_windows",
        "users",
        "roles",
        "permissions",
        "role_permissions",
        "user_roles",
        "user_delegations",
        "auth_sessions",
        "rate_limit_counters",
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
