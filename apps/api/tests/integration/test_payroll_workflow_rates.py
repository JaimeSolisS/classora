"""A back-dated rate under a finalized line (#110 E5, L; #442 QA round 3, D6).
Lines are finalized by the real route. L6: ``test_payroll_per_course.py`` adds
versions under lines finalized by SQL through ``add_rate``, which writes by SQL,
so it doesn't reach the refusal and needs no change. The ``course`` window, which
compares cohort first dates, is in ``test_payroll_workflow_course.py``."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_rate,
    add_terms,
    approved_lesson,
    clear,
    lines,
    paid_tutor,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    code,
    finalize,
    locked,
    prepare,
    row_of,
)
from tests.integration.test_attendance import lesson
from tests.integration.test_payroll_calculation import _store_closeout

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)


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
    set_floor(module_seeded_url, "10-01")
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


REFUSED = (422, "effective_from_under_finalized_line")


def add_version(
    client: TestClient,
    who: Staff,
    tutor: CreatedUser,
    on: str,
    rate: str | None = "150",
    *,
    kind: str = "individual",
    model: str | None = "per_hour",
) -> Any:
    return post(
        client,
        who.cookie,
        f"/api/v1/payroll/tutors/{tutor.id}/rates",
        {
            "class_kind": kind,
            "effective_from": on,
            "rate_model": None if rate is None else model,
            "rate": rate,
        },
    )


def finalized_line(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> Any:
    """110.00 on 2026-11-03, finalized by the route."""
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    locked(client, url, finance, owner, period)
    return period


@pytest.mark.parametrize(
    ("rate", "model"), [("300", "per_hour"), ("9000", "per_hour"), (None, None)]
)
def test_l2_lower_higher_and_a_stop_refuse_alike(
    module_seeded_url, client, owner, finance, tutor, rate, model
):
    url = module_seeded_url
    period = finalized_line(url, client, owner, finance, tutor)
    before = len(
        run_sql(
            url,
            "SELECT id FROM compensation_rates WHERE tutor_user_id = :u",
            {"u": tutor.id},
        )
    )

    response = add_version(client, owner, tutor, "2026-11-01", rate, model=model)

    assert code(response) == REFUSED
    assert response.json()["details"] == {"latest_finalized_on": "2026-11-03"}
    assert "110" not in response.text
    assert (
        len(
            run_sql(
                url,
                "SELECT id FROM compensation_rates WHERE tutor_user_id = :u",
                {"u": tutor.id},
            )
        )
        == before
    )
    assert [line.amount for line in lines(url, period)] == [Decimal("110.00")]


def test_l1_the_exact_line_date_refuses_and_the_next_day_passes(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    finalized_line(url, client, owner, finance, tutor)

    assert code(add_version(client, owner, tutor, "2026-11-03")) == REFUSED
    assert add_version(client, owner, tutor, "2026-11-04").status_code == 201


def test_l1_a_taken_date_is_still_the_older_refusal(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    finalized_line(url, client, owner, finance, tutor)

    assert code(add_version(client, owner, tutor, "2000-01-01")) == (
        409,
        "compensation_rate_exists",
    )


def test_l1_the_window_ends_at_the_next_version_of_the_kind(
    module_seeded_url, client, owner, finance, tutor
):
    """A version dated before a line but after the next version takes over no
    date of that line."""
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, "2000-01-01", "100")
    add_rate(url, tutor.id, "2026-10-01", "120")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    locked(client, url, finance, owner, period)

    # 2026-09-01 runs until 10-01, and the line is on 11-03.
    assert add_version(client, owner, tutor, "2026-09-01").status_code == 201
    assert code(add_version(client, owner, tutor, "2026-10-15")) == REFUSED


def test_l1_a_line_on_the_next_versions_date_isnt_taken_over(
    module_seeded_url, client, owner, finance, tutor
):
    """QA O1: the line on 11-03 is priced by the version of 11-03, so a version
    running up to that date doesn't touch it."""
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, "2000-01-01", "100")
    add_rate(url, tutor.id, "2026-11-03", "120")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(
        client,
        lesson(url, starts_at=datetime(2026, 11, 3, 6, 0, tzinfo=UTC), tutor=tutor),
        owner.user,
    )
    locked(client, url, finance, owner, period)
    assert lines(url, period)[0].service_date.isoformat() == "2026-11-03"

    created = add_version(client, owner, tutor, "2026-11-01")

    assert created.status_code == 201, created.text


def test_l1_another_kind_and_another_tutor_dont_count(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    finalized_line(url, client, owner, finance, tutor)
    other = create_user(url, roles=(TUTOR,))

    created = add_version(client, owner, tutor, "2026-11-01", kind="group")
    assert created.status_code == 201, created.text
    assert add_version(client, owner, other, "2026-11-01").status_code == 201


def test_l1_a_line_of_a_prepared_or_open_period_doesnt_refuse(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    assert code(prepare(client, finance, period)) == OK

    assert add_version(client, owner, tutor, "2026-11-01").status_code == 201


def test_l4_a_no_rate_gap_between_versions_stays_writable(
    module_seeded_url, client, owner, finance, tutor
):
    """The exit of #443 D7: the rate stops on 10-01, resumes on 12-01, and only
    work in between has no line."""
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, "2000-01-01", "120")
    add_rate(url, tutor.id, "2026-10-01", None)
    add_rate(url, tutor.id, "2026-11-05", "120")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    made = lesson(url, starts_at=datetime(2026, 11, 6, 16, 0, tzinfo=UTC), tutor=tutor)
    approved_lesson(client, made, owner.user)
    locked(client, url, finance, owner, period)

    # The gap holds no line of this period.
    assert add_version(client, owner, tutor, "2026-10-15").status_code == 201


def test_l4_a_tutor_with_only_covered_lines_can_still_back_date(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    set_floor(url, "11-02")
    period = paid_tutor(url, tutor.id, rate=None, salary="700.00")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    locked(client, url, finance, owner, period)
    assert any(line.amount == 0 for line in lines(url, period))

    assert add_version(client, owner, tutor, "2026-11-01").status_code == 201


def test_l4_end_to_end_an_item_in_a_finalized_period_gets_its_rate_and_leaves(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, kind="individual")
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    later = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    locked(client, url, finance, owner, first)
    made = lesson(url, kind="group", starts_at=STARTS, tutor=tutor)
    _store_closeout(url, made, owner.user)
    assert code(prepare(client, finance, later))[1] == "payroll_unresolved_work"

    created = add_version(client, owner, tutor, "2026-11-01", "90", kind="group")

    assert created.status_code == 201, created.text
    assert code(prepare(client, finance, later)) == OK
    assert code(finalize(client, url, owner, later)) == OK
    assert row_of(url, first).total_amount == Decimal("0.00")
