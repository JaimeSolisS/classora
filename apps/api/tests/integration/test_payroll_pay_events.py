"""Pay events through real change requests, makeups and substitutions (#107
D6, D7, D9, E3, E4; ADR-0017 E4, E5).
"""

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client
from tests.integration.catalog_support import post
from tests.integration.change_request_support import (
    OK,
    Scene,
    approve,
    create,
    created,
    hours,
    make_scene,
    ok,
)
from tests.integration.lifecycle_support import code_of
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_rate,
    add_rule,
    add_terms,
    clear,
    closed_out,
    line_of,
    lines,
    run,
)
from tests.integration.series_edit_support import as_user
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_makeups import (
    book,
    booked,
    cancelled_target,
)
from tests.integration.test_session_cancellation import cancel, read
from tests.integration.test_session_substitutions import substituted


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    clear(module_seeded_url)


@pytest.fixture
def scene(module_seeded_url: str, client: TestClient) -> Scene:
    return make_scene(module_seeded_url, client)


def on_payroll(scene: Scene, tutor: CreatedUser | None = None) -> uuid.UUID:
    """Terms, an hourly rate of 120 and one long open period around the
    scene's dates."""
    who = tutor or scene.ctx.tutor
    first: date = scene.ctx.tl.monday
    add_terms(scene.url, who.id)
    add_rate(scene.url, who.id)
    return add_period_row(
        scene.url,
        who.id,
        first.isoformat(),
        (first + timedelta(days=60)).isoformat(),
        "monthly",
    )


def from_scene_start(scene: Scene) -> str:
    return scene.ctx.tl.monday.isoformat()


def cancelled_by_family(scene: Scene, ahead: int = 23) -> dict[str, Any]:
    """An approved family cancellation of the scene's session, ``ahead``
    hours before its start."""
    request = created(create(scene, scene.guardian, at=scene.start - hours(ahead)))
    return ok(approve(scene, request["id"], at=scene.start - hours(ahead - 1)))


def accepted(
    scene: Scene, session: dict[str, Any], tutor: CreatedUser
) -> dict[str, Any]:
    """A session offered to ``tutor``, accepted."""
    ctx = scene.ctx
    cookie = as_user(ctx, tutor, ctx.tl.now)
    offer = read(ctx, session["id"])["pending_offer"]["id"]
    with frozen_clock(ctx.tl.now):
        answered = post(
            ctx.client, cookie, f"/api/v1/assignment-offers/{offer}/accept", None
        )
    assert code_of(answered) == OK, answered.text
    return read(ctx, session["id"])


def makeup_of(
    scene: Scene,
    target: Any,
    week: int,
    weekday: int,
    tutor: CreatedUser | None = None,
) -> dict[str, Any]:
    """A confirmed makeup of ``target`` at 10:00."""
    ctx = scene.ctx
    who = tutor or ctx.tutor
    made = booked(
        book(
            scene,
            target,
            starts_at=scene.at(ctx.tl.week(week, weekday), "10:00"),
            tutor_user_id=str(who.id),
        )
    )
    return accepted(scene, made, who)


# --- D6: cancellations -----


def test_a_late_cancellation_under_none_has_no_line(scene):
    period = on_payroll(scene)

    cancelled_by_family(scene)

    assert run(scene.url, period).line_count == 0


def test_a_late_cancellation_under_percent_uses_the_scheduled_minutes(scene):
    rule = add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "percent",
        percent="50",
    )
    period = on_payroll(scene)

    cancelled_by_family(scene)
    run(scene.url, period)

    line = line_of(scene.url, period)
    assert (line.kind, line.amount, line.minutes) == (
        "late_cancellation",
        Decimal("60.00"),
        60,
    )
    assert (line.approval_id, line.session_pay_rule_id) == (None, rule)
    assert str(line.class_session_id) == scene.session["id"]


def test_a_late_cancellation_under_fixed(scene):
    add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "fixed",
        amount="85.50",
    )
    period = on_payroll(scene)

    cancelled_by_family(scene)
    run(scene.url, period)

    assert line_of(scene.url, period).amount == Decimal("85.50")


def test_an_on_time_cancellation_has_no_line(scene):
    add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "fixed",
        amount="85.50",
    )
    period = on_payroll(scene)

    cancelled_by_family(scene, ahead=30)

    assert run(scene.url, period).line_count == 0


def test_an_excused_exception_has_no_line(scene):
    add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "fixed",
        amount="85.50",
    )
    period = on_payroll(scene)
    request = created(create(scene, scene.guardian, at=scene.start - hours(23)))
    ok(
        approve(
            scene,
            request["id"],
            at=scene.start - hours(22),
            exception=True,
            reason="Enfermedad",
        )
    )

    assert run(scene.url, period).line_count == 0


def test_a_center_cancellation_has_no_line(scene):
    add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "fixed",
        amount="85.50",
    )
    period = on_payroll(scene)

    cancelled_target(scene)

    assert run(scene.url, period).line_count == 0


