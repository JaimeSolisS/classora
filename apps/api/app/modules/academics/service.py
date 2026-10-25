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

Every function runs in the caller's Unit of Work and never commits.
"""

from app.modules.academics.closeout_rules import (
    ACTUAL_MINUTES_RANGE as ACTUAL_MINUTES_RANGE,
)
from app.modules.academics.closeout_rules import (
    ALREADY_SUBMITTED as ALREADY_SUBMITTED,
)
from app.modules.academics.closeout_rules import NOT_CLOSABLE as NOT_CLOSABLE
from app.modules.academics.closeout_rules import NOT_STARTED as NOT_STARTED
from app.modules.academics.closeout_rules import (
    SUMMARY_REQUIRED as SUMMARY_REQUIRED,
)
from app.modules.academics.closeout_rules import (
    TEACHING_HOURS_APPROVE as TEACHING_HOURS_APPROVE,
)
from app.modules.academics.closeout_rules import TUTOR_ONLY as TUTOR_ONLY
from app.modules.academics.closeout_rules import CloseoutAccess as CloseoutAccess
from app.modules.academics.closeout_rules import CloseoutBlock as CloseoutBlock
from app.modules.academics.closeout_rules import (
    all_marked_absent as all_marked_absent,
)
from app.modules.academics.closeout_rules import closeout_access as closeout_access
from app.modules.academics.closeout_rules import closeout_block as closeout_block
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
    CloseoutReviewUnavailableError as CloseoutReviewUnavailableError,
)
from app.modules.academics.errors import (
    CloseoutSummaryInvalidError as CloseoutSummaryInvalidError,
)
from app.modules.academics.errors import (
    CloseoutTutorOnlyError as CloseoutTutorOnlyError,
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
