"""The #67 A3 visibility rules: every D2 state × ``was_confirmed`` for the
family and tutor schedules, and the drift test between the pure rules and
the SQL predicates the reads use."""

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.role_schedules import (
    family_visible_condition,
    tutor_visible_condition,
)
from app.modules.scheduling.states import (
    FAMILY_VISIBLE_STATES,
    ONCE_CONFIRMED_VISIBLE_STATES,
    TUTOR_VISIBLE_STATES,
    SessionState,
    is_family_visible,
    is_tutor_visible,
)

FAMILY: dict[tuple[str, bool], bool] = {
    ("unassigned", False): False,
    ("unassigned", True): False,
    ("offered", False): False,
    ("offered", True): False,
    ("confirmed", False): True,
    ("confirmed", True): True,
    ("completed", False): True,
    ("completed", True): True,
    ("cancelled", False): False,
    ("cancelled", True): True,
    ("skipped_closure", False): False,
    ("skipped_closure", True): True,
    ("conflict", False): False,
    ("conflict", True): False,
}
TUTOR: dict[tuple[str, bool], bool] = {
    **FAMILY,
    ("offered", False): True,
    ("offered", True): True,
}


def test_every_state_is_covered():
    assert {state for state, _ in FAMILY} == {state.value for state in SessionState}


@pytest.mark.parametrize(("case", "expected"), FAMILY.items())
def test_the_family_rule(case, expected):
    state, was_confirmed = case
    assert is_family_visible(state, was_confirmed=was_confirmed) is expected


@pytest.mark.parametrize(("case", "expected"), TUTOR.items())
def test_the_tutor_rule(case, expected):
    state, was_confirmed = case
    assert is_tutor_visible(state, was_confirmed=was_confirmed) is expected


def test_the_one_argument_form_of_52_is_unchanged():
    for state in SessionState:
        assert is_family_visible(state.value) is (state in FAMILY_VISIBLE_STATES)
    assert scheduling.is_family_visible is is_family_visible


@pytest.mark.parametrize("rule", [is_family_visible, is_tutor_visible])
def test_unknown_states_are_never_visible(rule):
    assert rule("draft", was_confirmed=True) is False


def _sql(condition) -> str:  # type: ignore[no-untyped-def]
    return str(
        condition.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _quoted(states: frozenset[SessionState]) -> str:
    return ", ".join(f"'{state.value}'" for state in sorted(states))


@pytest.mark.parametrize(
    ("condition", "always"),
    [
        (family_visible_condition, FAMILY_VISIBLE_STATES),
        (tutor_visible_condition, TUTOR_VISIBLE_STATES),
    ],
)
def test_the_sql_predicates_are_built_from_the_same_sets(condition, always):
    """Drift test: the predicate is ``state IN (always) OR (state IN
    (once_confirmed) AND first_confirmed_at IS NOT NULL)``."""
    assert _sql(condition()) == (
        f"class_sessions.state IN ({_quoted(always)})"
        f" OR class_sessions.state IN ({_quoted(ONCE_CONFIRMED_VISIBLE_STATES)})"
        " AND class_sessions.first_confirmed_at IS NOT NULL"
    )
    rule = is_family_visible if always is FAMILY_VISIBLE_STATES else is_tutor_visible
    for state in SessionState:
        for was_confirmed in (False, True):
            in_sql = state in always or (
                was_confirmed and state in ONCE_CONFIRMED_VISIBLE_STATES
            )
            assert rule(state.value, was_confirmed=was_confirmed) is in_sql
