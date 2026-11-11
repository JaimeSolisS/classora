"""The built-in General subject needs no qualification (HF-12, #597 AC4):
tutor suggestions (one slot and a whole series) and the family slot finder
consider every active tutor for it.

Each test has a database of its own (``seeded_url``): every tutor of a
database is a candidate for General, so a shared one would mix the tests'
tutors. Session create, series create, substitution and the calendar marker
are covered next to their own rules (``test_class_session_create.py``,
``test_class_series_create.py``, ``test_session_substitutions.py``,
``test_class_session_calendar.py``).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import ACADEMIC_COORDINATOR
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import general_subject, get, post
from tests.integration.invitation_support import sign_in
from tests.integration.series_support import timeline
from tests.integration.session_request_support import (
    MONDAY_WINDOW,
    NOW,
    get_slots,
    guardian_with_child,
    slot_starts,
    slots_params,
)
from tests.integration.test_class_session_create import new_subject, new_tutor
from tests.integration.test_series_tutor_suggestions import PATH as SERIES_PATH
from tests.integration.test_series_tutor_suggestions import body as series_body
from tests.integration.test_series_tutor_suggestions import counts
from tests.integration.test_series_tutor_suggestions import listed as series_listed
from tests.integration.test_tutor_suggestions import (
    SUGGESTIONS,
    listed,
    query,
    remove_tutor_role,
)


@pytest.fixture
def client(seeded_url: str) -> Iterator[TestClient]:
    with auth_client(seeded_url) as test_client:
        yield test_client


def coordinator_cookie(url: str, client: TestClient) -> Any:
    user = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    return sign_in(client, user.username)


def test_every_active_tutor_is_suggested_for_a_general_slot(seeded_url, client):
    url = seeded_url
    general = general_subject(url)
    unqualified = new_tutor(url)
    qualified_elsewhere = new_tutor(url, qualified_for=[new_subject(url)])
    new_tutor(url, state="suspended")
    former = new_tutor(url)
    remove_tutor_role(url, former)
    cookie = coordinator_cookie(url, client)

    response = get(client, cookie, SUGGESTIONS, **query(general))

    assert set(listed(response)) == {unqualified, qualified_elsewhere}
    assert response.json()["eligible_count"] == 2
    # Another subject still needs the qualification.
    other = new_subject(url)
    assert listed(get(client, cookie, SUGGESTIONS, **query(other))) == []


def test_every_active_tutor_is_suggested_for_a_general_series(seeded_url, client):
    url = seeded_url
    tl = timeline()
    general = general_subject(url)
    unqualified = new_tutor(url)
    new_tutor(url, state="suspended")

    with frozen_clock(tl.now):
        cookie = coordinator_cookie(url, client)
        response = post(client, cookie, SERIES_PATH, series_body(general, tl))

    assert response.status_code == 200, response.text
    result = response.json()
    assert series_listed(result) == [unqualified]
    assert counts(result) == (12, 1, 1, 0, 0)


def test_the_family_slot_finder_offers_any_tutors_general_times(seeded_url, client):
    url = seeded_url
    general = general_subject(url)
    family = guardian_with_child(url)
    new_tutor(url, windows=MONDAY_WINDOW)  # no qualification at all

    with frozen_clock(NOW):
        cookie = sign_in(client, family.user.username)
        response = get_slots(client, cookie, slots_params(family.student_id, general))
        other = get_slots(
            client, cookie, slots_params(family.student_id, new_subject(url))
        )

    assert slot_starts(response) == ["10:00", "10:30", "11:00"]
    assert slot_starts(other) == []
