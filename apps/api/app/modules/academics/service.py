"""The academics module's public API (ADR-0002; #77 M5-10).

Session closeouts (:mod:`~app.modules.academics.closeouts`, the pure rules
in :mod:`~app.modules.academics.closeout_rules`): the ``session_closeouts``
row (:class:`SessionCloseout`), :func:`create_session_closeout`,
:func:`closeout_for_session`, :func:`closeouts_by_id`,
:func:`closeout_session_keys` (joined by ``scheduling`` into the inbox
summaries), :func:`summary_of`; the rules :func:`closeout_block`,
:func:`summary_errors`, :func:`all_marked_absent`, the record policy
:func:`closeout_access` and :func:`sees_summary`; and the errors.
``app.workflows.session_closeout`` composes them with
``scheduling.service.complete_class_session`` and
``administration.service.create_approval_request``.

Closeout review (#78, :mod:`~app.modules.academics.closeout_review`): the
closeout lock :func:`lock_closeout`, the immutable approval revisions
(:func:`add_approval`, :func:`current_approval`, :func:`closeout_history`),
the staff corrections (:func:`add_correction`), the E6 attendance unlock
predicate :func:`closeout_awaits_tutor` (registered by ``app.wiring``) and
the pure rules :func:`review_block`, :func:`is_self_review`,
:func:`corrected_fields`, :func:`attendance_changed`;
``app.workflows.session_closeout_review`` composes them.

Closeout on behalf of a tutor (#301): the rule :func:`on_behalf_block`, the
widened :func:`is_self_review`, the bodies :class:`CloseoutOnBehalfSubmit`
and :class:`CloseoutTakeOver` and their errors;
``app.workflows.closeout_on_behalf`` composes them.

Session notes (#79, :mod:`~app.modules.academics.notes`, the pure rules in
:mod:`~app.modules.academics.note_rules`): the ``session_notes`` row
(:class:`SessionNote`), :func:`create_note`, :func:`note_by_id`,
:func:`get_note_for_update`, :func:`update_note_body`,
:func:`publish_note`, :func:`unpublish_note`, :func:`soft_delete_note` and
the lists :func:`notes_for_session`, :func:`notes_for_student` and
:func:`family_notes_for_student` (the only family read: shared, live
notes); the rules module itself (:data:`note_rules`) and the errors.
``app.workflows.session_notes`` composes them with ``scheduling``,
``families`` and ``administration``.

Every function runs in the caller's Unit of Work and never commits.
"""

