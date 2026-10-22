"""Class sessions: constraints, exclusion semantics, transitions, audit and
the time-zone guard (#47 D1, D2, D4-D8; ADR-0005 D1, D2, D6).

The tests share one seeded database per module (``module_seeded_url``); each
creates its own subject, tutor, room and students, so no test sees another
test's rows. The time-zone guard (D7) uses a database of its own.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import StaleVersionError
from app.modules.administration import service as administration
from app.modules.identity.permissions import OWNER
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.service import (
    ROOM_OCCUPYING_STATES,
    TUTOR_OCCUPYING_STATES,
    InvalidSessionTransitionError,
    SessionOverlapError,
    SessionState,
)
from tests.factories.base import persist
from tests.factories.families import persist_student
from tests.factories.scheduling import (
    ClassSessionFactory,
    add_participant,
    persist_class_group,
    persist_class_session,
    persist_classroom,
    persist_subject,
    persist_tutor_user,
)
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import patch
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

S = SessionState
TABLE = "class_sessions"
T10 = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)
"""10:00 in Mexico City on Monday 2026-03-02."""


def at(hour: int, minute: int = 0) -> datetime:
    return T10.replace(hour=hour, minute=minute)


@dataclass(frozen=True)
class Refs:
    subject: uuid.UUID
    tutor: uuid.UUID
    other_tutor: uuid.UUID
    room: uuid.UUID
    other_room: uuid.UUID
    student: uuid.UUID


@pytest.fixture
def refs(module_seeded_url: str) -> Refs:
    async def _create(session: AsyncSession) -> Refs:
        return Refs(
            subject=(await persist_subject(session)).id,
            tutor=await persist_tutor_user(session),
            other_tutor=await persist_tutor_user(session),
            room=(await persist_classroom(session)).id,
            other_room=(await persist_classroom(session)).id,
            student=(await persist_student(session, contacts=0)).id,
        )

    return in_uow(module_seeded_url, _create)


def columns(refs: Refs, **overrides: Any) -> dict[str, Any]:
    """A valid confirmed 10:00-11:00 row with the test's tutor, then
    ``overrides``; ``scheduled_minutes`` follows the bounds unless given."""
    values: dict[str, Any] = {
        "kind": "individual",
        "state": "confirmed",
        "subject_id": refs.subject,
        "tutor_user_id": refs.tutor,
        "classroom_id": None,
        "starts_at": at(16),
        "ends_at": at(17),
        "actual_minutes": None,
        "cancelled_at": None,
        "cancellation_reason": None,
        **overrides,
    }
    values.setdefault(
        "scheduled_minutes",
        int((values["ends_at"] - values["starts_at"]).total_seconds() // 60),
    )
    return values


def new_group(url: str, refs: Refs) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        return (await persist_class_group(session, subject_id=refs.subject)).id

    return in_uow(url, _make)


def insert_raw(url: str, values: dict[str, Any]) -> Any:
    names = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    sql = f"INSERT INTO {TABLE} ({names}) VALUES ({params}) RETURNING id"  # noqa: S608 (fixed names)
    [row] = run_sql(url, sql, values)
    return row.id


def store(url: str, refs: Refs | None = None, **values: Any) -> str:
    """Insert one session through the ORM and :func:`flush_class_sessions`:
    ``"ok"`` or the overlap code."""

    async def _store(session: AsyncSession) -> None:
        session.add(ClassSession(**values))
        await scheduling.flush_class_sessions(session)

    try:
        in_uow(url, _store)
    except SessionOverlapError as exc:
        return exc.code
    return "ok"


def session_values(refs: Refs, state: S, **overrides: Any) -> dict[str, Any]:
    """:func:`columns` in ``state``: no tutor when unassigned, a reason and a
    time when cancelled (unless overridden)."""
    overrides.setdefault("tutor_user_id", None if state == S.UNASSIGNED else refs.tutor)
    if state == S.CANCELLED:
        overrides.setdefault("cancelled_at", at(1))
        overrides.setdefault("cancellation_reason", "Motivo")
    return columns(refs, state=state.value, **overrides)


# --- D1: constraints ------------------------------------------------------------------

INVALID = [
    ({"kind": "tutoring"}, "ck_class_sessions_kind_valid"),
    ({"state": "draft"}, "ck_class_sessions_state_valid"),
    ({"state": "unassigned"}, "ck_class_sessions_unassigned_has_no_tutor"),
    (
        {"state": "offered", "tutor_user_id": None},
        "ck_class_sessions_assigned_has_tutor",
    ),
    (
        {"state": "confirmed", "tutor_user_id": None},
        "ck_class_sessions_assigned_has_tutor",
    ),
    (
        {"state": "completed", "tutor_user_id": None},
        "ck_class_sessions_assigned_has_tutor",
    ),
    ({"ends_at": at(16, 14)}, "ck_class_sessions_duration_range"),
    ({"ends_at": at(16) + timedelta(minutes=481)}, "ck_class_sessions_duration_range"),
    (
        {
            "starts_at": at(16) + timedelta(seconds=30),
            "ends_at": at(17) + timedelta(seconds=30),
        },
        "ck_class_sessions_whole_minutes",
    ),
    (
        {
            "starts_at": at(16) + timedelta(microseconds=1),
            "ends_at": at(17) + timedelta(microseconds=1),
        },
        "ck_class_sessions_whole_minutes",
    ),
    # An end not after the start also breaks the 15-minute minimum, which
    # PostgreSQL checks first (CHECKs run in name order).
    (
        {"ends_at": at(16), "scheduled_minutes": 0},
        "ck_class_sessions_(duration_range|starts_before_ends)",
    ),
    (
        {"ends_at": at(15), "scheduled_minutes": -60},
        "ck_class_sessions_(duration_range|starts_before_ends)",
    ),
    ({"scheduled_minutes": 61}, "ck_class_sessions_scheduled_minutes_match"),
    ({"scheduled_minutes": 59}, "ck_class_sessions_scheduled_minutes_match"),
    (
        {"state": "cancelled", "cancelled_at": at(1)},
        "ck_class_sessions_cancellation_iff_cancelled",
    ),
    (
        {"state": "cancelled", "cancellation_reason": "Motivo"},
        "ck_class_sessions_cancellation_iff_cancelled",
    ),
    ({"state": "cancelled"}, "ck_class_sessions_cancellation_iff_cancelled"),
    (
        {"cancelled_at": at(1), "cancellation_reason": "Motivo"},
        "ck_class_sessions_cancellation_iff_cancelled",
    ),
    (
        {"state": "cancelled", "cancelled_at": at(1), "cancellation_reason": " x"},
        "ck_class_sessions_cancellation_reason_trimmed",
    ),
    (
        {"state": "cancelled", "cancelled_at": at(1), "cancellation_reason": ""},
        "ck_class_sessions_cancellation_reason_trimmed",
    ),
    ({"actual_minutes": -1}, "ck_class_sessions_actual_minutes_range"),
    ({"actual_minutes": 1441}, "ck_class_sessions_actual_minutes_range"),
]


@pytest.mark.parametrize(("overrides", "constraint"), INVALID)
def test_invalid_rows_are_rejected_in_raw_sql(
    module_seeded_url, refs, overrides, constraint
):
    with pytest.raises(IntegrityError, match=constraint):
        insert_raw(module_seeded_url, columns(refs, **overrides))


@pytest.mark.parametrize(("overrides", "constraint"), INVALID)
def test_invalid_rows_are_rejected_through_the_orm(
    module_seeded_url, refs, overrides, constraint
):
    async def _store(session: AsyncSession) -> None:
        session.add(ClassSession(**columns(refs, **overrides)))
        await scheduling.flush_class_sessions(session)

    with pytest.raises(IntegrityError, match=constraint):
        in_uow(module_seeded_url, _store)


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"state": "unassigned", "tutor_user_id": None},
        {"state": "conflict", "tutor_user_id": None},
        {"state": "skipped_closure"},
        {
            "state": "cancelled",
            "cancelled_at": at(1),
            "cancellation_reason": "Motivo",
        },
        {"ends_at": at(16, 15)},
        {"ends_at": at(16) + timedelta(minutes=480)},
        {"actual_minutes": 0},
        {"actual_minutes": 1440},
        {"kind": "group"},
        {"kind": "course"},
    ],
)
def test_valid_rows_are_stored(module_seeded_url, refs, overrides):
    if overrides.get("kind") == "group":
        # #56 A4: a group session belongs to a group.
        overrides = {**overrides, "group_id": new_group(module_seeded_url, refs)}
    session_id = insert_raw(module_seeded_url, columns(refs, **overrides))

    [row] = run_sql(
        module_seeded_url,
        "SELECT version, scheduled_minutes FROM class_sessions WHERE id = :id",
        {"id": session_id},
    )
    assert row.version == 1


def test_a_duplicate_participant_is_rejected(module_seeded_url, refs):
    session_id = insert_raw(module_seeded_url, columns(refs))
    insert = (
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:session, :student)"
    )
    params = {"session": session_id, "student": refs.student}
    run_sql(module_seeded_url, insert, params)

    with pytest.raises(
        IntegrityError, match="uq_class_session_participants_session_student"
    ):
        run_sql(module_seeded_url, insert, params)


@pytest.mark.parametrize(
    ("table", "attribute", "constraint"),
    [
        ("subjects", "subject", "fk_class_sessions_subject_id_subjects"),
        ("classrooms", "room", "fk_class_sessions_classroom_id_classrooms"),
        ("users", "tutor", "fk_class_sessions_tutor_user_id_users"),
        (
            "students",
            "student",
            "fk_class_session_participants_student_id_students",
        ),
    ],
)
def test_referenced_rows_cannot_be_deleted(
    module_seeded_url, refs, table, attribute, constraint
):
    session_id = insert_raw(module_seeded_url, columns(refs, classroom_id=refs.room))
    run_sql(
        module_seeded_url,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:session, :student)",
        {"session": session_id, "student": refs.student},
    )

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(
            module_seeded_url,
            f"DELETE FROM {table} WHERE id = :id",  # noqa: S608 (fixed names)
            {"id": getattr(refs, attribute)},
        )


def test_a_session_with_participants_cannot_be_deleted(module_seeded_url, refs):
    session_id = insert_raw(module_seeded_url, columns(refs))
    run_sql(
        module_seeded_url,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:session, :student)",
        {"session": session_id, "student": refs.student},
    )

    with pytest.raises(
        IntegrityError,
        match="fk_class_session_participants_class_session_id_class_sessions",
    ):
        run_sql(
            module_seeded_url,
            "DELETE FROM class_sessions WHERE id = :id",
            {"id": session_id},
        )


# --- D2: exclusion semantics ----------------------------------------------------------


@pytest.mark.parametrize("first", sorted(TUTOR_OCCUPYING_STATES))
@pytest.mark.parametrize("second", sorted(TUTOR_OCCUPYING_STATES))
def test_one_tutor_in_two_overlapping_occupying_sessions_is_rejected(
    module_seeded_url, refs, first, second
):
    assert store(module_seeded_url, **session_values(refs, first)) == "ok"

    outcome = store(
        module_seeded_url,
        **session_values(refs, second, starts_at=at(16, 30), ends_at=at(17, 30)),
    )

    assert outcome == "tutor_overlap"


@pytest.mark.parametrize(
    "free", [S.CANCELLED, S.SKIPPED_CLOSURE, S.CONFLICT, S.UNASSIGNED]
)
def test_a_tutor_is_free_beside_a_non_occupying_session(module_seeded_url, refs, free):
    assert store(module_seeded_url, **session_values(refs, free)) == "ok"
    assert store(module_seeded_url, **session_values(refs, S.CONFIRMED)) == "ok"


@pytest.mark.parametrize("first", sorted(ROOM_OCCUPYING_STATES))
def test_one_room_in_two_overlapping_occupying_sessions_is_rejected(
    module_seeded_url, refs, first
):
    held = session_values(refs, first, classroom_id=refs.room)
    assert store(module_seeded_url, **held) == "ok"

    outcome = store(
        module_seeded_url,
        **session_values(
            refs,
            S.UNASSIGNED if first != S.UNASSIGNED else S.CONFIRMED,
            classroom_id=refs.room,
            tutor_user_id=None if first != S.UNASSIGNED else refs.other_tutor,
        ),
    )

    assert outcome == "room_overlap"


def test_an_unassigned_and_a_confirmed_session_in_one_room_overlap(
    module_seeded_url, refs
):
    unassigned = session_values(refs, S.UNASSIGNED, classroom_id=refs.room)
    assert store(module_seeded_url, **unassigned) == "ok"

    confirmed = session_values(
        refs,
        S.CONFIRMED,
        classroom_id=refs.room,
        starts_at=at(16, 59),
        ends_at=at(18),
    )

    assert store(module_seeded_url, **confirmed) == "room_overlap"


@pytest.mark.parametrize("free", [S.CANCELLED, S.SKIPPED_CLOSURE, S.CONFLICT])
def test_a_room_is_free_beside_a_non_occupying_session(module_seeded_url, refs, free):
    values = session_values(refs, free, classroom_id=refs.room, tutor_user_id=None)
    assert store(module_seeded_url, **values) == "ok"
    confirmed = session_values(
        refs, S.CONFIRMED, classroom_id=refs.room, tutor_user_id=refs.other_tutor
    )
    assert store(module_seeded_url, **confirmed) == "ok"


def test_sessions_without_a_room_never_overlap_on_the_room(module_seeded_url, refs):
    for _ in range(2):
        values = session_values(refs, S.UNASSIGNED)
        assert store(module_seeded_url, **values) == "ok"


def test_back_to_back_sessions_share_a_tutor_and_a_room(module_seeded_url, refs):
    first = session_values(refs, S.CONFIRMED, classroom_id=refs.room)
    second = session_values(
        refs, S.CONFIRMED, classroom_id=refs.room, starts_at=at(17), ends_at=at(18)
    )

    assert store(module_seeded_url, **first) == "ok"
    assert store(module_seeded_url, **second) == "ok"


def test_a_one_minute_overlap_is_rejected(module_seeded_url, refs):
    assert store(module_seeded_url, **session_values(refs, S.CONFIRMED)) == "ok"
    tutor = session_values(refs, S.CONFIRMED, starts_at=at(16, 59), ends_at=at(18))
    room_first = session_values(
        refs, S.UNASSIGNED, classroom_id=refs.room, starts_at=at(18), ends_at=at(19)
    )

    assert store(module_seeded_url, **tutor) == "tutor_overlap"
    assert store(module_seeded_url, **room_first) == "ok"
    room = session_values(
        refs,
        S.UNASSIGNED,
        classroom_id=refs.room,
        starts_at=at(17, 0),
        ends_at=at(18, 1),
    )
    assert store(module_seeded_url, **room) == "room_overlap"


def test_different_tutors_and_rooms_at_the_same_time(module_seeded_url, refs):
    one = session_values(refs, S.CONFIRMED, classroom_id=refs.room)
    two = session_values(
        refs, S.CONFIRMED, classroom_id=refs.other_room, tutor_user_id=refs.other_tutor
    )

    assert store(module_seeded_url, **one) == "ok"
    assert store(module_seeded_url, **two) == "ok"


def test_other_integrity_errors_are_re_raised(module_seeded_url, refs):
    async def _store(session: AsyncSession) -> None:
        session.add(ClassSession(**columns(refs, kind="x")))
        await scheduling.flush_class_sessions(session)

    with pytest.raises(IntegrityError, match="ck_class_sessions_kind_valid"):
        in_uow(module_seeded_url, _store)


# --- D4: drift guard ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("constraint", "column", "states"),
    [
        ("ex_class_sessions_tutor_overlap", "tutor_user_id", TUTOR_OCCUPYING_STATES),
        ("ex_class_sessions_room_overlap", "classroom_id", ROOM_OCCUPYING_STATES),
    ],
)
def test_the_exclusion_predicates_match_the_occupying_sets(
    module_seeded_url, constraint, column, states
):
    [row] = run_sql(
        module_seeded_url,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint"
        " WHERE conrelid = 'class_sessions'::regclass AND conname = :name"
        " AND contype = 'x'",
        {"name": constraint},
    )
    definition = row.definition

    assert definition.startswith(f"EXCLUDE USING gist ({column} WITH =, tstzrange(")
    assert "'[)'::text) WITH &&)" in definition
    predicate = definition.split(" WHERE ", 1)[1]
    assert f"{column} IS NOT NULL" in predicate
    assert set(re.findall(r"'([a-z_]+)'::character varying", predicate)) == {
        state.value for state in states
    }


# --- D5: transitions ------------------------------------------------------------------


def create(url: str, **overrides: Any) -> uuid.UUID:
    async def _create(session: AsyncSession) -> uuid.UUID:
        return (await persist_class_session(session, **overrides)).id

    return in_uow(url, _create)


def transition(url: str, session_id: uuid.UUID, **kwargs: Any) -> ClassSession:
    async def _move(session: AsyncSession) -> ClassSession:
        return await scheduling.transition_class_session(session, session_id, **kwargs)

    return in_uow(url, _move)


def row_of(url: str, session_id: uuid.UUID) -> Any:
    [row] = run_sql(
        url,
        "SELECT state, tutor_user_id, classroom_id, cancelled_at,"
        " cancellation_reason, version FROM class_sessions WHERE id = :id",
        {"id": session_id},
    )
    return row


def test_an_offer_is_confirmed(module_seeded_url, refs):
    session_id = create(module_seeded_url, subject_id=refs.subject, state="unassigned")

    offered = transition(
        module_seeded_url,
        session_id,
        to_state=S.OFFERED,
        expected_version=1,
        tutor_user_id=refs.tutor,
    )
    confirmed = transition(
        module_seeded_url, session_id, to_state="confirmed", expected_version=2
    )

    assert (offered.state, offered.tutor_user_id) == ("offered", refs.tutor)
    assert confirmed.version == 3
    row = row_of(module_seeded_url, session_id)
    assert (row.state, row.tutor_user_id, row.version) == ("confirmed", refs.tutor, 3)


def test_a_declined_offer_frees_the_tutor_and_keeps_the_room(module_seeded_url, refs):
    held = dict(starts_at=at(16), ends_at=at(17), classroom_id=refs.room)
    session_id = create(
        module_seeded_url,
        subject_id=refs.subject,
        state="offered",
        tutor_user_id=refs.tutor,
        **held,
    )

    transition(module_seeded_url, session_id, to_state="unassigned", expected_version=1)

    row = row_of(module_seeded_url, session_id)
    assert (row.state, row.tutor_user_id, row.classroom_id) == (
        "unassigned",
        None,
        refs.room,
    )
    # The tutor's slot is free again; the room is still held.
    assert store(module_seeded_url, **session_values(refs, S.OFFERED)) == "ok"
    other = session_values(
        refs, S.CONFIRMED, classroom_id=refs.room, tutor_user_id=refs.other_tutor
    )
    assert store(module_seeded_url, **other) == "room_overlap"


def test_a_cancellation_records_the_reason_and_frees_tutor_and_room(
    module_seeded_url, refs, clock
):
    clock.set(datetime(2026, 2, 20, 12, 0, tzinfo=UTC))
    session_id = create(
        module_seeded_url,
        subject_id=refs.subject,
        tutor_user_id=refs.tutor,
        classroom_id=refs.room,
        starts_at=at(16),
        ends_at=at(17),
    )

    cancelled = transition(
        module_seeded_url,
        session_id,
        to_state="cancelled",
        expected_version=1,
        reason="  El tutor está enfermo ",
    )

    assert cancelled.cancelled_at == datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
    row = row_of(module_seeded_url, session_id)
    assert row.state == "cancelled"
    assert row.cancellation_reason == "El tutor está enfermo"
    assert row.tutor_user_id == refs.tutor
    other = session_values(refs, S.CONFIRMED, classroom_id=refs.room)
    assert store(module_seeded_url, **other) == "ok"


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 1001])
def test_a_cancellation_needs_a_reason(module_seeded_url, refs, reason):
    session_id = create(module_seeded_url, subject_id=refs.subject)

    with pytest.raises(ValueError, match="reason"):
        transition(
            module_seeded_url,
            session_id,
            to_state="cancelled",
            expected_version=1,
            reason=reason,
        )
    assert row_of(module_seeded_url, session_id).state == "confirmed"


def test_an_offer_needs_a_tutor(module_seeded_url, refs):
    session_id = create(module_seeded_url, subject_id=refs.subject, state="unassigned")

    with pytest.raises(ValueError, match="tutor_user_id"):
        transition(
            module_seeded_url, session_id, to_state="offered", expected_version=1
        )


@pytest.mark.parametrize("final", ["completed", "cancelled"])
@pytest.mark.parametrize("target", list(S))
def test_final_states_have_no_exits(module_seeded_url, refs, final, target):
    session_id = create(module_seeded_url, subject_id=refs.subject, state=final)

    with pytest.raises(InvalidSessionTransitionError) as caught:
        transition(
            module_seeded_url,
            session_id,
            to_state=target,
            expected_version=1,
            tutor_user_id=refs.tutor if target == S.OFFERED else None,
            reason="Motivo" if target == S.CANCELLED else None,
        )

    assert (caught.value.from_state, caught.value.to_state) == (final, target.value)
    row = row_of(module_seeded_url, session_id)
    assert (row.state, row.version) == (final, 1)


def test_a_stale_version_is_refused(module_seeded_url, refs):
    session_id = create(module_seeded_url, subject_id=refs.subject)

    with pytest.raises(StaleVersionError):
        transition(
            module_seeded_url, session_id, to_state="completed", expected_version=2
        )
    assert row_of(module_seeded_url, session_id).state == "confirmed"


def test_a_missing_session_is_not_found(module_seeded_url):
    with pytest.raises(RecordNotFoundError):
        transition(
            module_seeded_url, uuid.uuid7(), to_state="completed", expected_version=1
        )


def test_a_conflict_cannot_be_confirmed_into_a_taken_slot(module_seeded_url, refs):
    assert store(module_seeded_url, **session_values(refs, S.CONFIRMED)) == "ok"
    session_id = create(
        module_seeded_url,
        subject_id=refs.subject,
        state="conflict",
        tutor_user_id=refs.tutor,
        starts_at=at(16, 30),
        ends_at=at(17, 30),
    )

    with pytest.raises(SessionOverlapError) as caught:
        transition(
            module_seeded_url, session_id, to_state="confirmed", expected_version=1
        )

    assert caught.value.code == "tutor_overlap"
    row = row_of(module_seeded_url, session_id)
    assert (row.state, row.version) == ("conflict", 1)


def test_a_restored_skipped_session_occupies_again(module_seeded_url, refs):
    session_id = create(
        module_seeded_url,
        subject_id=refs.subject,
        state="skipped_closure",
        tutor_user_id=refs.tutor,
        classroom_id=refs.room,
        starts_at=at(16),
        ends_at=at(17),
    )
    assert store(module_seeded_url, **session_values(refs, S.CONFIRMED)) == "ok"
    run_sql(
        module_seeded_url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
        " cancellation_reason = 'x' WHERE tutor_user_id = :tutor AND id <> :id",
        {"tutor": refs.tutor, "id": session_id},
    )

    transition(module_seeded_url, session_id, to_state="confirmed", expected_version=1)

    assert row_of(module_seeded_url, session_id).state == "confirmed"
    assert store(module_seeded_url, **session_values(refs, S.CONFIRMED)) == (
        "tutor_overlap"
    )
    other = session_values(
        refs, S.UNASSIGNED, classroom_id=refs.room, tutor_user_id=None
    )
    assert store(module_seeded_url, **other) == "room_overlap"


# --- D6: audit ------------------------------------------------------------------------


def test_creates_transitions_and_participant_deletes_are_audited(
    module_seeded_url, refs
):
    async def _create(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        class_session = await persist(
            session,
            ClassSessionFactory,
            subject_id=refs.subject,
            tutor_user_id=refs.tutor,
            starts_at=at(16),
            ends_at=at(17),
        )
        participant = await add_participant(session, class_session, refs.student)
        return class_session.id, participant.id

    session_id, participant_id = in_uow(module_seeded_url, _create)
    transition(module_seeded_url, session_id, to_state="completed", expected_version=1)

    async def _remove(session: AsyncSession) -> None:
        participant = await session.get(ClassSessionParticipant, participant_id)
        await session.delete(participant)

    in_uow(module_seeded_url, _remove)

    recorded = events(module_seeded_url, record_type=TABLE, record_id=session_id)
    assert [e.action for e in recorded] == ["create", "update"]
    assert recorded[0].changes is not None
    assert recorded[0].changes["state"] == {"old": None, "new": "confirmed"}
    assert recorded[0].changes["scheduled_minutes"] == {"old": None, "new": 60}
    # id, created_at, updated_at and version are always left out (#22).
    assert recorded[1].changes == {"state": {"old": "confirmed", "new": "completed"}}
    assert row_of(module_seeded_url, session_id).version == 2
    participant_events = events(
        module_seeded_url,
        record_type="class_session_participants",
        record_id=participant_id,
    )
    assert [e.action for e in participant_events] == ["create", "delete"]
    assert participant_events[0].changes == {
        "class_session_id": {"old": None, "new": str(session_id)},
        "student_id": {"old": None, "new": str(refs.student)},
    }


# --- D7: the time-zone guard ----------------------------------------------------------

ZONE_CHECK = "scheduling.class_sessions"


def test_the_class_sessions_check_is_registered(module_seeded_url):
    assert (
        administration.time_zone_dependencies()[ZONE_CHECK]
        is scheduling.has_class_sessions
    )


def has_sessions(url: str) -> bool:
    return in_uow(url, scheduling.has_class_sessions)


def test_any_class_session_blocks_a_zone_change(seeded_url):
    owner = create_user(seeded_url, roles=(OWNER,))
    with auth_client(seeded_url) as client:
        cookie = sign_in(client, owner.username)

        def change(version: int, zone: str) -> Any:
            reauthenticate(client, cookie)
            return patch(
                client,
                cookie,
                "/api/v1/business-settings",
                {"version": version, "time_zone": zone},
            )

        assert has_sessions(seeded_url) is False
        allowed = change(1, "America/Cancun")
        assert allowed.status_code == 200, allowed.text

        create(seeded_url, state="cancelled")
        assert has_sessions(seeded_url) is True
        blocked = change(2, "America/Tijuana")

        assert code_of(blocked) == (409, "tz_change_requires_migration")
    [row] = run_sql(seeded_url, "SELECT time_zone, version FROM business_settings")
    assert (row.time_zone, row.version) == ("America/Cancun", 2)


# --- D8: the factory ------------------------------------------------------------------


def test_the_factory_default_is_a_confirmed_individual_hour(module_seeded_url):
    async def _create(session: AsyncSession) -> tuple[Any, ...]:
        made = await persist_class_session(session)
        return (
            made.kind,
            made.state,
            made.ends_at - made.starts_at,
            made.scheduled_minutes,
            made.starts_at.second,
            made.tutor_user_id is not None,
            made.classroom_id,
            made.starts_at.year,
        )

    assert in_uow(module_seeded_url, _create) == (
        "individual",
        "confirmed",
        timedelta(minutes=60),
        60,
        0,
        True,
        None,
        2026,
    )
