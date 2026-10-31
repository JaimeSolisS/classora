"""The family leak walk (#79 I1, #80 I1, #81 I1, #82, #83 I1; spec §2 "Internal tutor
notes are not automatically shared with families", §4 "grades … visible
according to permissions", "learning plans … visible to
students/guardians", §16): **no** ``GET`` route a family can reach returns
a tutor's internal, unpublished or deleted note, a draft, deleted or voided
evaluation, an evaluation's internal change or void reason, a draft,
deleted or never-activated learning plan, a plan's text from before an
edit, nor another student's note, evaluation or plan.

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
#80's source writes, per student: (i) a draft evaluation, (ii) a deleted
draft, (iii) a final evaluation then voided (its feedback and the void
reason), (iv) the internal change reason of an amended final evaluation,
and the amended evaluation's feedback and comment (visible to that family
only: (v) for the other family), shown on the academic history. #81's
source writes, per student, learning plans with canaries in the title,
description, objective and milestone text: (i) a draft, (ii) a deleted
draft, (iii) a draft discontinued before activation, (v) the pre-edit
milestone text of an active plan edited after its activation, and that
active plan and a plan discontinued after its activation (visible to that
family only: (iv) for the other family), shown on the plan list and
detail (``test_the_active_learning_plans_reach_their_own_family``).
#83's source writes, per student, progress reports with canaries in the
narrative and in every reason: (i) a draft, (ii) a submitted report, (iii) a
report sent back for changes and its reason, (iv) a report approved then
retracted and the retract reason, (v) a deleted draft, and a report whose
first narrative was sent back (that text and the reason hidden) and whose
second narrative was approved (visible to that family only: (vi) for the
other family), shown on the approved detail
(``test_the_approved_progress_reports_reach_their_own_family``). Its facts
are compiled after the other sources ran, so they would carry any copied
feedback, note or reason canary ((vii),
``test_the_approved_report_facts_copy_no_text``).
#84's source closes out new past sessions of the world tutor, per student:
(i) a submitted closeout's summary, (ii) a sent-back closeout's summary and
its reason, (iii) the new summary and the tutor's note of an approved
closeout back in review after the tutor's correction request (#305), (iv) a
staff correction's reason and an on-behalf submission's reason (#301), (v)
an internal note on a closed session; and an approved, staff-corrected
individual summary and an approved on-behalf summary (visible to that family
only: (vi) for the other family), shown on the session log. A group session
of both students, approved, is the positive control
(``test_the_approved_summaries_reach_their_own_family``): its summary on
both families' logs, each with only their own child's attendance.
#85's source builds a cohort with both students and a classmate (no
family in the world, the classmate's name is the canary) and one completed
course session, then per student evaluations linked to its required
assessment: (i) a draft (its id and feedback), (ii) a voided final one (its
id and the void reason), (iii) the internal change reason of an amended
earlier attempt; the counted (latest) one and the earlier attempt are
visible to that family only ((iv) for the other family). The positive
control (``test_the_completion_rows_reach_their_own_family``): each family
reads exactly its own row on ``/cohorts/{cohort_id}/completion``, with
the counted score.

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
from decimal import Decimal
from itertools import product
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import build_app
from app.modules.courses.models import CohortEnrollment
from app.modules.families.models import Guardian, Student
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.authz.harness import api_route_contexts, load_matrix
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.courses import persist_course_cohort
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
from tests.integration.catalog_support import get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.courses_support import put
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
    staff: CreatedUser
    """An Academic Coordinator (evaluation templates and voids, #80)."""
    note_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#79: every note, deleted ones too)."""
    evaluation_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#80: every evaluation, deleted too)."""
    template_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#80: the evaluation templates)."""
    session_subjects: dict[str, uuid.UUID] = field(default_factory=dict)
    """Each world session's subject (#81: plans in a subject the world
    tutor teaches the student)."""
    plan_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#81: every plan, deleted ones too)."""
    milestone_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#81: a milestone of every plan)."""
    shown_plans: dict[uuid.UUID, dict[str, set[str]]] = field(default_factory=dict)
    """Per student: the plans their family sees (plan id → its visible
    canaries), for the #81 positive control."""
    assignment_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#82: every homework assignment, deleted
    ones and ones the world's students didn't receive too)."""
    shown_feedback: dict[uuid.UUID, str] = field(default_factory=dict)
    """Per student: the feedback of their graded homework, for the #82
    history positive control."""
    url: str = ""
    """The database (sources that persist rows directly, #82)."""
    report_ids: list[str] = field(default_factory=list)
    """Filled by the canary sources (#83: every progress report, deleted
    and retracted ones too)."""
    shown_reports: dict[uuid.UUID, dict[str, set[str]]] = field(default_factory=dict)
    """Per student: the approved reports their family sees (report id → its
    visible canaries), for the #83 positive control."""
    shown_summaries: dict[uuid.UUID, dict[str, set[str]]] = field(default_factory=dict)
    """Per student: the sessions whose approved summary their family sees on
    the session log (session id → the summary's canaries), for the #84
    positive control."""
    closeout_group: str = ""
    """#84: the approved group session of both students (the minor present,
    the adult absent)."""
    closeout_group_summary: set[str] = field(default_factory=set)
    """#84: its approved summary, on both families' session logs."""
    cohort_id: str = ""
    """#85: the cohort of both students and a classmate."""
    shown_completion: dict[uuid.UUID, str] = field(default_factory=dict)
    """#85: per student, the counted score on the completion route."""

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
    staff = create_user(url, roles=(ACADEMIC_COORDINATOR,), display_name="Coord Fuga")
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
        "SELECT id, group_id, subject_id FROM class_sessions WHERE id IN (:a, :b, :c)",
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
        staff=staff,
        session_subjects={str(row.id): row.subject_id for row in rows},
        url=url,
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


def evaluation_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#80: per student (both are the world tutor's current assignments),
    (i) a draft, (ii) a deleted draft, (iii) a final evaluation then voided
    (its feedback, comment and the void reason), (iv) the change reason of
    an amended final evaluation, and that evaluation's amended feedback and
    comment (the student's family may see them; (v) for the other
    family)."""
    staff = sign_in(client, world.staff.username)
    response = post(
        client,
        staff,
        "/api/v1/evaluation-templates",
        {
            "name": f"Fuga {uuid.uuid4().hex[:8]}",
            "kind": "ongoing",
            "subject_id": str(world.subject_ids[0]),
            "criteria": [{"title": "Lectura", "max_score": "10"}],
        },
    )
    assert code_of(response) == (201, None), response.text
    template_id = str(response.json()["id"])
    world.template_ids.append(template_id)
    cookie = sign_in(client, world.tutor.username)
    hidden: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}

    def canary(kind: str) -> str:
        return f"CANARY-evaluation-{kind}-{uuid.uuid4().hex}"

    def evaluation(student: uuid.UUID, feedback: str, comment: str) -> str:
        response = post(
            client,
            cookie,
            "/api/v1/evaluations",
            {
                "student_id": str(student),
                "template_id": template_id,
                "scores": [{"position": 1, "score": "7", "comment": comment}],
                "feedback": feedback,
            },
        )
        assert code_of(response) == (201, None), response.text
        evaluation_id = str(response.json()["id"])
        world.evaluation_ids.append(evaluation_id)
        return evaluation_id

    def write(
        caller: Any, path: str, body: dict[str, Any], *, edit: bool = False
    ) -> None:
        send = patch if edit else post
        response = send(client, caller, path, body)
        assert code_of(response) == (200, None), response.text

    for student in world.students:
        draft = (canary("draft"), canary("draft-comment"))
        evaluation(student, *draft)
        hidden[student] |= set(draft)

        deleted = (canary("deleted"), canary("deleted-comment"))
        deleted_id = evaluation(student, *deleted)
        client.cookies.clear()
        response = client.delete(
            f"/api/v1/evaluations/{deleted_id}",
            params={"version": 1},
            headers=signed_headers(cookie),
        )
        assert response.status_code == 204, response.text
        hidden[student] |= set(deleted)

        voided = (canary("voided"), canary("voided-comment"))
        voided_id = evaluation(student, *voided)
        write(cookie, f"/api/v1/evaluations/{voided_id}/finalize", {"version": 1})
        reason = canary("void-reason")
        write(
            staff,
            f"/api/v1/evaluations/{voided_id}/void",
            {"version": 2, "reason": reason},
        )
        hidden[student] |= {*voided, reason}

        amended_id = evaluation(student, canary("before"), canary("before-comment"))
        write(cookie, f"/api/v1/evaluations/{amended_id}/finalize", {"version": 1})
        change_reason = canary("change-reason")
        shown = (canary("final"), canary("final-comment"))
        write(
            cookie,
            f"/api/v1/evaluations/{amended_id}",
            {
                "version": 2,
                "change_reason": change_reason,
                "feedback": shown[0],
                "scores": [{"position": 1, "score": "8", "comment": shown[1]}],
            },
            edit=True,
        )
        hidden[student].add(change_reason)
        visible[student] |= set(shown)

    return Canaries(
        hidden=hidden,
        visible=visible,
        shown_on=["/api/v1/students/{student_id}/academic-history"],
    )


def learning_plan_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#81: per student, in the subject of their individual session (the
    world tutor teaches it): (i) a draft, (ii) a deleted draft, (iii) a draft
    discontinued before activation, (v) the pre-edit milestone text of an
    active plan edited after its activation; the edited active plan and a
    plan discontinued after its activation are visible to that family
    only ((iv) for the other family)."""
    cookie = sign_in(client, world.tutor.username)
    hidden: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}

    def canary(kind: str) -> str:
        return f"CANARY-plan-{kind}-{uuid.uuid4().hex}"

    def plan(student: uuid.UUID, subject: uuid.UUID, kind: str) -> tuple[Any, ...]:
        texts = {
            part: canary(f"{kind}-{part}")
            for part in ("title", "goal", "objective", "why", "milestone", "todo")
        }
        response = post(
            client,
            cookie,
            f"/api/v1/students/{student}/learning-plans",
            {
                "subject_id": str(subject),
                "title": texts["title"],
                "description": texts["goal"],
                "objectives": [
                    {
                        "title": texts["objective"],
                        "description": texts["why"],
                        "milestones": [
                            {"title": texts["milestone"], "activities": texts["todo"]}
                        ],
                    }
                ],
            },
        )
        assert code_of(response) == (201, None), response.text
        view = response.json()
        world.plan_ids.append(view["id"])
        world.milestone_ids.append(view["objectives"][0]["milestones"][0]["id"])
        return view, texts

    def transition(plan_id: str, version: int, action: str) -> Any:
        response = post(
            client,
            cookie,
            f"/api/v1/learning-plans/{plan_id}/transition",
            {"version": version, "action": action},
        )
        assert code_of(response) == (200, None), response.text
        return response.json()

    for student, session in (
        (world.minor, world.minor_session),
        (world.adult, world.adult_session),
    ):
        subject = world.session_subjects[session]
        shown: dict[str, set[str]] = {}

        _, draft = plan(student, subject, "draft")
        hidden[student] |= set(draft.values())

        deleted_view, deleted = plan(student, subject, "deleted")
        client.cookies.clear()
        response = client.delete(
            f"/api/v1/learning-plans/{deleted_view['id']}",
            params={"version": 1},
            headers=signed_headers(cookie),
        )
        assert response.status_code == 204, response.text
        hidden[student] |= set(deleted.values())

        stopped_view, stopped = plan(student, subject, "never-active")
        transition(stopped_view["id"], 1, "discontinue")
        hidden[student] |= set(stopped.values())

        ended_view, ended = plan(student, subject, "ended")
        transition(ended_view["id"], 1, "activate")
        transition(ended_view["id"], 2, "discontinue")
        visible[student] |= set(ended.values())
        shown[ended_view["id"]] = set(ended.values())

        active_view, active = plan(student, subject, "active")
        active_view = transition(active_view["id"], 1, "activate")
        [objective] = active_view["objectives"]
        [milestone] = objective["milestones"]
        edited = {"milestone": canary("edited"), "todo": canary("edited-todo")}
        response = put(
            client,
            cookie,
            f"/api/v1/learning-plans/{active_view['id']}",
            {
                "version": active_view["version"],
                "title": active["title"],
                "description": active["goal"],
                "starts_on": active_view["starts_on"],
                "objectives": [
                    {
                        "id": objective["id"],
                        "title": active["objective"],
                        "description": active["why"],
                        "milestones": [
                            {
                                "id": milestone["id"],
                                "title": edited["milestone"],
                                "activities": edited["todo"],
                            }
                        ],
                    }
                ],
            },
        )
        assert code_of(response) == (200, None), response.text
        hidden[student] |= {active["milestone"], active["todo"]}
        now_shown = {
            active["title"],
            active["goal"],
            active["objective"],
            active["why"],
            *edited.values(),
        }
        visible[student] |= now_shown
        shown[active_view["id"]] = now_shown
        world.shown_plans[student] = shown

    return Canaries(hidden=hidden, visible=visible, shown_on=[])


