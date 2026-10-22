"""Class session routes (#48; ``app.modules.scheduling.service``; ADR-0005).

- ``POST /api/v1/class-sessions``: ``require("schedule.manage")`` (Owners and
  Administrators). A one-off individual session; order ``401``/``403`` →
  ``422`` body → student lock → zone ``FOR SHARE`` → ``422`` bounds →
  ``422`` references → ``409 scheduling_conflict`` (every conflict, unless
  an ``override`` covers them all, #49) → ``201`` (``offered`` with a
  tutor, ``unassigned`` without one). An ``override`` needs
  ``schedule.override`` (the Owner, or an Administrator while an Owner's
  delegation is active): ``403`` right after the body's ``422``, before
  anything is read or locked.
- ``POST /api/v1/class-sessions/suggestions`` (#50 B): read-only,
  ``require("schedule.manage")`` (the holders of the create it serves). The
  free times nearest the requested start for the same student, subject,
  tutor, classroom and duration; order ``401``/``403`` → ``422`` body →
  zone (plain read) → ``422`` bounds → ``422`` window → ``422`` references
  → ``200``. Nothing is written.
- ``GET /api/v1/class-sessions?from=…&to=…`` (#57 A): the calendar's range
  read, ``require("schedule.view")``; the sessions overlapping ``[from,
  to)`` (at most 45 days) with their markers, at most 1000.
- ``GET /api/v1/schedule/tutor-options`` (#57 B): the tutor filter's
  options, ``require("schedule.view")`` (Finance holds no ``tutor.view``).
- ``GET /api/v1/class-sessions/{class_session_id}``:
  ``require("schedule.view")`` (Owners, Administrators, Academic
  Coordinators and Finance staff); every holder sees every session.
- ``GET /api/v1/tutor-suggestions`` and ``GET
  /api/v1/class-sessions/{class_session_id}/replacement-tutors`` (#51):
  ``require("schedule.assign")`` (Owners, Administrators and Academic
  Coordinators; ADR-0005 D9). The eligible tutors in workload order;
  read-only and advisory.
- ``POST /api/v1/class-series`` (#53 E): ``require("schedule.manage")``. A
  recurring individual series; order ``401``/``403`` → ``422`` body →
  student lock → zone ``FOR SHARE`` → ``422`` recurrence → ``422``
  ``starts_on_in_past`` / ``crosses_local_date`` → ``422`` references →
  every occurrence in the horizon checked (closure dates skipped) → ``409
  scheduling_conflict`` listing every occurrence's conflicts → ``201``.
- ``GET /api/v1/class-series/{class_series_id}`` (#53 I):
  ``require("schedule.view")``; every holder sees every series.
- ``PATCH /api/v1/class-sessions/{class_session_id}`` (#54 C1):
  ``require("schedule.manage")``; moves one session or occurrence
  (:mod:`~app.modules.scheduling.session_edit` gives the order).
- ``POST /api/v1/class-series/{class_series_id}/edits`` and
  ``…/cancellations`` (#54 C3, C5): ``require("schedule.manage")``; change
  or cancel a series from a date on
  (:mod:`~app.modules.scheduling.series_edit` gives the orders).
- ``POST /api/v1/class-series/{class_series_id}/offers`` (#233 C2):
  ``require("schedule.assign")``; offers a series without a tutor from a
  local date (:mod:`~app.modules.scheduling.series_offers` gives the
  order).
- Assignment offers (#52; :mod:`~app.modules.scheduling.offer_flow` gives
  the orders): ``POST /api/v1/class-sessions/{class_session_id}/offers``
  (``require("schedule.assign")``; an ``override`` needs
  ``schedule.override``) offers an ``unassigned`` session to a tutor;
  ``GET`` on the same path (``require("schedule.view")``) is its offer
  history. ``GET /api/v1/assignment-offers`` and ``POST
  /api/v1/assignment-offers/{offer_id}/accept|decline`` are
  ``authenticated()`` with a record policy: only the offered tutor's own
  offers; anyone else gets an empty list or ``404``, never ``403``.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    ensure_permission,
    require,
)
from app.core.config import Settings, get_settings
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service
from app.modules.scheduling.schemas import (
    OFFERS_DEFAULT_LIMIT,
    OFFERS_MAX_LIMIT,
    TUTOR_SUGGESTIONS_DEFAULT_LIMIT,
    TUTOR_SUGGESTIONS_MAX_LIMIT,
    AssignmentOfferCreate,
    AssignmentOfferCreated,
    AssignmentOfferDecline,
    AssignmentOfferItem,
    AssignmentOfferPage,
    ClassSeriesCancel,
    ClassSeriesCancelled,
    ClassSeriesCreate,
    ClassSeriesCreated,
    ClassSeriesDetail,
    ClassSeriesEdit,
    ClassSeriesEdited,
    ClassSeriesOfferCreate,
    ClassSessionCalendar,
    ClassSessionCreate,
    ClassSessionCreated,
    ClassSessionDetail,
    ClassSessionOffers,
    ClassSessionRangeQuery,
    ClassSessionUpdate,
    ClassSessionUpdated,
    OfferStatusFilter,
    SlotSuggestionRequest,
    SlotSuggestions,
    TutorOptions,
    TutorSuggestionList,
    TutorSuggestionQuery,
)

SCHEDULE_VIEW: Final = "schedule.view"
SCHEDULE_MANAGE: Final = "schedule.manage"
SCHEDULE_OVERRIDE: Final = "schedule.override"
SCHEDULE_ASSIGN: Final = "schedule.assign"

router = APIRouter(prefix="/api/v1/class-sessions", tags=["class-sessions"])
options_router = APIRouter(prefix="/api/v1/schedule", tags=["class-sessions"])
suggestions_router = APIRouter(
    prefix="/api/v1/tutor-suggestions", tags=["class-sessions"]
)
series_router = APIRouter(prefix="/api/v1/class-series", tags=["class-series"])
offers_router = APIRouter(prefix="/api/v1/assignment-offers", tags=["class-sessions"])

TUTOR_OPTIONS_DEFAULT_LIMIT: Final = 20
TUTOR_OPTIONS_MAX_LIMIT: Final = 50


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): no schedule.manage, or an"
            " `override` without schedule.override. The privacy notice must be"
            " accepted first (privacy_notice_required); a failed Origin or"
            " X-CSRF-Token check (csrf_failed)."
        ),
        409: problem_response_doc(
            "The session breaks scheduling rules (scheduling_conflict);"
            " `conflicts` lists every one, hard and soft. A hard conflict, or"
            " a soft one the `override` doesn't list, always refuses."
            " Nothing is created. With a tutor, a session that has already"
            " begun can't be offered (class_session_not_offerable,"
            " details.reason = started)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, a kind other than individual; the `override`"
            " (reason_required, reason_too_long, codes_required,"
            " duplicate_code, code_not_overridable, code_unknown);"
            " the bounds (naive_datetime, not_whole_minutes,"
            " ends_before_start, duration_out_of_range, crosses_local_date);"
            " the references (student_unknown, student_inactive,"
            " subject_unknown, subject_inactive, tutor_unknown, tutor_inactive,"
            " classroom_unknown, classroom_inactive), all in one response; or"
            " `respond_by` (respond_by_requires_tutor, respond_by_invalid)."
        ),
    },
)
async def create_class_session(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: ClassSessionCreate,
    uow: UnitOfWorkDep,
) -> ClassSessionCreated:
    """Schedule a one-off individual session. With a tutor it is created
    `offered` to them, with a pending offer (deadline `respond_by`, by
    default the scheduling policy's response time but never after the
    start) and a notification to the tutor; without one `unassigned`. Every
    rule is checked first; with an `override` the listed soft conflicts are
    allowed, stored with the reason and audited. `warnings` lists the
    tutor's workload warnings."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    return await service.create_class_session(
        uow.session, body, actor_id=principal.user_id
    )


