"""Reference seeds without a database (M1-17 B1, B2, B3)."""

from typing import Any

import pytest
from sqlalchemy import Index, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.models import StandardColumns
from app.modules.catalog.models import EducationLevel
from app.modules.identity.models import User, UserDelegation, UserRole
from app.modules.identity.permissions import PERMISSIONS, ROLE_CODES, ROLE_PERMISSIONS
from app.seeds import runner
from app.seeds.education_levels import InsertOnlySeed
from app.seeds.registry import REFERENCE_SEEDS, UpsertSeed
from app.seeds.role_permissions import RolePermissionsSeed
from app.seeds.upsert import UpsertResult, upsert_rows
from tests.integration.models import SeedReference, VersionedSeedReference


class UnitBase(DeclarativeBase):
    """Never created in a database (keeps TestBase's tables unchanged)."""


class NoKeyReference(StandardColumns, UnitBase):
    """A table whose ``code`` has no unique constraint or index."""

    __tablename__ = "unit_no_key_references"

    code: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(100))


class IndexedReference(StandardColumns, UnitBase):
    """``code`` is unique through a unique index, not a constraint."""

    __tablename__ = "unit_indexed_references"
    __table_args__ = (Index("ix_unit_indexed_code", "code", unique=True),)

    code: Mapped[str] = mapped_column(String(40))
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)


class CapturingSession:
    """Records the statement instead of running it."""

    def __init__(self) -> None:
        self.sql = ""

    async def scalars(self, statement: Any) -> list[bool]:
        self.sql = str(statement.compile(dialect=postgresql.dialect()))
        return [True, False]