def assignment_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#82: one group homework for both students and a third student of the
    world tutor whose name is a canary (v), and one individual homework per
    student. Per student: (i) their own submission text and URL in the group
    homework (their own login), (ii) the tutor's feedback, then (iii) the
    re-grade's internal change reason; (iv) a deleted homework's
    instructions; (vi) the individual homework of the other student. The
    third student's handed-in work (recorded by the tutor) is hidden from
    both families. Visible to that family only: their submission, the
    current feedback and their individual homework."""
    cookie = sign_in(client, world.tutor.username)
    hidden: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}

    def canary(kind: str) -> str:
        return f"CANARY-assignment-{kind}-{uuid.uuid4().hex}"

    name = canary("name")

    async def _third(session: AsyncSession) -> uuid.UUID:
        student = await persist_student(
            session, contacts=0, birthdate=MINOR_BIRTHDATE, given_names=name
        )
        return student.id

    third = in_uow(world.url, _third)
    stored(
        world.url,
        STARTED + timedelta(days=3),
        students=(third,),
        tutor_user_id=world.tutor.id,
    )
    subject = str(world.subject_ids[0])

    def create(title: str, students: list[uuid.UUID], **values: Any) -> Any:
        response = post(
            client,
            cookie,
            "/api/v1/assignments",
            {
                "subject_id": subject,
                "title": title,
                "instructions": values.pop("instructions", "Leer el capítulo 3"),
                "due_on": "2031-03-12",
                "max_score": "10",
                "student_ids": [str(s) for s in students],
                **values,
            },
        )
        assert code_of(response) == (201, None), response.text
        view = response.json()
        world.assignment_ids.append(view["id"])
        return view

    group = create("Tarea de grupo", [*world.students, third])
    # The group class's homework (default recipients: its participants).
    response = post(
        client,
        cookie,
        "/api/v1/assignments",
        {
            "subject_id": str(world.session_subjects[world.group_session]),
            "title": "Tarea de la clase",
            "instructions": "Repasar",
            "due_on": "2031-03-12",
            "class_session_id": world.group_session,
        },
    )
    assert code_of(response) == (201, None), response.text
    world.assignment_ids.append(response.json()["id"])

    handed_in = canary("third-work")
    response = put(
        client,
        cookie,
        f"/api/v1/assignments/{group['id']}/students/{third}/submission",
        {"text": handed_in},
    )
    assert code_of(response) == (200, None), response.text
    for student in world.students:
        hidden[student] |= {name, handed_in}

    for actor in (world.minor_student, world.adult_student):
        student = actor.student_id
        own = sign_in(client, actor.user.username)
        text = canary("text")
        link = f"https://example.com/{canary('url')}"
        response = put(
            client,
            own,
            f"/api/v1/assignments/{group['id']}/students/{student}/submission",
            {"text": text, "url": link},
        )
        assert code_of(response) == (200, None), response.text
        version = response.json()["submission"]["version"]
        first = canary("feedback-before")
        response = put(
            client,
            cookie,
            f"/api/v1/assignments/{group['id']}/students/{student}/grade",
            {"version": version, "score": "7", "feedback": first},
        )
        assert code_of(response) == (200, None), response.text
        reason = canary("change-reason")
        feedback = canary("feedback")
        response = put(
            client,
            cookie,
            f"/api/v1/assignments/{group['id']}/students/{student}/grade",
            {
                "version": response.json()["version"],
                "score": "8",
                "feedback": feedback,
                "change_reason": reason,
            },
        )
        assert code_of(response) == (200, None), response.text
        hidden[student] |= {first, reason}
        visible[student] |= {text, link, feedback}
        world.shown_feedback[student] = feedback

        individual = canary("individual")
        create(individual, [student])
        visible[student].add(individual)

        deleted = canary("deleted")
        view = create(canary("deleted-title"), [student], instructions=deleted)
        client.cookies.clear()
        response = client.delete(
            f"/api/v1/assignments/{view['id']}",
            params={"version": view["version"]},
            headers=signed_headers(cookie),
        )
        assert response.status_code == 204, response.text
        hidden[student] |= {deleted}

    return Canaries(
        hidden=hidden,
        visible=visible,
        shown_on=["/api/v1/students/{student_id}/assignments"],
    )


def progress_report_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#83: per student (both are the world tutor's current assignments;
    the world's Academic Coordinator reviews), progress reports with
    canaries in the narrative: (i) a draft, (ii) a submitted report, (iii) a
    report sent back for changes and the reason, (iv) a report approved then
    retracted and the retract reason, (v) a deleted draft; and a report
    whose first narrative was sent back with a reason (both hidden) and
    whose second narrative was approved (visible to that family only)."""
    cookie = sign_in(client, world.tutor.username)
    reviewer = sign_in(client, world.staff.username)
    hidden: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}

    def canary(kind: str) -> str:
        return f"CANARY-report-{kind}-{uuid.uuid4().hex}"

    def report(student: uuid.UUID, kind: str) -> tuple[dict[str, Any], set[str]]:
        texts = {
            part: canary(f"{kind}-{part}")
            for part in ("summary", "strengths", "areas", "next")
        }
        response = post(
            client,
            cookie,
            "/api/v1/progress-reports",
            {
                "student_id": str(student),
                "kind": "on_demand",
                "period_start": "2031-02-05",
                "period_end": "2031-03-12",
                "summary": texts["summary"],
                "strengths": texts["strengths"],
                "areas_for_improvement": texts["areas"],
                "next_steps": texts["next"],
            },
        )
        assert code_of(response) == (201, None), response.text
        view = response.json()
        world.report_ids.append(view["id"])
        return view, set(texts.values())

    def act(caller: Any, view: dict[str, Any], action: str, **body: Any) -> Any:
        response = post(
            client,
            caller,
            f"/api/v1/progress-reports/{view['id']}/{action}",
            {"version": view["version"], **body},
        )
        assert code_of(response) == (200, None), response.text
        return response.json()

    for student in world.students:
        _, draft = report(student, "draft")
        hidden[student] |= draft

        submitted_view, submitted = report(student, "submitted")
        act(cookie, submitted_view, "submit")
        hidden[student] |= submitted

        sent_back_view, sent_back = report(student, "sent-back")
        sent_back_view = act(cookie, sent_back_view, "submit")
        reason = canary("changes-reason")
        act(reviewer, sent_back_view, "request-changes", reason=reason)
        hidden[student] |= sent_back | {reason}

        retracted_view, retracted = report(student, "retracted")
        retracted_view = act(cookie, retracted_view, "submit")
        retracted_view = act(reviewer, retracted_view, "approve")
        retract_reason = canary("retract-reason")
        act(reviewer, retracted_view, "retract", reason=retract_reason)
        hidden[student] |= retracted | {retract_reason}

        deleted_view, deleted = report(student, "deleted")
        client.cookies.clear()
        response = client.delete(
            f"/api/v1/progress-reports/{deleted_view['id']}",
            params={"version": deleted_view["version"]},
            headers=signed_headers(cookie),
        )
        assert response.status_code == 204, response.text
        hidden[student] |= deleted

        approved_view, before = report(student, "before")
        approved_view = act(cookie, approved_view, "submit")
        first_reason = canary("first-reason")
        approved_view = act(
            reviewer, approved_view, "request-changes", reason=first_reason
        )
        shown = {
            "summary": canary("approved-summary"),
            "strengths": canary("approved-strengths"),
            "areas_for_improvement": canary("approved-areas"),
            "next_steps": canary("approved-next"),
        }
        response = patch(
            client,
            cookie,
            f"/api/v1/progress-reports/{approved_view['id']}",
            {"version": approved_view["version"], **shown},
        )
        assert code_of(response) == (200, None), response.text
        approved_view = act(cookie, response.json(), "submit")
        act(reviewer, approved_view, "approve")
        hidden[student] |= before | {first_reason}
        visible[student] |= set(shown.values())
        world.shown_reports[student] = {approved_view["id"]: set(shown.values())}

    return Canaries(hidden=hidden, visible=visible, shown_on=[])