@router.post(
    "/suggestions",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` included), a kind other than individual,"
            " `limit` outside 1-10; the window (search_range_invalid,"
            " search_range_too_long, on search_to); the bounds"
            " (naive_datetime, not_whole_minutes, ends_before_start,"
            " duration_out_of_range, crosses_local_date); or the references"
            " (student_unknown, student_inactive, subject_unknown,"
            " subject_inactive, tutor_unknown, tutor_inactive,"
            " classroom_unknown, classroom_inactive), all in one response."
        ),
    },
)
async def suggest_class_session_slots(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: SlotSuggestionRequest,
    uow: UnitOfWorkDep,
) -> SlotSuggestions:
    """Free times for the same student, subject, tutor, classroom and
    duration: starts on a 15-minute local grid in the window (default the
    requested date and the 6 days after it, at most 14 dates), never the
    requested start, each passing every hard and soft rule; nearest the
    requested start first (a tie goes to the earlier one), at most `limit`
    (default 5). Each slot lists the workload warnings a create would get.
    Read-only: nothing is created, locked or audited."""
    return await service.suggest_slots(uow.session, body)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a missing, malformed"
            " or naive `from`/`to` (naive_datetime), `to` not after `from`"
            " (range_invalid), more than 45 days (range_too_long), an unknown"
            " parameter, a malformed id or an unknown kind."
        ),
    },
)
async def list_class_sessions(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    query: Annotated[ClassSessionRangeQuery, Query()],
    uow: UnitOfWorkDep,
) -> ClassSessionCalendar:
    """The sessions overlapping `[from, to)` (`starts_at < to` and `ends_at >
    from`), by start then id, at most 1000 (`truncated` when more match).
    Filters combine with AND; an unknown id matches nothing. `cancelled`
    sessions only with `include_cancelled`. `markers` are computed on read:
    `center_closed` (an unassigned, offered or confirmed session on a
    closure's local date) and `tutor_not_qualified` (a future offered or
    confirmed session whose tutor holds no approved qualification)."""
    return await service.list_calendar_sessions(uow.session, query)


@router.get(
    "/{class_session_id:uuid}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class session (not_found)."),
    },
)
async def get_class_session(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ClassSessionDetail:
    """One class session with its subject, tutor, classroom and students."""
    return await service.get_class_session(uow.session, class_session_id)


@router.patch(
    "/{class_session_id:uuid}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class session (not_found)."),
        409: problem_response_doc(
            "The session changed since it was read (version_conflict); it has"
            " begun (session_not_editable, details.reason = started) or is"
            " completed, cancelled or skipped for a closure"
            " (session_not_editable, details.state); or the new time breaks"
            " scheduling rules (scheduling_conflict, every conflict; a closure"
            " date is center_closed). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` or `tutor_user_id` included); the bounds"
            " (naive_datetime, not_whole_minutes, ends_before_start,"
            " duration_out_of_range, crosses_local_date); the references"
            " (classroom_unknown, classroom_inactive, and the session's"
            " student, subject or tutor codes), in one response; or"
            " session_unchanged."
        ),
    },
)
async def update_class_session(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    class_session_id: uuid.UUID,
    body: ClassSessionUpdate,
    uow: UnitOfWorkDep,
) -> ClassSessionUpdated:
    """Move one session (a one-off session or one occurrence of a series) to
    new times and, optionally, another room (`classroom_id` absent keeps the
    room, `null` removes it). The tutor, subject and students never change.
    Every rule is checked again at the new time, the session itself
    excluded. A series occurrence becomes an exception: later changes of
    the series' rule never recreate its original date. Offered and confirmed
    sessions keep their state and tutor; a session in `conflict` that now
    passes is scheduled. The tutor (offered or confirmed) and the family
    (confirmed) are notified. `warnings` is always empty for now."""
    return await service.update_class_session(
        uow.session, class_session_id, body, actor_id=principal.user_id
    )


