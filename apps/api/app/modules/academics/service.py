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

Every function runs in the caller's Unit of Work and never commits.
"""

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
from app.modules.academics.closeout_rules import NOT_STARTED as NOT_STARTED
from app.modules.academics.closeout_rules import NOT_SUBMITTED as NOT_SUBMITTED
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
    CloseoutReviewRouteRequiredError as CloseoutReviewRouteRequiredError,
)
from app.modules.academics.errors import (
    CloseoutSelfReviewError as CloseoutSelfReviewError,
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
    SessionNotClosableError as SessionNotClosableError,
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
from app.modules.academics.schemas import CloseoutApprove as CloseoutApprove
from app.modules.academics.schemas import CloseoutCorrection as CloseoutCorrection
from app.modules.academics.schemas import (
    CloseoutRequestChanges as CloseoutRequestChanges,
)
from app.modules.academics.schemas import CloseoutResubmit as CloseoutResubmit
