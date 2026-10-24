"""The kind-neutral enrollment mechanics (#66 B1, B2): the capacity conflict
names the group or the cohort, and the helpers refuse a series that is not a
group or course series of their owner."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.modules.scheduling.groups_rules import group_capacity_conflicts
from app.modules.scheduling.models import ClassSeries
from app.modules.scheduling.series_participants import (
    SeriesOwner,
    add_series_participant,
    plan_series_participant_add,
    remove_series_participant,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

COHORT = uuid.UUID("00000000-0000-4000-8000-000000000066")
GROUP = uuid.UUID("00000000-0000-4000-8000-000000000056")
NOW = datetime(2031, 1, 5, 18, 0, tzinfo=UTC)


def test_the_capacity_conflict_names_the_cohort():
    [conflict] = group_capacity_conflicts(3, max_students=2, cohort_id=COHORT)

    assert (conflict.code, conflict.severity) == ("group_capacity_exceeded", "soft")
    assert conflict.details == {
        "cohort_id": str(COHORT),
        "max_students": 2,
        "enrolled_count": 3,
    }
    assert group_capacity_conflicts(2, max_students=2, cohort_id=COHORT) == []


@pytest.mark.parametrize("owners", [{}, {"group_id": GROUP, "cohort_id": COHORT}])
def test_the_capacity_conflict_needs_exactly_one_owner(owners):
    with pytest.raises(ValueError, match="exactly one"):
        group_capacity_conflicts(3, max_students=2, **owners)


def _series(kind: str, **owner: uuid.UUID | None) -> ClassSeries:
    return ClassSeries(
        id=uuid.uuid4(),
        kind=kind,
        group_id=owner.get("group_id"),
        cohort_id=owner.get("cohort_id"),
    )


class _Session:
    """Only ``get``: the helpers check the caller's locked series first."""

    def __init__(self, series: ClassSeries) -> None:
        self.series = series

    async def get(self, model: Any, key: uuid.UUID) -> ClassSeries | None:
        return self.series if key == self.series.id else None


def test_the_owner_matches_its_own_series_only():
    cohort, group = SeriesOwner.cohort(COHORT), SeriesOwner.group(GROUP)

    assert cohort.owns(_series("course", cohort_id=COHORT))
    assert not cohort.owns(_series("course", cohort_id=uuid.uuid4()))
    assert not cohort.owns(_series("group", group_id=COHORT))
    assert group.owns(_series("group", group_id=GROUP))
    assert not group.owns(_series("individual"))


@pytest.mark.parametrize(
    ("series", "message"),
    [
        (_series("individual"), "individual series"),
        (_series("course", cohort_id=uuid.uuid4()), "is not of"),
        (_series("group", group_id=GROUP), "is not of"),
    ],
)
async def test_the_helpers_refuse_another_kind_or_owner(series, message):
    session: Any = _Session(series)
    owner = SeriesOwner.cohort(COHORT)
    common = {"series_ids": [series.id], "student_id": uuid.uuid4(), "owner": owner}

    with pytest.raises(ValueError, match=message):
        await plan_series_participant_add(
            session, max_students=2, active_count=0, now=NOW, zone="UTC", **common
        )
    with pytest.raises(ValueError, match=message):
        await add_series_participant(session, now=NOW, **common)
    with pytest.raises(ValueError, match=message):
        await remove_series_participant(session, now=NOW, **common)


async def test_an_unknown_series_is_refused():
    session: Any = _Session(_series("course", cohort_id=COHORT))

    with pytest.raises(ValueError, match="no class series"):
        await add_series_participant(
            session,
            series_ids=[uuid.uuid4()],
            student_id=uuid.uuid4(),
            now=NOW,
            owner=SeriesOwner.cohort(COHORT),
        )
