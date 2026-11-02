"""The payroll calculation through real closeouts and the real routes (#107 D,
F, G, H, I1; spec W3).

Pay events that need change requests and makeups are in
``test_payroll_pay_events.py``, the races in
``test_payroll_calculation_concurrency.py``.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

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
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    NOT_OPEN,
    OK,
    add_period_row,
    add_rate,
    add_rule,
    add_terms,
    approved_lesson,
    clear,
    line_of,
    lines,
    paid_tutor,
    refused_status,
    run,
    session_lines,
    stable,
)
from tests.integration.payroll_period_support import finalize
from tests.integration.schedule_support import stored
from tests.integration.test_attendance import Lesson, lesson
from tests.integration.test_class_session_create import new_student
from tests.integration.test_closeout_on_behalf import submit_for
from tests.integration.test_closeout_review import (
    approve,
    correct_closeout,
    resubmit,
    send_back,
    version_of,
)
from tests.integration.test_session_closeout import marked, submitted

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
ROOT = "/api/v1/payroll/periods"
DENIED = (403, "permission_denied")
IN_USE = (409, "compensation_rate_in_use")
RULE_IN_USE = (409, "session_pay_rule_in_use")
OUT_OF_RANGE = (409, "payroll_amount_out_of_range")
AMOUNT_CANARY = "4321.77"
RATE_CANARY = "4321.7700"


def local(day: str, hhmm: str = "10:00") -> datetime:
    return datetime.fromisoformat(f"2026-{day}T{hhmm}:00").replace(tzinfo=ZONE)


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


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,), display_name="Dueña Centro")


@pytest.fixture(scope="module")
def finance(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


def worked(
    url: str,
    client: TestClient,
    owner: CreatedUser,
    tutor: CreatedUser,
    starts_at: datetime,
    *,
    kind: str = "individual",
    students: int = 1,
    statuses: list[str] | None = None,
    minutes: int = 55,
) -> Lesson:
    made = lesson(url, kind=kind, students=students, starts_at=starts_at, tutor=tutor)
    approved_lesson(client, made, owner, statuses=statuses, minutes=minutes)
    return made


# --- K6: W3, what is paid -----


def test_only_an_approved_closeout_is_paid(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)

    body = submitted(client, made)
    assert run(url, period).line_count == 0
    assert lines(url, period) == []

    staff = sign_in(client, owner.username)
    assert code_of(approve(client, staff, made.id, version_of(body))) == OK
    result = run(url, period)

    assert (result.line_count, str(result.total)) == (1, "110.00")
    line = line_of(url, period)
    assert (line.kind, line.amount, line.minutes) == ("session", Decimal("110.00"), 55)
    assert str(line.class_session_id) == made.id
    assert line.service_date.isoformat() == "2026-11-03"
    assert line.class_kind == "individual"
    assert (line.rate_model, line.rate) == ("per_hour", Decimal("120.0000"))
    assert line.approval_id is not None
    assert line.tutor_user_id == tutor.id


def test_changes_requested_on_the_first_revision_pays_nothing(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = submitted(client, made)
    staff = sign_in(client, owner.username)

    assert code_of(send_back(client, staff, made.id, version_of(body))) == OK

    assert run(url, period).line_count == 0


def test_a_rate_change_mid_period_splits_by_service_date(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    old = add_rate(url, tutor.id, rate="120")
    new = add_rate(url, tutor.id, "11-05", rate="180")
    period = add_period_row(url, tutor.id, "11-02", "11-08")

    before = worked(url, client, owner, tutor, local("11-04"), minutes=60)
    on_it = worked(url, client, owner, tutor, local("11-05"), minutes=60)
    run(url, period)

    first, second = lines(url, period)
    assert str(first.class_session_id) == before.id
    assert (first.compensation_rate_id, first.rate) == (old, Decimal("120.0000"))
    assert first.amount == Decimal("120.00")
    assert str(second.class_session_id) == on_it.id
    assert (second.compensation_rate_id, second.rate) == (new, Decimal("180.0000"))
    assert second.amount == Decimal("180.00")


def test_periods_use_the_center_date_not_the_utc_date(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, rate="120")
    monday = add_rate(url, tutor.id, "11-02", rate="240")
    first = add_period_row(url, tutor.id, "10-26", "11-01")
    second = add_period_row(url, tutor.id, "11-02", "11-08", first=False)

    sunday_night = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)
    monday_midnight = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
    first_day = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    student = new_student(url)
    last = Lesson(
        id=stored(
            url,
            sunday_night,
            students=(student,),
            minutes=30,
            kind="individual",
            tutor_user_id=tutor.id,
        ),
        tutor=tutor,
        students=[student],
    )
    approved_lesson(client, last, owner, minutes=30)
    opening = worked(url, client, owner, tutor, first_day, minutes=60)
    next_one = worked(url, client, owner, tutor, monday_midnight, minutes=60)

    run(url, first)
    run(url, second)

    start, end = lines(url, first)
    assert (str(start.class_session_id), str(end.class_session_id)) == (
        opening.id,
        last.id,
    )
    assert start.service_date.isoformat() == "2026-10-26"
    assert end.service_date.isoformat() == "2026-11-01"
    assert end.rate == Decimal("120.0000")  # the Monday rate doesn't reach Sunday
    line = line_of(url, second)
    assert str(line.class_session_id) == next_one.id
    assert line.service_date.isoformat() == "2026-11-02"
    assert line.compensation_rate_id == monday


def test_a_session_before_midnight_on_the_first_day_is_the_period_before(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, "11-02", "11-08")

    worked(
        url,
        client,
        owner,
        tutor,
        datetime(2026, 11, 2, 5, 30, tzinfo=UTC),
    )

    assert run(url, period).line_count == 0


def test_a_new_revision_replaces_the_line(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = approved_lesson(client, made, owner, minutes=55)
    run(url, period)
    first = line_of(url, period)

    staff = sign_in(client, owner.username)
    corrected = correct_closeout(
        client, staff, made.id, version_of(body), actual_minutes=30
    )
    assert code_of(corrected) == OK
    run(url, period)

    line = line_of(url, period)
    assert line.id != first.id
    assert line.approval_id != first.approval_id
    assert (line.minutes, line.amount) == (30, Decimal("60.00"))


def test_a_closeout_back_in_review_stays_paid(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = approved_lesson(client, made, owner, minutes=55)
    cookie = sign_in(client, tutor.username)

    back = resubmit(
        client, cookie, made.id, version_of(body), minutes=50, reason="Antes"
    )
    assert code_of(back) == OK
    run(url, period)

    assert line_of(url, period).minutes == 55


# --- K6: no-shows, attribution, salary, no basis -----


def test_an_absent_student_is_a_no_show(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    worked(
        url,
        client,
        owner,
        tutor,
        local("11-03"),
        statuses=["absent"],
        minutes=60,
    )
    [rule] = run_sql(
        url,
        "SELECT id FROM session_pay_rules WHERE event = 'no_show'"
        " AND effective_from = '2000-01-01'",
    )

    run(url, period)

    line = line_of(url, period)
    assert (line.kind, line.amount) == ("no_show", Decimal("120.00"))
    assert line.session_pay_rule_id == rule.id
    assert line.approval_id is not None


def test_a_group_all_absent_is_a_no_show_partly_absent_is_a_session(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, kind="group", rate="300", model="per_session")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    every = ["absent", "absent", "absent"]
    worked(
        url,
        client,
        owner,
        tutor,
        local("11-03"),
        kind="group",
        students=3,
        statuses=every,
    )
    worked(
        url,
        client,
        owner,
        tutor,
        local("11-04"),
        kind="group",
        students=2,
        statuses=["absent", "present"],
    )

    run(url, period)

    all_out, partly = lines(url, period)
    assert (all_out.kind, all_out.amount) == ("no_show", Decimal("300.00"))
    assert (partly.kind, partly.amount) == ("session", Decimal("300.00"))


def test_a_closeout_on_behalf_stays_the_tutors(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    reviewer_period = paid_tutor(url, owner.id, "11-02", "11-08")
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    _, version = marked(client, made)
    staff = sign_in(client, owner.username)
    response = submit_for(client, staff, made.id, version)
    assert code_of(response)[0] == 201, response.text
    reviewer = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    other = sign_in(client, reviewer.username)
    assert code_of(approve(client, other, made.id, version_of(response.json()))) == OK

    run(url, period)
    run(url, reviewer_period)

    assert line_of(url, period).tutor_user_id == tutor.id
    assert lines(url, reviewer_period) == []


def test_a_salaried_tutor_with_a_rate_gets_the_ordinary_line(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, salary="9000.00")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)

    run(url, period)

    [line] = session_lines(url, period)
    assert line.amount == Decimal("120.00")


def test_a_covered_session_is_a_zero_line(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=None, salary="9000.00")
    worked(url, client, owner, tutor, local("11-03"), minutes=60)

    result = run(url, period)

    assert (result.line_count, str(result.total)) == (2, "9000.00")  # the salary
    [line] = session_lines(url, period)
    assert (line.kind, line.amount, line.minutes) == ("session", Decimal("0.00"), 60)
    assert (line.rate_model, line.rate, line.compensation_rate_id) == (
        None,
        None,
        None,
    )


def test_a_fixed_no_show_stays_covered(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_rule(url, "no_show", "2025-01-01", "fixed", amount="500.00")
    period = paid_tutor(url, tutor.id, rate=None, salary="9000.00")
    worked(url, client, owner, tutor, local("11-03"), statuses=["absent"])

    run(url, period)

    [line] = session_lines(url, period)
    assert (line.kind, line.amount, line.session_pay_rule_id) == (
        "session",
        Decimal("0.00"),
        None,
    )


def test_no_rate_no_terms_and_a_fixed_rule_pay_nothing(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_rule(url, "no_show", "2025-01-01", "fixed", amount="500.00")
    period = paid_tutor(url, tutor.id, rate=None)
    worked(url, client, owner, tutor, local("11-03"))
    worked(url, client, owner, tutor, local("11-04"), statuses=["absent"])

    assert run(url, period).line_count == 0

    no_terms = create_user(url, roles=(TUTOR,))
    add_rate(url, no_terms.id)
    orphan = add_period_row(url, no_terms.id, "11-02", "11-08")
    worked(url, client, owner, no_terms, local("11-03"))

    assert run(url, orphan).line_count == 0


# --- F: recalculate -----


def test_recalculate_twice_leaves_the_same_lines(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    worked(url, client, owner, tutor, local("11-03"))
    worked(url, client, owner, tutor, local("11-04"), statuses=["absent"])
    run(url, period)
    first = lines(url, period)

    run(url, period)

    second = lines(url, period)
    assert stable(first) == stable(second)
    assert {row.id for row in first}.isdisjoint({row.id for row in second})


def test_a_later_approval_shows_on_the_next_run(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    worked(url, client, owner, tutor, local("11-03"))
    run(url, period)
    before = stable(lines(url, period))

    made = worked(url, client, owner, tutor, local("11-04"))
    run(url, period)

    after = stable(lines(url, period))
    assert after[0] == before[0]
    assert str(after[1]["class_session_id"]) == made.id


def test_a_period_may_run_before_it_ends(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, "12-07", "12-13")
    worked(url, client, owner, tutor, local("12-07", "08:00"))

    assert run(url, period).line_count == 1


def test_a_closed_period_refuses_and_keeps_its_lines(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = approved_lesson(client, made, owner, minutes=55)
    run(url, period)
    before = lines(url, period)
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :u WHERE id = :p",
        {"u": owner.id, "p": period},
    )
    staff = sign_in(client, owner.username)
    corrected = correct_closeout(
        client, staff, made.id, version_of(body), actual_minutes=30
    )
    assert code_of(corrected) == OK

    assert refused_status(url, period) == "prepared"
    assert lines(url, period) == before

    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'open', prepared_at = NULL,"
        " prepared_by_user_id = NULL WHERE id = :p",
        {"p": period},
    )
    finalize(url, period, owner.id)

    assert refused_status(url, period) == "finalized"
    assert lines(url, period) == before


def test_the_run_replaces_only_the_rebuilt_kinds():
    """#107 E7: #109 extends the tuple, and #111's lines survive. A kind outside
    it surviving a run is ``test_payroll_salary.py``."""
    assert payroll.REBUILT_KINDS == (
        "session",
        "late_cancellation",
        "no_show",
        "makeup",
        "salary",
    )


MAX_RATE = "9999999999.9999"


def test_d11_a_line_out_of_range_answers_409_and_writes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_rule(url, "no_show", "2025-01-01", "percent", percent="1000")
    period = paid_tutor(url, tutor.id, rate=MAX_RATE)
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    run(url, period)
    before = lines(url, period)
    made = worked(
        url, client, owner, tutor, local("11-04"), statuses=["absent"], minutes=720
    )
    staff = sign_in(client, finance.username)

    response = post(client, staff, f"{ROOT}/{period}/recalculate", None)

    assert code_of(response) == OUT_OF_RANGE
    assert response.json()["details"] == {"class_session_id": made.id}
    assert "99999" not in response.text
    assert lines(url, period) == before


def test_a_total_out_of_range_answers_409_and_writes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    """#107 D3 (engineer): each line fits, about 6 x 10^11, and their sum
    doesn't."""
    url = module_seeded_url
    add_rule(url, "no_show", "2025-01-01", "percent", percent="1000")
    period = paid_tutor(url, tutor.id, rate=MAX_RATE)
    worked(url, client, owner, tutor, local("11-03"), statuses=["absent"], minutes=360)
    assert run(url, period).line_count == 1
    before = lines(url, period)
    worked(url, client, owner, tutor, local("11-04"), statuses=["absent"], minutes=360)
    staff = sign_in(client, finance.username)

    response = post(client, staff, f"{ROOT}/{period}/recalculate", None)

    assert code_of(response) == OUT_OF_RANGE
    assert response.json().get("details") is None
    assert "99999" not in response.text
    assert lines(url, period) == before


