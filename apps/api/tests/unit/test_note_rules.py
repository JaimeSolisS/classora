"""Session note rules (#79 A3, C2, D1, E3, E4, E5, I4): table-driven, no
database. Every relation x assignment x visibility for every action, the
state drift test, the body and reason schemas, and the families recipient
rule's pure part (the integration tests cover its queries)."""

import uuid

import pytest
from pydantic import ValidationError

from app.core.authz import Principal
from app.modules.academics import note_rules as rules
from app.modules.academics.models import (
    NOTE_BODY_MAX,
    NOTE_UNPUBLISH_REASON_MAX,
    NOTE_VISIBILITIES,
)
from app.modules.academics.note_rules import NoteRelation, SessionNoteAccess
from app.modules.academics.schemas import (
    FamilyLearningNote,
    SessionNoteCreate,
    SessionNoteEdit,
    SessionNoteUnpublish,
)
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.modules.scheduling.states import SessionState

ME = uuid.uuid4()
OTHER = uuid.uuid4()


def principal(*roles: str, user_id: uuid.UUID = ME) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def test_the_bounds_match_the_model():
    assert rules.BODY_MAX == NOTE_BODY_MAX == 4000
    assert rules.UNPUBLISH_REASON_MAX == NOTE_UNPUBLISH_REASON_MAX == 500
    assert (rules.INTERNAL, rules.FAMILY) == NOTE_VISIBILITIES


def test_the_notable_states_match_the_scheduling_states():
    values = {state.value for state in SessionState}
    assert rules.NOTABLE_STATES <= values
    assert rules.NOTABLE_STATES == {
        SessionState.CONFIRMED.value,
        SessionState.COMPLETED.value,
    }


@pytest.mark.parametrize("state", [state.value for state in SessionState])
def test_only_confirmed_and_completed_sessions_take_notes(state):
    assert rules.is_notable(state) is (state in {"confirmed", "completed"})


# --- E3: the relation -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "author", "assigned", "expected"),
    [
        ((TUTOR,), ME, True, NoteRelation.AUTHOR),
        ((TUTOR,), ME, False, NoteRelation.AUTHOR),
        ((TUTOR,), OTHER, True, NoteRelation.ASSIGNED_TUTOR),
        ((TUTOR,), OTHER, False, NoteRelation.NONE),
        # A tutor who lost the role relates to nothing (404 everywhere).
        ((), ME, True, NoteRelation.NONE),
        ((STUDENT,), ME, False, NoteRelation.NONE),
        ((OWNER,), OTHER, False, NoteRelation.REVIEWER),
        ((ADMINISTRATOR,), OTHER, False, NoteRelation.REVIEWER),
        ((ACADEMIC_COORDINATOR,), OTHER, False, NoteRelation.REVIEWER),
        ((FINANCE_STAFF,), OTHER, False, NoteRelation.NONE),
        ((GUARDIAN,), OTHER, False, NoteRelation.NONE),
        ((STUDENT,), OTHER, False, NoteRelation.NONE),
        # Several roles: the first that applies.
        ((TUTOR, ACADEMIC_COORDINATOR), ME, False, NoteRelation.AUTHOR),
        ((TUTOR, ACADEMIC_COORDINATOR), OTHER, False, NoteRelation.REVIEWER),
        ((TUTOR, FINANCE_STAFF), OTHER, False, NoteRelation.NONE),
    ],
)
def test_the_relation(roles, author, assigned, expected):
    relation = rules.note_relation(
        principal(*roles), author_user_id=author, student_assigned=assigned
    )
    assert relation is expected
    assert rules.can_read(relation) is (expected is not NoteRelation.NONE)


# --- D1: write and state blocks -------------------------------------------------------

READABLE = [r for r in NoteRelation if r is not NoteRelation.NONE]


def _expected_write(
    action: str, relation: NoteRelation, assigned: bool, reviewer: bool
) -> str | None:
    author = relation is NoteRelation.AUTHOR
    if action == "unpublish" and ((author and assigned) or reviewer):
        return None
    if not author:
        return "note_author_only"
    return None if assigned else "note_read_only"


@pytest.mark.parametrize("action", rules.ACTIONS)
@pytest.mark.parametrize("relation", READABLE)
@pytest.mark.parametrize("assigned", [True, False])
@pytest.mark.parametrize("reviewer", [True, False])
def test_the_write_block(action, relation, assigned, reviewer):
    assert rules.write_block(
        action, relation, student_assigned=assigned, reviewer=reviewer
    ) == _expected_write(action, relation, assigned, reviewer)


@pytest.mark.parametrize(
    ("action", "visibility", "expected"),
    [
        ("edit", "internal", None),
        ("edit", "family", "note_published"),
        ("delete", "internal", None),
        ("delete", "family", "note_published"),
        ("publish", "internal", None),
        ("publish", "family", "note_already_published"),
        ("unpublish", "family", None),
        ("unpublish", "internal", "note_not_published"),
    ],
)
def test_the_state_block(action, visibility, expected):
    assert rules.state_block(action, visibility) == expected