def closeout_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#84: per student, new past individual sessions of the world tutor
    closed out with canaries in every summary field: (i) submitted, (ii) sent
    back (and the reason), (iii) approved, then back in review after the
    tutor's correction request (the new text and the tutor's note), (iv) a
    staff correction's reason and an on-behalf submission's reason, (v) an
    internal note; an approved, staff-corrected summary and an on-behalf
    summary approved by an Owner (visible to that family only). Then a group
    session of both students, approved (the positive control)."""
    tutor = sign_in(client, world.tutor.username)
    reviewer = sign_in(client, world.staff.username)
    boss_user = create_user(world.url, roles=(OWNER,), display_name="Dueña Fuga")
    boss = sign_in(client, boss_user.username)
    hidden: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    days = iter(range(20, 0, -1))

    def canary(kind: str) -> str:
        return f"CANARY-closeout-{kind}-{uuid.uuid4().hex}"

    def summary(kind: str) -> dict[str, str]:
        return {
            part: canary(f"{kind}-{part}")
            for part in ("topics", "performance", "homework", "next_steps")
        }

    def closeout_path(session_id: str, action: str = "") -> str:
        return f"/api/v1/class-sessions/{session_id}/closeout{action}"

    def session_of(*students: uuid.UUID, kind: str = "individual") -> str:
        return stored(
            world.url,
            NOW - timedelta(days=next(days)),
            students=students,
            tutor_user_id=world.tutor.id,
            kind=kind,
        )

    def marked(session_id: str, marks: dict[uuid.UUID, str]) -> int:
        client.cookies.clear()
        response = client.put(
            f"/api/v1/class-sessions/{session_id}/attendance",
            json={
                "version": 1,
                "marks": [
                    {"student_id": str(s), "status": m} for s, m in marks.items()
                ],
            },
            headers=signed_headers(tutor),
        )
        assert code_of(response) == (200, None), response.text
        return int(response.json()["version"])

    def submitted(session_id: str, student: uuid.UUID, text: dict[str, str]) -> int:
        version = marked(session_id, {student: "present"})
        response = post(
            client,
            tutor,
            closeout_path(session_id),
            {"version": version, "actual_minutes": 55, "summary": text},
        )
        assert code_of(response) == (201, None), response.text
        return int(response.json()["closeout"]["version"])

    def decided(caller: Any, session_id: str, action: str, **body: Any) -> int:
        response = post(client, caller, closeout_path(session_id, action), body)
        assert code_of(response) == (200, None), response.text
        return int(response.json()["closeout"]["version"])

    for student in world.students:
        world.shown_summaries[student] = {}

        # (i) submitted.
        text = summary("submitted")
        submitted(session_of(student), student, text)
        hidden[student] |= set(text.values())

        # (ii) sent back, with the reason.
        session_id = session_of(student)
        text = summary("sent-back")
        version = submitted(session_id, student, text)
        reason = canary("review-reason")
        decided(
            reviewer, session_id, "/request-changes", version=version, reason=reason
        )
        hidden[student] |= set(text.values()) | {reason}

        # (iii) approved, then the tutor's correction request under review.
        session_id = session_of(student)
        version = submitted(session_id, student, summary("first-approved"))
        version = decided(reviewer, session_id, "/approve", version=version)
        text = summary("correction-request")
        note = canary("tutor-note")
        client.cookies.clear()
        response = client.put(
            closeout_path(session_id),
            json={
                "version": version,
                "actual_minutes": 55,
                "summary": text,
                "reason": note,
            },
            headers=signed_headers(tutor),
        )
        assert code_of(response) == (200, None), response.text
        assert response.json()["closeout"]["status"] == "submitted"
        hidden[student] |= set(text.values()) | {note}

        # (iv) a staff correction's reason: the corrected summary is shown.
        session_id = session_of(student)
        version = submitted(session_id, student, summary("before-correction"))
        version = decided(reviewer, session_id, "/approve", version=version)
        text = summary("corrected")
        correction_reason = canary("correction-reason")
        decided(
            reviewer,
            session_id,
            "/corrections",
            version=version,
            summary=text,
            reason=correction_reason,
        )
        hidden[student].add(correction_reason)
        visible[student] |= set(text.values())
        world.shown_summaries[student][session_id] = set(text.values())

        # (v) an internal note on that session.
        internal = canary("internal-note")
        response = post(
            client,
            tutor,
            f"/api/v1/class-sessions/{session_id}/notes",
            {"student_id": str(student), "body": internal},
        )
        assert code_of(response) == (201, None), response.text
        world.note_ids.append(str(response.json()["id"]))
        hidden[student].add(internal)

        # (iv) an on-behalf reason: approved by an Owner, its summary shown.
        session_id = session_of(student)
        version = marked(session_id, {student: "present"})
        text = summary("on-behalf")
        on_behalf_reason = canary("on-behalf-reason")
        response = post(
            client,
            reviewer,
            closeout_path(session_id, "/on-behalf"),
            {
                "version": version,
                "actual_minutes": 50,
                "summary": text,
                "reason": on_behalf_reason,
            },
        )
        assert code_of(response) == (201, None), response.text
        decided(
            boss,
            session_id,
            "/approve",
            version=int(response.json()["closeout"]["version"]),
        )
        hidden[student].add(on_behalf_reason)
        visible[student] |= set(text.values())
        world.shown_summaries[student][session_id] = set(text.values())

    # The positive control: a group session of both, approved.
    group = session_of(*world.students, kind="group")
    version = marked(group, {world.minor: "present", world.adult: "absent"})
    text = summary("group")
    response = post(
        client,
        tutor,
        closeout_path(group),
        {"version": version, "actual_minutes": 55, "summary": text},
    )
    assert code_of(response) == (201, None), response.text
    decided(
        reviewer,
        group,
        "/approve",
        version=int(response.json()["closeout"]["version"]),
    )
    world.closeout_group = group
    world.closeout_group_summary = set(text.values())
    return Canaries(hidden=hidden, visible=visible, shown_on=[])


def completion_canaries(client: TestClient, world: LeakWorld) -> Canaries:
    """#85: a cohort of both students and a classmate whose name is a canary,
    with a completed course session; per student, evaluations linked to the
    cohort's required assessment: (i) a draft (id and feedback), (ii) a
    voided final one (id and void reason), (iii) the change reason of an
    earlier, amended attempt; the counted latest attempt and the earlier one
    are the student's family's only ((iv) for the other family)."""
    assessment_id = uuid.uuid4()
    classmate_name = f"CANARY-classmate-{uuid.uuid4().hex[:12]}"
    hidden: dict[uuid.UUID, set[str]] = {s: {classmate_name} for s in world.students}
    visible: dict[uuid.UUID, set[str]] = {s: set() for s in world.students}
    subject_id = world.subject_ids[0]

    async def _cohort(session: AsyncSession) -> tuple[str, uuid.UUID]:
        cohort = await persist_course_cohort(
            session,
            subject_id=subject_id,
            assessments=[
                {"id": assessment_id, "title": "Examen", "min_score_percent": 70}
            ],
        )
        classmate = await persist_student(
            session, contacts=0, given_names=classmate_name, family_names="Fuga"
        )
        for student_id in (*world.students, classmate.id):
            session.add(
                CohortEnrollment(
                    cohort_id=cohort.id,
                    student_id=student_id,
                    enrolled_at=NOW - timedelta(days=30),
                    enrolled_by_user_id=world.staff.id,
                )
            )
        await session.flush()
        return str(cohort.id), classmate.id

    world.cohort_id, classmate = in_uow(world.url, _cohort)
    course_session = stored(
        world.url,
        NOW - timedelta(days=25, hours=3),
        state="completed",
        students=(*world.students, classmate),
        tutor_user_id=world.tutor.id,
        kind="course",
        cohort_id=world.cohort_id,
        subject_id=subject_id,
    )

    def evaluation(
        student: uuid.UUID,
        percent: str,
        days_ago: int,
        *,
        status: str = "final",
        feedback: str | None = None,
        reason: str | None = None,
        void_reason: str | None = None,
    ) -> str:
        final = status == "final"
        [row] = run_sql(
            world.url,
            "INSERT INTO evaluations (student_id, subject_id, author_user_id,"
            " template_id, template_version, template_name, kind, evaluated_on,"
            " class_session_id, cohort_id, course_assessment_id,"
            " course_assessment_title, status, feedback, score_total, max_total,"
            " score_percent, finalized_at, amended_at, last_change_reason,"
            " voided_at, voided_by_user_id, void_reason)"
            " VALUES (:st, :sub, :a, :t, 1, 'Fuga', 'ongoing', :on, :session,"
            " :cohort, :assessment, 'Examen', :status, :f, :score, :max, :pct,"
            " :fin, :amended, :reason, :void, :void_by, :void_reason)"
            " RETURNING id",
            {
                "st": student,
                "sub": subject_id,
                "a": world.tutor.id,
                "t": uuid.UUID(world.template_ids[0]),
                "on": (NOW - timedelta(days=days_ago)).date(),
                "session": course_session,
                "cohort": world.cohort_id,
                "assessment": assessment_id,
                "status": status,
                "f": feedback,
                "score": Decimal(percent) / 10 if final else None,
                "max": Decimal(10) if final else None,
                "pct": Decimal(percent) if final else None,
                "fin": NOW - timedelta(days=days_ago) if final else None,
                "amended": NOW if reason else None,
                "reason": reason,
                "void": NOW if void_reason else None,
                "void_by": world.staff.id if void_reason else None,
                "void_reason": void_reason,
            },
        )
        return str(row.id)

    for student, counted in ((world.minor, "73.25"), (world.adult, "81.75")):
        feedback = f"CANARY-completion-draft-{uuid.uuid4().hex}"
        draft = evaluation(student, "99.00", 1, status="draft", feedback=feedback)
        void_reason = f"CANARY-completion-void-{uuid.uuid4().hex}"
        voided = evaluation(student, "99.00", 2, void_reason=void_reason)
        reason = f"CANARY-completion-change-{uuid.uuid4().hex}"
        earlier = evaluation(student, "40.00", 10, reason=reason)
        latest = evaluation(student, counted, 5)
        hidden[student] |= {draft, feedback, voided, void_reason, reason}
        visible[student] |= {earlier, latest}
        world.shown_completion[student] = counted
    return Canaries(hidden=hidden, visible=visible, shown_on=[])


CANARY_SOURCES: list[Callable[[TestClient, LeakWorld], Canaries]] = [
    session_note_canaries,
    evaluation_canaries,
    learning_plan_canaries,
    assignment_canaries,
    progress_report_canaries,
    closeout_canaries,
    completion_canaries,
]


# --- The route map --------------------------------------------------------------------


def _unknown(_: LeakWorld) -> list[str]:
    return [str(uuid.uuid4())]


PATH_PARAMS: dict[str, Callable[[LeakWorld], list[str]]] = {
    # The world's records: both students, every session, every note.
    "student_id": lambda w: [str(s) for s in w.students],
    "class_session_id": lambda w: list(w.sessions),
    "note_id": lambda w: list(w.note_ids),
    "evaluation_id": lambda w: list(w.evaluation_ids),
    "template_id": lambda w: list(w.template_ids),
    "plan_id": lambda w: list(w.plan_ids),
    "milestone_id": lambda w: list(w.milestone_ids),
    "assignment_id": lambda w: list(w.assignment_ids),
    "report_id": lambda w: list(w.report_ids),
    "group_id": lambda w: [str(w.group_id)],
    # #85: the world cohort and an unknown id.
    "cohort_id": lambda w: [w.cohort_id, str(uuid.uuid4())],
    "subject_id": lambda w: [str(s) for s in w.subject_ids],
    "guardian_id": lambda w: [str(w.minor_guardian_id), str(w.adult_guardian_id)],
    "user_id": lambda w: [str(w.tutor.id)] + [str(a.user.id) for a in w.actors],
    # Parameters of routes whose records the world doesn't create: an
    # unknown id still exercises the route's 404 path.
    "family_id": _unknown,
    "request_id": _unknown,
    "change_request_id": _unknown,
    "level_id": _unknown,
    "period_id": _unknown,
    "closure_id": _unknown,
    "classroom_id": _unknown,
    "consent_id": _unknown,
    "payment_id": _unknown,
    # #100: the charge detail (the family is unknown too, so 404).
    "charge_id": _unknown,
    # #98: the receipt PDF route (the family is unknown too, so 404).
    "receipt_id": _unknown,
    # #102: the family package detail (the family is unknown too, so 404;
    # the billing walk is tests/integration/test_billing_family_leak.py).
    "package_id": _unknown,
    # #316: no family-reachable GET route takes it today (the schedule
    # routes are staff-only, PATCH /progress-report-schedules/{id} isn't a
    # GET); mapped so a future GET route is still walked.
    "schedule_id": _unknown,
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
        # #80 evaluations
        "/api/v1/students/{student_id}/academic-history",
        "/api/v1/students/{student_id}/evaluations",
        "/api/v1/evaluations/{evaluation_id}",
        "/api/v1/evaluation-templates",
        "/api/v1/evaluation-templates/{template_id}",
        "/api/v1/class-sessions/{class_session_id}/evaluation-context",
        # #81 learning plans
        "/api/v1/students/{student_id}/learning-plans",
        "/api/v1/learning-plans/{plan_id}",
        # #82 homework assignments
        "/api/v1/assignments",
        "/api/v1/assignments/{assignment_id}",
        "/api/v1/students/{student_id}/assignments",
        "/api/v1/students/{student_id}/assignments/{assignment_id}",
        # #83 progress reports
        "/api/v1/progress-reports",
        "/api/v1/progress-reports/{report_id}",
        "/api/v1/students/{student_id}/progress-reports",
        "/api/v1/students/{student_id}/progress-reports/{report_id}",
        # #84 progress dashboard
        "/api/v1/students/{student_id}/progress",
        "/api/v1/students/{student_id}/session-log",
        # #85 course completion
        "/api/v1/cohorts/{cohort_id}/completion",
    } <= paths
    assert "/api/v1/notifications/stream" not in paths
    # #316: the report schedules are staff-only (the matrix gates them behind
    # progress_report.review), so the walk leaves them out;
    # test_report_schedules_are_staff_only checks the 403.
    assert "/api/v1/students/{student_id}/progress-report-schedules" not in paths


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


def test_evaluation_notifications_carry_ids_only(client, walk):
    """#80 B3: finalizing notified both families (the student's login and
    the guardians with academic access), with ids and the date only: no
    score, percentage, feedback, comment or reason."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(client, cookie, "/api/v1/notifications", limit=100)
        assert code_of(response) == (200, None)
        finalized = [
            item
            for item in response.json()["items"]
            if item["notification_type"] == "academics.evaluation_finalized"
        ]
        assert finalized, actor.label
        for item in finalized:
            assert set(item["params"]) == {
                "student_id",
                "evaluation_id",
                "subject_id",
                "evaluated_on",
            }
            assert item["params"]["student_id"] == str(actor.student_id)