def test_a_tutor_cancellation_has_no_line(scene):
    add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "fixed",
        amount="85.50",
    )
    period = on_payroll(scene)
    request = created(create(scene, scene.ctx.tutor, at=scene.start - hours(23)))
    ok(approve(scene, request["id"], at=scene.start - hours(22)))

    assert run(scene.url, period).line_count == 0


# --- E3, E4: makeups -----


def test_the_makeup_chain_pays_each_session_worked(scene):
    url = scene.url
    period = on_payroll(scene)
    third_week = scene.ctx.tl.week(3, 1).isoformat()
    second = add_rule(url, "makeup", third_week, "percent", percent="50")

    target = cancelled_target(scene)
    first_makeup = booked(book(scene, target["id"]))
    answered = cancel(scene.ctx, first_makeup["id"], first_makeup["version"])
    assert code_of(answered) == OK, answered.text
    m2 = makeup_of(scene, first_makeup["id"], 2, 3)
    m3 = makeup_of(scene, target["id"], 3, 2)
    closed_out(scene.ctx, m2, minutes=60)
    closed_out(scene.ctx, m3, minutes=60)

    run(url, period)

    earlier, later = lines(url, period)
    assert (str(earlier.class_session_id), earlier.kind) == (m2["id"], "makeup")
    assert earlier.amount == Decimal("120.00")  # the seed, on its own date
    assert (str(later.class_session_id), later.kind) == (m3["id"], "makeup")
    assert (later.amount, later.session_pay_rule_id) == (Decimal("60.00"), second)


def test_each_tutor_is_paid_for_their_makeup_in_their_period(scene):
    url = scene.url
    subject = scene.ctx.subject
    other = new_tutor_user(url, qualified_for=(subject,))
    period = on_payroll(scene)
    other_period = on_payroll(scene, other)

    target = cancelled_target(scene)
    first_makeup = booked(book(scene, target["id"]))
    answered = cancel(scene.ctx, first_makeup["id"], first_makeup["version"])
    assert code_of(answered) == OK, answered.text
    m2 = makeup_of(scene, first_makeup["id"], 2, 3)
    m3 = makeup_of(scene, target["id"], 3, 2, other)
    closed_out(scene.ctx, m2, minutes=60)
    closed_out(scene.ctx, m3, tutor=other, minutes=60)

    run(url, period)
    run(url, other_period)

    assert str(line_of(url, period).class_session_id) == m2["id"]
    line = line_of(url, other_period)
    assert (str(line.class_session_id), line.kind) == (m3["id"], "makeup")
    assert line.tutor_user_id == other.id


def test_a_late_cancelled_makeup_and_the_next_makeup_both_pay(scene):
    url = scene.url
    add_rule(url, "late_cancellation", from_scene_start(scene), "fixed", amount="40.00")
    period = on_payroll(scene)
    target = cancelled_target(scene)
    m1 = makeup_of(scene, target["id"], 2, 3)
    request = created(
        create(
            scene,
            scene.guardian,
            session_id=m1["id"],
            at=scene.at(scene.ctx.tl.week(2, 3), "10:00") - hours(23),
        )
    )
    ok(
        approve(
            scene,
            request["id"],
            at=scene.at(scene.ctx.tl.week(2, 3), "10:00") - hours(22),
        )
    )
    m3 = makeup_of(scene, target["id"], 3, 2)
    closed_out(scene.ctx, m3, minutes=60)

    run(url, period)

    cancelled, worked = lines(url, period)
    assert (str(cancelled.class_session_id), cancelled.kind) == (
        m1["id"],
        "late_cancellation",
    )
    assert cancelled.amount == Decimal("40.00")
    assert (str(worked.class_session_id), worked.kind) == (m3["id"], "makeup")


def test_a_makeup_that_is_a_no_show_is_one_no_show_line(scene):
    url = scene.url
    period = on_payroll(scene)
    target = cancelled_target(scene)
    m1 = makeup_of(scene, target["id"], 2, 3)
    closed_out(scene.ctx, m1, status="absent", minutes=60)

    run(url, period)

    line = line_of(url, period)
    assert (line.kind, str(line.class_session_id)) == ("no_show", m1["id"])


# --- D9: attribution -----


def test_an_accepted_substitute_is_paid_and_the_original_tutor_isnt(scene):
    url = scene.url
    ctx = scene.ctx
    substitute = new_tutor_user(url, qualified_for=(ctx.subject,))
    period = on_payroll(scene)
    their_period = on_payroll(scene, substitute)
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        item = substituted(ctx.client, cookie, scene.session, substitute.id)
    offer = item["offer_id"]
    answer_cookie = as_user(ctx, substitute, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        answered = post(
            ctx.client, answer_cookie, f"/api/v1/assignment-offers/{offer}/accept", None
        )
    assert code_of(answered) == OK, answered.text
    closed_out(ctx, read(ctx, scene.session["id"]), tutor=substitute, minutes=60)

    run(url, period)
    run(url, their_period)

    assert lines(url, period) == []
    assert line_of(url, their_period).tutor_user_id == substitute.id
