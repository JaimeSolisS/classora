"""The family leak walk (#79 I1; spec §2 "Internal tutor notes are not
automatically shared with families", §16): **no** ``GET`` route a family
can reach returns a tutor's internal, unpublished or deleted note, nor
another student's note.

Shared infrastructure: later family-facing issues (#80 evaluations, #81
learning plans, #82 assignments, #83 progress reports, #84 dashboards)
extend this file instead of writing their own walk.

**The world** (:class:`LeakWorld`, built once per module): a minor student
with a linked guardian, an adult student with a guardian who holds the
``guardian_academic_access`` consent, both students with a login, both in
one group session and each in an individual session of one tutor
(confirmed, started: family-visible).

**The canaries** (:data:`CANARY_SOURCES`): each source creates its records
in the world and returns a :class:`Canaries`: unique strings per student
that must **never** reach a family (``hidden``), strings that the
student's own family may see (``visible``; they must never reach the
**other** student's family), and the route templates where the visible
ones must appear (``shown_on``, the positive control). #79's source writes,
per student and session: (i) an internal note, (ii) a note published then
unpublished, (iii) a note published, unpublished and soft-deleted, and a
published note (visible to that family only: (iv) for the other family).

**The walk** (:func:`family_get_routes`): every ``GET`` route of
``build_app(include_dev_routes=True)``, except

- :data:`EXCLUDED_ROUTES` (named, with a reason: the event stream, which
  never ends and carries ids only);
- the routes the static matrix (``tests/authz/matrix.toml``) gates behind a
  permission no family role holds (staff-only routes: a family gets ``403
  permission_denied`` before any record is read).

Each path parameter is filled from :data:`PATH_PARAMS` (every value of the
list is tried); **a route with a parameter name missing from the map fails
the walk** (``test_every_path_parameter_is_mapped``), so a new route has to
be covered. Query parameters come from :data:`QUERY_PARAMS` when a route
needs them to return data (a route answering ``4xx`` leaks nothing either).
Every route is called as the minor's guardian, the adult's guardian, the
minor and the adult.

**Extending it** (a new family-facing record or route):

1. a new path parameter name → add it to :data:`PATH_PARAMS` (a function of
   the world returning the ids to try; add the ids to :class:`LeakWorld` if
   the world must create them);
2. a route that needs query parameters to return data → :data:`QUERY_PARAMS`;
3. a new kind of record a family must not see (or may see) → a canary
   source appended to :data:`CANARY_SOURCES`, returning its hidden and
   visible strings per student and its ``shown_on`` templates;
4. a route that can't be called (a stream, a download that never ends) →
   :data:`EXCLUDED_ROUTES` with the reason.
"""

import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import build_app
from app.modules.families.models import Guardian, Student
from app.modules.identity.permissions import GUARDIAN, STUDENT, TUTOR
from tests.authz.harness import api_route_contexts, load_matrix
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.schedule_support import stored, z
from tests.integration.session_request_support import MINOR_BIRTHDATE

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
STARTED = NOW - timedelta(minutes=30)

FAMILY_ROLES = frozenset({STUDENT, GUARDIAN})

EXCLUDED_ROUTES: dict[str, str] = {
    "/api/v1/notifications/stream": (
        "a Server-Sent Events stream that never ends; it carries notification"
        " ids only (the list is walked: /api/v1/notifications)"
    ),
}


# --- The world ------------------------------------------------------------------------


@dataclass
class LeakActor:
    label: str
    user: CreatedUser
    student_id: uuid.UUID
    """The student whose family this actor is."""


@dataclass
class LeakWorld:
    minor: uuid.UUID
    adult: uuid.UUID
    minor_guardian: LeakActor
    adult_guardian: LeakActor
    minor_student: LeakActor
    adult_student: LeakActor
    minor_guardian_id: uuid.UUID
    adult_guardian_id: uuid.UUID
    tutor: CreatedUser
    group_session: str
    minor_session: str
    adult_session: str
    group_id: uuid.UUID
    subject_ids: list[uuid.UUID]
    note_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#79: every note, deleted ones too)."""

    @property
    def actors(self) -> list[LeakActor]:
        return [
            self.minor_guardian,
            self.adult_guardian,
            self.minor_student,
            self.adult_student,
        ]

    @property
    def students(self) -> list[uuid.UUID]:
        return [self.minor, self.adult]

    @property
    def sessions(self) -> list[str]:
        return [self.group_session, self.minor_session, self.adult_session]