def test_the_active_learning_plans_reach_their_own_family(client, walk):
    """#81 positive control: the active plan (with its edited milestone) and
    the plan discontinued after its activation reach their own family, on
    the list (titles) and the detail (every visible string)."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        shown = walk.world.shown_plans[actor.student_id]
        assert len(shown) == 2
        listed = get(
            client, cookie, f"/api/v1/students/{actor.student_id}/learning-plans"
        )
        assert code_of(listed) == (200, None), (actor.label, listed.text)
        assert {item["id"] for item in listed.json()["items"]} == set(shown)
        for plan_id, expected in shown.items():
            detail = get(client, cookie, f"/api/v1/learning-plans/{plan_id}")
            assert code_of(detail) == (200, None), (actor.label, detail.text)
            assert {c for c in expected if c in detail.text} == expected


def test_learning_plan_notifications_carry_ids_only(client, walk):
    """#81 E6: activating notified both families (the student's login and
    the guardians with academic access), with ids only."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(client, cookie, "/api/v1/notifications", limit=100)
        assert code_of(response) == (200, None)
        activated = [
            item
            for item in response.json()["items"]
            if item["notification_type"] == "academics.learning_plan_activated"
        ]
        assert len(activated) == 2, actor.label
        for item in activated:
            assert set(item["params"]) == {"student_id", "plan_id", "subject_id"}
            assert item["params"]["student_id"] == str(actor.student_id)
            assert item["params"]["plan_id"] in walk.world.shown_plans[actor.student_id]


