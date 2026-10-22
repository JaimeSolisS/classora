"""The scheduling module's public API (ADR-0002; #47 M4-02, ADR-0005).

Pure rules: the states, kinds, occupying sets and transitions
(:mod:`~app.modules.scheduling.states`), the time bounds
(:mod:`~app.modules.scheduling.times`) and the effective capacity
(:mod:`~app.modules.scheduling.capacity`). Writes
(:mod:`~app.modules.scheduling.sessions`): :func:`flush_class_sessions`
(exclusion violations → :class:`SessionOverlapError`),
:func:`transition_class_session` and the time-zone check
:func:`has_class_sessions`, registered as :data:`TIME_ZONE_DEPENDENCY`
(``scheduling.class_sessions``) with
``administration.service.register_time_zone_dependency`` in ``app/wiring.py``.

One-off individual sessions (#48, implemented in
:mod:`~app.modules.scheduling.booking`): :func:`create_class_session` (the
per-student lock, the zone ``FOR SHARE``, the ``422`` bounds and
references, every ADR-0005 conflict check, then ``offered`` / ``unassigned``)
and :func:`get_class_session`. The conflict codes, severities and pure
rules are in :mod:`~app.modules.scheduling.conflicts` (with each check's data
source); :func:`find_conflicts` runs them for one body. The student lock
(:func:`lock_students`, :mod:`~app.modules.scheduling.locks`) must be taken
first by every writer that adds a participant or moves a session in time.

Overrides of soft conflicts (#49, :mod:`~app.modules.scheduling.overrides`):
:func:`evaluate_override` (pure: a hard conflict or an unlisted soft one
refuses) and :func:`record_session_override` (the append-only row plus the
``scheduling.session_override`` audit event). Every later write path that
runs the conflict checks uses both.

Workload warnings and slot suggestions (#50): the pure rules
:func:`workload_warnings` / :func:`sort_warnings`
(:mod:`~app.modules.scheduling.workload`), the loader
:func:`tutor_workload_sessions` and :func:`session_warnings` (the create's
step, :mod:`~app.modules.scheduling.workload_sessions`); the pure
:func:`candidate_slots` / :func:`slot_conflicts` over a :class:`SlotContext`
(:mod:`~app.modules.scheduling.suggestions`), loaded once by
:func:`load_slot_context`, and the read-only route's :func:`suggest_slots`
(:mod:`~app.modules.scheduling.slot_search`). #51, #58 and #59 reuse them.

The calendar (#57, :mod:`~app.modules.scheduling.calendar_read`):
:func:`list_calendar_sessions` (the range read with its read-time markers,
:mod:`~app.modules.scheduling.markers`) and :func:`list_tutor_options`.

Tutor suggestions (#51, :mod:`~app.modules.scheduling.tutor_suggestions`;
the pure eligibility and workload ranking rule in
:mod:`~app.modules.scheduling.eligibility`): :func:`suggest_tutors` (the one
implementation, for #52 / #55 too), :func:`suggest_for_slot` and
:func:`suggest_replacements` (the two routes' reads).

Recurring series (#53): :func:`create_class_series` and
:func:`get_class_series` (:mod:`~app.modules.scheduling.series`), the pure
weekly rule (:mod:`~app.modules.scheduling.recurrence`), the one
materializer :func:`materialize_series` (create and nightly extend modes,
:mod:`~app.modules.scheduling.materialize`) with its state seam
:func:`occurrence_initial_state`, the shared conflict collector
:func:`collect_conflicts` (:mod:`~app.modules.scheduling.checks`; #48's
create calls it too), the ``scheduling.occurrence_conflict`` approval
handler :data:`OCCURRENCE_CONFLICT_HANDLER` and the time-zone check
:func:`has_class_series` (:data:`SERIES_TIME_ZONE_DEPENDENCY`). The nightly
task lives in :mod:`~app.modules.scheduling.tasks`, imported only by
``app/core/jobs.py``.

Assignment offers (#52, :mod:`~app.modules.scheduling.offers` for the pure
rules, the invariant and the lock order;
:mod:`~app.modules.scheduling.offer_flow` for the writes and reads): the
create path makes the offer of a session created with a tutor;
:func:`offer_class_session` offers an ``unassigned`` session later;
:func:`answer_offer` (accept / decline, the offered tutor only),
:func:`expire_offer` (the periodic ``scheduling:expire_offers`` task,
:mod:`~app.modules.scheduling.tasks`), :func:`withdraw_pending_offers` (the
seam for #55 / #58; session scope only), :func:`list_my_offers` and
:func:`list_session_offers`. Series offers (#233,
:mod:`~app.modules.scheduling.series_offers`): a series created with a tutor
gets one; :func:`offer_class_series` offers a series without a tutor from a
date; :func:`withdraw_series_offer` is the seam for #54 / #55. A declined
or expired offer becomes a ``scheduling.offer_reassignment`` inbox item
(:data:`OFFER_REASSIGNMENT_HANDLER`) and notifies the ``schedule.assign``
holders; ``app.wiring`` registers the handler and the three notification
types (:func:`register_offer_notifications`). Family visibility:
:data:`FAMILY_VISIBLE_STATES` / :func:`is_family_visible` (#67 builds on
them).

Closure changes on existing occurrences (#232,
:mod:`~app.modules.scheduling.closure_changes`): the nightly job runs
:func:`reconcile_series_closures` per series before the extension; the
``scheduling.occurrence_skipped_closure`` notification type is registered
by ``app.wiring`` (:func:`register_closure_notifications`).

The #47 errors are plain exceptions; the create maps them into ``409
scheduling_conflict`` (:class:`SchedulingConflictError`) or ``422``
(:class:`SessionValidationError`). Every function runs in the caller's Unit
of Work and never commits.
"""

