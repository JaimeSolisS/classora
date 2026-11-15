"""Unresolved work on stored sessions and the staff route (#443; ADR-0017 E2,
#106 E4, #107 E6, E12).

**Set by SQL, on purpose:** terms, rates and periods (as every payroll test), the
``finalized`` status (#110 owns the route), and the closeouts of the pagination
and statement tests (:func:`stored`). A date before the first period that has
terms is refused by the route (#106 E4) and is set by SQL too. Other closeouts
are real (:func:`worked`). Real cancellations, substitutes and cohorts are in
``test_payroll_unresolved_flows.py``.

The center is ``America/Mexico_City``, UTC-6 in November.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import encode_cursor
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
from app.workflows import payroll_adjustments, payroll_calculation
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
)
from tests.integration.catalog_support import count_statements, get
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    OK,
    add_period_row,
    add_rate,
    add_terms,
    approved_lesson,
    clear,
    line_of,
    lines,
    paid_tutor,
    run,
    session_lines,
)
from tests.integration.payroll_period_support import (
    d,
    finalize,
    reset,
    run_job,
    set_floor,
)
from tests.integration.test_attendance import Lesson, lesson
from tests.integration.test_closeout_review import (
    correct_closeout,
    resubmit,
    send_back,
    version_of,
)
from tests.integration.test_payroll_calculation import _store_closeout
from tests.integration.test_session_closeout import submitted

ZONE = ZoneInfo("America/Mexico_City")
NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
ROUTE = "/api/v1/payroll/unresolved-work"
DENIED = (403, "permission_denied")
RATE_CANARY = "4321.77"
FIELDS = {
    "class_session_id",
    "tutor_user_id",
    "tutor_display_name",
    "service_date",
    "class_kind",
    "source",
    "reason",
    "blocking",
}


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
    set_floor(module_seeded_url, "11-01")
    yield
    clear(module_seeded_url)
    reset(module_seeded_url)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


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
    **kwargs: Any,
) -> Lesson:
    """A real closeout, marked, submitted and approved."""
    made = lesson(url, starts_at=starts_at, tutor=tutor, **kwargs)
    approved_lesson(client, made, owner)
    return made


def stored(
    url: str, owner: CreatedUser, tutor: CreatedUser, starts_at: datetime
) -> Lesson:
    """A closeout with a revision, by SQL."""
    made = lesson(url, starts_at=starts_at, tutor=tutor)
    _store_closeout(url, made, owner)
    return made


def items(
    url: str, tutor: CreatedUser, through: str | None = None
) -> list[tuple[str, str]]:
    """``(session, reason)``, from the reader."""

    async def read(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(
            session, tutor.id, None if through is None else d(through)
        )

    return [(str(item.class_session_id), item.reason) for item in in_uow(url, read)]


def blocking(url: str, period: Any) -> list[str]:
    async def read(session: AsyncSession) -> Any:
        summary = (await payroll.period_lines(session, period)).period
        return await payroll_calculation.blocking_work(session, summary)

    return [str(item.class_session_id) for item in in_uow(url, read)]


def run_adjustments(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def listed(client: TestClient, cookie: SetCookie, **params: Any) -> Any:
    response = get(client, cookie, ROUTE, **params)
    assert code_of(response) == OK, response.text
    return response.json()


# --- A2, A3, A4: the reasons -----


def test_no_terms_is_blocking_and_a_tutor_with_no_period_has_no_first_period(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made = worked(url, client, owner, tutor, local("11-10"))

    assert items(url, tutor) == [(made.id, "no_pay_terms")]

    body = listed(client, sign_in(client, finance.username), tutor_user_id=tutor.id)
    assert body["items"] == [
        {
            "class_session_id": made.id,
            "tutor_user_id": str(tutor.id),
            "tutor_display_name": "Tutora Lista",
            "service_date": "2026-11-10",
            "class_kind": "individual",
            "source": "closeout",
            "reason": "no_pay_terms",
            "blocking": True,
        }
    ]
    assert body["next_cursor"] is None


def test_terms_with_no_rate_is_no_rate(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, kind="group")
    period = add_period_row(url, tutor.id, "11-02", "11-15")
    made = worked(url, client, owner, tutor, local("11-10"))

    assert items(url, tutor) == [(made.id, "no_rate")]
    assert blocking(url, period) == [made.id]


def test_a_version_with_no_model_is_no_rate(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    add_rate(url, tutor.id, "11-05", rate=None)
    made = worked(url, client, owner, tutor, local("11-10"))
    before = worked(url, client, owner, tutor, local("11-04"))

    assert items(url, tutor) == [(made.id, "no_rate")]
    assert before.id not in dict(items(url, tutor))


def test_a_stop_is_no_pay_terms(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    add_terms(url, tutor.id, "11-05", None)
    add_period_row(url, tutor.id, "11-02", "11-08")
    made = worked(url, client, owner, tutor, local("11-06"))

    assert items(url, tutor) == [(made.id, "no_pay_terms")]


def test_f3_work_before_the_first_period_lists_and_doesnt_block(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id, "11-16")
    first = add_period_row(url, tutor.id, "11-16", "11-22")
    early = worked(url, client, owner, tutor, local("11-10"))

    assert items(url, tutor) == [(early.id, "before_first_period")]
    assert blocking(url, first) == []
    [item] = listed(client, sign_in(client, finance.username), tutor_user_id=tutor.id)[
        "items"
    ]
    assert (item["reason"], item["blocking"]) == ("before_first_period", False)

    late = worked(url, client, owner, tutor, local("11-17"))

    assert blocking(url, first) == [late.id]
    assert dict(items(url, tutor)) == {
        early.id: "before_first_period",
        late.id: "no_rate",
    }


def test_a3_before_the_first_period_beats_no_rate(
    module_seeded_url, client, owner, tutor
):
    # Terms on a date before the first period: #106 E4 refuses it, so by SQL.
    url = module_seeded_url
    add_terms(url, tutor.id, "11-01")
    first = add_period_row(url, tutor.id, "11-16", "11-22")
    made = worked(url, client, owner, tutor, local("11-10"))

    assert items(url, tutor) == [(made.id, "before_first_period")]
    assert blocking(url, first) == []


def test_the_first_period_is_the_oldest_whatever_its_status(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id, "11-16")
    first = add_period_row(url, tutor.id, "11-16", "11-22")
    add_period_row(url, tutor.id, "11-23", "11-29", first=False)
    finalize(url, first, owner.id)
    made = worked(url, client, owner, tutor, local("11-10"))
    on_the_day = worked(url, client, owner, tutor, local("11-16"))

    assert dict(items(url, tutor)) == {
        made.id: "before_first_period",
        on_the_day.id: "no_rate",
    }


def test_f4_a_terms_gap_inside_a_period_blocks(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    add_terms(url, tutor.id, "11-10", None)
    add_terms(url, tutor.id, "11-20")
    period = add_period_row(url, tutor.id, "11-02", "11-29", "monthly")
    gap = worked(url, client, owner, tutor, local("11-12"))
    paid = worked(url, client, owner, tutor, local("11-21"))

    assert items(url, tutor) == [(gap.id, "no_pay_terms")]
    assert blocking(url, period) == [gap.id]
    assert paid.id not in dict(items(url, tutor))


# --- A5: not an item -----


def test_a5_work_before_the_floor_by_the_center_date(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    # 05:00Z on November 1 is 23:00 on October 31 in the center.
    before = worked(url, client, owner, tutor, datetime(2026, 11, 1, 5, 0, tzinfo=UTC))
    # 06:00Z is the first minute of the floor's date.
    on_it = worked(url, client, owner, tutor, datetime(2026, 11, 1, 6, 0, tzinfo=UTC))

    found = items(url, tutor)

    assert found == [(on_it.id, "no_pay_terms")]
    assert before.id not in dict(found)


def test_a5_payroll_off_lists_nothing(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    worked(url, client, owner, tutor, local("11-10"))
    set_floor(url, None)
    period = add_period_row(url, tutor.id, "11-02", "11-15")

    assert items(url, tutor) == []
    assert blocking(url, period) == []
    assert listed(client, sign_in(client, finance.username)) == {
        "items": [],
        "next_cursor": None,
    }


def test_a5_salary_covers_a_kind_with_no_rate(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, rate=None, salary="9000")
    worked(url, client, owner, tutor, local("11-03"))

    assert items(url, tutor) == []
    run(url, period)
    [line] = session_lines(url, period)
    assert line.amount == 0
    assert items(url, tutor) == []


def test_a5_a_resolvable_closeout_with_no_line_yet_isnt_an_item(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    worked(url, client, owner, tutor, local("11-03"))

    assert lines(url, period) == []
    assert items(url, tutor) == []


def test_a2_a_closeout_with_no_revision_isnt_an_item(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = submitted(client, made)
    assert items(url, tutor) == []

    staff = sign_in(client, owner.username)
    assert code_of(send_back(client, staff, made.id, version_of(body))) == OK

    assert items(url, tutor) == []


def test_a2_a_closeout_back_in_review_keeps_its_revision(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = approved_lesson(client, made, owner)
    cookie = sign_in(client, tutor.username)
    back = resubmit(
        client, cookie, made.id, version_of(body), minutes=50, reason="Antes"
    )
    assert code_of(back) == OK, back.text

    assert items(url, tutor) == [(made.id, "no_pay_terms")]


def test_a2_two_revisions_are_one_item(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    body = approved_lesson(client, made, owner)
    staff = sign_in(client, owner.username)
    corrected = correct_closeout(
        client, staff, made.id, version_of(body), actual_minutes=40
    )
    assert code_of(corrected) == OK, corrected.text

    assert items(url, tutor) == [(made.id, "no_pay_terms")]


@pytest.mark.parametrize("status", ["open", "finalized"])
def test_a2_a_line_in_any_period_keeps_work_off_the_list(
    module_seeded_url, client, owner, tutor, status
):
    # The basis goes after the line was written: the line still pays the work.
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    worked(url, client, owner, tutor, local("11-03"))
    run(url, period)
    if status == "finalized":
        finalize(url, period, owner.id)
    add_terms(url, tutor.id, "11-01", None)

    assert len(lines(url, period)) == 1
    assert items(url, tutor) == []


# --- A6: any period status -----


def test_a6_a_finalized_item_stays_until_it_has_a_rate(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    following = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    made = worked(url, client, owner, tutor, local("11-03"))
    run(url, period)
    finalize(url, period, owner.id)

    assert items(url, tutor) == [(made.id, "no_rate")]
    assert blocking(url, following) == [made.id]

    add_rate(url, tutor.id)

    assert items(url, tutor) == []
    assert blocking(url, following) == []


# --- B: the readers -----


def test_b1_through_is_inclusive_and_center_local(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    # 05:00Z on the 9th is 23:00 on the 8th in the center.
    on_it = worked(url, client, owner, tutor, datetime(2026, 11, 9, 5, 0, tzinfo=UTC))
    after = worked(url, client, owner, tutor, datetime(2026, 11, 9, 6, 0, tzinfo=UTC))

    assert items(url, tutor, "11-08") == [(on_it.id, "no_pay_terms")]
    assert {item[0] for item in items(url, tutor, "11-09")} == {on_it.id, after.id}
    assert items(url, tutor, "11-07") == []


def test_f5_e12_which_items_block_a_period(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    add_terms(url, tutor.id, "10-26", None)
    add_terms(url, tutor.id, "11-03")
    add_period_row(url, tutor.id, "10-19", "11-01", "biweekly")
    period = add_period_row(url, tutor.id, "11-02", "11-08", first=False)
    # A gap before the period's own terms, and no rate after them.
    add_rate(url, tutor.id, "11-03", rate=None)
    set_floor(url, "10-19")
    gap = worked(url, client, owner, tutor, local("10-28"))
    on_end = worked(url, client, owner, tutor, datetime(2026, 11, 9, 5, 0, tzinfo=UTC))
    after = worked(url, client, owner, tutor, datetime(2026, 11, 9, 6, 0, tzinfo=UTC))

    assert set(blocking(url, period)) == {gap.id, on_end.id}
    assert dict(items(url, tutor)) == {
        gap.id: "no_pay_terms",
        on_end.id: "no_rate",
        after.id: "no_rate",
    }


def test_one_tutors_facts_dont_reach_anothers(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,), display_name="Otra Tutora")
    period = paid_tutor(url, other.id, "11-16", "11-22")
    bare = worked(url, client, owner, tutor, local("11-03"))
    theirs = worked(url, client, owner, other, local("11-04"))
    add_period_row(url, tutor.id, "11-02", "11-08")
    add_rate(url, tutor.id, rate="120")

    # ``other``'s terms don't give ``tutor`` a basis, and their first period
    # doesn't apply to ``tutor``. ``tutor``'s period doesn't move ``other``'s.
    assert items(url, tutor) == [(bare.id, "no_pay_terms")]
    assert items(url, other) == []
    assert theirs.id not in blocking(url, period)
    assert blocking(url, period) == []


def test_the_list_reads_each_tutors_own_basis(
    module_seeded_url, client, owner, finance, tutor
):
    # The route loads every tutor at once, so a mixed-up batch shows only here.
    url = module_seeded_url
    paid = create_user(url, roles=(TUTOR,), display_name="Tutora Pagada")
    rateless = create_user(url, roles=(TUTOR,), display_name="Sin Tarifa")
    paid_tutor(url, paid.id, "11-16", "11-22")
    add_terms(url, rateless.id, "11-16")
    bare = worked(url, client, owner, tutor, local("11-03"))
    no_rate = worked(url, client, owner, rateless, local("11-17"))
    resolved = worked(url, client, owner, paid, local("11-17"))

    found = {
        item["class_session_id"]: item["reason"]
        for item in listed(client, sign_in(client, finance.username), limit=100)[
            "items"
        ]
    }

    assert found[bare.id] == "no_pay_terms"
    assert found[no_rate.id] == "no_rate"
    assert resolved.id not in found


def test_c3_every_tutor_with_work_is_listed_and_filters_apply(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,), display_name="Otra Tutora")
    add_terms(url, other.id)
    add_period_row(url, other.id, "11-02", "11-08")
    bare = worked(url, client, owner, tutor, local("11-03"))
    rated = worked(url, client, owner, other, local("11-04"))
    staff = sign_in(client, finance.username)

    everything = {
        item["class_session_id"]: item
        for item in listed(client, staff, limit=100)["items"]
    }
    assert everything[bare.id]["tutor_display_name"] == "Tutora Lista"
    assert everything[rated.id]["tutor_display_name"] == "Otra Tutora"
    only = listed(client, staff, tutor_user_id=other.id)["items"]
    assert [item["class_session_id"] for item in only] == [rated.id]
    no_rate = listed(client, staff, reason="no_rate", limit=100)["items"]
    assert rated.id in [item["class_session_id"] for item in no_rate]
    assert bare.id not in [item["class_session_id"] for item in no_rate]
    assert {item["reason"] for item in no_rate} == {"no_rate"}
    assert (
        listed(client, staff, tutor_user_id=tutor.id, reason="no_rate")["items"] == []
    )


def test_f6_a_rate_and_a_run_fix_it(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    add_terms(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    made = worked(url, client, owner, tutor, local("11-03"))
    run(url, period)
    assert items(url, tutor) == [(made.id, "no_rate")]

    add_rate(url, tutor.id)

    assert items(url, tutor) == []
    assert lines(url, period) == []
    run(url, period)
    assert str(line_of(url, period).class_session_id) == made.id


def test_f6_terms_for_a_tutor_with_no_period_fix_it(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_rate(url, tutor.id)
    made = worked(url, client, owner, tutor, local("11-03"))
    assert items(url, tutor) == [(made.id, "no_pay_terms")]

    add_terms(url, tutor.id, "11-02")

    assert items(url, tutor) == []
    run_job(url)
    [period] = run_sql(
        url,
        "SELECT id FROM payroll_periods WHERE tutor_user_id = :u"
        " AND starts_on <= :day AND ends_on >= :day",
        {"u": tutor.id, "day": d("11-03")},
    )
    run(url, period.id)
    assert str(line_of(url, period.id).class_session_id) == made.id


# --- C: the route -----


def test_f10_pages_are_stable_and_newest_first(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    made = [
        stored(url, owner, tutor, local(f"11-{day:02d}", hour))
        for day in (3, 4, 5)
        for hour in ("09:00", "11:00", "13:00")
    ]
    staff = sign_in(client, finance.username)

    seen: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(10):
        params = {"limit": 2, "tutor_user_id": tutor.id}
        body = listed(client, staff, **params, **({"cursor": cursor} if cursor else {}))
        assert len(body["items"]) <= 2
        seen += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(made)
    assert {item["class_session_id"] for item in seen} == {m.id for m in made}
    keys = [(item["service_date"], item["class_session_id"]) for item in seen]
    assert [key[0] for key in keys] == sorted((key[0] for key in keys), reverse=True)
    for day in ("2026-11-03", "2026-11-04", "2026-11-05"):
        ids = [key[1] for key in keys if key[0] == day]
        assert ids == sorted(ids)
    assert listed(client, staff, tutor_user_id=tutor.id, limit=9)["next_cursor"] is None


def test_a_page_after_a_fix_neither_repeats_nor_skips(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_period_row(url, tutor.id, "11-02", "11-08")
    made = {
        stored(url, owner, tutor, local(f"11-{day:02d}")).id for day in (3, 4, 5, 6)
    }
    staff = sign_in(client, finance.username)
    first = listed(client, staff, limit=2, tutor_user_id=tutor.id)
    assert [item["service_date"] for item in first["items"]] == [
        "2026-11-06",
        "2026-11-05",
    ]

    # The rate from the 6th resolves an item already on the first page.
    add_rate(url, tutor.id, "11-06")
    second = listed(
        client, staff, limit=2, tutor_user_id=tutor.id, cursor=first["next_cursor"]
    )

    ids = [item["class_session_id"] for item in first["items"] + second["items"]]
    assert set(ids) == made
    assert len(ids) == len(made)


def test_f10_statements_dont_grow_with_items_or_tutors(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    few_tutor = create_user(url, roles=(TUTOR,))
    many_tutor = create_user(url, roles=(TUTOR,))
    third = create_user(url, roles=(TUTOR,))
    for tutor, count in ((few_tutor, 3), (many_tutor, 30)):
        for index in range(count):
            stored(
                url,
                owner,
                tutor,
                local(f"11-{2 + index % 7:02d}", f"{8 + index // 7:02d}:00"),
            )

    async def one(session: AsyncSession, who: CreatedUser) -> Any:
        return await payroll_calculation.unresolved_work(session, who.id, None)

    few, found_few = count_statements(url, lambda s: one(s, few_tutor))
    many, found_many = count_statements(url, lambda s: one(s, many_tutor))
    assert (len(found_few), len(found_many)) == (3, 30)
    assert few == many == 8

    async def page(session: AsyncSession, **kwargs: Any) -> Any:
        return await payroll_calculation.list_unresolved_work(
            session, page=payroll_calculation.CursorParams(25, None), **kwargs
        )

    single, _ = count_statements(url, lambda s: page(s, tutor_user_id=few_tutor.id))
    wide, body = count_statements(url, lambda s: page(s))
    stored(url, owner, third, local("11-02"))
    wider, _ = count_statements(url, lambda s: page(s))
    assert len(body.items) == 25
    assert single == wide == wider == 9


def test_the_readers_take_no_lock(module_seeded_url, owner, tutor):
    """The list, ``blocking_work`` (#110 F5) and the period read too. The period's
    own reads are in ``test_payroll_workflow_concurrency.py``."""
    url = module_seeded_url
    stored(url, owner, tutor, local("11-03"))
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    seen: list[str] = []

    def record(conn, cursor, statement, *_: Any) -> None:  # type: ignore[no-untyped-def]
        seen.append(statement)

    async def read(session: AsyncSession) -> Any:
        page = payroll_calculation.CursorParams(25, None)
        found = await payroll_calculation.list_unresolved_work(session, page=page)
        summary = await payroll.period_row(session, period)
        await payroll_calculation.blocking_work(session, summary)
        return found

    event.listen(Engine, "before_cursor_execute", record)
    try:
        in_uow(url, read)
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert seen
    assert [text for text in seen if " FOR " in text.upper()] == []


def test_only_a_session_sourced_line_keeps_work_off_the_list(
    module_seeded_url, client, owner, tutor
):
    """#443 D10, #111 F2, on real rows: the job pays A late, so A is settled; B
    has no basis and stays listed, although an ``adjustment`` line is in its
    period."""
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    finalize(url, first, owner.id)
    settled = worked(url, client, owner, tutor, local("11-03"))
    unpaid = worked(url, client, owner, tutor, local("11-10"), kind="group")

    assert run_adjustments(url).posted == 1

    [line] = lines(url, second)
    assert line.kind == "adjustment"
    # A's basis goes after it was paid: it stays settled.
    add_rate(url, tutor.id, "11-03", rate=None)
    assert items(url, tutor) == [(unpaid.id, "no_rate")]
    assert settled.id not in dict(items(url, tutor))
    assert blocking(url, second) == [unpaid.id]


def test_f11_the_body_holds_no_amount_or_student_data(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    # The rate of another kind: it is in the database, and pays nothing here.
    add_rate(url, tutor.id, rate=RATE_CANARY, kind="group")
    add_period_row(url, tutor.id, "11-02", "11-08")
    made = lesson(url, starts_at=local("11-03"), tutor=tutor)
    approved_lesson(client, made, owner, minutes=47)
    [student] = made.students

    response = get(
        client,
        sign_in(client, finance.username),
        ROUTE,
        tutor_user_id=tutor.id,
    )

    assert code_of(response) == OK
    [item] = response.json()["items"]
    assert set(item) == FIELDS
    assert RATE_CANARY not in response.text
    assert not {"minutes", "amount", "rate", "student_id"} & set(item)
    assert str(student) not in response.text


@pytest.fixture(scope="module")
def refused(client: TestClient, module_seeded_url: str) -> dict[str, SetCookie]:
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
def test_c5_everyone_else_is_refused(client, refused, role):
    response = get(client, refused[role], ROUTE)

    assert code_of(response) == DENIED
    assert RATE_CANARY not in response.text


def test_c5_a_tutor_is_refused_their_own_list(module_seeded_url, client, owner, tutor):
    worked(module_seeded_url, client, owner, tutor, local("11-03"))

    response = get(
        client, sign_in(client, tutor.username), ROUTE, tutor_user_id=tutor.id
    )

    assert code_of(response) == DENIED


@pytest.mark.parametrize("who", ["owner", "finance"])
def test_c5_owner_and_finance_read(client, owner, finance, who):
    user = {"owner": owner, "finance": finance}[who]

    assert code_of(get(client, sign_in(client, user.username), ROUTE)) == OK


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"reason": "no_money"}, "reason"),
        ({"tutor_user_id": "not-a-uuid"}, "tutor_user_id"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
    ],
)
def test_a_bad_query_is_a_422_problem(client, finance, params, field):
    response = get(client, sign_in(client, finance.username), ROUTE, **params)

    assert response.status_code == 422, response.text
    body = response.json()
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert field in str(body["errors"])
    assert "no_money" not in str(body.get("detail"))


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!",
        encode_cursor(["2026-11-03"]),
        encode_cursor(["not-a-date", "019a0000-0000-7000-8000-000000000000"]),
        encode_cursor(["2026-11-03", "not-a-uuid"]),
        encode_cursor(["2026-11-03", 7]),
    ],
)
def test_a_bad_cursor_is_a_400_problem(client, finance, cursor):
    response = get(client, sign_in(client, finance.username), ROUTE, cursor=cursor)

    assert code_of(response) == (400, "invalid_cursor")
    assert cursor not in response.text


def test_an_unknown_tutor_lists_nothing(client, finance):
    response = listed(
        client,
        sign_in(client, finance.username),
        tutor_user_id="019a0000-0000-7000-8000-000000000000",
    )

    assert response == {"items": [], "next_cursor": None}