def test_the_graded_homework_reaches_its_own_family_history(client, walk):
    """#82 G1 positive control: the student's own scored homework grade is an
    ``assignment_grade`` entry of their academic history (with the current
    feedback), and the group homework detail shows the student's own row."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(
            client, cookie, f"/api/v1/students/{actor.student_id}/academic-history"
        )
        assert code_of(response) == (200, None), (actor.label, response.text)
        grades = [
            e for e in response.json()["entries"] if e["kind"] == "assignment_grade"
        ]
        assert [g["feedback"] for g in grades] == [
            walk.world.shown_feedback[actor.student_id]
        ], actor.label
        assert grades[0]["score"] == "8.00"


def test_assignment_notifications_carry_ids_only(client, walk):
    """#82 E8: setting and grading notified both families (the student's
    login and the guardians with academic access), each about their own
    student, with ids (and the deadline's date) only."""
    keys = {
        "academics.assignment_set": {
            "student_id",
            "assignment_id",
            "subject_id",
            "due_on",
        },
        "academics.assignment_graded": {"student_id", "assignment_id", "subject_id"},
    }
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(client, cookie, "/api/v1/notifications", limit=100)
        assert code_of(response) == (200, None)
        items = [
            item
            for item in response.json()["items"]
            if item["notification_type"] in keys
        ]
        assert {item["notification_type"] for item in items} == set(keys), actor.label
        for item in items:
            assert set(item["params"]) == keys[item["notification_type"]]
            assert item["params"]["student_id"] == str(actor.student_id)
            assert item["params"]["assignment_id"] in walk.world.assignment_ids


def test_the_approved_progress_reports_reach_their_own_family(client, walk):
    """#83 positive control: the approved report (its second narrative)
    reaches its own family on the list (by id) and the detail (every
    visible string), and the list holds nothing else."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        shown = walk.world.shown_reports[actor.student_id]
        listed = get(
            client, cookie, f"/api/v1/students/{actor.student_id}/progress-reports"
        )
        assert code_of(listed) == (200, None), (actor.label, listed.text)
        assert {item["id"] for item in listed.json()["items"]} == set(shown)
        for report_id, expected in shown.items():
            detail = get(
                client,
                cookie,
                f"/api/v1/students/{actor.student_id}/progress-reports/{report_id}",
            )
            assert code_of(detail) == (200, None), (actor.label, detail.text)
            assert {c for c in expected if c in detail.text} == expected


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_the_approved_report_facts_copy_no_text(client, walk):
    """#83 I1 (vii): the frozen facts were compiled after every other source
    ran (notes, evaluation feedback and comments, change and void reasons,
    homework text, links and feedback): the only canaries they may name are
    the titles of the student's family-visible learning plans."""
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        [report_id] = walk.world.shown_reports[actor.student_id]
        detail = get(
            client,
            cookie,
            f"/api/v1/students/{actor.student_id}/progress-reports/{report_id}",
        )
        assert code_of(detail) == (200, None), (actor.label, detail.text)
        facts = detail.json()["facts"]
        titles = {plan["title"] for plan in facts["learning_plans"]}
        assert titles, actor.label
        assert all(t.startswith("CANARY-plan-") for t in titles)
        named = {text for text in _strings(facts) if "CANARY-" in text}
        assert named == titles, actor.label
        # The facts saw the other sources' records.
        assert facts["evaluations"]["count"] >= 1
        assert facts["assignments"]["count"] >= 1
        assert facts["attendance"]["sessions"] >= 1


def test_progress_report_notifications_carry_ids_only(client, walk):
    """#83 E7, AM3: approving notified both families (the student's login
    and the guardians with academic access), each about their own student,
    with ids, the kind and the period only; families get nothing about a
    retraction or a request for changes."""
    keys = {
        "student_id",
        "report_id",
        "kind",
        "period_start",
        "period_end",
        "audience",
    }
    for actor in walk.world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(client, cookie, "/api/v1/notifications", limit=100)
        assert code_of(response) == (200, None)
        items = [
            item
            for item in response.json()["items"]
            if item["notification_type"].startswith("academics.progress_report")
        ]
        types = {item["notification_type"] for item in items}
        assert types == {"academics.progress_report_approved"}, actor.label
        # Two approvals per student: the retracted one and the shown one.
        assert len(items) == 2, actor.label
        for item in items:
            assert set(item["params"]) == keys
            assert item["params"]["student_id"] == str(actor.student_id)
            assert item["params"]["audience"] == "family"
            assert item["params"]["report_id"] in walk.world.report_ids


def test_report_schedules_are_staff_only(client, walk):
    """#316 F6 (E4): a schedule of each world student exists (the world's
    Academic Coordinator adds it for the world tutor); every family actor
    gets ``403 permission_denied`` on S1 for either student, and the body
    names neither the tutor nor the schedule. The world tutor too."""
    world = walk.world
    staff = sign_in(client, world.staff.username)
    schedule_ids = []
    for student in world.students:
        added = post(
            client,
            staff,
            f"/api/v1/students/{student}/progress-report-schedules",
            {"author_user_id": str(world.tutor.id), "cadence": "monthly"},
        )
        assert code_of(added) == (201, None), added.text
        schedule_ids.append(added.json()["id"])
    for user in [actor.user for actor in world.actors] + [world.tutor]:
        cookie = sign_in(client, user.username)
        for student in world.students:
            response = get(
                client, cookie, f"/api/v1/students/{student}/progress-report-schedules"
            )
            assert code_of(response) == (403, "permission_denied"), response.text
            for hidden in (*schedule_ids, str(world.tutor.id)):
                assert hidden not in response.text


def test_the_approved_summaries_reach_their_own_family(client, walk):
    """#84 positive control: each family's session log shows its own child's
    approved summaries (and never the other child's, nor any hidden one: the
    walk), the shared group summary, and only its own child's attendance."""
    world = walk.world
    expected_mark = {world.minor: "present", world.adult: "absent"}
    for actor in world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(
            client, cookie, f"/api/v1/students/{actor.student_id}/session-log"
        )
        assert response.status_code == 200, response.text
        items = {item["class_session_id"]: item for item in response.json()["items"]}
        for session_id, texts in world.shown_summaries[actor.student_id].items():
            assert set(items[session_id]["summary"].values()) == texts, actor.label
        group = items[world.closeout_group]
        assert set(group["summary"].values()) == world.closeout_group_summary
        assert group["attendance"] == expected_mark[actor.student_id], actor.label
        assert group["session_kind"] == "group"
        # The progress summary of the same student answers too (no text).
        progress = get(client, cookie, f"/api/v1/students/{actor.student_id}/progress")
        assert progress.status_code == 200, progress.text
        assert progress.json()["attendance"]["sessions"] >= 1


def test_the_completion_rows_reach_their_own_family(client, walk):
    """#85 positive control: each family reads exactly its own student's row
    on the cohort's completion, with the counted (latest) score, and never
    the classmate's (the walk)."""
    world = walk.world
    for actor in world.actors:
        cookie = sign_in(client, actor.user.username)
        response = get(client, cookie, f"/api/v1/cohorts/{world.cohort_id}/completion")
        assert response.status_code == 200, (actor.label, response.text)
        [row] = response.json()["students"]
        assert row["student"]["id"] == str(actor.student_id), actor.label
        [item] = row["assessments"]["items"]
        assert item["score_percent"] == world.shown_completion[actor.student_id]
        assert (item["status"], item["attempts"]) == ("passed", 2), actor.label