class UntouchableSession:
    """Fails the test if the helper uses the session at all."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"session.{name} was used")


SESSION: Any = UntouchableSession()


def test_reference_seed_names_are_unique():
    names = [seed.name for seed in REFERENCE_SEEDS]

    assert len(names) == len(set(names))


def test_registry_is_an_ordered_tuple():
    assert isinstance(REFERENCE_SEEDS, tuple)


def test_identity_seeds_are_registered_in_dependency_order():
    assert [seed.name for seed in REFERENCE_SEEDS][:3] == [
        "identity.permissions",
        "identity.roles",
        "identity.role_permissions",
    ]


def test_education_levels_come_after_the_identity_seeds():
    # Insert-only through the ORM (#32 B1, B2), so not an UpsertSeed.
    names = [seed.name for seed in REFERENCE_SEEDS]
    assert names == [
        "identity.permissions",
        "identity.roles",
        "identity.role_permissions",
        "catalog.education_levels",
    ]
    seed = REFERENCE_SEEDS[3]
    assert isinstance(seed, InsertOnlySeed)
    assert seed.model is EducationLevel
    assert list(seed.rows) == [
        {"seed_key": "elementary", "name": "Primaria", "sort_order": 10},
        {"seed_key": "middle_school", "name": "Secundaria", "sort_order": 20},
        {"seed_key": "high_school", "name": "Preparatoria", "sort_order": 30},
        {"seed_key": "university", "name": "Universidad", "sort_order": 40},
    ]


def test_identity_seed_rows_come_from_the_catalog_constants():
    permissions, roles, role_permissions = REFERENCE_SEEDS[:3]

    assert isinstance(permissions, UpsertSeed)
    assert isinstance(roles, UpsertSeed)
    assert list(permissions.rows) == [
        {"code": p.code, "delegable": p.delegable} for p in PERMISSIONS
    ]
    assert list(roles.rows) == [{"code": code} for code in ROLE_CODES]
    assert isinstance(role_permissions, RolePermissionsSeed)
    assert role_permissions.mapping is ROLE_PERMISSIONS


def test_no_reference_seed_writes_identity_users_or_credentials():
    # F5 (M1-17 rule): reference seeds never create accounts or credentials.
    for seed in REFERENCE_SEEDS:
        model = getattr(seed, "model", None)
        assert model not in {User, UserRole, UserDelegation}
        for row in getattr(seed, "rows", ()):
            assert not any("password" in key for key in row)


@pytest.mark.anyio
async def test_duplicate_natural_key_raises_before_any_sql():
    rows = [{"code": "a", "label": "A"}, {"code": "a", "label": "Otra"}]

    with pytest.raises(ValueError, match="duplicate natural key in row 1"):
        await upsert_rows(SESSION, SeedReference, rows)


@pytest.mark.anyio
async def test_empty_input_does_nothing():
    assert await upsert_rows(SESSION, SeedReference, []) == UpsertResult(0, 0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("model", "rows", "key", "message"),
    [
        (NoKeyReference, [{"code": "a", "label": "A"}], ("code",), "unique"),
        (SeedReference, [{"code": "a", "label": "A"}], ("label",), "unique"),
        (SeedReference, [{"code": "a", "label": "A"}], (), "at least one"),
        (SeedReference, [{"code": "a", "id": None}], ("code",), "must not set"),
        (SeedReference, [{"code": "a", "nope": 1}], ("code",), "unknown columns"),
        (
            SeedReference,
            [{"code": "a", "label": "A"}, {"code": "b"}],
            ("code",),
            "different columns",
        ),
    ],
)
async def test_invalid_input_raises_before_any_sql(model, rows, key, message):
    with pytest.raises(ValueError, match=message):
        await upsert_rows(SESSION, model, rows, key=key)


@pytest.mark.anyio
async def test_rows_must_include_the_key():
    with pytest.raises(ValueError, match="must include the key"):
        await upsert_rows(SESSION, SeedReference, [{"label": "A"}], key=("code",))


def test_upsert_seed_is_a_reference_seed():
    seed = UpsertSeed(name="x", model=SeedReference, rows=())

    assert seed.name == "x"
    assert seed.key == ("code",)


def test_summary_lists_names_and_counts_only():
    lines = runner.seed_summary([("roles", UpsertResult(inserted=2, updated=1))])

    assert lines == ["roles: 2 inserted, 1 updated"]


def test_summary_shows_deleted_only_when_non_zero():
    lines = runner.seed_summary(
        [
            ("a", UpsertResult(inserted=0, updated=0, deleted=1)),
            ("b", UpsertResult(inserted=1, updated=0, deleted=0)),
        ]
    )

    assert lines == ["a: 0 inserted, 0 updated, 1 deleted", "b: 1 inserted, 0 updated"]


def test_summary_of_an_empty_registry():
    assert runner.seed_summary([]) == ["No reference seeds are registered."]


def test_alembic_heads_reads_the_migration_scripts():
    assert runner.alembic_heads() == {"0024"}


def test_configuration_error_exits_1_without_traceback(capsys):
    def no_engine(settings: Any) -> Any:
        raise AssertionError("no engine may be created")

    assert runner.main(engine_factory=no_engine) == 1

    err = capsys.readouterr().err
    assert err.startswith("Configuration error: the API cannot start.")
    assert "APP_ENV" in err
    assert "Traceback" not in err


def test_configuration_error_never_prints_the_dsn(capsys, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "mysql://u:s3cr3t@h/d")
    monkeypatch.setenv("DATABASE_DIRECT_URL", "mysql://u:s3cr3t@h/d")

    assert runner.main() == 1

    err = capsys.readouterr().err
    assert "DATABASE_DIRECT_URL" in err
    assert "s3cr3t" not in err


@pytest.mark.anyio
async def test_statement_updates_only_distinct_rows():
    session: Any = CapturingSession()

    result = await upsert_rows(session, SeedReference, [{"code": "a", "label": "A"}])

    assert result == UpsertResult(inserted=1, updated=1)
    assert "ON CONFLICT (code) DO UPDATE SET label = excluded.label" in session.sql
    assert "updated_at = now()" in session.sql
    assert "WHERE seed_references.label IS DISTINCT FROM excluded.label" in session.sql
    assert "version" not in session.sql
    assert "note" not in session.sql
    assert "DELETE" not in session.sql


@pytest.mark.anyio
async def test_versioned_statement_increments_version():
    session: Any = CapturingSession()

    await upsert_rows(session, VersionedSeedReference, [{"code": "a", "label": "A"}])

    assert "version = (versioned_seed_references.version +" in session.sql


@pytest.mark.anyio
async def test_unique_index_is_accepted_and_key_only_rows_do_nothing():
    session: Any = CapturingSession()

    await upsert_rows(session, IndexedReference, [{"code": "a"}])

    assert "ON CONFLICT (code) DO NOTHING" in session.sql
