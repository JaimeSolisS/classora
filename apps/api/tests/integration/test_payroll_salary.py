"""Salary lines through the real run and routes (#108 C, D, F, G, H; ADR-0017
E1, E7). The pieces themselves are ``tests/unit/test_payroll_salary_pure.py``,
the races ``test_payroll_calculation_concurrency.py``.
"""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    STUDENT,
    TUTOR,
)
from app.modules.payroll import service as payroll
from app.workflows import payroll_calculation
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
)
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_rate,
    add_terms,
    clear,
    lines,
    paid_tutor,
    refused_status,
    run,
    salary_lines,
    session_lines,
    stable,
)
from tests.integration.payroll_period_support import finalize, set_floor
from tests.integration.test_payroll_calculation import (
    NOW,
    ROOT,
    local,
    stored_approvals,
    worked,
)

DENIED = (403, "permission_denied")
IN_USE = (409, "pay_terms_in_use")
OUT_OF_RANGE = (409, "payroll_amount_out_of_range")
MONTHLY_SALARY = "12000.00"
SALARY_CANARY = "4321.77"
MONEY_LIMIT = "999999999999.99"


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    clear(module_seeded_url)
    run_sql(
        module_seeded_url,
        "UPDATE payroll_settings SET payroll_starts_on = NULL,"
        " salary_proration_basis = 'calendar_days'",
    )


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,), display_name="Dueña Centro")


@pytest.fixture(scope="module")
def finance(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


def terms_path(tutor: CreatedUser, terms: uuid.UUID) -> str:
    return f"/api/v1/payroll/tutors/{tutor.id}/pay-terms/{terms}"


def line_ids(url: str, period: uuid.UUID, kind: str) -> list[Any]:
    return [line.id for line in lines(url, period) if line.kind == kind]


def set_status_prepared(url: str, period: uuid.UUID, by: uuid.UUID) -> None:
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :u WHERE id = :p",
        {"u": by, "p": period},
    )


def set_status_open(url: str, period: uuid.UUID) -> None:
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'open', prepared_at = NULL,"
        " prepared_by_user_id = NULL WHERE id = :p",
        {"p": period},
    )


def patch_basis(client: TestClient, cookie: SetCookie, basis: str) -> None:
    current = get(client, cookie, "/api/v1/payroll/settings").json()
    response = patch(
        client,
        cookie,
        "/api/v1/payroll/settings",
        {
            "payroll_starts_on": current["payroll_starts_on"],
            "biweekly_anchor_on": current["biweekly_anchor_on"],
            "salary_proration_basis": basis,
            "version": current["version"],
        },
    )
    assert code_of(response) == (200, None), response.text


# --- C: the run -----


def test_a_full_month_is_one_line_with_everything_it_needs(module_seeded_url):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    terms = add_terms(url, tutor.id, frequency="monthly", salary=MONTHLY_SALARY)
    period = add_period_row(url, tutor.id, "11-01", "11-30", "monthly")

    result = run(url, period)

    assert (result.line_count, str(result.total)) == (1, MONTHLY_SALARY)
    [line] = lines(url, period)
    assert (line.kind, line.amount, line.salary_amount) == (
        "salary",
        Decimal(MONTHLY_SALARY),
        Decimal(MONTHLY_SALARY),
    )
    assert line.tutor_pay_terms_id == terms
    assert (line.salary_starts_on, line.salary_ends_on) == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )
    assert (
        line.proration_numerator,
        line.proration_denominator,
        line.proration_basis,
    ) == (30, 30, "calendar_days")
    assert (line.class_session_id, line.service_date, line.rate_model) == (
        None,
        None,
        None,
    )


