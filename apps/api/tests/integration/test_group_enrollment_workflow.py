"""The billing hook of group enrollments (#56 J4, E2, E3): the routes return
the workflow's change, and a failure in the workflow's step rolls the
whole change back (one Unit of Work). (The #56 sweeper reader was replaced
by ``billing:post_enrollment_charges``, #92 J7.)"""

import uuid
from datetime import datetime
from typing import Any

import pytest

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import GroupEnrollmentCreate
from app.workflows import group_enrollment
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.group_support import (
    create_group,
    enroll,
    enrollments,
    group_body,
    group_rows,
    participants,
    series_of,
    series_students,
    withdraw,
)
from tests.integration.lifecycle_support import code_of, events, in_uow
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


def make_group(client, cookie, refs, starts_on, students: int = 1) -> dict[str, Any]:
    response = create_group(
        client,
        cookie,
        group_body(
            subject=refs["subject"],
            starts_on=starts_on,
            students=refs["students"][:students],
            room=refs["room"],
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def test_the_routes_return_the_workflow_change(
    module_seeded_url, client, refs, monkeypatch
):
    seen: list[scheduling.GroupEnrollmentChange] = []
    step = group_enrollment.apply_enrollment_change

    async def spy(session, change):
        seen.append(change)
        await step(session, change)

    monkeypatch.setattr(group_enrollment, "apply_enrollment_change", spy)
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        group = make_group(client, cookie, refs, tl.monday)
        enrolled = enroll(client, cookie, group["id"], refs["students"][1]).json()
        withdrawn = withdraw(client, cookie, group["id"], enrolled["id"]).json()

    created, joined, left = seen
    assert created.change == "enrolled"
    assert str(created.student_id) == str(refs["students"][0])
    assert str(created.group_id) == group["id"]
    assert (str(joined.enrollment_id), joined.change) == (enrolled["id"], "enrolled")
    assert str(joined.student_id) == enrolled["student"]["id"]
    assert joined.effective_at == instant(enrolled["enrolled_at"])
    assert str(joined.actor_user_id) == enrolled["enrolled_by"]["id"]
    assert (str(left.enrollment_id), left.change) == (withdrawn["id"], "withdrawn")
    assert left.effective_at == instant(withdrawn["withdrawn_at"])


def test_a_failing_step_rolls_the_enrollment_back(
    module_seeded_url, client, refs, monkeypatch
):
    url = module_seeded_url
    tl = timeline()
    student = refs["students"][1]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday)
    [series] = series_of(url, group["id"])
    before_events = len(events(url, action="create"))

    async def broken(session, change):
        raise RuntimeError("charge failed")

    monkeypatch.setattr(group_enrollment, "apply_enrollment_change", broken)

    async def _enroll(session) -> None:
        await group_enrollment.enroll_student(
            session,
            uuid.UUID(group["id"]),
            GroupEnrollmentCreate(student_id=student),
            actor_id=uuid.UUID(group["roster"][0]["enrolled_by"]["id"]),
        )

    with frozen_clock(tl.now), pytest.raises(RuntimeError, match="charge failed"):
        in_uow(url, _enroll)

    assert len(enrollments(url, group["id"])) == 1
    assert series_students(url, series.id) == {str(refs["students"][0])}
    assert all(
        str(student) not in participants(url, row.id)
        for row in group_rows(url, group["id"])
    )
    assert len(events(url, action="create")) == before_events