@pytest.mark.parametrize("action", rules.ACTIONS)
@pytest.mark.parametrize("relation", list(NoteRelation))
@pytest.mark.parametrize("assigned", [True, False])
@pytest.mark.parametrize("visibility", NOTE_VISIBILITIES)
@pytest.mark.parametrize("reviewer", [True, False])
def test_the_can_flags(action, relation, assigned, visibility, reviewer):
    expected = (
        relation is not NoteRelation.NONE
        and _expected_write(action, relation, assigned, reviewer) is None
        and rules.state_block(action, visibility) is None
    )
    assert (
        rules.can(
            action,
            relation,
            student_assigned=assigned,
            reviewer=reviewer,
            visibility=visibility,
        )
        is expected
    )


def test_the_matrix_rows():
    """The groomed permission matrix, row by row (D1 step 4)."""
    # The current tutor with the student assigned: own notes only.
    for action in rules.ACTIONS:
        assert (
            rules.write_block(
                action, NoteRelation.AUTHOR, student_assigned=True, reviewer=False
            )
            is None
        )
        assert (
            rules.write_block(
                action,
                NoteRelation.ASSIGNED_TUTOR,
                student_assigned=True,
                reviewer=False,
            )
            == "note_author_only"
        )
        # The former author: read-only.
        assert (
            rules.write_block(
                action, NoteRelation.AUTHOR, student_assigned=False, reviewer=False
            )
            == "note_read_only"
        )
    # Reviewers unpublish only.
    for action in ("edit", "publish", "delete"):
        assert (
            rules.write_block(
                action, NoteRelation.REVIEWER, student_assigned=False, reviewer=True
            )
            == "note_author_only"
        )
    assert (
        rules.write_block(
            "unpublish", NoteRelation.REVIEWER, student_assigned=False, reviewer=True
        )
        is None
    )


def test_only_the_author_unpublishes_without_a_reason():
    assert not rules.unpublish_needs_reason(NoteRelation.AUTHOR)
    assert rules.unpublish_needs_reason(NoteRelation.REVIEWER)


# --- C2 / E1: the session routes ------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "user_id", "tutor_user_id", "expected"),
    [
        ((TUTOR,), ME, ME, SessionNoteAccess.TUTOR),
        ((TUTOR,), ME, OTHER, SessionNoteAccess.HIDDEN),
        ((TUTOR,), ME, None, SessionNoteAccess.HIDDEN),
        ((), ME, ME, SessionNoteAccess.HIDDEN),
        ((OWNER,), ME, OTHER, SessionNoteAccess.REVIEWER),
        ((ACADEMIC_COORDINATOR,), ME, OTHER, SessionNoteAccess.REVIEWER),
        ((FINANCE_STAFF,), ME, OTHER, SessionNoteAccess.HIDDEN),
        ((GUARDIAN,), ME, OTHER, SessionNoteAccess.HIDDEN),
        ((TUTOR, ADMINISTRATOR), ME, ME, SessionNoteAccess.TUTOR),
    ],
)
def test_the_session_access(roles, user_id, tutor_user_id, expected):
    assert (
        rules.session_note_access(principal(*roles, user_id=user_id), tutor_user_id)
        is expected
    )


# --- C1 / D1: the bodies --------------------------------------------------------------


def _codes(error: ValidationError) -> list[str]:
    return [item["type"] for item in error.errors()]


def test_the_note_body_is_trimmed_and_bounded():
    student = uuid.uuid4()
    assert SessionNoteCreate(student_id=student, body="  Hola  ").body == "Hola"
    assert len(SessionNoteCreate(student_id=student, body="x" * 4000).body) == 4000
    for body, code in (("", "body_required"), ("   ", "body_required")):
        with pytest.raises(ValidationError) as caught:
            SessionNoteCreate(student_id=student, body=body)
        assert _codes(caught.value) == [code]
    with pytest.raises(ValidationError) as caught:
        SessionNoteEdit(version=1, body="x" * 4001)
    assert _codes(caught.value) == ["too_long"]
    with pytest.raises(ValidationError) as caught:
        SessionNoteCreate.model_validate(
            {"student_id": str(student), "body": "x", "visibility": "family"}
        )
    assert _codes(caught.value) == ["extra_forbidden"]


def test_the_unpublish_reason_is_optional_trimmed_and_bounded():
    assert SessionNoteUnpublish(version=1).reason is None
    assert SessionNoteUnpublish(version=1, reason="   ").reason is None
    assert SessionNoteUnpublish(version=1, reason=" Dato médico ").reason == (
        "Dato médico"
    )
    assert SessionNoteUnpublish(version=1, reason="x" * 500).reason == "x" * 500
    with pytest.raises(ValidationError) as caught:
        SessionNoteUnpublish(version=1, reason="x" * 501)
    assert _codes(caught.value) == ["reason_too_long"]


def test_the_family_note_is_a_closed_allow_list():
    assert set(FamilyLearningNote.model_fields) == {
        "id",
        "class_session_id",
        "session_starts_at",
        "subject_name",
        "author",
        "body",
        "published_at",
    }