def test_c4_statements_with_and_without_a_salary(module_seeded_url, owner):
    """Twelve sent, thirteen with the audit insert at commit, and two more with
    a salary, whatever the number of pieces."""
    url = module_seeded_url
    hourly = create_user(url, roles=(TUTOR,))
    hourly_period = paid_tutor(url, hourly.id)
    one_piece = create_user(url, roles=(TUTOR,))
    one_period = paid_tutor(url, one_piece.id, salary="3000.00")
    many_pieces = create_user(url, roles=(TUTOR,))
    add_terms(url, many_pieces.id, salary="3000.00")
    add_rate(url, many_pieces.id)
    many_period = add_period_row(url, many_pieces.id, "10-05", "11-29")
    stored_approvals(url, hourly, owner, 3)
    stored_approvals(url, one_piece, owner, 3)
    stored_approvals(url, many_pieces, owner, 3)

    def count(period: uuid.UUID) -> tuple[int, Any]:
        return count_statements(
            url, lambda s: payroll_calculation.recalculate(s, period)
        )

    none, _ = count(hourly_period)
    one, one_result = count(one_period)
    many, many_result = count(many_period)

    assert (one_result.line_count, many_result.line_count) == (4, 11)
    assert (none, one, many) == (13, 15, 15)  # M7-08c B4: +1, the settled read


def test_a_version_with_a_frequency_and_no_salary_adds_no_line(
    module_seeded_url, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)

    assert run(url, period).line_count == 0