from app.modules.scheduling.booking import (
    create_class_session as create_class_session,
)
from app.modules.scheduling.booking import find_conflicts as find_conflicts
from app.modules.scheduling.booking import (
    get_class_session as get_class_session,
)
from app.modules.scheduling.calendar_read import (
    list_calendar_sessions as list_calendar_sessions,
)
from app.modules.scheduling.calendar_read import (
    list_tutor_options as list_tutor_options,
)
from app.modules.scheduling.capacity import (
    GROUP_CAPACITY_EXCEEDED as GROUP_CAPACITY_EXCEEDED,
)
from app.modules.scheduling.capacity import (
    ROOM_CAPACITY_EXCEEDED as ROOM_CAPACITY_EXCEEDED,
)
from app.modules.scheduling.capacity import (
    capacity_violations as capacity_violations,
)
from app.modules.scheduling.capacity import (
    effective_capacity as effective_capacity,
)
from app.modules.scheduling.checks import SlotCandidate as SlotCandidate
from app.modules.scheduling.checks import collect_conflicts as collect_conflicts
from app.modules.scheduling.checks import (
    find_offer_conflicts as find_offer_conflicts,
)
from app.modules.scheduling.closure_changes import (
    OCCURRENCE_SKIPPED_CLOSURE as OCCURRENCE_SKIPPED_CLOSURE,
)
from app.modules.scheduling.closure_changes import (
    reconcile_series_closures as reconcile_series_closures,
)
from app.modules.scheduling.closure_changes import (
    register_closure_notifications as register_closure_notifications,
)
from app.modules.scheduling.conflicts import HARD as HARD
from app.modules.scheduling.conflicts import SOFT as SOFT
from app.modules.scheduling.conflicts import WARNING as WARNING
from app.modules.scheduling.conflicts import Conflict as Conflict
from app.modules.scheduling.conflicts import sort_conflicts as sort_conflicts
from app.modules.scheduling.errors import (
    ClassSessionNotAssignableError as ClassSessionNotAssignableError,
)
from app.modules.scheduling.errors import (
    ClassSessionNotOfferableError as ClassSessionNotOfferableError,
)
from app.modules.scheduling.errors import (
    InvalidSessionTimesError as InvalidSessionTimesError,
)
from app.modules.scheduling.errors import (
    InvalidSessionTransitionError as InvalidSessionTransitionError,
)
from app.modules.scheduling.errors import OfferExpiredError as OfferExpiredError
from app.modules.scheduling.errors import (
    OfferNotPendingError as OfferNotPendingError,
)
from app.modules.scheduling.errors import (
    ReassignmentStillNeededError as ReassignmentStillNeededError,
)
from app.modules.scheduling.errors import (
    SchedulingConflictError as SchedulingConflictError,
)
from app.modules.scheduling.errors import (
    SessionOverlapError as SessionOverlapError,
)
from app.modules.scheduling.errors import (
    SessionValidationError as SessionValidationError,
)
from app.modules.scheduling.locks import lock_students as lock_students
from app.modules.scheduling.materialize import (
    OCCURRENCE_CONFLICT_TYPE as OCCURRENCE_CONFLICT_TYPE,
)
from app.modules.scheduling.materialize import (
    MaterializeCounts as MaterializeCounts,
)
from app.modules.scheduling.materialize import (
    materialize_series as materialize_series,
)
from app.modules.scheduling.materialize import (
    occurrence_initial_state as occurrence_initial_state,
)
from app.modules.scheduling.materialize import (
    revalidate_references as revalidate_references,
)
from app.modules.scheduling.occurrence_approval import (
    OCCURRENCE_CONFLICT_HANDLER as OCCURRENCE_CONFLICT_HANDLER,
)
from app.modules.scheduling.offer_approval import (
    OFFER_REASSIGNMENT_HANDLER as OFFER_REASSIGNMENT_HANDLER,
)
from app.modules.scheduling.offer_flow import REASSIGNMENT_TYPE as REASSIGNMENT_TYPE
from app.modules.scheduling.offer_flow import answer_offer as answer_offer
from app.modules.scheduling.offer_flow import expire_offer as expire_offer
from app.modules.scheduling.offer_flow import list_my_offers as list_my_offers
from app.modules.scheduling.offer_flow import (
    list_session_offers as list_session_offers,
)
from app.modules.scheduling.offer_flow import (
    offer_class_session as offer_class_session,
)
from app.modules.scheduling.offer_flow import (
    withdraw_pending_offers as withdraw_pending_offers,
)
from app.modules.scheduling.offer_flow import (
    withdraw_series_offer as withdraw_series_offer,
)
from app.modules.scheduling.offer_records import (
    register_offer_notifications as register_offer_notifications,
)
from app.modules.scheduling.offers import (
    can_transition_offer as can_transition_offer,
)
from app.modules.scheduling.offers import is_overdue as is_overdue
from app.modules.scheduling.offers import (
    resolve_respond_by as resolve_respond_by,
)
from app.modules.scheduling.overrides import (
    OverrideDecision as OverrideDecision,
)
from app.modules.scheduling.overrides import (
    evaluate_override as evaluate_override,
)
from app.modules.scheduling.overrides import (
    record_session_override as record_session_override,
)
from app.modules.scheduling.series import (
    SERIES_TIME_ZONE_DEPENDENCY as SERIES_TIME_ZONE_DEPENDENCY,
)
from app.modules.scheduling.series import (
    create_class_series as create_class_series,
)
from app.modules.scheduling.series import get_class_series as get_class_series
from app.modules.scheduling.series import has_class_series as has_class_series
from app.modules.scheduling.series_offers import (
    offer_class_series as offer_class_series,
)
from app.modules.scheduling.sessions import (
    TIME_ZONE_DEPENDENCY as TIME_ZONE_DEPENDENCY,
)
from app.modules.scheduling.sessions import (
    flush_class_sessions as flush_class_sessions,
)
from app.modules.scheduling.sessions import (
    has_class_sessions as has_class_sessions,
)
from app.modules.scheduling.sessions import (
    transition_class_session as transition_class_session,
)
from app.modules.scheduling.slot_search import (
    load_slot_context as load_slot_context,
)
from app.modules.scheduling.slot_search import suggest_slots as suggest_slots
from app.modules.scheduling.states import (
    ALLOWED_TRANSITIONS as ALLOWED_TRANSITIONS,
)
from app.modules.scheduling.states import (
    FAMILY_VISIBLE_STATES as FAMILY_VISIBLE_STATES,
)
from app.modules.scheduling.states import FINAL_STATES as FINAL_STATES
from app.modules.scheduling.states import INITIAL_STATES as INITIAL_STATES
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES as ROOM_OCCUPYING_STATES,
)
from app.modules.scheduling.states import (
    STUDENT_OCCUPYING_STATES as STUDENT_OCCUPYING_STATES,
)
from app.modules.scheduling.states import (
    TUTOR_OCCUPYING_STATES as TUTOR_OCCUPYING_STATES,
)
from app.modules.scheduling.states import SessionKind as SessionKind
from app.modules.scheduling.states import SessionState as SessionState
from app.modules.scheduling.states import can_transition as can_transition
from app.modules.scheduling.states import is_family_visible as is_family_visible
from app.modules.scheduling.suggestions import SlotContext as SlotContext
from app.modules.scheduling.suggestions import (
    candidate_slots as candidate_slots,
)
from app.modules.scheduling.suggestions import slot_conflicts as slot_conflicts
from app.modules.scheduling.times import (
    scheduled_minutes as scheduled_minutes,
)
from app.modules.scheduling.times import (
    validate_session_bounds as validate_session_bounds,
)
from app.modules.scheduling.tutor_suggestions import (
    suggest_for_slot as suggest_for_slot,
)
from app.modules.scheduling.tutor_suggestions import (
    suggest_replacements as suggest_replacements,
)
from app.modules.scheduling.tutor_suggestions import (
    suggest_tutors as suggest_tutors,
)
from app.modules.scheduling.workload import BREAK_MAX as BREAK_MAX
from app.modules.scheduling.workload import (
    WorkloadSession as WorkloadSession,
)
from app.modules.scheduling.workload import (
    WorkloadWarning as WorkloadWarning,
)
from app.modules.scheduling.workload import sort_warnings as sort_warnings
from app.modules.scheduling.workload import (
    workload_warnings as workload_warnings,
)
from app.modules.scheduling.workload_sessions import (
    session_warnings as session_warnings,
)
from app.modules.scheduling.workload_sessions import (
    tutor_workload_sessions as tutor_workload_sessions,
)