@router.get(
    "/{class_session_id}/replacement-tutors",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class session (not_found)."),
        409: problem_response_doc(
            "The session is completed, cancelled or skipped for a closure"
            " (class_session_not_assignable)."
        ),
        422: problem_response_doc("limit outside 1-50 (validation_error)."),
    },
)
async def list_replacement_tutors(
    principal: Annotated[Principal, require(SCHEDULE_ASSIGN)],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
    limit: Annotated[
        int, Query(ge=1, le=TUTOR_SUGGESTIONS_MAX_LIMIT)
    ] = TUTOR_SUGGESTIONS_DEFAULT_LIMIT,
) -> TutorSuggestionList:
    """Replacement tutors for the session's own subject, time and
    classroom, never its current tutor; the session itself counts for
    nobody's overlap or workload.

    A tutor is listed when they are an active tutor with an approved
    qualification for the subject, available for the whole slot (a center
    closure doesn't count against them) and without an overlapping offered,
    confirmed or completed session. Order: scheduled minutes in the slot's
    local ISO week, then on its local date (fewest first), then the tutors
    who prefer the classroom, then name. Limits are shown, never applied.
    Advisory and read-only: creating or offering re-checks everything."""
    return await service.suggest_replacements(
        uow.session, class_session_id, limit=limit
    )


