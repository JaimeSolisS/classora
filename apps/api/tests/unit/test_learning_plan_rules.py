"""The learning plan rules (#81 A3): limits, progress, overdue, the relation,
family visibility and every relation × status × action × deleted
combination of the write blocks."""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import product

import pytest

from app.modules.academics import learning_plan_rules as rules
from app.modules.academics.learning_plan_rules import PlanRelation as R
from app.modules.academics.models import (
    MILESTONE_STATUSES,
    PLAN_STATUSES,
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
)
from app.modules.identity.permissions import LEARNING_PLAN_MANAGE, TUTOR

WHEN = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
TODAY = date(2031, 3, 5)


def test_the_limits_and_codes_match_the_catalog_and_the_tables():
    assert (rules.OBJECTIVES_MAX, rules.MILESTONES_PER_OBJECTIVE_MAX) == (10, 10)
    assert rules.MILESTONES_PER_PLAN_MAX == 50
    assert (rules.TITLE_MAX, rules.ITEM_TITLE_MAX) == (120, 200)
    assert (rules.DESCRIPTION_MAX, rules.OBJECTIVE_DESCRIPTION_MAX) == (2000, 1000)
    assert rules.ACTIVITIES_MAX == 2000
    assert rules.LIST_MAX == 100
    assert rules.LEARNING_PLAN_MANAGE == LEARNING_PLAN_MANAGE
    assert rules.TUTOR_ROLE == TUTOR
    assert rules.STATUSES == PLAN_STATUSES
    assert rules.MILESTONE_STATUSES == MILESTONE_STATUSES
    assert LearningPlan.__table__.c.title.type.length == rules.TITLE_MAX
    assert LearningPlanObjective.__table__.c.title.type.length == rules.ITEM_TITLE_MAX
    assert LearningPlanMilestone.__table__.c.title.type.length == rules.ITEM_TITLE_MAX


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], (0, 0, Decimal(0))),
        (["pending"], (0, 1, Decimal(0))),
        (["done"], (1, 1, Decimal(100))),
        (["done", "pending"], (1, 2, Decimal(50))),
        (["done", "in_progress", "pending"], (1, 3, Decimal(33))),
        (["done", "done", "pending"], (2, 3, Decimal(67))),
        # 1/8 = 12.5 → 13 (half up, never banker's rounding).
        (["done"] + ["pending"] * 7, (1, 8, Decimal(13))),
        (["done"] * 5 + ["pending"] * 3, (5, 8, Decimal(63))),
        (["in_progress"] * 50, (0, 50, Decimal(0))),
        (["done"] * 49 + ["pending"], (49, 50, Decimal(98))),
    ],
)
def test_progress(statuses, expected):
    done, total, percent = rules.progress(statuses)
    assert (done, total, percent) == expected
    assert isinstance(percent, Decimal)
    assert f"{percent:f}" == str(expected[2])


@pytest.mark.parametrize(
    ("status", "due_on", "expected"),
    [
        ("pending", date(2031, 3, 4), True),
        ("in_progress", date(2031, 3, 4), True),
        ("done", date(2031, 3, 4), False),
        ("pending", TODAY, False),
        ("pending", date(2031, 3, 6), False),
        ("pending", None, False),
        ("done", None, False),
    ],
)
@pytest.mark.parametrize("plan_status", ["draft", "active"])
def test_is_overdue(status, due_on, expected, plan_status):
    assert rules.is_overdue(status, due_on, TODAY, plan_status=plan_status) is expected


@pytest.mark.parametrize(
    ("status", "due_on"),
    [
        ("pending", date(2031, 3, 4)),
        ("in_progress", date(2031, 3, 4)),
        ("in_progress", date(2000, 1, 1)),
        ("done", date(2031, 3, 4)),
        ("pending", TODAY),
        ("pending", None),
    ],
)
@pytest.mark.parametrize("plan_status", ["completed", "discontinued"])
def test_a_closed_plan_has_no_overdue_milestone(status, due_on, plan_status):
    # #332: once completed or discontinued, nothing counts as overdue.
    assert rules.is_overdue(status, due_on, TODAY, plan_status=plan_status) is False


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({}, R.NONE),
        ({"manager": True}, R.MANAGER),
        ({"manager": True, "tutor": True, "student_assigned": True}, R.MANAGER),
        ({"manager": True, "family": True}, R.MANAGER),
        (
            {"tutor": True, "student_assigned": True, "subject_assigned": True},
            R.SUBJECT_TUTOR,
        ),
        ({"tutor": True, "student_assigned": True}, R.ASSIGNED_TUTOR),
        (
            {"tutor": True, "student_assigned": True, "is_creator": True},
            R.ASSIGNED_TUTOR,
        ),
        ({"tutor": True, "is_creator": True}, R.FORMER_CREATOR),
        ({"tutor": True, "is_creator": True, "family": True}, R.FORMER_CREATOR),
        ({"tutor": True}, R.NONE),
        ({"tutor": True, "family": True}, R.FAMILY),
        ({"family": True}, R.FAMILY),
        # Without the tutor role, assignments and authorship give nothing (a
        # tutor who lost the role).
        ({"student_assigned": True, "subject_assigned": True}, R.NONE),
        ({"is_creator": True}, R.NONE),
        ({"is_creator": True, "family": True}, R.FAMILY),
    ],
)
def test_plan_relation(facts, expected):
    values = {
        "manager": False,
        "tutor": False,
        "student_assigned": False,
        "subject_assigned": False,
        "is_creator": False,
        "family": False,
    } | facts
    assert rules.plan_relation(**values) is expected