@dataclass
class Canaries:
    hidden: dict[uuid.UUID, set[str]]
    """Per student: never in any family response."""
    visible: dict[uuid.UUID, set[str]]
    """Per student: only ever in that student's family's responses."""
    shown_on: list[str]
    """Route templates (``{student_id}`` = the actor's student) where the
    visible strings of the actor's student must appear."""


def _family_member(
    url: str,
    student_id: uuid.UUID,
    *,
    consent: bool,
    student_login_consent: bool = False,
) -> tuple[CreatedUser, uuid.UUID]:
    """A guardian account with an active profile linked to the student."""
    account = create_user(url, roles=(GUARDIAN,), display_name="Familia Fuga")

    async def _link(session: AsyncSession) -> uuid.UUID:
        guardian: Guardian = await persist(session, GuardianFactory, user_id=account.id)
        student = await session.get(Student, student_id)
        assert student is not None
        await link_guardian(session, student, guardian)
        if consent:
            await login_consent(
                session,
                student,
                guardian,
                recorded_by=account.id,
                scope="guardian_academic_access",
            )
        if student_login_consent:
            await login_consent(session, student, guardian, recorded_by=account.id)
        return guardian.id

    return account, in_uow(url, _link)


def _student_with_login(url: str, *, minor: bool) -> tuple[CreatedUser, uuid.UUID]:
    account = create_user(url, roles=(STUDENT,), display_name="Alumno Fuga")

    async def _make(session: AsyncSession) -> uuid.UUID:
        student = await persist_student(
            session, contacts=0, birthdate=MINOR_BIRTHDATE if minor else ADULT_BIRTHDATE
        )
        await student_login(session, student, account.id)
        return student.id

    return account, in_uow(url, _make)


def build_world(url: str) -> LeakWorld:
    minor_login, minor = _student_with_login(url, minor=True)
    adult_login, adult = _student_with_login(url, minor=False)
    # The minor's login rests on this guardian's student_login consent.
    minor_parent, minor_guardian_id = _family_member(
        url, minor, consent=False, student_login_consent=True
    )
    adult_parent, adult_guardian_id = _family_member(url, adult, consent=True)
    tutor = create_user(url, roles=(TUTOR,), display_name="Tutora Fuga")
    group_session = stored(
        url,
        STARTED,
        students=(minor, adult),
        tutor_user_id=tutor.id,
        kind="group",
    )
    minor_session = stored(
        url, STARTED + timedelta(days=1), students=(minor,), tutor_user_id=tutor.id
    )
    adult_session = stored(
        url, STARTED + timedelta(days=2), students=(adult,), tutor_user_id=tutor.id
    )
    rows = run_sql(
        url,
        "SELECT group_id, subject_id FROM class_sessions WHERE id IN (:a, :b, :c)",
        {"a": group_session, "b": minor_session, "c": adult_session},
    )
    [group_id] = [row.group_id for row in rows if row.group_id is not None]
    return LeakWorld(
        minor=minor,
        adult=adult,
        minor_guardian=LeakActor("minor's guardian", minor_parent, minor),
        adult_guardian=LeakActor("adult's guardian", adult_parent, adult),
        minor_student=LeakActor("minor", minor_login, minor),
        adult_student=LeakActor("adult", adult_login, adult),
        minor_guardian_id=minor_guardian_id,
        adult_guardian_id=adult_guardian_id,
        tutor=tutor,
        group_session=group_session,
        minor_session=minor_session,
        adult_session=adult_session,
        group_id=group_id,
        subject_ids=[row.subject_id for row in rows],
    )


# --- Canary sources -------------------------------------------------------------------