from app.modules.academics import note_rules as note_rules
from app.modules.academics.closeout_review import (
    ApprovalSnapshot as ApprovalSnapshot,
)
from app.modules.academics.closeout_review import CloseoutHistory as CloseoutHistory
from app.modules.academics.closeout_review import add_approval as add_approval
from app.modules.academics.closeout_review import add_correction as add_correction
from app.modules.academics.closeout_review import (
    closeout_awaits_tutor as closeout_awaits_tutor,
)
from app.modules.academics.closeout_review import (
    closeout_history as closeout_history,
)
from app.modules.academics.closeout_review import (
    current_approval as current_approval,
)
from app.modules.academics.closeout_review import lock_closeout as lock_closeout
from app.modules.academics.closeout_rules import (
    ACTUAL_MINUTES_RANGE as ACTUAL_MINUTES_RANGE,
)
from app.modules.academics.closeout_rules import (
    ALREADY_SUBMITTED as ALREADY_SUBMITTED,
)
from app.modules.academics.closeout_rules import APPROVED as APPROVED
from app.modules.academics.closeout_rules import (
    CHANGES_REQUESTED as CHANGES_REQUESTED,
)
from app.modules.academics.closeout_rules import NOT_CLOSABLE as NOT_CLOSABLE
from app.modules.academics.closeout_rules import NOT_ENDED as NOT_ENDED
from app.modules.academics.closeout_rules import NOT_STARTED as NOT_STARTED
from app.modules.academics.closeout_rules import NOT_SUBMITTED as NOT_SUBMITTED
from app.modules.academics.closeout_rules import (
    ON_BEHALF_OWN_SESSION as ON_BEHALF_OWN_SESSION,
)
from app.modules.academics.closeout_rules import SUBMITTED as SUBMITTED
from app.modules.academics.closeout_rules import (
    SUMMARY_REQUIRED as SUMMARY_REQUIRED,
)
from app.modules.academics.closeout_rules import (
    TEACHING_HOURS_APPROVE as TEACHING_HOURS_APPROVE,
)
from app.modules.academics.closeout_rules import TUTOR_ONLY as TUTOR_ONLY
from app.modules.academics.closeout_rules import UNDER_REVIEW as UNDER_REVIEW
from app.modules.academics.closeout_rules import WITH_TUTOR as WITH_TUTOR
from app.modules.academics.closeout_rules import CloseoutAccess as CloseoutAccess
from app.modules.academics.closeout_rules import CloseoutBlock as CloseoutBlock
from app.modules.academics.closeout_rules import OnBehalfBlock as OnBehalfBlock
from app.modules.academics.closeout_rules import (
    all_marked_absent as all_marked_absent,
)
from app.modules.academics.closeout_rules import (
    attendance_changed as attendance_changed,
)
from app.modules.academics.closeout_rules import closeout_access as closeout_access
from app.modules.academics.closeout_rules import closeout_block as closeout_block
from app.modules.academics.closeout_rules import (
    corrected_fields as corrected_fields,
)
from app.modules.academics.closeout_rules import (
    is_current_tutor as is_current_tutor,
)
from app.modules.academics.closeout_rules import is_self_review as is_self_review
from app.modules.academics.closeout_rules import on_behalf_block as on_behalf_block
from app.modules.academics.closeout_rules import review_block as review_block
from app.modules.academics.closeout_rules import sees_summary as sees_summary
from app.modules.academics.closeout_rules import summary_errors as summary_errors
from app.modules.academics.closeouts import (
    closeout_for_session as closeout_for_session,
)
from app.modules.academics.closeouts import (
    closeout_session_keys as closeout_session_keys,
)
from app.modules.academics.closeouts import closeouts_by_id as closeouts_by_id
from app.modules.academics.closeouts import (
    create_session_closeout as create_session_closeout,
)
from app.modules.academics.closeouts import summary_of as summary_of
from app.modules.academics.errors import (
    CloseoutAlreadySubmittedError as CloseoutAlreadySubmittedError,
)
from app.modules.academics.errors import (
    CloseoutNotSubmittedError as CloseoutNotSubmittedError,
)
from app.modules.academics.errors import (
    CloseoutNotWithTutorError as CloseoutNotWithTutorError,
)
from app.modules.academics.errors import (
    CloseoutOnBehalfOwnSessionError as CloseoutOnBehalfOwnSessionError,
)
from app.modules.academics.errors import (
    CloseoutReviewRouteRequiredError as CloseoutReviewRouteRequiredError,
)
from app.modules.academics.errors import (
    CloseoutSelfReviewError as CloseoutSelfReviewError,
)
from app.modules.academics.errors import (
    CloseoutSubmittedOnBehalfError as CloseoutSubmittedOnBehalfError,
)
from app.modules.academics.errors import (
    CloseoutSummaryInvalidError as CloseoutSummaryInvalidError,
)
from app.modules.academics.errors import (
    CloseoutTutorOnlyError as CloseoutTutorOnlyError,
)
from app.modules.academics.errors import (
    CloseoutUnchangedError as CloseoutUnchangedError,
)
from app.modules.academics.errors import (
    CloseoutUnderReviewError as CloseoutUnderReviewError,
)
from app.modules.academics.errors import (
    CloseoutWithTutorError as CloseoutWithTutorError,
)
from app.modules.academics.errors import (
    NoteAlreadyPublishedError as NoteAlreadyPublishedError,
)
from app.modules.academics.errors import (
    NoteAuthorOnlyError as NoteAuthorOnlyError,
)
from app.modules.academics.errors import NoteFieldError as NoteFieldError
from app.modules.academics.errors import (
    NoteNotPublishedError as NoteNotPublishedError,
)
from app.modules.academics.errors import (
    NotePublishedError as NotePublishedError,
)
from app.modules.academics.errors import (
    NoteReadOnlyError as NoteReadOnlyError,
)
from app.modules.academics.errors import (
    NoteTutorOnlyError as NoteTutorOnlyError,
)
from app.modules.academics.errors import (
    SessionNotClosableError as SessionNotClosableError,
)
from app.modules.academics.errors import (
    SessionNotEndedError as SessionNotEndedError,
)
from app.modules.academics.errors import (
    SessionNotNotableError as SessionNotNotableError,
)
from app.modules.academics.errors import (
    SessionNotStartedError as SessionNotStartedError,
)
from app.modules.academics.models import (
    CLOSEOUT_SUBMITTED as CLOSEOUT_SUBMITTED,
)
from app.modules.academics.models import SessionCloseout as SessionCloseout
from app.modules.academics.models import (
    SessionCloseoutApproval as SessionCloseoutApproval,
)
from app.modules.academics.models import (
    SessionCloseoutCorrection as SessionCloseoutCorrection,
)
from app.modules.academics.models import SessionNote as SessionNote
from app.modules.academics.notes import NotePage as NotePage
from app.modules.academics.notes import NoteRow as NoteRow
from app.modules.academics.notes import create_note as create_note
from app.modules.academics.notes import (
    family_notes_for_student as family_notes_for_student,
)
from app.modules.academics.notes import (
    get_note_for_update as get_note_for_update,
)
from app.modules.academics.notes import note_by_id as note_by_id
from app.modules.academics.notes import (
    notes_for_session as notes_for_session,
)
from app.modules.academics.notes import (
    notes_for_student as notes_for_student,
)
from app.modules.academics.notes import publish_note as publish_note
from app.modules.academics.notes import (
    soft_delete_note as soft_delete_note,
)
from app.modules.academics.notes import (
    unpublish_note as unpublish_note,
)
from app.modules.academics.notes import (
    update_note_body as update_note_body,
)
from app.modules.academics.schemas import CloseoutApprove as CloseoutApprove
from app.modules.academics.schemas import CloseoutCorrection as CloseoutCorrection
from app.modules.academics.schemas import (
    CloseoutOnBehalfSubmit as CloseoutOnBehalfSubmit,
)
from app.modules.academics.schemas import (
    CloseoutRequestChanges as CloseoutRequestChanges,
)
from app.modules.academics.schemas import CloseoutResubmit as CloseoutResubmit
from app.modules.academics.schemas import CloseoutTakeOver as CloseoutTakeOver