# --- G: deleting what a line uses -----


def rates_path(tutor: Any, rate: Any) -> str:
    return f"/api/v1/payroll/tutors/{tutor}/rates/{rate}"


def test_deleting_a_rate_takes_its_open_lines_with_it(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    wrong = add_rate(url, tutor.id, rate="120")
    right = add_rate(url, tutor.id, "11-05", rate="150")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    worked(url, client, owner, tutor, local("11-04"), minutes=60)
    worked(url, client, owner, tutor, local("11-05"), minutes=60)
    run(url, period)

    staff = sign_in(client, finance.username)
    assert delete(client, staff, rates_path(tutor.id, wrong)).status_code == 204

    assert line_of(url, period).compensation_rate_id == right
    assert (
        run_sql(url, "SELECT 1 FROM compensation_rates WHERE id = :id", {"id": wrong})
        == []
    )
    assert run(url, period).line_count == 1  # the Wednesday has no rate left


def test_a_prepared_or_finalized_line_keeps_the_rate(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    rate = add_rate(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    worked(url, client, owner, tutor, local("11-04"))
    run(url, period)
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :u WHERE id = :p",
        {"u": owner.id, "p": period},
    )
    staff = sign_in(client, finance.username)
    path = rates_path(tutor.id, rate)

    assert code_of(delete(client, staff, path)) == IN_USE

    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'open', prepared_at = NULL,"
        " prepared_by_user_id = NULL WHERE id = :p",
        {"p": period},
    )
    finalize(url, period, owner.id)

    assert code_of(delete(client, staff, path)) == IN_USE
    assert len(lines(url, period)) == 1


def test_a_rate_of_another_tutor_is_still_404(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,))
    rate = add_rate(url, other.id)
    staff = sign_in(client, finance.username)

    assert delete(client, staff, rates_path(tutor.id, rate)).status_code == 404


