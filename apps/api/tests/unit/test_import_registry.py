"""The import kinds registry and the two kinds' row checks (#122 A1-A3, B4-B6).

The pipeline itself (staging, commit, expiry) runs against PostgreSQL in
``tests/integration/test_imports_pipeline.py``.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.administration.imports import registry
from app.modules.administration.imports.registry import (
    CommitOutcome,
    ImportColumn,
    ImportRowRef,
    RowIssue,
    RowResult,
    import_kind,
    importable_kinds,
    register_import_kind,
    registered_import_kinds,
    unregister_import_kind,
)
from app.modules.billing import service as billing_service
from app.modules.families import imports as student_imports
from app.modules.families import service as families_service
from app.modules.identity import imports as tutor_imports
from app.modules.identity import service as identity_service
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    resolve_permissions,
)
from app.modules.scheduling import service as scheduling_service

TODAY = date(2026, 11, 5)


@dataclass(frozen=True)
class FakeKind:
    """A kind with the #123 seams: its own batch size and a failed outcome
    with column errors."""

    code: str = "fake_things"
    permission: str = "student.manage"
    columns: Sequence[ImportColumn] = (ImportColumn("name", required=True),)
    batch_size: int = 7

    def may_import(self, principal: Principal) -> bool:
        return principal.has(self.permission)

    async def prepare(
        self,
        session: AsyncSession,
        rows: Sequence[Mapping[str, str]],
        principal: Principal | None,
    ) -> list[RowResult]:
        return [RowResult("ready") for _ in rows]

    async def commit_row(
        self,
        session: AsyncSession,
        row_values: Mapping[str, str],
        ref: ImportRowRef,
        actor_user_id: uuid.UUID,
    ) -> CommitOutcome:
        return CommitOutcome.failed(
            "scheduling_conflict", [RowIssue(None, "tutor_overlap")]
        )


def principal(*roles: str) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        roles=frozenset(roles),
        permissions=resolve_permissions("active", roles, ()),
    )


@pytest.fixture
def fake() -> FakeKind:
    kind = FakeKind()
    register_import_kind(kind)
    yield kind
    unregister_import_kind(kind.code)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_registering_is_idempotent_and_a_second_object_raises(fake):
    register_import_kind(fake)

    with pytest.raises(ValueError, match="already registered"):
        register_import_kind(FakeKind())
    assert import_kind("fake_things") is fake
    assert "fake_things" in registered_import_kinds()


@pytest.mark.parametrize(
    "change",
    [
        {"code": "Fake"},
        {"code": "ab"},
        {"code": "x" * 33},
        {"permission": "nope"},
        {"columns": ()},
        {"columns": (ImportColumn("a"), ImportColumn("a"))},
        {"columns": (ImportColumn("Name"),)},
        {"columns": tuple(ImportColumn(f"c{i}") for i in range(31))},
        {"batch_size": 0},
    ],
)
def test_a_malformed_kind_is_refused(change):
    with pytest.raises(ValueError):
        register_import_kind(FakeKind(**change))
    assert "fake_things" not in registered_import_kinds()


@pytest.mark.anyio
async def test_the_seams_for_issue_123(fake):
    # #122 A2 (#123 E7): commit_row gets the row reference, a failed outcome
    # may carry column errors, and the kind sets its batch size.
    outcome = await fake.commit_row(
        None,  # type: ignore[arg-type]
        {"name": "x"},
        ImportRowRef(uuid.uuid4(), 2),
        uuid.uuid4(),
    )

    assert outcome == CommitOutcome(
        status="failed",
        code="scheduling_conflict",
        errors=(RowIssue(None, "tutor_overlap"),),
    )
    assert fake.batch_size == 7
    assert registry.DEFAULT_BATCH_SIZE == 100
    assert CommitOutcome.created(uuid.UUID(int=1)).record_id == uuid.UUID(int=1)
    assert CommitOutcome.skipped("duplicate_existing").code == "duplicate_existing"


def test_who_may_import_what():
    # #122 E5, #123 E1: the permission manual creation needs.
    every = ["opening_balances", "schedules", "students", "tutors"]
    assert importable_kinds(principal(OWNER)) == every
    assert importable_kinds(principal(ADMINISTRATOR)) == every
    assert importable_kinds(principal(ACADEMIC_COORDINATOR)) == ["students"]
    assert importable_kinds(principal(FINANCE_STAFF)) == ["opening_balances"]
    for role in ("tutor", "student", "guardian"):
        assert importable_kinds(principal(role)) == []


def test_the_kinds_are_exported_by_their_modules_and_registered_by_the_wiring():
    assert families_service.STUDENTS_IMPORT_KIND is student_imports.STUDENTS_IMPORT_KIND
    assert identity_service.TUTORS_IMPORT_KIND is tutor_imports.TUTORS_IMPORT_KIND
    assert import_kind("students") is families_service.STUDENTS_IMPORT_KIND
    assert import_kind("tutors") is identity_service.TUTORS_IMPORT_KIND
    assert import_kind("schedules") is scheduling_service.SCHEDULES_IMPORT_KIND
    assert (
        import_kind("opening_balances") is billing_service.OPENING_BALANCES_IMPORT_KIND
    )
    assert administration.register_import_kind is register_import_kind


def test_the_123_kinds_permissions_and_batch_sizes():
    schedules = scheduling_service.SCHEDULES_IMPORT_KIND
    balances = billing_service.OPENING_BALANCES_IMPORT_KIND
    assert (schedules.code, schedules.permission, schedules.batch_size) == (
        "schedules",
        "schedule.manage",
        20,
    )
    assert (balances.code, balances.permission, balances.batch_size) == (
        "opening_balances",
        "billing.manage",
        100,
    )
    assert [c.name for c in schedules.columns if c.required] == [
        "student_given_names",
        "student_family_names",
        "student_birthdate",
        "subject",
        "education_level",
        "starts_on",
        "start_time",
        "duration_minutes",
        "weekdays",
    ]
    assert [c.name for c in schedules.columns if not c.required] == [
        "interval",
        "until",
        "count",
        "tutor_username",
        "classroom",
    ]
    assert [c.name for c in balances.columns] == [
        "student_given_names",
        "student_family_names",
        "student_birthdate",
        "description",
        "amount",
        "service_date",
        "due_date",
    ]
    assert all(c.required for c in balances.columns)


KINDS = ("students", "tutors", "schedules", "opening_balances")


def test_the_cli_and_the_worker_register_the_import_kinds():
    from app import cli, worker

    for run in (
        lambda: pytest.raises(SystemExit, cli.main, ["bootstrap-owner"]),
        lambda: worker.main([]),
    ):
        for code in KINDS:
            unregister_import_kind(code)
        run()
        assert set(KINDS) <= registered_import_kinds()


# --- Students (B5) -----------------------------------------------------------------


def student_row(**change: str) -> dict[str, str]:
    row = {
        "given_names": "Ana",
        "family_names": "Pérez",
        "birthdate": "2012-03-04",
        "contact_name": "María",
        "contact_relationship": "Madre",
        "contact_phone": "55 1234 5678",
    }
    row.update(change)
    return row


def student_codes(row: Mapping[str, str], levels: Mapping[str, uuid.UUID] = {}):  # noqa: B006
    checked = student_imports.check_row(row, levels, TODAY)
    return checked.body, {(issue.column, issue.code) for issue in checked.errors}


def test_a_good_student_row():
    level = uuid.uuid4()
    body, errors = student_codes(
        student_row(
            birthdate="04/03/2012",
            education_level="Secundaria",
            email="ana@example.com",
            contact2_name="Luis",
            contact2_relationship="Padre",
            contact2_phone="+52 55 8765 4321",
        ),
        {"secundaria": level},
    )

    assert errors == set()
    assert body is not None
    assert body.birthdate == date(2012, 3, 4)
    assert body.education_level_id == level
    assert [c.name for c in body.emergency_contacts] == ["María", "Luis"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"given_names": ""}, ("given_names", "required")),
        ({"family_names": "x" * 101}, ("family_names", "too_long")),
        ({"birthdate": ""}, ("birthdate", "required")),
        ({"birthdate": "2012-02-30"}, ("birthdate", "invalid_date")),
        ({"birthdate": "3/4/12"}, ("birthdate", "invalid_date")),
        ({"birthdate": "1899-12-31"}, ("birthdate", "invalid_date")),
        ({"birthdate": "2026-11-06"}, ("birthdate", "future_date")),
        ({"email": "ana@"}, ("email", "invalid_email")),
        ({"phone": "12"}, ("phone", "invalid_phone")),
        ({"contact_phone": "abc"}, ("contact_phone", "invalid_phone")),
        ({"contact_name": ""}, ("contact_name", "required")),
        ({"contact2_name": "Luis"}, ("contact2_relationship", "required")),
        ({"education_level": "Nope"}, ("education_level", "education_level_unknown")),
    ],
)
def test_student_row_codes(change, expected):
    body, errors = student_codes(student_row(**change))

    assert body is None
    assert expected in errors


def test_the_worker_recheck_reports_a_level_gone_as_unavailable():
    checked = student_imports.check_row(
        student_row(education_level="Prepa"),
        {},
        TODAY,
        level_code="education_level_unavailable",
    )

    assert [(i.column, i.code) for i in checked.errors] == [
        ("education_level", "education_level_unavailable")
    ]


def test_a_formula_is_kept_as_text():
    # #122 F4: never evaluated.
    body, errors = student_codes(student_row(school_name='=HYPERLINK("x")'))

    assert errors == set()
    assert body is not None and body.school_name == '=HYPERLINK("x")'


# --- Tutors (B6) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"username": "", "display_name": "Ana"}, ("username", "required")),
        ({"username": "a", "display_name": "Ana"}, ("username", "username_invalid")),
        ({"username": "añá", "display_name": "Ana"}, ("username", "username_invalid")),
        ({"username": "ana.p", "display_name": " "}, ("display_name", "required")),
        (
            {"username": "ana.p", "display_name": "x" * 201},
            ("display_name", "too_long"),
        ),
        (
            {"username": "ana.p", "display_name": "Ana", "email": "x"},
            ("email", "invalid_email"),
        ),
        (
            {"username": "ana.p", "display_name": "Ana", "phone": "x"},
            ("phone", "invalid_phone"),
        ),
    ],
)
def test_tutor_row_codes(row, expected):
    item, errors = tutor_imports.check_row(row)

    assert item is None
    assert expected in {(issue.column, issue.code) for issue in errors}


def test_a_good_tutor_row_is_normalized():
    item, errors = tutor_imports.check_row(
        {"username": "  Ana.Perez ", "display_name": " Ana Pérez ", "email": ""}
    )

    assert errors == []
    assert item is not None
    assert item.username == "ana.perez"
    assert item.row.display_name == "Ana Pérez"
    assert item.row.email is None