@options_router.get(
    "/tutor-options",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "q empty or longer than 100 characters, or limit outside 1-50"
            " (validation_error)."
        ),
    },
)
async def list_tutor_options(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    uow: UnitOfWorkDep,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[
        int, Query(ge=1, le=TUTOR_OPTIONS_MAX_LIMIT)
    ] = TUTOR_OPTIONS_DEFAULT_LIMIT,
) -> TutorOptions:
    """The users holding the tutor role whose username or display name
    contains `q` (not case-sensitive), by display name; inactive tutors are
    included with their `state`. No contact data."""
    return await service.list_tutor_options(
        uow.session, q=(q or "").strip() or None, limit=limit
    )


@suggestions_router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid (validation_error): a missing or malformed"
            " parameter, `limit` outside 1-50; the bounds (naive_datetime,"
            " not_whole_minutes, ends_before_start, duration_out_of_range,"
            " crosses_local_date); or the references (subject_unknown,"
            " subject_inactive, classroom_unknown, classroom_inactive), all in"
            " one response."
        ),
    },
)
async def list_tutor_suggestions(
    principal: Annotated[Principal, require(SCHEDULE_ASSIGN)],
    query: Annotated[TutorSuggestionQuery, Query()],
    uow: UnitOfWorkDep,
) -> TutorSuggestionList:
    """The tutors who can teach a new slot (subject, start, end, optional
    classroom), the first `limit` in ranking order, with how many are
    eligible in all.

    A tutor is listed when they are an active tutor with an approved
    qualification for the subject, available for the whole slot (a center
    closure doesn't count against them) and without an overlapping offered,
    confirmed or completed session. Order: scheduled minutes in the slot's
    local ISO week, then on its local date (fewest first), then the tutors
    who prefer the classroom, then name. Limits are shown, never applied.
    Advisory and read-only: creating or offering re-checks everything."""
    return await service.suggest_for_slot(uow.session, query)