def line_of_rule(
    url: str, period: uuid.UUID, tutor: CreatedUser, rule: uuid.UUID
) -> None:
    """A line of a rule dated after its session: by SQL, because no run can
    make one. The real path is a cancellation dated on or after the rule."""
    run_sql(
        url,
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " class_session_id, service_date, class_kind, minutes, rate_model, rate,"
        " compensation_rate_id, session_pay_rule_id)"
        " SELECT :p, :u, 'late_cancellation', 70, :s, '2026-11-03', 'individual', 60,"
        " 'per_hour', 120, r.id, :rule FROM compensation_rates r"
        " WHERE r.tutor_user_id = :u",
        {
            "p": period,
            "u": tutor.id,
            "rule": rule,
            "s": lesson(url, starts_at=local("11-03"), tutor=tutor).id,
        },
    )


def test_deleting_an_upcoming_rule_takes_its_open_lines_with_it(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rule = add_rule(url, "no_show", "2099-12-20", "fixed", amount="70.00")
    period = paid_tutor(url, tutor.id)
    line_of_rule(url, period, tutor, rule)
    staff = sign_in(client, finance.username)

    response = delete(client, staff, f"/api/v1/payroll/pay-rules/{rule}")

    assert response.status_code == 204, response.text
    assert lines(url, period) == []


def test_a_prepared_line_keeps_an_upcoming_rule(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    rule = add_rule(url, "no_show", "2099-12-20", "fixed", amount="70.00")
    period = paid_tutor(url, tutor.id)
    line_of_rule(url, period, tutor, rule)
    run_sql(
        url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :u WHERE id = :p",
        {"u": owner.id, "p": period},
    )
    staff = sign_in(client, finance.username)

    response = delete(client, staff, f"/api/v1/payroll/pay-rules/{rule}")

    assert code_of(response) == RULE_IN_USE
    assert len(lines(url, period)) == 1


# --- H: routes, roles, audit -----


def test_the_routes(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    second = worked(url, client, owner, tutor, local("11-05"), minutes=60)
    first = worked(url, client, owner, tutor, local("11-03"), minutes=30)
    staff = sign_in(client, finance.username)

    response = post(client, staff, f"{ROOT}/{period}/recalculate", None)

    assert code_of(response) == OK, response.text
    assert response.json() == {"line_count": 2, "total": "180.00"}
    shown = get(client, staff, f"{ROOT}/{period}/lines")
    assert code_of(shown) == OK
    body = shown.json()
    assert body["period"] == {
        "id": str(period),
        "tutor_user_id": str(tutor.id),
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-08",
        "pay_frequency": "weekly",
        "status": "open",
    }
    assert body["total"] == "180.00"
    assert [line["class_session_id"] for line in body["lines"]] == [
        first.id,
        second.id,
    ]
    line = body["lines"][0]
    assert line["rate"] == "120.0000"
    assert line["amount"] == "60.00"
    assert set(line) == {
        "id",
        "kind",
        "amount",
        "class_session_id",
        "approval_id",
        "service_date",
        "class_kind",
        "minutes",
        "rate_model",
        "rate",
        "compensation_rate_id",
        "session_pay_rule_id",
        "cumulative_sessions",
        "cohort_sessions",
        "tutor_pay_terms_id",
        "salary_amount",
        "salary_starts_on",
        "salary_ends_on",
        "proration_numerator",
        "proration_denominator",
        "proration_basis",
    }


def test_an_unknown_period_is_404(client, finance):
    staff = sign_in(client, finance.username)
    missing = uuid.uuid7()

    assert post(client, staff, f"{ROOT}/{missing}/recalculate", None).status_code == 404
    assert get(client, staff, f"{ROOT}/{missing}/lines").status_code == 404


def test_a_closed_period_answers_409_with_its_status(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    finalize(url, period, owner.id)
    staff = sign_in(client, finance.username)

    response = post(client, staff, f"{ROOT}/{period}/recalculate", None)

    assert code_of(response) == NOT_OPEN
    assert response.json()["status"] == 409
    assert response.json()["details"] == {"status": "finalized"}


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
def test_everyone_else_is_refused(module_seeded_url, client, denied, tutor, role):
    period = paid_tutor(module_seeded_url, tutor.id)
    cookie = denied[role]

    assert code_of(post(client, cookie, f"{ROOT}/{period}/recalculate", None)) == DENIED
    assert code_of(get(client, cookie, f"{ROOT}/{period}/lines")) == DENIED


def test_a_tutor_is_refused_on_their_own_period(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=AMOUNT_CANARY)
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    cookie = sign_in(client, tutor.username)

    assert code_of(post(client, cookie, f"{ROOT}/{period}/recalculate", None)) == DENIED
    assert code_of(get(client, cookie, f"{ROOT}/{period}/lines")) == DENIED


def test_the_event_names_ids_and_a_count_and_no_amount(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=AMOUNT_CANARY)
    worked(url, client, owner, tutor, local("11-03"), minutes=60)
    staff = sign_in(client, finance.username)

    response = post(client, staff, f"{ROOT}/{period}/recalculate", None)
    assert response.json()["total"] == AMOUNT_CANARY

    [event] = run_sql(
        url,
        "SELECT * FROM audit_events WHERE action = 'payroll.period_recalculated'"
        " AND record_id = :p",
        {"p": period},
    )
    assert event.actor_user_id == finance.id
    assert event.details == {
        "payroll_period_id": str(period),
        "tutor_user_id": str(tutor.id),
        "line_count": 1,
    }
    audit = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE action = 'payroll.period_recalculated'"
        " AND (details::text LIKE :amount OR details::text LIKE :rate)",
        {"amount": f"%{AMOUNT_CANARY}%", "rate": f"%{RATE_CANARY}%"},
    )
    assert audit[0].n == 0
    line_events = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE record_type = 'payroll_lines'",
    )
    assert line_events[0].n == 0


# --- I1: the statement pin -----


def stored_approvals(url: str, tutor: CreatedUser, owner: CreatedUser, n: int) -> None:
    """``n`` closeouts with a revision, by SQL: the pin needs rows, not flows."""
    for index in range(n):
        day = f"11-{2 + index % 7:02d}"
        made = lesson(
            url, starts_at=local(day, f"{8 + index // 7:02d}:00"), tutor=tutor
        )
        _store_closeout(url, made, owner)


def _store_closeout(url: str, made: Lesson, owner: CreatedUser) -> None:
    [closeout] = run_sql(
        url,
        "INSERT INTO session_closeouts (class_session_id, tutor_user_id, status,"
        " submitted_at, last_submitted_at)"
        " VALUES (:s, :t, 'submitted', now(), now()) RETURNING id",
        {"s": made.id, "t": made.tutor.id},
    )
    [approval] = run_sql(
        url,
        "INSERT INTO session_closeout_approvals (closeout_id, revision, source,"
        " class_session_id, tutor_user_id, session_starts_at, session_kind,"
        " scheduled_minutes, approved_minutes, attendance, approved_by_user_id,"
        " approved_at)"
        " SELECT :c, 1, 'review', id, tutor_user_id, starts_at, kind,"
        " scheduled_minutes, 60, CAST(:a AS jsonb), :o, now() FROM class_sessions"
        " WHERE id = :s RETURNING id",
        {
            "c": closeout.id,
            "s": made.id,
            "o": owner.id,
            "a": f'{{"{made.students[0]}": "present"}}',
        },
    )
    run_sql(
        url,
        "UPDATE session_closeouts SET status = 'approved',"
        " current_approval_id = :a WHERE id = :c",
        {"a": approval.id, "c": closeout.id},
    )


def test_a_run_costs_the_same_statements_whatever_the_sessions(
    module_seeded_url, owner
):
    url = module_seeded_url
    small = create_user(url, roles=(TUTOR,))
    large = create_user(url, roles=(TUTOR,))
    period_small = paid_tutor(url, small.id)
    period_large = paid_tutor(url, large.id)
    stored_approvals(url, small, owner, 3)
    stored_approvals(url, large, owner, 30)

    few, first = count_statements(
        url, lambda s: payroll_calculation.recalculate(s, period_small)
    )
    many, second = count_statements(
        url, lambda s: payroll_calculation.recalculate(s, period_large)
    )

    assert (first.line_count, second.line_count) == (3, 30)
    assert few == many == 10  # eleven with the audit insert at commit


def test_compute_lines_writes_nothing(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    worked(url, client, owner, tutor, local("11-03"), minutes=60)

    async def drafts(session: AsyncSession) -> Any:
        locked = await payroll.lock_open_period(session, period)
        return await payroll_calculation.compute_lines(session, locked)

    [draft] = in_uow(url, drafts)

    assert draft.kind == "session"
    assert lines(url, period) == []