def test_c5_salary_plus_paid_extras(module_seeded_url, client, owner, tutor):
    """ADR-0017 E1: a rate pays on top of the salary, and a kind with no rate
    stays covered."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary="3000.00")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    worked(url, client, owner, tutor, local("11-04"), kind="group", minutes=60)

    result = run(url, period)

    paid, covered = session_lines(url, period)
    [salary] = salary_lines(url, period)
    assert (paid.class_kind, paid.amount) == ("individual", Decimal("120.00"))
    assert (covered.class_kind, covered.amount) == ("group", Decimal("0.00"))
    assert salary.amount == Decimal("3000.00")
    assert (result.line_count, str(result.total)) == (3, "3120.00")


def test_c6_a_salary_line_keeps_no_work_off_the_unresolved_list(
    module_seeded_url, client, owner, tutor
):
    """A closeout after a mid-period stop stays ``no_pay_terms``, beside the
    salary line for the days before it."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=None, salary="3500.00")
    add_terms(url, tutor.id, "11-05", None)
    set_floor(url, "11-02")
    made = worked(url, client, owner, tutor, local("11-06"), minutes=60)

    run(url, period)

    [salary] = lines(url, period)
    assert (salary.kind, salary.amount) == ("salary", Decimal("1500.00"))

    async def read(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(session, tutor.id, None)

    [item] = in_uow(url, read)
    assert (str(item.class_session_id), item.reason) == (made.id, "no_pay_terms")


def test_c7_a_second_run_leaves_equal_lines(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary="3000.00")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    run(url, period)
    first = stable(lines(url, period))

    run(url, period)

    assert stable(lines(url, period)) == first


def test_c7_a_piece_that_no_longer_exists_disappears(module_seeded_url, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=None, salary="3000.00")
    add_terms(url, tutor.id, "11-05", salary="3500.00")
    assert run(url, period).line_count == 2

    add_terms(url, tutor.id, "11-02", salary=None)
    run(url, period)

    [line] = lines(url, period)
    assert (line.salary_starts_on, line.proration_numerator) == (date(2026, 11, 5), 4)


def test_c7_the_runs_total_is_the_routes_total(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary="3000.00")
    add_terms(url, tutor.id, "11-05", salary="3500.33")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    staff = sign_in(client, finance.username)

    ran = post(client, staff, f"{ROOT}/{period}/recalculate", None)
    read = get(client, staff, f"{ROOT}/{period}/lines")

    assert ran.json()["total"] == read.json()["total"]
    assert ran.json()["line_count"] == len(read.json()["lines"]) == 3


def test_c7_a_total_out_of_range_answers_409_and_writes_nothing(
    module_seeded_url, client, finance, tutor
):
    """Each week fits, and four of them don't."""
    url = module_seeded_url
    add_terms(url, tutor.id, salary=MONEY_LIMIT)
    period = add_period_row(url, tutor.id, "10-05", "11-01")
    staff = sign_in(client, finance.username)

    response = post(client, staff, f"{ROOT}/{period}/recalculate", None)

    assert code_of(response) == OUT_OF_RANGE
    assert "99999" not in response.text
    assert lines(url, period) == []


# --- D: the scoping of a run (#107 QA O3) -----


def salaried_with_session(
    url: str,
    client: TestClient,
    owner: CreatedUser,
    tutor: CreatedUser,
) -> uuid.UUID:
    period = paid_tutor(url, tutor.id, salary="3000.00")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    run(url, period)
    return period


def test_d3_a_second_run_replaces_the_salary_row(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = salaried_with_session(url, client, owner, tutor)
    [salary_id] = line_ids(url, period, "salary")

    run(url, period)

    [new_id] = line_ids(url, period, "salary")
    assert new_id != salary_id


def test_d4_a_run_leaves_the_tutors_other_period_alone(module_seeded_url, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id, salary="3000.00")
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    run(url, first)
    before = lines(url, first)

    run(url, second)

    assert lines(url, first) == before
    assert len(lines(url, second)) == 1


# --- F: deleting a terms version -----


def test_f2_a_prepared_or_finalized_line_keeps_the_version(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    terms = add_terms(url, tutor.id, salary="3000.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    run(url, period)
    set_status_prepared(url, period, owner.id)
    staff = sign_in(client, finance.username)
    path = terms_path(tutor, terms)

    assert code_of(delete(client, staff, path)) == IN_USE

    set_status_open(url, period)
    finalize(url, period, owner.id)

    assert code_of(delete(client, staff, path)) == IN_USE
    assert len(lines(url, period)) == 1


def test_f3_an_open_line_goes_with_its_version(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    kept = add_terms(url, tutor.id, salary="3000.00")
    gone = add_terms(url, tutor.id, "11-05", salary="3500.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    assert run(url, period).line_count == 2
    staff = sign_in(client, finance.username)

    assert delete(client, staff, terms_path(tutor, gone)).status_code == 204

    [line] = lines(url, period)
    assert line.tutor_pay_terms_id == kept
    assert (
        run_sql(url, "SELECT 1 FROM tutor_pay_terms WHERE id = :id", {"id": gone}) == []
    )


def test_f3_a_prepared_line_of_one_period_keeps_the_lines_of_the_other(
    module_seeded_url, client, owner, finance, tutor
):
    """The refused delete rolls back: the open period keeps its line too."""
    url = module_seeded_url
    terms = add_terms(url, tutor.id, salary="3000.00")
    prepared = add_period_row(url, tutor.id, "11-02", "11-08")
    still_open = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    run(url, prepared)
    run(url, still_open)
    set_status_prepared(url, prepared, owner.id)
    staff = sign_in(client, finance.username)

    assert code_of(delete(client, staff, terms_path(tutor, terms))) == IN_USE

    assert len(lines(url, still_open)) == 1


def test_f4_the_guard_reads_its_parameter(
    module_seeded_url, client, owner, finance, tutor
):
    """A5: V1 is on a finalized line and V2, of the same tutor, on none. A guard
    that lost the parameter would refuse both, or neither."""
    url = module_seeded_url
    first = add_terms(url, tutor.id, salary="3000.00")
    second = add_terms(url, tutor.id, "11-16", salary="3500.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    run(url, period)
    finalize(url, period, owner.id)
    staff = sign_in(client, finance.username)

    assert code_of(delete(client, staff, terms_path(tutor, first))) == IN_USE
    assert delete(client, staff, terms_path(tutor, second)).status_code == 204
    assert len(lines(url, period)) == 1


# --- G: the basis -----


def test_g1_a_new_basis_reaches_the_open_period_on_the_next_run(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=None, salary="3000.00")
    add_terms(url, tutor.id, "11-05", None)
    run(url, period)
    [before] = lines(url, period)
    assert (before.amount, before.proration_basis) == (
        Decimal("1285.71"),
        "calendar_days",
    )
    staff = sign_in(client, finance.username)

    patch_basis(client, staff, "weekdays")
    run(url, period)

    [after] = lines(url, period)
    assert (after.amount, after.proration_basis) == (Decimal("1800.00"), "weekdays")
    assert (after.proration_numerator, after.proration_denominator) == (3, 5)


def test_g2_only_an_open_period_takes_a_new_basis(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=None, salary="3000.00")
    add_terms(url, tutor.id, "11-05", None)
    run(url, period)
    before = lines(url, period)
    set_status_prepared(url, period, owner.id)
    staff = sign_in(client, finance.username)

    patch_basis(client, staff, "weekdays")

    assert refused_status(url, period) == "prepared"
    assert lines(url, period) == before

    async def drafts(session: AsyncSession) -> Any:
        read = await payroll.period_lines(session, period)
        return await payroll_calculation.compute_lines(session, read.period)

    [draft] = in_uow(url, drafts)
    assert (str(draft.amount), draft.proration_basis) == ("1800.00", "weekdays")

    set_status_open(url, period)
    finalize(url, period, owner.id)

    assert refused_status(url, period) == "finalized"
    assert lines(url, period) == before


# --- H: reading -----


def test_h1_every_line_has_the_salary_fields_and_salary_comes_last(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary="3000.00")
    raised = add_terms(url, tutor.id, "11-05", salary="3500.00")
    worked(url, client, owner, tutor, local("11-06"), minutes=60)
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    run(url, period)
    staff = sign_in(client, finance.username)

    body = get(client, staff, f"{ROOT}/{period}/lines").json()

    assert [line["kind"] for line in body["lines"]] == [
        "session",
        "session",
        "salary",
        "salary",
    ]
    fields = (
        "tutor_pay_terms_id",
        "salary_amount",
        "salary_starts_on",
        "salary_ends_on",
        "proration_numerator",
        "proration_denominator",
        "proration_basis",
    )
    assert all(line[field] is None for line in body["lines"][:2] for field in fields)
    assert body["lines"][0]["service_date"] < body["lines"][1]["service_date"]
    assert {key: body["lines"][3][key] for key in (*fields, "amount")} == {
        "tutor_pay_terms_id": str(raised),
        "salary_amount": "3500.00",
        "salary_starts_on": "2026-11-05",
        "salary_ends_on": "2026-11-08",
        "proration_numerator": 4,
        "proration_denominator": 7,
        "proration_basis": "calendar_days",
        "amount": "2000.00",
    }
    assert body["lines"][2]["salary_starts_on"] == "2026-11-02"


@pytest.fixture(scope="module")
def denied(client: TestClient, module_seeded_url: str) -> dict[str, SetCookie]:
    url = module_seeded_url
    return {
        name: sign_in(client, create_user(url, **kwargs).username)
        for name, kwargs in {
            "administrator": {"roles": (ADMINISTRATOR,)},
            "delegated": {
                "roles": (ADMINISTRATOR,),
                "delegations": [PAYROLL_FINALIZE],
            },
            "coordinator": {"roles": (ACADEMIC_COORDINATOR,)},
            "tutor": {"roles": (TUTOR,)},
            "student": {"roles": (STUDENT,)},
            "guardian": {"roles": (GUARDIAN,)},
        }.items()
    }


@pytest.mark.parametrize(
    "role",
    ["administrator", "delegated", "coordinator", "tutor", "student", "guardian"],
)
def test_h2_everyone_else_is_refused_a_salary_line(
    module_seeded_url, client, denied, tutor, role
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary=SALARY_CANARY)
    run(url, period)

    response = get(client, denied[role], f"{ROOT}/{period}/lines")

    # #110 E8: an open period is a 404 to a delegated Administrator.
    assert code_of(response) == ((404, "not_found") if role == "delegated" else DENIED)
    assert SALARY_CANARY not in response.text


def test_h2_a_tutor_is_refused_their_own_salary_line(module_seeded_url, client, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary=SALARY_CANARY)
    run(url, period)
    cookie = sign_in(client, tutor.username)

    assert code_of(get(client, cookie, f"{ROOT}/{period}/lines")) == DENIED


@pytest.mark.parametrize("role", ["owner", "finance"])
def test_h2_staff_with_the_view_read_a_salary_line(
    module_seeded_url, client, owner, finance, tutor, role
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary=SALARY_CANARY)
    run(url, period)
    who = {"owner": owner, "finance": finance}[role]

    response = get(client, sign_in(client, who.username), f"{ROOT}/{period}/lines")

    assert code_of(response) == (200, None)
    assert response.json()["lines"][0]["salary_amount"] == SALARY_CANARY


def test_h3_the_event_holds_no_amount(module_seeded_url, client, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary=SALARY_CANARY)
    staff = sign_in(client, finance.username)

    post(client, staff, f"{ROOT}/{period}/recalculate", None)

    [event] = run_sql(
        url,
        "SELECT * FROM audit_events WHERE action = 'payroll.period_recalculated'"
        " AND record_id = :p",
        {"p": period},
    )
    assert event.details == {
        "payroll_period_id": str(period),
        "tutor_user_id": str(tutor.id),
        "line_count": 1,
    }
