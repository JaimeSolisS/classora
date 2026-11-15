"""Series edits and ``PATCH`` of a tutoring group's series (#276 B, C1; the
#56 changes to #54's code), through the real app with a frozen clock.

- **A split keeps ``group_id``:** a ``mode=following`` edit makes a new
  ``group`` series of the same group; every row of both series keeps the
  group, the group's ``current_series`` is the new one, and a later
  enrollment reaches it.
- **An enrollment bumps the versions:** a series edit or a ``PATCH`` sent
  with a version read before it is ``409 version_conflict`` and changes
  nothing.

The group is a Monday/Wednesday 16:00 series without a tutor, from the
Monday after the timeline's "now" (:mod:`tests.integration.group_support`).
"""

from datetime import timedelta
from typing import Any

import pytest

from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import patch, post
from tests.integration.conftest import run_sql
from tests.integration.group_support import (
    create_group,
    enroll,
    group_body,
    group_rows,
    participants,
    read_group,
    series_of,
    series_students,
)
from tests.integration.lifecycle_support import code_of
from tests.integration.series_edit_support import SERIES, SESSIONS, edit_body
from tests.integration.series_support import iso_z, owner_cookie, timeline
from tests.integration.test_class_session_create import (
    CREATED,
    new_room,
    new_student,
    new_subject,
)

OK = (200, None)
STALE = (409, "version_conflict")
AFFECTED = {"unassigned", "offered", "confirmed", "conflict", "skipped_closure"}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def setup(module_seeded_url: str, client: Any) -> dict[str, Any]:
    """A group of two students (no tutor, a 10-seat room), a third student,
    the timeline and an Owner's cookie."""
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    students = [new_student(url) for _ in range(3)]
    room = new_room(url, capacity=10)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_group(
            client,
            cookie,
            group_body(
                subject=subject, starts_on=tl.monday, students=students[:2], room=room
            ),
        )
    assert code_of(response) == CREATED, response.text
    group_id = response.json()["id"]
    [series] = series_of(url, group_id)
    return {
        "tl": tl,
        "cookie": cookie,
        "group_id": group_id,
        "series": series,
        "students": students,
        "room": room,
    }


def _split_from(url: str, series_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT split_from_series_id FROM class_series WHERE id = :id",
        {"id": str(series_id)},
    )
    return row.split_from_series_id


def _rows_of_series(url: str, series_ids: list[Any]) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, series_id, group_id, state, starts_at FROM class_sessions"
        " WHERE series_id = ANY(:ids) ORDER BY starts_at",
        {"ids": [str(s) for s in series_ids]},
    )


def _snapshot(url: str, group_id: Any) -> list[Any]:
    """Everything a refused edit or ``PATCH`` must leave alone."""
    sessions = run_sql(
        url,
        "SELECT id, state, starts_at, ends_at, classroom_id, series_id, version"
        " FROM class_sessions WHERE group_id = :id ORDER BY id",
        {"id": str(group_id)},
    )
    series = run_sql(
        url,
        "SELECT id, version, start_time, starts_on, last_occurrence_on"
        " FROM class_series WHERE group_id = :id ORDER BY id",
        {"id": str(group_id)},
    )
    return [tuple(r) for r in sessions] + [tuple(r) for r in series]


def test_a_following_split_keeps_the_group(module_seeded_url, client, setup):
    url = module_seeded_url
    tl = setup["tl"]
    old = setup["series"]
    first_two = {str(s) for s in setup["students"][:2]}

    with frozen_clock(tl.now):
        response = post(
            client,
            setup["cookie"],
            f"{SERIES}/{old.id}/edits",
            edit_body(
                from_date=tl.week(4),
                version=old.version,
                start_time="17:00",
                classroom_id=setup["room"],
            ),
        )

    assert code_of(response) == OK, response.text
    edited = response.json()
    assert edited["split"] is True
    assert edited["previous_series_id"] == str(old.id)
    new_id = edited["series"]["id"]
    assert [
        (str(s.id), s.kind, str(s.group_id)) for s in series_of(url, setup["group_id"])
    ] == [
        (str(old.id), "group", setup["group_id"]),
        (new_id, "group", setup["group_id"]),
    ]
    assert str(_split_from(url, new_id)) == str(old.id)
    assert series_students(url, new_id) == first_two

    rows = _rows_of_series(url, [old.id, new_id])
    assert {str(r.series_id) for r in rows} == {str(old.id), new_id}
    assert all(str(r.group_id) == setup["group_id"] for r in rows)

    with frozen_clock(tl.now):
        group = read_group(client, setup["cookie"], setup["group_id"])
    assert code_of(group) == OK, group.text
    assert group.json()["current_series"]["id"] == new_id

    # B3: an enrollment after the split reaches the new series.
    third = setup["students"][2]
    with frozen_clock(tl.now):
        enrolled = enroll(client, setup["cookie"], setup["group_id"], third)
    assert code_of(enrolled) == CREATED, enrolled.text
    assert series_students(url, new_id) == first_two | {str(third)}
    new_rows = [
        r
        for r in _rows_of_series(url, [new_id])
        if r.state in AFFECTED and r.starts_at > tl.now
    ]
    assert new_rows
    assert all(str(third) in participants(url, r.id) for r in new_rows)


def test_a_stale_series_edit_after_an_enrollment_is_refused(
    module_seeded_url, client, setup
):
    url = module_seeded_url
    tl = setup["tl"]
    old = setup["series"]
    with frozen_clock(tl.now):
        enrolled = enroll(
            client, setup["cookie"], setup["group_id"], setup["students"][2]
        )
    assert code_of(enrolled) == CREATED, enrolled.text
    before = _snapshot(url, setup["group_id"])

    with frozen_clock(tl.now):
        response = post(
            client,
            setup["cookie"],
            f"{SERIES}/{old.id}/edits",
            edit_body(
                from_date=tl.week(4),
                version=old.version,
                start_time="17:00",
                classroom_id=setup["room"],
            ),
        )

    assert code_of(response) == STALE, response.text
    assert _snapshot(url, setup["group_id"]) == before


def test_a_stale_patch_of_a_group_occurrence_after_an_enrollment_is_refused(
    module_seeded_url, client, setup
):
    url = module_seeded_url
    tl = setup["tl"]
    row = group_rows(url, setup["group_id"])[2]
    with frozen_clock(tl.now):
        enrolled = enroll(
            client, setup["cookie"], setup["group_id"], setup["students"][2]
        )
    assert code_of(enrolled) == CREATED, enrolled.text
    before = _snapshot(url, setup["group_id"])

    with frozen_clock(tl.now):
        response = patch(
            client,
            setup["cookie"],
            f"{SESSIONS}/{row.id}",
            {
                "version": row.version,
                "starts_at": iso_z(row.starts_at + timedelta(hours=1)),
                "ends_at": iso_z(row.ends_at + timedelta(hours=1)),
            },
        )

    assert code_of(response) == STALE, response.text
    assert _snapshot(url, setup["group_id"]) == before