def session_note_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#79: per student and session, (i) internal, (ii) published then
    unpublished, (iii) published, unpublished then deleted, and a published
    note (the student's family may see it; (iv) for the other family)."""
    cookie = sign_in(client, world.tutor.username)
    hidden: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}

    def note(session_id: str, student: uuid.UUID, kind: str) -> tuple[str, str]:
        text = f"CANARY-{kind}-{uuid.uuid4().hex}"
        response = post(
            client,
            cookie,
            f"/api/v1/class-sessions/{session_id}/notes",
            {"student_id": str(student), "body": text},
        )
        assert code_of(response) == (201, None), response.text
        note_id = str(response.json()["id"])
        world.note_ids.append(note_id)
        return note_id, text

    def write(path: str, body: dict[str, Any]) -> None:
        response = post(client, cookie, path, body)
        assert code_of(response) == (200, None), response.text

    for session_id, student in (
        (world.group_session, world.minor),
        (world.group_session, world.adult),
        (world.minor_session, world.minor),
        (world.adult_session, world.adult),
    ):
        _, internal = note(session_id, student, "internal")
        hidden[student].add(internal)

        unpublished_id, unpublished = note(session_id, student, "unpublished")
        write(f"/api/v1/notes/{unpublished_id}/publish", {"version": 1})
        write(f"/api/v1/notes/{unpublished_id}/unpublish", {"version": 2})
        hidden[student].add(unpublished)

        deleted_id, deleted = note(session_id, student, "deleted")
        write(f"/api/v1/notes/{deleted_id}/publish", {"version": 1})
        write(f"/api/v1/notes/{deleted_id}/unpublish", {"version": 2})
        client.cookies.clear()
        response = client.delete(
            f"/api/v1/notes/{deleted_id}",
            params={"version": 3},
            headers=signed_headers(cookie),
        )
        assert response.status_code == 204, response.text
        hidden[student].add(deleted)

        shared_id, shared = note(session_id, student, "shared")
        write(f"/api/v1/notes/{shared_id}/publish", {"version": 1})
        visible[student].add(shared)

    return Canaries(
        hidden=hidden,
        visible=visible,
        shown_on=["/api/v1/students/{student_id}/learning-notes"],
    )


CANARY_SOURCES: list[Callable[[TestClient, LeakWorld], Canaries]] = [
    session_note_canaries,
]


# --- The route map --------------------------------------------------------------------


def _unknown(_: LeakWorld) -> list[str]:
    return [str(uuid.uuid4())]


PATH_PARAMS: dict[str, Callable[[LeakWorld], list[str]]] = {
    # The world's records: both students, every session, every note.
    "student_id": lambda w: [str(s) for s in w.students],
    "class_session_id": lambda w: list(w.sessions),
    "note_id": lambda w: list(w.note_ids),
    "group_id": lambda w: [str(w.group_id)],
    "subject_id": lambda w: [str(s) for s in w.subject_ids],
    "guardian_id": lambda w: [str(w.minor_guardian_id), str(w.adult_guardian_id)],
    "user_id": lambda w: [str(w.tutor.id)] + [str(a.user.id) for a in w.actors],
    # Parameters of routes whose records the world doesn't create: an
    # unknown id still exercises the route's 404 path.
    "family_id": _unknown,
    "request_id": _unknown,
    "change_request_id": _unknown,
    "cohort_id": _unknown,
    "level_id": _unknown,
    "period_id": _unknown,
    "closure_id": _unknown,
    "classroom_id": _unknown,
    "consent_id": _unknown,
}
"""Every path parameter name of a family-reachable ``GET`` route → the
values to try. A missing name fails ``test_every_path_parameter_is_mapped``."""

QUERY_PARAMS: dict[str, Callable[[LeakWorld], dict[str, str]]] = {
    "/api/v1/students/{student_id}/schedule": lambda w: {
        "from": z(NOW - timedelta(days=7)),
        "to": z(NOW + timedelta(days=7)),
    },
    "/api/v1/groups/{group_id}/sessions": lambda w: {
        "from": z(NOW - timedelta(days=7)),
        "to": z(NOW + timedelta(days=7)),
    },
}
"""Route template → the query parameters it needs to return data."""

_PARAM = re.compile(r"{(\w+)(?::\w+)?}")


@dataclass(frozen=True)
class FamilyRoute:
    path: str
    params: tuple[str, ...]


def family_get_routes() -> list[FamilyRoute]:
    """Every ``GET`` route a family role may reach (see the module
    docstring), sorted by path."""
    entries = {(entry.method, entry.path): entry for entry in load_matrix()}
    found = []
    for context in api_route_contexts(build_app(include_dev_routes=True)):
        if not isinstance(context.original_route, APIRoute):
            continue
        if "GET" not in (context.methods or ()):
            continue
        path = str(context.path)
        if path in EXCLUDED_ROUTES:
            continue
        entry = entries[("GET", path)]
        if entry.access == "permission" and not FAMILY_ROLES & set(entry.allow):
            continue
        found.append(FamilyRoute(path, tuple(_PARAM.findall(path))))
    return sorted(found, key=lambda route: route.path)