@series_router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "An occurrence breaks scheduling rules (scheduling_conflict):"
            " `conflicts` lists every conflict of every occurrence in the"
            " horizon, each with `details.occurrence_start` and `details.date`,"
            " by occurrence. Nothing is created. With a tutor, a series with no"
            " occurrence still to come can't be offered (series_not_offerable,"
            " details.reason = ended)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` or `rrule` included), a kind other than"
            " individual, `start_time` not HH:MM (time_format), a duration"
            " outside 15-480; the recurrence (weekdays_empty,"
            " weekday_out_of_range, weekdays_duplicate, interval_out_of_range,"
            " until_and_count, count_out_of_range, until_before_start,"
            " no_occurrences); starts_on_in_past; crosses_local_date on"
            " start_time; the references (student_unknown, student_inactive,"
            " subject_unknown, subject_inactive, tutor_unknown, tutor_inactive,"
            " classroom_unknown, classroom_inactive), all in one response; or"
            " `respond_by` (respond_by_requires_tutor, respond_by_invalid)."
        ),
    },
)
async def create_class_series(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    body: ClassSeriesCreate,
    uow: UnitOfWorkDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClassSeriesCreated:
    """Schedule a recurring individual series. Every occurrence inside the
    rolling horizon (`SCHEDULING_HORIZON_WEEKS`, default 12 weeks) is created
    now and checked like a one-off session (a closure date is stored as
    `skipped_closure` instead); any conflict refuses the whole series. Later
    occurrences are created by the nightly job. With a tutor the occurrences
    are `offered` to them, without one `unassigned`. `warnings` is always
    empty for now."""
    return await service.create_class_series(
        uow.session,
        body,
        horizon_weeks=settings.scheduling_horizon_weeks,
        actor_id=principal.user_id,
    )


@series_router.get(
    "/{class_series_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class series (not_found)."),
    },
)
async def get_class_series(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    class_series_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ClassSeriesDetail:
    """One class series with its rule, references, tutor assignment and the
    occurrences that start now or later."""
    return await service.get_class_series(uow.session, class_series_id)


@series_router.post(
    "/{class_series_id}/edits",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class series (not_found)."),
        409: problem_response_doc(
            "The series changed since it was read (version_conflict); its tutor"
            " offer is waiting for an answer (series_offer_pending); it has no"
            " occurrence on or after the date (series_ended); an occurrence on"
            " the date has begun (occurrence_started); or a changed or new"
            " occurrence breaks scheduling rules (scheduling_conflict: every"
            " conflict of every occurrence, with `details.occurrence_start`,"
            " the new instant, and `details.date`). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` included), `from_date` missing for"
            " `following` (from_date_required) or given for `all`"
            " (from_date_not_allowed), `start_time` not HH:MM (time_format), a"
            " duration outside 15-480; from_date_in_past; the recurrence"
            " (weekdays_empty, weekday_out_of_range, weekdays_duplicate,"
            " interval_out_of_range, until_and_count, count_out_of_range,"
            " until_before_start, no_occurrences); crosses_local_date on"
            " start_time; the references (classroom_unknown,"
            " classroom_inactive, and the series' student, subject or tutor"
            " codes); or series_unchanged."
        ),
    },
)
async def edit_class_series(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    class_series_id: uuid.UUID,
    body: ClassSeriesEdit,
    uow: UnitOfWorkDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClassSeriesEdited:
    """Change a series' time, duration, room or weekly rule from a date on
    (`following`) or from its next occurrence that hasn't begun (`all`).
    The body carries the full new rule. Past, begun, completed and cancelled
    occurrences are never rewritten. When the series has occurrences before
    that date it is split: it ends the day before, and a new series (with
    `split_from_series_id`) starts on the date; otherwise it is changed in
    place. Future occurrences on a date the new rule keeps are moved (same
    id; edited ones lose their exception), the others are cancelled
    (`series_changed`), and new dates are created; a cancelled occurrence on
    a kept date moves to the new series as it is. Every changed or new
    occurrence is checked; any conflict refuses the whole change.
    `warnings` is always empty for now."""
    return await service.edit_class_series(
        uow.session,
        class_series_id,
        body,
        horizon_weeks=settings.scheduling_horizon_weeks,
        actor_id=principal.user_id,
    )


@series_router.post(
    "/{class_series_id}/cancellations",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class series (not_found)."),
        409: problem_response_doc(
            "The series changed since it was read (version_conflict); it has no"
            " occurrence on or after the date (series_ended, also for a repeated"
            " cancellation); or an occurrence on the date has begun"
            " (occurrence_started). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, `from_date` missing for `following` (from_date_required)"
            " or given for `all` (from_date_not_allowed), the reason"
            " (reason_required, reason_too_long); or from_date_in_past."
        ),
    },
)
async def cancel_class_series(
    principal: Annotated[Principal, require(SCHEDULE_MANAGE)],
    class_series_id: uuid.UUID,
    body: ClassSeriesCancel,
    uow: UnitOfWorkDep,
) -> ClassSeriesCancelled:
    """Cancel a series' occurrences from a date on (`following`) or from its
    next occurrence that hasn't begun (`all`), with a reason. Occurrences
    skipped for a closure, and cancelled ones, are kept as they are; the
    series ends the day before the date. A pending tutor offer of the series
    is withdrawn. The tutor (offered or confirmed) and the family
    (confirmed) are notified."""
    return await service.cancel_class_series(
        uow.session, class_series_id, body, actor_id=principal.user_id
    )


@series_router.post(
    "/{class_series_id}/offers",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class series (not_found)."),
        409: problem_response_doc(
            "The series changed since it was read (version_conflict); it already"
            " has a tutor (series_not_offerable, details.reason = assigned) or no"
            " occurrence from `from_date` is still to come (series_not_offerable,"
            " details.reason = ended); or the tutor breaks scheduling rules on an"
            " occurrence (scheduling_conflict, every conflict of every covered"
            " occurrence with `details.occurrence_start` and `details.date`)."
            " Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field (an `override` included), a naive `respond_by`;"
            " from_date_in_past; the tutor (tutor_unknown, tutor_inactive) or"
            " `respond_by` (respond_by_invalid), in one response."
        ),
    },
)
async def offer_class_series(
    principal: Annotated[Principal, require(SCHEDULE_ASSIGN)],
    class_series_id: uuid.UUID,
    body: ClassSeriesOfferCreate,
    uow: UnitOfWorkDep,
) -> AssignmentOfferCreated:
    """Offer a series without a tutor to one, from a local date (default:
    the later of its start and today): every `unassigned` occurrence from
    that date that has not started becomes `offered` to them in one offer,
    and the tutor is notified. Occurrences created later join the offer;
    once accepted they are created `confirmed`. Each covered occurrence is
    checked for the new tutor: overlap (hard), availability, qualification,
    operating hours and the room's availability; there is no override, so
    any conflict refuses the offer. Offering closes the inbox items of the
    series' earlier declined or expired offers. `warnings` is always
    empty."""
    return await service.offer_class_series(
        uow.session, class_series_id, body, actor_id=principal.user_id
    )


