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
runs the conflict checks uses both. Series writes (#236) use
:func:`evaluate_series_override` and :func:`record_series_overrides`
(occurrence and code pairs; #56 reuses them), and
:func:`override_conflict_occurrence` overrides one ``conflict`` occurrence
(:mod:`~app.modules.scheduling.conflict_override`).

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
:data:`FAMILY_VISIBLE_STATES` / :func:`is_family_visible`, and
:func:`is_tutor_visible` (#67).

Role-scoped schedules (#67, :mod:`~app.modules.scheduling.role_schedules`):
:func:`tutor_schedule`, :func:`family_schedule` and :func:`family_students`.
The tutor assignment source of the ``families`` student record policy
(:mod:`~app.modules.scheduling.tutor_assignments`):
:func:`student_ids_for_tutor` and :data:`TUTOR_ASSIGNMENT_SOURCE`, which
``app.wiring`` registers; :func:`subject_ids_for_tutor_student` (#81: the
subjects in which a tutor currently teaches one student, for the learning
plan subject rule in ``app.workflows.learning_plans``);
:func:`current_tutor_subjects_for_student` (#316 B1: a student's current
tutors with those subjects, for the report schedules).

Attendance (#76, :mod:`~app.modules.scheduling.attendance`; the pure
rules in :mod:`~app.modules.scheduling.attendance_rules`):
:func:`get_session_attendance`, :func:`record_attendance` (the tutor's
marks; #77 closeout reuses it), :func:`correct_attendance` (staff, with a
reason) and :func:`attendance_statuses` (the statuses of sessions, for #77,
#84 and #85), :func:`student_attendance_summary` (one student's counts over
a local period, #83 progress report facts), :func:`cohort_participation`
(#85 course completion: each student's held, attended, absent, awaiting and
remaining sessions of a cohort);
the progress dashboard reads (#84, :mod:`~app.modules.scheduling.student_progress`
over the same in-scope sessions): :func:`student_attendance_by_month`,
:func:`student_session_log` and :func:`tutor_taught_student` (the former-tutor
rule);
:func:`register_completed_attendance_unlock` (#78 E6: the
predicate that lets the tutor mark a ``completed`` session again while its
closeout is ``changes_requested``; ``app.wiring`` registers academics').

Session closeout (#77, :mod:`~app.modules.scheduling.closeout`): the read
bundle :func:`closeout_session_context`, :func:`lock_closeout_session`,
:func:`complete_class_session` (``confirmed → completed`` with
``actual_minutes`` once every participant is marked; ``409
closeout_attendance_incomplete`` otherwise) and the inbox summaries
:func:`closeout_session_summaries`, composed with ``academics`` by
``app.workflows.session_closeout``; #78 adds :func:`closeout_attendance`
and :func:`set_actual_minutes` (``app.workflows.session_closeout_review``);
#301 adds the overdue list :func:`sessions_awaiting_closeout`
(``app.workflows.closeout_on_behalf``).

Session notes (#79, :mod:`~app.modules.scheduling.note_context`): the
session facts :func:`note_session` (``FOR SHARE`` on a create),
:func:`is_participant` and the unexecuted :func:`note_session_columns`
(with the #67 family-visible condition for the family list), composed
with ``academics`` by ``app.workflows.session_notes``; the family half of
the #67 student rule, :func:`ensure_family_student_access` (no staff
branch), guards the family note route.

Evaluations (#80, :mod:`~app.modules.scheduling.evaluation_context`): the
session facts :func:`evaluation_session` (kind, state, start, current
tutor, subject, cohort and participants in one statement; ``FOR SHARE`` on
a create), composed with ``academics`` by ``app.workflows.evaluations``,
which also uses :func:`student_ids_for_tutor`,
:func:`ensure_family_student_access` and :func:`note_session_columns`.

Session charges (#91, :mod:`~app.modules.scheduling.billing_context`):
:data:`BILLABLE_STATES` (``confirmed``, ``completed``), the unexecuted
:func:`billable_participant_columns` (the candidates of
``app.workflows.session_charges``) and :func:`lock_session_for_billing`
(the session with its participants, the row ``FOR SHARE``: every writer
that changes a session's state, time, duration or participants takes it
``FOR UPDATE``, so a charge is posted from committed, current data).
Since #92 the candidates include ``group`` sessions (with ``group_id``).
Packages (#94) also use :func:`lock_session_for_billing` (the closeout
approval's consumption and the expiry job's fallback) and
:func:`session_starts` (the start of a consumed session).
Schedule reconciliation (#99) adds ``version``, ``cohort_id``,
``first_confirmed_at`` and ``cancelled_at`` to :class:`BillableSession`,
the unexecuted :func:`reconciliation_session_columns` (the participants of
``cancelled``, ``skipped_closure``, ``confirmed`` and ``completed``
sessions, :data:`RECONCILED_STATES`) and :func:`cancellation_classification`
(an approved cancellation request's effective outcome at its
``created_at``, else a center cancellation: ``excused`` with a makeup at
``cancelled_at``).

Enrollment charges (#92, :mod:`~app.modules.scheduling.enrollment_billing`):
:func:`enrollment_session_counts` and :func:`owner_schedules` (the J3
proration counts: materialized plus projected occurrences, not cancelled or
skipped, and the first session), :func:`lock_group_for_billing` (the group
row ``FOR SHARE``, the enrollment sweeper's first lock), the unexecuted
:func:`group_enrollment_columns` and :func:`owner_session_columns` (joined
by ``app.workflows.enrollment_charges``), :func:`group_enrollment_period`,
:func:`covering_group_periods` with :func:`covers` (the period of a group
session's participant) and :func:`group_sessions_after` (the J4 removal
credits and re-enrollment restorations). Tuition plans (#93) add
:func:`named_group_periods` (group periods by id or student, with the
group's name). Withdrawals (#101) add the optional ``local_window`` and
the ``remaining_minutes`` of the counts, ``GroupSessionSeat.affected`` (the
B3 states a withdrawal leaves), :func:`group_enrollment_state` and
:func:`lock_group_enrollment_for_settlement` (the M4 withdrawal's lock
sequence without the change).

Closure changes on existing occurrences (#232,
:mod:`~app.modules.scheduling.closure_changes`): the nightly job runs
:func:`reconcile_series_closures` per series before the extension; the
``scheduling.occurrence_skipped_closure`` notification type is registered
by ``app.wiring`` (:func:`register_closure_notifications`).

Editing (#54): :func:`update_class_session` (``PATCH`` one session or
occurrence, :mod:`~app.modules.scheduling.session_edit`; #58 adds its
``override`` and workload ``warnings`` for the calendar's drag and drop),
:func:`edit_class_series` and :func:`cancel_class_series` (a series from a
date on, :mod:`~app.modules.scheduling.series_edit`, which documents the
lock order, the "started" rule and the split algorithm),
:func:`cap_offer_deadlines` (the #52 ``respond_by`` cap after a move) and
the three edit notification types (:func:`register_edit_notifications`,
:mod:`~app.modules.scheduling.notifications`).

Substitutions and cancellations (#55): :func:`substitute_class_session`,
:func:`withdraw_substitution` and :func:`list_session_substitutions`
(:mod:`~app.modules.scheduling.substitution_flow`; the rules, invariants
and lock order in :mod:`~app.modules.scheduling.substitutions`; the
substitute answers through :func:`answer_offer`),
:func:`withdraw_pending_substitutions` (the seam of writers that move a
``confirmed`` row), :func:`cancel_class_session` (one session or
occurrence, :mod:`~app.modules.scheduling.cancellation`; #62 reuses it)
with its composition-root seam :func:`register_cancellation_step`, and the
three notification types (:func:`register_substitution_notifications`).

Ongoing tutoring groups (#56): :func:`create_group`, :func:`update_group`,
:func:`enroll_in_group` and :func:`withdraw_from_group`
(:mod:`~app.modules.scheduling.groups`: capacity, the mid-term rule and the
lock order; the pure rules in :mod:`~app.modules.scheduling.groups_rules`),
each returning the :class:`GroupEnrollmentChange` the
``app.workflows.group_enrollment`` workflow (the M6-07 billing hook)
receives (the enrollment charges and their seam are below); and
the reads with the student record policy :func:`get_group`,
:func:`list_groups`, :func:`list_group_sessions` and
:func:`get_group_enrollment` (:mod:`~app.modules.scheduling.group_reads`),
and for the price catalog (#89) :func:`group_subject_id` and
:func:`group_name_columns`.
The group row lock (:func:`lock_group`, :mod:`~app.modules.scheduling.
group_lock`) comes after the students and the zone for every writer that
changes a group occurrence's participants or room.

Enrollment mechanics shared by groups and cohorts (#66 B1,
:mod:`~app.modules.scheduling.series_participants`): :class:`SeriesOwner`,
:func:`lock_producing_series`, :func:`plan_series_participant_add`,
:func:`add_series_participant` and :func:`remove_series_participant` (the
#56 B3 rule), with :func:`record_enrollment_override` (a group or a cohort
enrollment) and :func:`overridden_cohort_enrollment_ids`;
``app.workflows.cohort_enrollment`` composes them with ``courses.service``.

Course cohorts (#65, :mod:`~app.modules.scheduling.course_series`; the
cohort itself lives in ``courses``): ``app.workflows.cohorts`` derives the
series ``COUNT`` from :func:`course_rule_dates` and :func:`closed_dates`,
checks the fields with :func:`check_course_series` and creates the cohort's
``course`` series with :func:`create_course_series`; the reads are
:func:`cohort_schedules`, :func:`cohort_series_read` and
:func:`list_cohort_sessions`. A course series takes the cohort row lock
(``courses.service.lock_cohort``) where a group series takes the group
lock: in the materialization and the series offer.

Session requests by families (#59): the read-only slot search
:func:`available_slots` (the pure rules in
:mod:`~app.modules.scheduling.family_slots`, loaded once by
:func:`load_family_context`), :func:`create_session_request`,
:func:`list_session_requests`, :func:`get_session_request` and
:func:`withdraw_session_request` (:mod:`~app.modules.scheduling.requests`,
the family record policy in :mod:`~app.modules.scheduling.requests_policy`);
the ``scheduling.session_request`` approval handler
:data:`SESSION_REQUEST_APPROVAL_HANDLER` (its approve / reject accept only a
request the review routes already decided, #60 E1) and the time-zone check
:func:`has_pending_session_requests`
(:data:`SESSION_REQUESTS_TIME_ZONE_DEPENDENCY`), both registered by
``app.wiring``. #62 and #67 reuse the record policy. The staff review (#60,
:mod:`~app.modules.scheduling.request_review`):
:func:`get_session_request_review`, :func:`confirm_session_request`,
:func:`reject_session_request` and :func:`notify_request_decision`, called by
``app.workflows.confirm_session_request``, and the two decision notification
types (:func:`register_request_notifications`).

Change requests by families and tutors (#62): :func:`create_change_request`,
:func:`list_change_requests`, :func:`get_change_request` and
:func:`withdraw_change_request` (:mod:`~app.modules.scheduling.change_requests`;
the record policy in :mod:`~app.modules.scheduling.change_requests_policy`,
the outcome rules in :mod:`~app.modules.scheduling.change_request_outcome`);
the staff review (:mod:`~app.modules.scheduling.change_request_review`):
:func:`get_change_request_review`, :func:`approve_change_request`,
:func:`reject_change_request` and :func:`notify_change_request_decision`,
called by ``app.workflows.change_request_decision``; the
``scheduling.change_request`` approval handler
:data:`CHANGE_REQUEST_APPROVAL_HANDLER`, the two decision notification types
(:func:`register_change_request_notifications`) and the time-zone check
:func:`has_pending_reschedule_requests`
(:data:`CHANGE_REQUESTS_TIME_ZONE_DEPENDENCY`), all registered by
``app.wiring``. M6-14 (billing) and M7-04 (tutor pay) read an approved
request's effective outcome with :func:`get_change_request_outcome`.

Availability changes that affect sessions (M4-18 #63,
:mod:`~app.modules.scheduling.reassignment`): the tutor lock
:func:`lock_tutor` (taken before reading a tutor's availability by every
writer that books a tutor, and by a change of that availability),
:func:`affected_tutor_sessions` and :data:`AVAILABILITY_IMPACT_SOURCE` (the
``staff`` impact seam, registered by ``app.wiring``),
:func:`flag_sessions_for_reassignment` and :func:`notify_reassignment_needed`
(the ``app.workflows.availability_change`` approval), the read-time open
rule :func:`is_flag_open` / :func:`open_reassignment_flags` (the session
detail's ``reassignment``, the calendar's ``reassignment_needed`` marker)
and :func:`register_reassignment_notifications`.

The #47 errors are plain exceptions; the create maps them into ``409
scheduling_conflict`` (:class:`SchedulingConflictError`) or ``422``
(:class:`SessionValidationError`). Every function runs in the caller's Unit
of Work and never commits.
"""

from app.modules.scheduling.attendance import (
    AttendanceSummary as AttendanceSummary,
)
from app.modules.scheduling.attendance import (
    ParticipationCounts as ParticipationCounts,
)
from app.modules.scheduling.attendance import (
    attendance_statuses as attendance_statuses,
)
from app.modules.scheduling.attendance import (
    cohort_participation as cohort_participation,
)
from app.modules.scheduling.attendance import (
    correct_attendance as correct_attendance,
)
from app.modules.scheduling.attendance import (
    get_session_attendance as get_session_attendance,
)
from app.modules.scheduling.attendance import (
    record_attendance as record_attendance,
)
from app.modules.scheduling.attendance import (
    register_completed_attendance_unlock as register_completed_attendance_unlock,
)
from app.modules.scheduling.attendance import (
    student_attendance_summary as student_attendance_summary,
)
from app.modules.scheduling.attendance_rules import (
    ATTENDANCE_STATUSES as ATTENDANCE_STATUSES,
)
from app.modules.scheduling.attendance_rules import (
    attendance_block as attendance_block,
)
from app.modules.scheduling.billing_context import (
    BILLABLE_STATES as BILLABLE_STATES,
)
from app.modules.scheduling.billing_context import (
    RECONCILED_STATES as RECONCILED_STATES,
)
from app.modules.scheduling.billing_context import (
    BillableSession as BillableSession,
)
from app.modules.scheduling.billing_context import (
    CancellationFacts as CancellationFacts,
)
from app.modules.scheduling.billing_context import (
    billable_participant_columns as billable_participant_columns,
)
from app.modules.scheduling.billing_context import (
    cancellation_classification as cancellation_classification,
)
from app.modules.scheduling.billing_context import (
    lock_session_for_billing as lock_session_for_billing,
)
from app.modules.scheduling.billing_context import (
    reconciliation_session_columns as reconciliation_session_columns,
)
from app.modules.scheduling.billing_context import (
    session_starts as session_starts,
)
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
from app.modules.scheduling.calendar_read import validate_range as validate_range
from app.modules.scheduling.cancellation import (
    CancellationInitiator as CancellationInitiator,
)
from app.modules.scheduling.cancellation import (
    cancel_class_session as cancel_class_session,
)
from app.modules.scheduling.cancellation import (
    register_cancellation_step as register_cancellation_step,
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
from app.modules.scheduling.change_request_approval import (
    CHANGE_REQUEST_APPROVAL_HANDLER as CHANGE_REQUEST_APPROVAL_HANDLER,
)
from app.modules.scheduling.change_request_review import (
    ApprovedChange as ApprovedChange,
)
from app.modules.scheduling.change_request_review import (
    approve_change_request as approve_change_request,
)
from app.modules.scheduling.change_request_review import (
    get_change_request_review as get_change_request_review,
)
from app.modules.scheduling.change_request_review import (
    notify_change_request_decision as notify_change_request_decision,
)
from app.modules.scheduling.change_request_review import (
    register_change_request_notifications as register_change_request_notifications,
)
from app.modules.scheduling.change_request_review import (
    reject_change_request as reject_change_request,
)
from app.modules.scheduling.change_requests import (
    CHANGE_REQUEST_TYPE as CHANGE_REQUEST_TYPE,
)
from app.modules.scheduling.change_requests import (
    CHANGE_REQUESTS_TIME_ZONE_DEPENDENCY as CHANGE_REQUESTS_TIME_ZONE_DEPENDENCY,
)
from app.modules.scheduling.change_requests import (
    ChangeRequestOutcomeView as ChangeRequestOutcomeView,
)
from app.modules.scheduling.change_requests import (
    create_change_request as create_change_request,
)
from app.modules.scheduling.change_requests import (
    get_change_request as get_change_request,
)
from app.modules.scheduling.change_requests import (
    get_change_request_outcome as get_change_request_outcome,
)
from app.modules.scheduling.change_requests import (
    has_pending_reschedule_requests as has_pending_reschedule_requests,
)
from app.modules.scheduling.change_requests import (
    list_change_requests as list_change_requests,
)
from app.modules.scheduling.change_requests import (
    withdraw_change_request as withdraw_change_request,
)
from app.modules.scheduling.checks import SlotCandidate as SlotCandidate
from app.modules.scheduling.checks import collect_conflicts as collect_conflicts
from app.modules.scheduling.checks import (
    find_offer_conflicts as find_offer_conflicts,
)
from app.modules.scheduling.closeout import AwaitingCloseout as AwaitingCloseout
from app.modules.scheduling.closeout import (
    CloseoutSessionContext as CloseoutSessionContext,
)
from app.modules.scheduling.closeout import ClosingSession as ClosingSession
from app.modules.scheduling.closeout import CompletedSession as CompletedSession
from app.modules.scheduling.closeout import (
    closeout_attendance as closeout_attendance,
)
from app.modules.scheduling.closeout import (
    closeout_session_context as closeout_session_context,
)
from app.modules.scheduling.closeout import (
    closeout_session_summaries as closeout_session_summaries,
)
from app.modules.scheduling.closeout import (
    complete_class_session as complete_class_session,
)
from app.modules.scheduling.closeout import (
    lock_closeout_session as lock_closeout_session,
)
from app.modules.scheduling.closeout import (
    sessions_awaiting_closeout as sessions_awaiting_closeout,
)
from app.modules.scheduling.closeout import (
    set_actual_minutes as set_actual_minutes,
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
from app.modules.scheduling.conflict_override import (
    override_conflict_occurrence as override_conflict_occurrence,
)
from app.modules.scheduling.conflicts import HARD as HARD
from app.modules.scheduling.conflicts import SOFT as SOFT
from app.modules.scheduling.conflicts import WARNING as WARNING
from app.modules.scheduling.conflicts import Conflict as Conflict
from app.modules.scheduling.conflicts import sort_conflicts as sort_conflicts
from app.modules.scheduling.course_series import CohortSchedule as CohortSchedule
from app.modules.scheduling.course_series import (
    CohortSeriesRead as CohortSeriesRead,
)
from app.modules.scheduling.course_series import (
    check_course_series as check_course_series,
)
from app.modules.scheduling.course_series import (
    cohort_schedules as cohort_schedules,
)
from app.modules.scheduling.course_series import (
    cohort_series_read as cohort_series_read,
)
from app.modules.scheduling.course_series import (
    course_rule_dates as course_rule_dates,
)
from app.modules.scheduling.course_series import (
    create_course_series as create_course_series,
)
from app.modules.scheduling.course_series import (
    list_cohort_sessions as list_cohort_sessions,
)
from app.modules.scheduling.enrollment_billing import CountedSlot as CountedSlot
from app.modules.scheduling.enrollment_billing import (
    GroupForBilling as GroupForBilling,
)
from app.modules.scheduling.enrollment_billing import GroupPeriod as GroupPeriod
from app.modules.scheduling.enrollment_billing import (
    GroupSessionSeat as GroupSessionSeat,
)
from app.modules.scheduling.enrollment_billing import (
    NamedGroupPeriod as NamedGroupPeriod,
)
from app.modules.scheduling.enrollment_billing import (
    OwnerSchedule as OwnerSchedule,
)
from app.modules.scheduling.enrollment_billing import (
    SessionCounts as SessionCounts,
)
from app.modules.scheduling.enrollment_billing import (
    covering_group_periods as covering_group_periods,
)
from app.modules.scheduling.enrollment_billing import covers as covers
from app.modules.scheduling.enrollment_billing import (
    enrollment_session_counts as enrollment_session_counts,
)
from app.modules.scheduling.enrollment_billing import (
    group_enrollment_columns as group_enrollment_columns,
)
from app.modules.scheduling.enrollment_billing import (
    group_enrollment_period as group_enrollment_period,
)
from app.modules.scheduling.enrollment_billing import (
    group_sessions_after as group_sessions_after,
)
from app.modules.scheduling.enrollment_billing import (
    lock_group_for_billing as lock_group_for_billing,
)
from app.modules.scheduling.enrollment_billing import (
    named_group_periods as named_group_periods,
)
from app.modules.scheduling.enrollment_billing import (
    owner_schedules as owner_schedules,
)
from app.modules.scheduling.enrollment_billing import (
    owner_session_columns as owner_session_columns,
)
from app.modules.scheduling.errors import (
    AlreadyEnrolledError as AlreadyEnrolledError,
)
from app.modules.scheduling.errors import (
    AttendanceIncompleteError as AttendanceIncompleteError,
)
from app.modules.scheduling.errors import (
    ClassSessionNotAssignableError as ClassSessionNotAssignableError,
)
from app.modules.scheduling.errors import (
    ClassSessionNotOfferableError as ClassSessionNotOfferableError,
)
from app.modules.scheduling.errors import (
    EnrollmentNotActiveError as EnrollmentNotActiveError,
)
from app.modules.scheduling.errors import (
    EnrollmentNotFoundError as EnrollmentNotFoundError,
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
    SessionNotCompletableError as SessionNotCompletableError,
)
from app.modules.scheduling.errors import (
    SessionOverlapError as SessionOverlapError,
)
from app.modules.scheduling.errors import (
    SessionValidationError as SessionValidationError,
)
from app.modules.scheduling.errors import (
    StudentConsentRequiredError as StudentConsentRequiredError,
)
from app.modules.scheduling.evaluation_context import (
    EvaluationSession as EvaluationSession,
)
from app.modules.scheduling.evaluation_context import (
    evaluation_session as evaluation_session,
)
from app.modules.scheduling.group_lock import lock_group as lock_group
from app.modules.scheduling.group_reads import get_group as get_group
from app.modules.scheduling.group_reads import (
    get_group_enrollment as get_group_enrollment,
)
from app.modules.scheduling.group_reads import (
    group_name_columns as group_name_columns,
)
from app.modules.scheduling.group_reads import group_subject_id as group_subject_id
from app.modules.scheduling.group_reads import (
    list_group_sessions as list_group_sessions,
)
from app.modules.scheduling.group_reads import list_groups as list_groups
from app.modules.scheduling.groups import GroupCreation as GroupCreation
from app.modules.scheduling.groups import (
    GroupEnrollmentChange as GroupEnrollmentChange,
)
from app.modules.scheduling.groups import (
    GroupEnrollmentState as GroupEnrollmentState,
)
from app.modules.scheduling.groups import create_group as create_group
from app.modules.scheduling.groups import enroll_in_group as enroll_in_group
from app.modules.scheduling.groups import (
    group_enrollment_state as group_enrollment_state,
)
from app.modules.scheduling.groups import (
    lock_group_enrollment_for_settlement as lock_group_enrollment_for_settlement,
)
from app.modules.scheduling.groups import update_group as update_group
from app.modules.scheduling.groups import (
    withdraw_from_group as withdraw_from_group,
)
from app.modules.scheduling.locks import lock_students as lock_students
from app.modules.scheduling.locks import lock_tutor as lock_tutor
from app.modules.scheduling.materialize import (
    OCCURRENCE_CONFLICT_TYPE as OCCURRENCE_CONFLICT_TYPE,
)
from app.modules.scheduling.materialize import (
    MaterializeCounts as MaterializeCounts,
)
from app.modules.scheduling.materialize import closed_dates as closed_dates
from app.modules.scheduling.materialize import (
    materialize_series as materialize_series,
)
from app.modules.scheduling.materialize import (
    occurrence_initial_state as occurrence_initial_state,
)
from app.modules.scheduling.materialize import (
    revalidate_references as revalidate_references,
)
from app.modules.scheduling.note_context import NoteSession as NoteSession
from app.modules.scheduling.note_context import is_participant as is_participant
from app.modules.scheduling.note_context import note_session as note_session
from app.modules.scheduling.note_context import (
    note_session_columns as note_session_columns,
)
from app.modules.scheduling.notifications import (
    register_edit_notifications as register_edit_notifications,
)
from app.modules.scheduling.occurrence_approval import (
    OCCURRENCE_CONFLICT_HANDLER as OCCURRENCE_CONFLICT_HANDLER,
)
from app.modules.scheduling.offer_approval import (
    OFFER_REASSIGNMENT_HANDLER as OFFER_REASSIGNMENT_HANDLER,
)
from app.modules.scheduling.offer_flow import REASSIGNMENT_TYPE as REASSIGNMENT_TYPE
from app.modules.scheduling.offer_flow import answer_offer as answer_offer
from app.modules.scheduling.offer_flow import (
    cap_offer_deadlines as cap_offer_deadlines,
)
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
    withdraw_pending_substitutions as withdraw_pending_substitutions,
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
    OccurrenceConflict as OccurrenceConflict,
)
from app.modules.scheduling.overrides import (
    OverrideDecision as OverrideDecision,
)
from app.modules.scheduling.overrides import (
    SeriesOverrideDecision as SeriesOverrideDecision,
)
from app.modules.scheduling.overrides import (
    evaluate_override as evaluate_override,
)
from app.modules.scheduling.overrides import (
    evaluate_series_override as evaluate_series_override,
)
from app.modules.scheduling.overrides import (
    overridden_cohort_enrollment_ids as overridden_cohort_enrollment_ids,
)
from app.modules.scheduling.overrides import (
    record_enrollment_override as record_enrollment_override,
)
from app.modules.scheduling.overrides import (
    record_series_overrides as record_series_overrides,
)
from app.modules.scheduling.overrides import (
    record_session_override as record_session_override,
)
from app.modules.scheduling.permissions import STAFF_VIEWER as STAFF_VIEWER
from app.modules.scheduling.permissions import RecordViewer as RecordViewer
from app.modules.scheduling.reassignment import (
    AVAILABILITY_IMPACT_SOURCE as AVAILABILITY_IMPACT_SOURCE,
)
from app.modules.scheduling.reassignment import (
    affected_tutor_sessions as affected_tutor_sessions,
)
from app.modules.scheduling.reassignment import (
    flag_sessions_for_reassignment as flag_sessions_for_reassignment,
)
from app.modules.scheduling.reassignment import (
    notify_reassignment_needed as notify_reassignment_needed,
)
from app.modules.scheduling.reassignment import (
    open_reassignment_flags as open_reassignment_flags,
)
from app.modules.scheduling.reassignment import (
    register_reassignment_notifications as register_reassignment_notifications,
)
from app.modules.scheduling.reassignment_rules import is_flag_open as is_flag_open
from app.modules.scheduling.record_viewer import record_viewer as record_viewer
from app.modules.scheduling.record_viewer import (
    taught_cohort_ids as taught_cohort_ids,
)
from app.modules.scheduling.request_approval import (
    SESSION_REQUEST_APPROVAL_HANDLER as SESSION_REQUEST_APPROVAL_HANDLER,
)
from app.modules.scheduling.request_review import (
    SESSION_REQUEST_CONFIRMED_NOTIFICATION as SESSION_REQUEST_CONFIRMED_NOTIFICATION,
)
from app.modules.scheduling.request_review import (
    SESSION_REQUEST_REJECTED_NOTIFICATION as SESSION_REQUEST_REJECTED_NOTIFICATION,
)
from app.modules.scheduling.request_review import (
    confirm_session_request as confirm_session_request,
)
from app.modules.scheduling.request_review import (
    get_session_request_review as get_session_request_review,
)
from app.modules.scheduling.request_review import (
    notify_request_decision as notify_request_decision,
)
from app.modules.scheduling.request_review import (
    register_request_notifications as register_request_notifications,
)
from app.modules.scheduling.request_review import (
    reject_session_request as reject_session_request,
)
from app.modules.scheduling.requests import (
    MAX_PENDING_REQUESTS_PER_STUDENT as MAX_PENDING_REQUESTS_PER_STUDENT,
)
from app.modules.scheduling.requests import (
    SESSION_REQUEST_TYPE as SESSION_REQUEST_TYPE,
)
from app.modules.scheduling.requests import (
    SESSION_REQUESTS_TIME_ZONE_DEPENDENCY as SESSION_REQUESTS_TIME_ZONE_DEPENDENCY,
)
from app.modules.scheduling.requests import available_slots as available_slots
from app.modules.scheduling.requests import (
    create_session_request as create_session_request,
)
from app.modules.scheduling.requests import (
    get_session_request as get_session_request,
)
from app.modules.scheduling.requests import (
    has_pending_session_requests as has_pending_session_requests,
)
from app.modules.scheduling.requests import (
    list_session_requests as list_session_requests,
)
from app.modules.scheduling.requests import (
    load_family_context as load_family_context,
)
from app.modules.scheduling.requests import (
    withdraw_session_request as withdraw_session_request,
)
from app.modules.scheduling.role_schedules import (
    ensure_family_student_access as ensure_family_student_access,
)
from app.modules.scheduling.role_schedules import (
    family_schedule as family_schedule,
)
from app.modules.scheduling.role_schedules import (
    family_students as family_students,
)
from app.modules.scheduling.role_schedules import (
    tutor_schedule as tutor_schedule,
)
from app.modules.scheduling.series import (
    SERIES_TIME_ZONE_DEPENDENCY as SERIES_TIME_ZONE_DEPENDENCY,
)
from app.modules.scheduling.series import (
    create_class_series as create_class_series,
)
from app.modules.scheduling.series import get_class_series as get_class_series
from app.modules.scheduling.series import has_class_series as has_class_series
from app.modules.scheduling.series_edit import (
    cancel_class_series as cancel_class_series,
)
from app.modules.scheduling.series_edit import (
    edit_class_series as edit_class_series,
)
from app.modules.scheduling.series_offers import (
    offer_class_series as offer_class_series,
)
from app.modules.scheduling.series_participants import SeriesOwner as SeriesOwner
from app.modules.scheduling.series_participants import (
    add_series_participant as add_series_participant,
)
from app.modules.scheduling.series_participants import (
    lock_producing_series as lock_producing_series,
)
from app.modules.scheduling.series_participants import (
    plan_series_participant_add as plan_series_participant_add,
)
from app.modules.scheduling.series_participants import (
    remove_series_participant as remove_series_participant,
)
from app.modules.scheduling.session_edit import (
    update_class_session as update_class_session,
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
from app.modules.scheduling.states import (
    is_tutor_visible as is_tutor_visible,
)
from app.modules.scheduling.student_progress import (
    MonthAttendance as MonthAttendance,
)
from app.modules.scheduling.student_progress import (
    SessionLogPage as SessionLogPage,
)
from app.modules.scheduling.student_progress import (
    SessionLogRow as SessionLogRow,
)
from app.modules.scheduling.student_progress import (
    student_attendance_by_month as student_attendance_by_month,
)
from app.modules.scheduling.student_progress import (
    student_session_log as student_session_log,
)
from app.modules.scheduling.student_progress import (
    tutor_taught_student as tutor_taught_student,
)
from app.modules.scheduling.substitution_flow import (
    list_session_substitutions as list_session_substitutions,
)
from app.modules.scheduling.substitution_flow import (
    substitute_class_session as substitute_class_session,
)
from app.modules.scheduling.substitution_flow import (
    withdraw_substitution as withdraw_substitution,
)
from app.modules.scheduling.substitution_records import (
    register_substitution_notifications as register_substitution_notifications,
)
from app.modules.scheduling.substitutions import (
    substitution_refusal as substitution_refusal,
)
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
from app.modules.scheduling.tutor_assignments import (
    TUTOR_ASSIGNMENT_LOOKBACK_DAYS as TUTOR_ASSIGNMENT_LOOKBACK_DAYS,
)
from app.modules.scheduling.tutor_assignments import (
    TUTOR_ASSIGNMENT_SOURCE as TUTOR_ASSIGNMENT_SOURCE,
)
from app.modules.scheduling.tutor_assignments import (
    current_tutor_subjects_for_student as current_tutor_subjects_for_student,
)
from app.modules.scheduling.tutor_assignments import (
    student_ids_for_tutor as student_ids_for_tutor,
)
from app.modules.scheduling.tutor_assignments import (
    subject_ids_for_tutor_student as subject_ids_for_tutor_student,
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
