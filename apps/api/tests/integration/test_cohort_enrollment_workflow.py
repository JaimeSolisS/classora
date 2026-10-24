"""The billing hook of cohort enrollments (#66 J3, D3, E3): the routes return
the workflow's change, a failure after the participant insert rolls the
whole change back (one Unit of Work), a caller standing in for M6-16 (#101)
withdraws inside its own Unit of Work and fails, and the sweeper's reader
lists the periods in order in one statement."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.courses import service as courses
from app.workflows import cohort_enrollment
from app.workflows.cohort_schemas import CohortEnrollmentCreate, CohortWithdrawal
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import count_statements
from tests.integration.cohort_support import (
    cohort_body,
    cohort_rows,
    cohort_series,
    create_cohort,
    enroll,
    enrollment_rows,
    new_template,
    participants,
    series_students,
    withdraw,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import (
    CREATED,
    new_room,
    new_student,
    new_subject,
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, Any]:
    return {
        "students": [new_student(module_seeded_url) for _ in range(3)],
        "subject": new_subject(module_seeded_url),
        "room": new_room(module_seeded_url, capacity=10),
    }


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def make_cohort(client, cookie, refs, starts_on, **changes: Any) -> dict[str, Any]:
    template = new_template(client, cookie, [refs["subject"]])
    response = create_cohort(
        client,
        cookie,
        cohort_body(
            template=template["id"],
            subject=refs["subject"],
            starts_on=starts_on,
            room=refs["room"],
            **changes,
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def override_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM class_session_overrides")
    return int(row.n)


def run_in_uow(url: str, change: Any) -> Any:
    """``change(uow)`` in its own Unit of Work (committed unless it
    raises)."""

    async def _run() -> Any:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                return await change(uow)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_the_routes_return_the_workflow_change(
    module_seeded_url, client, refs, monkeypatch
):
    seen: list[cohort_enrollment.CohortEnrollmentChange] = []
    step = cohort_enrollment.apply_cohort_enrollment_change

    async def spy(session, change):
        seen.append(change)
        await step(session, change)

    monkeypatch.setattr(cohort_enrollment, "apply_cohort_enrollment_change", spy)
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        enrolled = enroll(client, cookie, cohort["id"], refs["students"][0]).json()
        withdrawn = withdraw(client, cookie, cohort["id"], enrolled["id"]).json()

    joined, left = seen
    assert (str(joined.enrollment_id), joined.change) == (enrolled["id"], "enrolled")
    assert str(joined.cohort_id) == cohort["id"]
    assert str(joined.student_id) == enrolled["student"]["id"]
    assert joined.effective_at == instant(enrolled["enrolled_at"])
    assert str(joined.actor_user_id) == enrolled["enrolled_by"]["id"]
    assert (str(left.enrollment_id), left.change) == (withdrawn["id"], "withdrawn")
    assert left.effective_at == instant(withdrawn["withdrawn_at"])
    assert str(left.actor_user_id) == withdrawn["withdrawn_by"]["id"]
    with pytest.raises(AttributeError):
        joined.change = "withdrawn"  # type: ignore[misc]  # frozen


def test_a_failing_step_rolls_the_whole_enrollment_back(
    module_seeded_url, client, refs, monkeypatch
):
    url = module_seeded_url
    tl = timeline()
    student = refs["students"][1]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday, max_students=1)
        first = enroll(client, cookie, cohort["id"], refs["students"][0]).json()
    [series] = cohort_series(url, cohort["id"])
    before_events = len(events(url))
    before_overrides = override_count(url)

    async def broken(session, change):
        raise RuntimeError("charge failed")

    monkeypatch.setattr(cohort_enrollment, "apply_cohort_enrollment_change", broken)

    async def _enroll(uow: UnitOfWork) -> None:
        # Above the maximum, with an override: the override row and its
        # event are written before the failing step too.
        await cohort_enrollment.enroll_in_cohort(
            uow,
            uuid.UUID(cohort["id"]),
            CohortEnrollmentCreate.model_validate(
                {
                    "student_id": str(student),
                    "override": {
                        "codes": ["group_capacity_exceeded"],
                        "reason": "Hermanos",
                    },
                }
            ),
            actor_id=uuid.UUID(first["enrolled_by"]["id"]),
        )

    with frozen_clock(tl.now), pytest.raises(RuntimeError, match="charge failed"):
        run_in_uow(url, _enroll)

    assert [str(e.student_id) for e in enrollment_rows(url, cohort["id"])] == [
        str(refs["students"][0])
    ]
    assert series_students(url, series.id) == {str(refs["students"][0])}
    assert all(
        str(student) not in participants(url, row.id)
        for row in cohort_rows(url, cohort["id"])
    )
    assert override_count(url) == before_overrides
    assert len(events(url)) == before_events


def test_a_caller_like_m6_16_withdraws_in_its_own_unit_of_work(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    student = refs["students"][0]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        item = enroll(client, cookie, cohort["id"], student).json()
    [series] = cohort_series(url, cohort["id"])
    before_events = len(events(url))

    async def withdraw_enrollment(uow: UnitOfWork) -> None:
        """Stands in for #101: the withdrawal, then its refund fails."""
        change = await cohort_enrollment.withdraw_from_cohort(
            uow,
            uuid.UUID(cohort["id"]),
            uuid.UUID(item["id"]),
            CohortWithdrawal(version=1, reason="Baja"),
            actor_id=uuid.UUID(item["enrolled_by"]["id"]),
        )
        assert change.change == "withdrawn"
        raise RuntimeError("refund failed")

    with frozen_clock(tl.now), pytest.raises(RuntimeError, match="refund failed"):
        run_in_uow(url, withdraw_enrollment)

    [row] = enrollment_rows(url, cohort["id"])
    assert (row.withdrawn_at, row.version) == (None, 1)
    assert series_students(url, series.id) == {str(student)}
    assert all(
        participants(url, r.id) == {str(student)}
        for r in cohort_rows(url, cohort["id"])
    )
    assert len(events(url)) == before_events


def test_the_sweeper_reads_the_periods_in_order_in_one_statement(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b, c = refs["students"]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
    for minutes, (action, student) in enumerate(
        [("enroll", a), ("enroll", b), ("withdraw", a), ("enroll", c)], start=1
    ):
        with frozen_clock(tl.now + timedelta(minutes=minutes)):
            cookie = owner_cookie(url, client)
            if action == "enroll":
                response = enroll(client, cookie, cohort["id"], student)
            else:
                period = next(
                    e
                    for e in enrollment_rows(url, cohort["id"])
                    if e.student_id == student
                )
                response = withdraw(client, cookie, cohort["id"], period.id)
        assert response.status_code in (200, 201), response.text
    since = tl.now + timedelta(minutes=2)
    ours = {str(e.id) for e in enrollment_rows(url, cohort["id"])}

    count, periods = count_statements(
        url,
        lambda session: courses.list_cohort_enrollment_periods(
            session, changed_since=since, limit=1000
        ),
    )

    mine = [p for p in periods if str(p.enrollment_id) in ours]
    assert count == 1
    # b (enrolled at +2), a (withdrawn at +3), c (enrolled at +4).
    assert [(str(p.student_id), p.withdrawn_at is not None) for p in mine] == [
        (str(b), False),
        (str(a), True),
        (str(c), False),
    ]
    assert all(str(p.cohort_id) == cohort["id"] for p in mine)
    assert [p.changed_at for p in mine] == sorted(p.changed_at for p in mine)