@router.post(
    "/{class_session_id}/offers",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Permission denied (permission_denied): no schedule.assign, or an"
            " `override` without schedule.override. The privacy notice must be"
            " accepted first (privacy_notice_required); a failed Origin or"
            " X-CSRF-Token check (csrf_failed)."
        ),
        404: problem_response_doc("No such class session (not_found)."),
        409: problem_response_doc(
            "The session changed since it was read (version_conflict); it is"
            " not unassigned (class_session_not_offerable, details.state) or has"
            " begun (class_session_not_offerable, details.reason = started);"
            " or the tutor breaks scheduling rules (scheduling_conflict, every"
            " conflict listed). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, a naive `respond_by`, the `override` (reason_required,"
            " reason_too_long, codes_required, duplicate_code,"
            " code_not_overridable, code_unknown); the tutor (tutor_unknown,"
            " tutor_inactive) or `respond_by` (respond_by_invalid), in one"
            " response."
        ),
    },
)
async def offer_class_session(
    principal: Annotated[Principal, require(SCHEDULE_ASSIGN)],
    class_session_id: uuid.UUID,
    body: AssignmentOfferCreate,
    uow: UnitOfWorkDep,
) -> AssignmentOfferCreated:
    """Offer an `unassigned` session to a tutor: it becomes `offered` to them
    with a pending offer and the tutor is notified. Checked again for the
    new tutor: overlap (hard), availability, qualification, center closure,
    operating hours and the room's availability (soft; an `override` with
    schedule.override allows them). Not re-checked: the booking notice and
    the student and room overlaps. Offering closes the session's open
    reassignment item. `warnings` lists the tutor's workload warnings."""
    if body.override is not None:
        ensure_permission(principal, SCHEDULE_OVERRIDE)
    return await service.offer_class_session(
        uow.session, class_session_id, body, actor_id=principal.user_id
    )


@router.get(
    "/{class_session_id}/offers",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class session (not_found)."),
    },
)
async def list_class_session_offers(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ClassSessionOffers:
    """The session's offers, newest first, with each answer and decline
    note."""
    return await service.list_session_offers(uow.session, class_session_id)


@offers_router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown status, or limit/offset out of range (validation_error)."
        ),
    },
)
async def list_assignment_offers(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    status: OfferStatusFilter = "pending",
    limit: Annotated[int, Query(ge=1, le=OFFERS_MAX_LIMIT)] = OFFERS_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssignmentOfferPage:
    """The signed-in user's own offers: `pending` (by deadline, the
    default), `answered` (accepted, declined, expired or withdrawn; newest
    first) or `all` (newest first). Anyone who has none gets an empty
    page."""
    return await service.list_my_offers(
        uow.session,
        caller_id=principal.user_id,
        status=status,
        limit=limit,
        offset=offset,
    )


_ANSWER_RESPONSES = {
    **PERMISSION_RESPONSES,
    404: problem_response_doc(
        "No such offer, or it was offered to someone else (not_found)."
    ),
    409: problem_response_doc(
        "The offer was already answered, expired or withdrawn"
        " (offer_not_pending, details.status), or its deadline has passed"
        " (offer_expired)."
    ),
}


@offers_router.post("/{offer_id}/accept", responses=_ANSWER_RESPONSES)
async def accept_assignment_offer(
    principal: Annotated[Principal, authenticated()],
    offer_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> AssignmentOfferItem:
    """Accept an offer made to you before its deadline: its sessions become
    `confirmed` with you as the tutor."""
    return await service.answer_offer(
        uow.session, offer_id, caller_id=principal.user_id, accept=True
    )


@offers_router.post(
    "/{offer_id}/decline",
    responses={
        **_ANSWER_RESPONSES,
        422: problem_response_doc(
            "An unknown field, or a note longer than 500 characters"
            " (decline_note_too_long)."
        ),
    },
)
async def decline_assignment_offer(
    principal: Annotated[Principal, authenticated()],
    offer_id: uuid.UUID,
    uow: UnitOfWorkDep,
    body: AssignmentOfferDecline | None = None,
) -> AssignmentOfferItem:
    """Decline an offer made to you before its deadline, with an optional
    note for the staff: its sessions go back to `unassigned` and the staff
    get an inbox item to offer them again."""
    return await service.answer_offer(
        uow.session,
        offer_id,
        caller_id=principal.user_id,
        accept=False,
        note=None if body is None else body.note,
    )
