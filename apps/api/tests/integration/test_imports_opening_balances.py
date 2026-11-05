"""The ``opening_balances`` import kind end to end (#123 C1-C5, D1, E1, E2;
E2, E6, E8, E10).

The clock is frozen at :data:`payment_support.NOW` (noon on 2031-03-10,
center zone), so the center's today is ``TODAY``. The task runs in-process
through :func:`run_job`, under the same frozen clock. Every money test
checks ``Σ charges`` before and after.
"""

import asyncio
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration.imports import service
from app.modules.administration.imports.registry import ImportRowRef
from app.modules.billing.service import OPENING_BALANCES_IMPORT_KIND
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import payment_support as ps
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.import_support import (
    IMPORTS,
    action,
    clear_queue,
    codes,
    csv_text,
    job_path,
    post_import,
    principal_of,
    rows_of,
    run_job,
    unique,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import events, in_uow

TODAY = ps.TODAY
NOW = ps.NOW
HEADER = (
    "student_given_names",
    "student_family_names",
    "student_birthdate",
    "description",
    "amount",
    "service_date",
    "due_date",
)
UNTOUCHED = (
    "payments",
    "payment_allocations",
    "receipts",
    "number_series",
    "charge_adjustments",
    "refunds",
)
"""The money tables an import never writes (C5)."""


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    return {
        role: create_user(module_seeded_url, roles=(role,))
        for role in (
            OWNER,
            ADMINISTRATOR,
            ACADEMIC_COORDINATOR,
            FINANCE_STAFF,
            TUTOR,
            STUDENT,
            GUARDIAN,
        )
    }


@pytest.fixture
def finance(client, users) -> Any:
    with frozen_clock(NOW):
        return sign_in(client, users[FINANCE_STAFF].username)


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    with frozen_clock(NOW):
        yield


@pytest.fixture(autouse=True)
def empty_queue(module_seeded_url: str) -> Iterator[None]:
    yield
    clear_queue(module_seeded_url)


# --- Data -----------------------------------------------------------------------


def family(url: str, students: int = 1) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A new family whose students have unique family names."""
    family_id, members = ps.family(url, students)
    for member in members:
        rename(url, member)
    return family_id, members


def rename(url: str, student: uuid.UUID, **values: Any) -> None:
    run_sql(
        url,
        "UPDATE students SET family_names = :f WHERE id = :i",
        {"f": values.get("family_names", unique("Saldo")), "i": student},
    )


def key(url: str, student: uuid.UUID) -> dict[str, str]:
    [row] = run_sql(
        url,
        "SELECT given_names, family_names, birthdate FROM students WHERE id = :i",
        {"i": student},
    )
    return {
        "student_given_names": row.given_names,
        "student_family_names": row.family_names.upper(),
        "student_birthdate": row.birthdate.strftime("%d/%m/%Y"),
    }


def line(url: str, student: uuid.UUID, **change: str) -> dict[str, str]:
    values = {
        **key(url, student),
        "description": "Colegiatura febrero 2031",
        "amount": "1250.50",
        "service_date": (TODAY - timedelta(days=20)).isoformat(),
        "due_date": (TODAY - timedelta(days=5)).isoformat(),
    }
    values.update(change)
    return values


def stage(
    client: TestClient, cookie: Any, rows: Sequence[Mapping[str, str]], **options: Any
) -> dict[str, Any]:
    response = post_import(
        client,
        cookie,
        "opening_balances",
        csv_text(HEADER, rows, **options),
        "saldos.csv",
    )
    assert response.status_code == 201, response.text
    job: dict[str, Any] = response.json()
    return job


def commit_and_run(client: TestClient, cookie: Any, url: str, job_id: Any) -> None:
    response = action(client, cookie, job_id, "commit")
    assert response.status_code == 202, response.text
    run_job(url)


def items(client: TestClient, cookie: Any, job_id: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = rows_of(client, cookie, job_id)["items"]
    return found


def charges_of(url: str, *students: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charges WHERE student_id = ANY(:s) ORDER BY posted_at, id",
        {"s": list(students)},
    )


def total(url: str, *students: uuid.UUID) -> Decimal:
    [row] = run_sql(
        url,
        "SELECT COALESCE(SUM(amount), 0) AS total FROM charges"
        " WHERE student_id = ANY(:s)",
        {"s": list(students)},
    )
    return Decimal(row.total)


def table_counts(url: str) -> dict[str, int]:
    return {
        table: int(
            run_sql(url, f"SELECT count(*) AS n FROM {table}")[0].n  # noqa: S608
        )
        for table in UNTOUCHED
    }


def outstanding(url: str, family_id: uuid.UUID) -> Decimal:
    balance = cs.balances(url, family_id)
    return Decimal(0) if balance is None else Decimal(str(balance.outstanding))


# --- C1, C3, C5: a good file ------------------------------------------------------


def test_c3_c5_a_good_file_posts_manual_charges(
    client, finance, users, module_seeded_url
):
    url = module_seeded_url
    first_family, (ana, luis) = family(url, 2)
    second_family, (sofia,) = family(url, 1)
    # The second family has no billing account yet: it is created.
    assert ps.account_of(url, second_family) is None
    before_outstanding = outstanding(url, first_family)
    before_counts = table_counts(url)
    rows = [
        line(url, ana),
        line(url, luis, amount="300", description="Libros 2030"),
        line(
            url,
            sofia,
            amount="99.9",
            service_date=TODAY.strftime("%d/%m/%Y"),
            due_date=(TODAY + timedelta(days=366)).isoformat(),
        ),
    ]

    job = stage(client, finance, rows, delimiter=";", bom=True, newline="\r\n")
    assert job["counts"]["ready"] == 3
    commit_and_run(client, finance, url, job["id"])
    done = get(client, finance, job_path(job["id"])).json()
    report = items(client, finance, job["id"])

    assert done["status"] == "committed" and done["counts"]["created"] == 3
    posted = charges_of(url, ana, luis, sofia)
    assert [str(c.id) for c in posted] == [r["record_id"] for r in report]
    assert [(c.student_id, c.amount) for c in posted] == [
        (ana, Decimal("1250.50")),
        (luis, Decimal("300.00")),
        (sofia, Decimal("99.90")),
    ]
    for charge, item in zip(posted, report, strict=True):
        assert charge.kind == "manual"
        assert charge.list_amount == charge.amount
        assert charge.discount_amount == 0
        assert str(charge.import_job_id) == job["id"]
        assert charge.import_row_number == item["row_number"]
        assert charge.posted_by_user_id == users[FINANCE_STAFF].id
        [created] = events(url, record_type="charges", record_id=charge.id)
        assert created.action == "create"
        assert created.actor_user_id == users[FINANCE_STAFF].id
    assert posted[0].due_date == TODAY - timedelta(days=5)
    assert posted[0].service_date == TODAY - timedelta(days=20)
    assert posted[2].due_date == TODAY + timedelta(days=366)
    account = ps.account_of(url, second_family)
    assert account is not None and posted[2].billing_account_id == account
    # C5: Σ created amounts on the summary; no other money row; invariants.
    assert outstanding(url, first_family) - before_outstanding == Decimal("1550.50")
    assert table_counts(url) == before_counts
    for family_id in (first_family, second_family):
        found = ps.account_of(url, family_id)
        cs.assert_invariants(url, found)


# --- C2: staging errors -------------------------------------------------------------


def test_c2_every_staging_code(client, finance, module_seeded_url):
    url = module_seeded_url
    _, (good,) = family(url)
    _, (twin_a,) = family(url)
    _, (twin_b,) = family(url)
    twins = unique("Gemelo")
    for twin in (twin_a, twin_b):
        run_sql(
            url,
            "UPDATE students SET given_names = 'Gemelo', family_names = :f,"
            " birthdate = '2015-05-05' WHERE id = :i",
            {"f": twins, "i": twin},
        )
    alone = in_uow(url, _student_without_family)
    rename(url, alone)
    future = (TODAY + timedelta(days=1)).isoformat()
    cases: list[tuple[dict[str, str], set[tuple[Any, str]]]] = [
        (line(url, good, student_given_names="Nadie"), {(None, "student_unknown")}),
        (line(url, twin_a), {(None, "student_ambiguous")}),
        (line(url, alone), {(None, "student_has_no_family")}),
        (line(url, good, amount="1,250.00"), {("amount", "invalid_amount")}),
        (line(url, good, amount="$100"), {("amount", "invalid_amount")}),
        (line(url, good, amount="-5"), {("amount", "invalid_amount")}),
        (line(url, good, amount="1.005"), {("amount", "invalid_amount")}),
        (line(url, good, amount="1000000000000"), {("amount", "invalid_amount")}),
        (line(url, good, amount="0.00"), {("amount", "amount_not_positive")}),
        (line(url, good, due_date="31/02/2031"), {("due_date", "invalid_date")}),
        (
            line(url, good, service_date="1999-12-31"),
            {("service_date", "date_out_of_range")},
        ),
        (
            line(url, good, due_date=(TODAY + timedelta(days=367)).isoformat()),
            {("due_date", "date_out_of_range")},
        ),
        (line(url, good, service_date=future), {("service_date", "future_date")}),
        (line(url, good, description="x" * 201), {("description", "too_long")}),
        (line(url, good, description=""), {("description", "required")}),
    ]
    before = total(url, good)

    job = stage(client, finance, [case for case, _ in cases])
    report = items(client, finance, job["id"])

    assert job["counts"]["error"] == len(cases)
    for item, (_, expected) in zip(report, cases, strict=True):
        assert item["status"] == "error"
        assert codes(item["errors"]) == expected, (item["row_number"], item["errors"])
    assert total(url, good) == before


async def _student_without_family(session: AsyncSession) -> uuid.UUID:
    from tests.factories.families import persist_student

    return (await persist_student(session, contacts=0)).id


# --- C2, C4: never twice ------------------------------------------------------------


def test_c2_duplicates_in_file_and_only_against_imported_charges(
    client, finance, module_seeded_url
):
    url = module_seeded_url
    _, (student,) = family(url)
    # A charge posted by hand with the same line is never matched.
    in_uow(
        url,
        lambda s: _hand_charge(s, student),
        actor_id=create_user(url, roles=(OWNER,)).id,
    )
    same = line(url, student, description="Saldo inicial")
    louder = line(url, student, description="SALDO INICIAL", amount="1")

    job = stage(client, finance, [same, louder])
    report = items(client, finance, job["id"])

    assert [(r["status"], codes(r["warnings"])) for r in report] == [
        ("ready", set()),
        ("duplicate", {(None, "duplicate_in_file")}),
    ]


async def _hand_charge(session: AsyncSession, student: uuid.UUID) -> None:
    from app.core.money import Money
    from app.modules.billing import service as billing
    from app.modules.families import service as families

    found = await families.family_of_student(session, student)
    assert found is not None
    await billing.post_manual_charge(
        session,
        billing.ManualChargeDraft(
            family_id=found.id,
            student_id=student,
            description="Saldo inicial",
            amount=Money("10.00"),
            service_date=TODAY - timedelta(days=20),
            due_date=TODAY,
        ),
    )


def test_c4_1_2_3_a_second_commit_a_rerun_and_the_same_file_post_nothing(
    client, finance, module_seeded_url
):
    url = module_seeded_url
    _, (ana, luis) = family(url, 2)
    rows = [line(url, ana), line(url, luis)]
    job = stage(client, finance, rows)
    commit_and_run(client, finance, url, job["id"])
    posted = total(url, ana, luis)
    assert posted == Decimal("2501.00")

    # 1. A second commit of the job is 409.
    again = action(client, finance, job["id"], "commit")
    assert again.status_code == 409, again.text
    # 2. Re-running the task on a committed job posts nothing.
    run_job(url)
    assert total(url, ana, luis) == posted
    # 3. The same CSV as a new job stages every row duplicate_existing.
    copy = stage(client, finance, rows)
    assert copy["counts"]["duplicate"] == 2 and copy["counts"]["ready"] == 0
    assert {(None, "duplicate_existing")} == {
        (w["column"], w["code"])
        for r in items(client, finance, copy["id"])
        for w in r["warnings"]
    }
    nothing = action(client, finance, copy["id"], "commit")
    assert nothing.status_code == 422
    assert total(url, ana, luis) == posted


def test_c4_4_a_forced_repost_of_one_row_is_skipped(
    client, finance, users, module_seeded_url
):
    url = module_seeded_url
    _, (student,) = family(url)
    values = line(url, student)
    job = stage(client, finance, [values])
    ref = ImportRowRef(uuid.UUID(job["id"]), 2)
    actor = users[FINANCE_STAFF].id

    def commit(row_values: Mapping[str, str]) -> Any:
        async def call(session: AsyncSession) -> Any:
            return await OPENING_BALANCES_IMPORT_KIND.commit_row(
                session, row_values, ref, actor
            )

        return in_uow(url, call, actor_id=actor)

    first = commit(values)
    again = commit(values)
    # The same (job, row) with another line: the unique index backstop.
    other = commit({**values, "description": "Otra descripción"})

    assert first.status == "created"
    assert (again.status, again.code) == ("skipped", "duplicate_existing")
    assert (other.status, other.code) == ("skipped", "duplicate_existing")
    assert [c.id for c in charges_of(url, student)] == [first.record_id]
    assert total(url, student) == Decimal("1250.50")


def test_c4_5_two_jobs_of_one_line_committed_concurrently_post_once(
    client, finance, users, module_seeded_url
):
    url = module_seeded_url
    _, (student,) = family(url)
    rows = [line(url, student)]
    first = stage(client, finance, rows)
    second = stage(client, finance, rows)
    for job in (first, second):
        assert action(client, finance, job["id"], "commit").status_code == 202
    actor = users[FINANCE_STAFF].id

    async def both() -> None:
        engines = [short_lived_engine(url), short_lived_engine(url)]

        async def one(engine: Any, job_id: str) -> bool:
            async with UnitOfWork(create_sessionmaker(engine), actor) as uow:
                return await service.commit_next_batch(uow.session, uuid.UUID(job_id))

        try:
            await asyncio.gather(
                one(engines[0], first["id"]), one(engines[1], second["id"])
            )
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(both())
    run_job(url)

    assert total(url, student) == Decimal("1250.50")
    outcomes = sorted(
        (r["status"], codes(r["warnings"]))
        for job in (first, second)
        for r in items(client, finance, job["id"])
    )
    assert outcomes == [
        ("created", set()),
        ("skipped", {(None, "duplicate_existing")}),
    ]


# --- C3: the family at commit -------------------------------------------------------


def test_c3_the_current_family_is_read_at_commit(client, finance, module_seeded_url):
    url = module_seeded_url
    old_family, (moved, stays) = family(url, 2)
    new_family, _ = family(url, 0)
    job = stage(client, finance, [line(url, moved), line(url, stays)])
    run_sql(
        url,
        "UPDATE family_students SET family_id = :f WHERE student_id = :s",
        {"f": new_family, "s": moved},
    )
    run_sql(url, "DELETE FROM family_students WHERE student_id = :s", {"s": stays})

    commit_and_run(client, finance, url, job["id"])
    report = items(client, finance, job["id"])

    [charge] = charges_of(url, moved)
    assert charge.billing_account_id == ps.account_of(url, new_family)
    assert ps.account_of(url, old_family) is None
    assert report[1]["status"] == "failed"
    assert codes(report[1]["errors"]) == {(None, "student_has_no_family")}
    assert charges_of(url, stays) == []


def test_c5_the_manual_charge_route_still_refuses_an_import_reference(
    client, users, module_seeded_url
):
    url = module_seeded_url
    family_id, (student,) = family(url)
    owner = cs.staff(url, client, (OWNER,))

    response = cs.charge_post(
        client,
        owner.cookie,
        family_id,
        cs.charge_body(student, import_job_id=str(uuid.uuid4()), import_row_number=2),
    )

    assert response.status_code == 422, response.text
    assert charges_of(url, student) == []


# --- C2: bounded staging ------------------------------------------------------------


def test_c2_staging_runs_a_fixed_number_of_statements(module_seeded_url, users):
    url = module_seeded_url
    importer = principal_of(users[FINANCE_STAFF], FINANCE_STAFF)
    _, (student,) = family(url)

    def staged(count: int) -> int:
        csv = csv_text(
            HEADER,
            [line(url, student, description=f"Saldo {i}") for i in range(count)],
        )

        async def call(session: AsyncSession) -> object:
            return await service.create_import_job(
                session,
                principal=importer,
                kind_code="opening_balances",
                file_name=None,
                csv_text=csv,
            )

        statements, detail = count_statements(url, call)
        assert detail.counts.ready == count  # type: ignore[attr-defined]
        return statements

    assert staged(3) == staged(300)


# --- E1, E2: who may import ---------------------------------------------------------


def _schedules_csv() -> str:
    from app.modules.scheduling.service import SCHEDULES_IMPORT_KIND

    header = [column.name for column in SCHEDULES_IMPORT_KIND.columns]
    return csv_text(header, [{"student_given_names": "Ana"}])


def _balances_csv() -> str:
    return csv_text(HEADER, [{"student_given_names": "Ana"}])


@pytest.mark.parametrize(
    ("role", "schedules", "balances"),
    [
        (OWNER, 201, 201),
        (ADMINISTRATOR, 201, 201),
        (FINANCE_STAFF, 403, 201),
        (ACADEMIC_COORDINATOR, 403, 403),
        (TUTOR, 403, 403),
        (STUDENT, 403, 403),
        (GUARDIAN, 403, 403),
    ],
)
def test_e2_who_may_import_the_new_kinds(client, users, role, schedules, balances):
    cookie = sign_in(client, users[role].username)

    by_schedules = post_import(client, cookie, "schedules", _schedules_csv())
    by_balances = post_import(client, cookie, "opening_balances", _balances_csv())

    assert by_schedules.status_code == schedules, by_schedules.text
    assert by_balances.status_code == balances, by_balances.text


def test_e2_finance_never_sees_a_schedules_job(client, users, finance):
    owner = sign_in(client, users[OWNER].username)
    hidden = post_import(client, owner, "schedules", _schedules_csv()).json()
    shown = post_import(client, owner, "opening_balances", _balances_csv()).json()

    assert get(client, finance, job_path(hidden["id"])).status_code == 404
    assert get(client, finance, job_path(hidden["id"], "/rows")).status_code == 404
    assert get(client, finance, job_path(shown["id"])).status_code == 200
    listed = get(client, finance, IMPORTS, limit=100).json()["items"]
    assert {item["kind"] for item in listed} == {"opening_balances"}


def test_e2_anonymous_gets_401(client):
    client.cookies.clear()
    response = client.post(
        IMPORTS, json={"kind": "opening_balances", "csv": _balances_csv()}
    )
    assert response.status_code == 401


def test_d1_the_ledger_backstop_counts_an_import_line_as_posted(module_seeded_url):
    # A different job and row with the same line: post_charge's natural key
    # (under the account lock) returns the existing charge.
    url = module_seeded_url
    _, (student,) = family(url)
    finance_user = create_user(url, roles=(FINANCE_STAFF,))
    jobs = [
        _job(url, finance_user.id),
        _job(url, finance_user.id),
    ]

    async def post(session: AsyncSession, job_id: uuid.UUID) -> Any:
        from app.core.money import Money
        from app.modules.billing import service as billing
        from app.modules.families import service as families

        found = await families.family_of_student(session, student)
        assert found is not None
        return await billing.post_manual_charge(
            session,
            billing.ManualChargeDraft(
                family_id=found.id,
                student_id=student,
                description="Saldo",
                amount=Money("5.00"),
                service_date=TODAY,
                due_date=TODAY,
                import_job_id=job_id,
                import_row_number=2,
            ),
        )

    first = in_uow(url, lambda s: post(s, jobs[0]), actor_id=finance_user.id)
    second = in_uow(url, lambda s: post(s, jobs[1]), actor_id=finance_user.id)

    assert first.created is True
    assert (second.created, second.charge_id) == (False, first.charge_id)
    assert total(url, student) == Decimal("5.00")


def _job(url: str, user_id: uuid.UUID) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO import_jobs (kind, created_by_user_id)"
        " VALUES ('opening_balances', :u) RETURNING id",
        {"u": user_id},
    )
    return uuid.UUID(str(row.id))


def test_c1_dates_in_both_forms(client, finance, module_seeded_url):
    url = module_seeded_url
    _, (student,) = family(url)
    rows = [
        line(
            url,
            student,
            service_date=date(2030, 12, 31).strftime("%d/%m/%Y"),
            due_date="2031-01-15",
        )
    ]
    job = stage(client, finance, rows)
    commit_and_run(client, finance, url, job["id"])

    [charge] = charges_of(url, student)
    assert (charge.service_date, charge.due_date) == (
        date(2030, 12, 31),
        date(2031, 1, 15),
    )