@pytest.mark.parametrize(
    ("status", "activated", "deleted", "expected"),
    [
        ("draft", False, False, False),
        ("draft", False, True, False),
        ("active", True, False, True),
        ("completed", True, False, True),
        ("discontinued", True, False, True),
        ("discontinued", False, False, False),
        ("discontinued", True, True, False),
    ],
)
def test_family_visible(status, activated, deleted, expected):
    activated_at = WHEN if activated else None
    assert rules.family_visible(status, activated_at, deleted) is expected


@pytest.mark.parametrize(
    ("relation", "status", "activated", "deleted"),
    list(
        product(
            list(R),
            rules.STATUSES,
            [False, True],
            [False, True],
        )
    ),
)
def test_can_read(relation, status, activated, deleted):
    activated_at = WHEN if activated else None
    readable = rules.can_read(
        relation, status=status, activated_at=activated_at, deleted=deleted
    )
    if deleted or relation is R.NONE:
        assert readable is False
    elif relation is R.FAMILY:
        assert readable is rules.family_visible(status, activated_at, deleted)
    else:
        assert readable is True


@pytest.mark.parametrize(
    ("relation", "expected"),
    [
        (R.MANAGER, None),
        (R.SUBJECT_TUTOR, None),
        (R.ASSIGNED_TUTOR, "plan_subject_not_assigned"),
        (R.FORMER_CREATOR, "plan_read_only"),
        (R.FAMILY, "not_found"),
        (R.NONE, "not_found"),
    ],
)
def test_access_block(relation, expected):
    assert rules.access_block(relation) == expected


TRANSITIONS = {
    ("draft", "activate"): None,
    ("draft", "complete"): "plan_not_active",
    ("draft", "discontinue"): None,
    ("active", "activate"): "plan_not_draft",
    ("active", "complete"): None,
    ("active", "discontinue"): None,
    ("completed", "activate"): "plan_closed",
    ("completed", "complete"): "plan_closed",
    ("completed", "discontinue"): "plan_closed",
    ("discontinued", "activate"): "plan_closed",
    ("discontinued", "complete"): "plan_closed",
    ("discontinued", "discontinue"): "plan_closed",
}
STATES = {
    **TRANSITIONS,
    ("draft", "edit"): None,
    ("active", "edit"): None,
    ("completed", "edit"): "plan_closed",
    ("discontinued", "edit"): "plan_closed",
    ("draft", "progress"): "plan_not_active",
    ("active", "progress"): None,
    ("completed", "progress"): "plan_closed",
    ("discontinued", "progress"): "plan_closed",
    ("draft", "delete"): None,
    ("active", "delete"): "plan_not_draft",
    ("completed", "delete"): "plan_not_draft",
    ("discontinued", "delete"): "plan_not_draft",
}


def test_the_tables_cover_every_status_and_action():
    assert set(TRANSITIONS) == set(product(rules.STATUSES, rules.TRANSITIONS))
    assert set(STATES) == set(product(rules.STATUSES, rules.ACTIONS))


@pytest.mark.parametrize(("status", "action"), sorted(TRANSITIONS))
def test_transition_block(status, action):
    assert rules.transition_block(status, action) == TRANSITIONS[(status, action)]


@pytest.mark.parametrize(("status", "action"), sorted(STATES))
def test_state_block(status, action):
    assert rules.state_block(status, action) == STATES[(status, action)]


@pytest.mark.parametrize(
    ("relation", "status", "action", "deleted"),
    list(product(list(R), rules.STATUSES, rules.ACTIONS, [False, True])),
)
def test_write_block(relation, status, action, deleted):
    blocked = rules.write_block(relation, status=status, deleted=deleted, action=action)
    if deleted:
        assert blocked == "not_found"
    elif relation not in (R.MANAGER, R.SUBJECT_TUTOR):
        assert blocked == rules.access_block(relation)
    else:
        assert blocked == STATES[(status, action)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("   ", None), ("  Leer  ", "Leer"), ("a\n", "a")],
)
def test_clean(value, expected):
    assert rules.clean(value) == expected