def concrete_paths(route: FamilyRoute, world: LeakWorld) -> Iterator[str]:
    """Every combination of the mapped values of the route's parameters."""
    choices = [PATH_PARAMS[name](world) for name in route.params]
    for values in product(*choices):
        path = route.path
        for name, value in zip(route.params, values, strict=True):
            path = re.sub(r"{" + name + r"(?::\w+)?}", value, path)
        yield path


# --- Fixtures -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass
class Walk:
    world: LeakWorld
    canaries: list[Canaries]

    def hidden(self) -> set[str]:
        return {
            text for c in self.canaries for texts in c.hidden.values() for text in texts
        }

    def visible(self, student: uuid.UUID) -> set[str]:
        return {text for c in self.canaries for text in c.visible.get(student, set())}

    def forbidden_for(self, actor: LeakActor) -> set[str]:
        """Every hidden string, and the visible strings of other students."""
        others = {s for s in self.world.students if s != actor.student_id}
        return self.hidden() | {text for s in others for text in self.visible(s)}


@pytest.fixture(scope="module")
def walk(module_seeded_url: str, client: TestClient) -> Walk:
    world = build_world(module_seeded_url)
    return Walk(world, [source(client, world) for source in CANARY_SOURCES])


def _leaks(response: Response, forbidden: set[str]) -> set[str]:
    text = response.text
    return {canary for canary in forbidden if canary in text}


# --- The tests ------------------------------------------------------------------------


def test_every_path_parameter_is_mapped():
    routes = family_get_routes()
    unmapped = {
        f"{route.path}: unmapped parameter {name!r}"
        for route in routes
        for name in route.params
        if name not in PATH_PARAMS
    }
    assert not unmapped, (
        "Add these to PATH_PARAMS in tests/integration/test_session_notes_leak.py"
        f" (see its docstring): {sorted(unmapped)}"
    )
    # The walk covers the note routes and the family schedule.
    paths = {route.path for route in routes}
    assert {
        "/api/v1/students/{student_id}/learning-notes",
        "/api/v1/students/{student_id}/notes",
        "/api/v1/class-sessions/{class_session_id}/notes",
        "/api/v1/students/{student_id}/schedule",
        "/api/v1/notifications",
    } <= paths
    assert "/api/v1/notifications/stream" not in paths


def test_no_family_response_carries_a_hidden_note(client, walk):
    routes = family_get_routes()
    leaks: list[str] = []
    calls = 0
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        forbidden = walk.forbidden_for(actor)
        for route in routes:
            query = QUERY_PARAMS.get(route.path, lambda w: {})(walk.world)
            for path in concrete_paths(route, walk.world):
                response = get(client, cookie, path, **query)
                calls += 1
                assert response.status_code < 500, (path, response.text)
                for canary in _leaks(response, forbidden):
                    leaks.append(f"{actor.label}: GET {path} → {canary}")
    assert not leaks, "\n".join(leaks)
    assert calls > 4 * len(routes)


def test_the_visible_notes_reach_their_own_family(client, walk):
    """The positive control: the walk would see a leak (the shared notes do
    reach their family on the family route)."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        for canaries in walk.canaries:
            expected = canaries.visible[actor.student_id]
            assert expected
            for template in canaries.shown_on:
                response = get(
                    client, cookie, template.format(student_id=actor.student_id)
                )
                assert code_of(response) == (200, None), (actor.label, response.text)
                assert {c for c in expected if c in response.text} == expected


def test_notifications_carry_no_note_text(client, walk):
    """Publishing notified both families (E7), with ids only."""
    every = walk.hidden() | {
        text for student in walk.world.students for text in walk.visible(student)
    }
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(client, cookie, "/api/v1/notifications", limit=100)
        assert code_of(response) == (200, None)
        types = {item["notification_type"] for item in response.json()["items"]}
        assert "academics.learning_note_published" in types, actor.label
        assert not _leaks(response, every), actor.label
