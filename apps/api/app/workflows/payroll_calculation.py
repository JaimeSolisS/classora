"""The payroll calculation (#107 D, F; spec W3, W7; ADR-0017 E2, E9; data-model
§15).

Pay is derived, never posted. Nothing pays at a closeout approval, a
cancellation or a reschedule: :func:`recalculate` reads the committed state of
``academics`` and ``scheduling`` and rebuilds the lines of one ``open`` period.
It takes no lock on either module and no advisory lock, and no writer of those
modules takes a period lock, so a payroll failure can't roll back a closeout,
and neither side waits for the other (§15).

- :func:`compute_lines` only reads and returns frozen drafts: the session lines
  by service date, then session, then the salary lines by their first day
  (#108 C2), then the item lines by item (#109 C1). A session an adjustment
  names is settled (#111 E7) and gets no line, in a period of any status, so a
  rate added later can't pay it again (M7-08c B1). In a per-course walk it still
  counts in ``k`` (B2): the line is dropped after the walk, as a session with no
  terms on its date earns none. ``finalize``'s drift check
  (#110 D3) compares them with the stored lines, with no write. It reads the
  period's work in center-local dates: ``floor_instant(starts_on)`` to
  ``floor_instant(ends_on + 1)``, half-open, and a service date is the local date
  of the start. It takes the versions and the items of the tutor ``FOR KEY
  SHARE``, so a concurrent delete waits for the run.
- :func:`recalculate`: the period ``FOR UPDATE``, re-read → ``404`` → ``409
  payroll_period_not_open`` → :func:`compute_lines` → replace the rebuilt kinds
  → the event. A second run leaves the same lines but for ``id`` and
  ``created_at``. Two runs at once take turns on the period row.

A revision or an approval that arrives later shows on the next run of an open
period. A prepared period waits for ``return`` or the drift check of
:mod:`app.workflows.payroll_workflow`. Work and revisions dated in a finalized
period are the ``payroll:post_adjustments`` job's
(:mod:`app.workflows.payroll_adjustments`, #111): they become adjustments in the
tutor's earliest open period, whose lines a run never rebuilds.

**Per-course pay (#442; ADR-0017 E3):** :func:`_cohort_walks` adds, for the
cohorts of the period's course sessions, every session of those cohorts in any
state, the tutor's approvals of them, and what the tutor's finalized periods
and per-course anchors fix (M7-08d E1). The period bounds pick which lines are
written, not which sessions count, so periods may run in any order.

**Unresolved work (#443; ADR-0017 E2, #106 E4, #107 E12, E13).** Work that was
approved, or a paying family cancellation, with no session-sourced line and no
adjustment naming it (settled, #111 E7), since the floor.
:func:`_unresolved_items` composes the anti-join here, from the
unexecuted ``SELECT`` s of ``academics`` and ``scheduling`` (ADR-0002), reads the
tutors' versions in one statement each, and classifies in Python
(:func:`~app.modules.payroll.calculation.classify_unresolved`): the same rules
as a line. A date a finalized salary line paid is judged as it stood at that
finalize (#111 E4, :func:`salary_covers`), as the job judges it.
:func:`unresolved_work` is one tutor's list, :func:`blocking_work`
what ``prepare`` and ``finalize`` refuse on (#110), and
:func:`list_unresolved_work` the staff route. It reads the whole of "approved, no
line, since the floor" into memory. That is small for one center: a run empties
most of it, and what is left is what Finance has to fix. It takes no lock, so it
can't delay a run.

**Salary (#108; ADR-0017 E1, E7).** :func:`_salary_lines` reads the basis and
the anchor only for a tutor with a salary on some terms version, and
:func:`~app.modules.payroll.salary.salary_drafts` decides the lines. A salary line
never keeps work off the unresolved list, which looks at session-sourced lines
only.

**Bonuses and deductions (#109; ADR-0017 E8).** :func:`_item_lines` reads the
tutor's items, and the periods and finalized lines only for a one-time item.
:func:`~app.modules.payroll.pay_item_lines.pay_item_drafts` decides the lines.
Pull model, like everything here: a change to an item shows on the next run. An
item line keeps no work off the unresolved list.

**Statements:** thirteen for a run, whatever the number of sessions, with the
zone not yet read, the event included; eighteen with a course session in the
period, whatever the number of sessions or cohorts. A salary on the terms adds
two, whatever the number of pieces: fifteen and twenty. A one-time item adds two,
whatever the number of items. A session line to draft adds one, whatever the
number of lines: the settled read (M7-08c B4).

The unresolved reads cost eight statements, ten with a course session among the
candidates, one more with a salaried tutor among them (#111 E4), and the route
one more for the names, whatever the number of items.

Log lines and the event carry ids and counts, never an amount.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import date, timedelta
from itertools import groupby
from operator import attrgetter
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.core.pagination import (
    CursorPage,
    CursorParams,
    InvalidCursorError,
    build_cursor_page,
    decode_cursor,
)
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    ClassKind,
    PayrollPeriodSummary,
    PayrollRecalculation,
    UnresolvedReason,
    UnresolvedWorkItem,
)
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import floor_instant, local_date

RECALCULATED_EVENT: Final = "payroll.period_recalculated"
PERIOD_RECORD_TYPE: Final = "payroll_period"
COURSE: Final = "course"


def _approved_session(
    approval: academics.CurrentApproval, zone: str, makeups: Collection[uuid.UUID]
) -> payroll.ApprovedSession:
    return payroll.ApprovedSession(
        approval_id=approval.approval_id,
        class_session_id=approval.class_session_id,
        class_kind=approval.session_kind,
        service_date=local_date(approval.session_starts_at, zone),
        approved_minutes=approval.approved_minutes,
        all_absent=academics.all_marked_absent(
            approval.attendance, len(approval.attendance)
        ),
        is_makeup=approval.class_session_id in makeups,
    )


async def _cohort_walks(
    session: AsyncSession,
    tutor: uuid.UUID,
    zone: str,
    approvals: Sequence[academics.CurrentApproval],
    makeups: Collection[uuid.UUID],
) -> list[payroll.CohortWalk]:
    """The cohorts of the period's course sessions (#442 A, C), by
    :func:`cohort_walks`."""
    return await cohort_walks(
        session,
        tutor,
        zone,
        [a.class_session_id for a in approvals if a.session_kind == COURSE],
        makeups,
    )


async def cohort_walks(
    session: AsyncSession,
    tutor: uuid.UUID,
    zone: str,
    course_session_ids: Collection[uuid.UUID],
    makeups: Collection[uuid.UUID],
) -> list[payroll.CohortWalk]:
    """The tutor's walks of the cohorts of these course sessions (#442 A, C), for
    a run and for an anchor (M7-08d E1). Five statements, none with no course
    session.

    A cohort's sessions come oldest first, and a started session can't move, so
    that is the order of ``(session_starts_at, class_session_id)``. The finalized
    periods are read before their lines, and a finalized line wins over an
    approval, so a period finalized between the two reads counts once. A
    session's anchor (M7-08d E1) is a fixed step like a finalized line, from the
    same statement, and the fixed steps walk by their stored ``k``
    (:func:`~app.modules.payroll.calculation.in_walk_order`). An approval dated in
    a finalized period with neither isn't walked: the job anchors it, and the
    walk carries it as ``late`` for the anchors of its cohort, with the ones an
    adjustment settled (one more statement, none with no such session)."""
    cohort_ids = await scheduling.cohort_ids_of_sessions(session, course_session_ids)
    sessions = await scheduling.cohort_sessions(session, cohort_ids)
    session_ids = [row.class_session_id for row in sessions]
    approved = {
        approval.class_session_id: _approved_session(approval, zone, makeups)
        for approval in await academics.current_approvals_for_sessions(
            session, tutor, session_ids
        )
    }
    finalized_dates = (
        await payroll.finalized_period_dates(session, tutor) if session_ids else []
    )
    finalized = {
        line.class_session_id: line
        for line in await payroll.finalized_course_lines(session, tutor, session_ids)
    }

    def in_finalized(work: payroll.ApprovedSession) -> bool:
        return any(
            starts_on <= work.service_date <= ends_on
            for starts_on, ends_on in finalized_dates
        )

    def step(session_id: uuid.UUID) -> payroll.CohortStep | None:
        if session_id in finalized:
            return finalized[session_id]
        work = approved.get(session_id)
        return None if work is None or in_finalized(work) else work

    grouped = []
    for _, group in groupby(sessions, key=attrgetter("cohort_id")):
        rows = list(group)
        late = [
            approved[row.class_session_id]
            for row in rows
            if row.class_session_id not in finalized
            and row.class_session_id in approved
            and in_finalized(approved[row.class_session_id])
        ]
        grouped.append((rows, late))
    settled = await payroll.settled_session_ids(
        session, {work.class_session_id for _, late in grouped for work in late}
    )
    walks = []
    for rows, late in grouped:
        steps = (step(row.class_session_id) for row in rows)
        walks.append(
            payroll.CohortWalk(
                first_session_date=local_date(rows[0].starts_at, zone),
                planned_sessions=payroll.planned_sessions(r.state for r in rows),
                steps=payroll.in_walk_order(
                    [item for item in steps if item is not None]
                ),
                late=late,
                settled=frozenset(
                    work.class_session_id
                    for work in late
                    if work.class_session_id in settled
                ),
            )
        )
    return walks


async def _salary_lines(
    session: AsyncSession,
    period: PayrollPeriodSummary,
    terms: Sequence[payroll.TermsBasisVersion],
) -> list[payroll.LineDraft]:
    """The period's salary lines. Two statements, none with no salary."""
    if all(version.salary is None for version in terms):
        return []
    return payroll.salary_drafts(
        starts_on=period.starts_on,
        ends_on=period.ends_on,
        terms=terms,
        anchor=await payroll.biweekly_anchor(session),
        proration_basis=await payroll.salary_proration_basis(session),
    )


async def _item_lines(
    session: AsyncSession, period: PayrollPeriodSummary
) -> list[payroll.LineDraft]:
    """The period's item lines. One statement, three with a one-time item."""
    items = await payroll.pay_item_facts(session, period.tutor_user_id)
    one_time = [item.id for item in items if item.recurrence != payroll.RECURRING]
    return payroll.pay_item_drafts(
        period=payroll.PeriodDates(period.id, period.starts_on, period.ends_on),
        items=items,
        unfinalized_periods=(
            await payroll.unfinalized_periods(session, period.tutor_user_id)
            if one_time
            else []
        ),
        finalized_item_ids=await payroll.finalized_pay_item_ids(session, one_time),
    )


async def compute_lines(
    session: AsyncSession, period: PayrollPeriodSummary
) -> list[payroll.LineDraft]:
    """The lines the period's facts give now. Reads only."""
    zone = await administration.get_center_time_zone(session)
    starts_from = floor_instant(period.starts_on, zone)
    starts_before = floor_instant(period.ends_on + timedelta(days=1), zone)
    tutor = period.tutor_user_id
    basis = payroll.PayBasis(
        terms=await payroll.terms_basis_versions(session, tutor),
        rates=await payroll.rate_versions(session, tutor),
        rules=await payroll.session_pay_rule_versions(session),
    )
    approvals = await academics.current_approvals(
        session, tutor, starts_from, starts_before
    )
    makeups = await scheduling.makeup_session_ids(
        session, [approval.class_session_id for approval in approvals]
    )
    cancellations = await scheduling.payable_cancellations(
        session, tutor, starts_from, starts_before
    )
    session_lines = payroll.draft_lines(
        starts_on=period.starts_on,
        ends_on=period.ends_on,
        basis=basis,
        approved=[_approved_session(approval, zone, makeups) for approval in approvals],
        cancellations=[
            payroll.PayableCancellation(
                class_session_id=cancellation.class_session_id,
                class_kind=cancellation.kind,
                service_date=local_date(cancellation.starts_at, zone),
                scheduled_minutes=cancellation.scheduled_minutes,
                classification=cancellation.classification,
            )
            for cancellation in cancellations
        ],
        cohorts=await _cohort_walks(session, tutor, zone, approvals, makeups),
    )
    settled = await payroll.settled_session_ids(
        session,
        {line.class_session_id for line in session_lines if line.class_session_id},
    )
    return [
        *(line for line in session_lines if line.class_session_id not in settled),
        *await _salary_lines(session, period, basis.terms),
        *await _item_lines(session, period),
    ]


async def recalculate(
    session: AsyncSession, period_id: uuid.UUID
) -> PayrollRecalculation:
    """Rebuild the session-sourced, salary and item lines of an ``open``
    period. The result describes all its lines (#109 E2)."""
    period = await payroll.lock_open_period(session, period_id)
    drafts = await compute_lines(session, period)
    result = await payroll.replace_lines(session, period, drafts)
    record_audit_event(
        session,
        action=RECALCULATED_EVENT,
        record_type=PERIOD_RECORD_TYPE,
        record_id=period.id,
        details={
            "payroll_period_id": period.id,
            "tutor_user_id": period.tutor_user_id,
            "line_count": result.line_count,
        },
    )
    return result


async def first_session_dates(
    session: AsyncSession, class_session_ids: Collection[uuid.UUID], zone: str
) -> dict[uuid.UUID, date]:
    """Each of these sessions' cohort's first date, from the rows
    :func:`_cohort_walks` reads, so a cohort's first session is one thing. Two
    statements, none for no ids."""
    cohort_ids = await scheduling.cohort_ids_of_sessions(session, class_session_ids)
    rows = await scheduling.cohort_sessions(session, cohort_ids)
    dates: dict[uuid.UUID, date] = {}
    for _, group in groupby(rows, key=attrgetter("cohort_id")):
        cohort = list(group)
        first = local_date(cohort[0].starts_at, zone)
        for row in cohort:
            dates[row.class_session_id] = first
    return dates


async def _unresolved_candidates(
    session: AsyncSession, tutor_user_id: uuid.UUID | None, floor: date, zone: str
) -> list[payroll.UnresolvedCandidate]:
    """Work since the floor with no session-sourced line, in two statements,
    and the first date of its cohorts in two more."""
    starts_from = floor_instant(floor, zone)
    closeouts = academics.current_approval_columns(
        starts_from=starts_from, tutor_user_id=tutor_user_id
    ).subquery()
    cancellations = scheduling.payable_cancellation_columns(
        starts_from=starts_from, tutor_user_id=tutor_user_id
    ).subquery()
    approved = (
        await session.execute(
            select(
                closeouts.c.class_session_id,
                closeouts.c.tutor_user_id,
                closeouts.c.session_kind,
                closeouts.c.session_starts_at,
            ).where(
                payroll.has_no_session_line(closeouts.c.class_session_id),
                payroll.has_no_adjustment(closeouts.c.class_session_id),
            )
        )
    ).all()
    cancelled = (
        await session.execute(
            select(
                cancellations.c.class_session_id,
                cancellations.c.tutor_user_id,
                cancellations.c.kind,
                cancellations.c.starts_at,
                cancellations.c.classification,
            ).where(
                payroll.has_no_session_line(cancellations.c.class_session_id),
                payroll.has_no_adjustment(cancellations.c.class_session_id),
            )
        )
    ).all()
    first_dates = await first_session_dates(
        session,
        [row.class_session_id for row in approved if row.session_kind == COURSE],
        zone,
    )
    return [
        *(
            payroll.UnresolvedCandidate(
                class_session_id=row.class_session_id,
                tutor_user_id=row.tutor_user_id,
                class_kind=row.session_kind,
                service_date=local_date(row.session_starts_at, zone),
                source="closeout",
                first_session_date=first_dates.get(row.class_session_id),
            )
            for row in approved
        ),
        *(
            payroll.UnresolvedCandidate(
                class_session_id=row.class_session_id,
                tutor_user_id=row.tutor_user_id,
                class_kind=row.kind,
                service_date=local_date(row.starts_at, zone),
                source="cancellation",
                classification=row.classification,
            )
            for row in cancelled
        ),
    ]


async def _unresolved_items(
    session: AsyncSession, tutor_user_id: uuid.UUID | None
) -> list[payroll.UnresolvedItem]:
    """Every tutor's items, or one's, in no order. Empty while payroll is off.
    A tutor is whoever has a candidate, with a terms row or not: a substitute
    may otherwise be paid nothing (#443 C3)."""
    floor = await payroll.payroll_starts_on(session)
    if floor is None:
        return []
    zone = await administration.get_center_time_zone(session)
    candidates = await _unresolved_candidates(session, tutor_user_id, floor, zone)
    by_tutor: dict[uuid.UUID, list[payroll.UnresolvedCandidate]] = {}
    for candidate in candidates:
        by_tutor.setdefault(candidate.tutor_user_id, []).append(candidate)
    terms = await payroll.terms_basis_versions_of_tutors(session, by_tutor)
    rates = await payroll.rate_versions_of_tutors(session, by_tutor)
    covers = await salary_covers(session, terms)
    first_periods = await payroll.first_period_starts(session, by_tutor)
    rules = await payroll.session_pay_rule_versions(session, lock=False)
    return [
        item
        for tutor, work in by_tutor.items()
        for item in payroll.classify_unresolved(
            payroll.PayBasis(
                terms=terms.get(tutor, []),
                rates=rates.get(tutor, []),
                rules=rules,
                salary_covers=covers.get(tutor, []),
            ),
            work,
            first_periods.get(tutor),
        )
    ]


async def salary_covers(
    session: AsyncSession,
    terms: Mapping[uuid.UUID, Sequence[payroll.TermsBasisVersion]],
) -> dict[uuid.UUID, list[payroll.SalaryCover]]:
    """The finalized salary dates (#111 E4) of the tutors with a salary on some
    terms version: nobody else has a salary line. One statement, none without a
    salaried tutor."""
    return await payroll.salary_covers_of_tutors(
        session,
        {
            tutor
            for tutor, versions in terms.items()
            if any(version.salary is not None for version in versions)
        },
    )


def _newest_first(item: payroll.UnresolvedItem) -> tuple[int, uuid.UUID]:
    """Newest service date first, then session."""
    return -item.service_date.toordinal(), item.class_session_id


async def unresolved_work(
    session: AsyncSession, tutor_user_id: uuid.UUID, through: date | None
) -> list[payroll.UnresolvedItem]:
    """The tutor's items dated on or before ``through``, if given (#443 B1),
    newest first."""
    items = await _unresolved_items(session, tutor_user_id)
    return sorted(
        (item for item in items if through is None or item.service_date <= through),
        key=_newest_first,
    )


async def blocking_work(
    session: AsyncSession, period: PayrollPeriodSummary
) -> list[payroll.UnresolvedItem]:
    """What stops ``prepare`` and ``finalize`` of this period (#107 E12, #443
    B2): the tutor's items with a blocking reason dated on or before
    ``ends_on``. Earlier items count, in whatever status their own period is.
    :mod:`app.workflows.payroll_workflow` turns a result into ``409
    payroll_unresolved_work``.

    **The contract (#443 D9, #110 B2, D2):** ``prepare`` runs :func:`recalculate`
    first, then this. An existing line keeps work off the list, so a line that a
    recalculation would drop isn't an item until it has run. ``finalize`` calls
    this again: work approved after ``prepare`` with no basis has no draft and no
    line, so the drift comparison can't see it.

    **No lock (#443 D3):** a rate delete or a stop that commits after this check
    is a later change, for drift or #111."""
    items = await unresolved_work(session, period.tutor_user_id, period.ends_on)
    return [item for item in items if item.blocking]


def _position(cursor: str) -> tuple[int, uuid.UUID]:
    service_date, class_session_id = decode_cursor(cursor, types=(str, str))
    try:
        return (
            -date.fromisoformat(str(service_date)).toordinal(),
            uuid.UUID(str(class_session_id)),
        )
    except ValueError:
        raise InvalidCursorError() from None


async def list_unresolved_work(
    session: AsyncSession,
    *,
    page: CursorParams,
    tutor_user_id: uuid.UUID | None = None,
    reason: UnresolvedReason | None = None,
) -> CursorPage[UnresolvedWorkItem]:
    """``GET /api/v1/payroll/unresolved-work`` (#443 C). Filters and the cursor
    apply after the classification, because the reason isn't stored."""
    items = sorted(
        (
            item
            for item in await _unresolved_items(session, tutor_user_id)
            if reason is None or item.reason == reason
        ),
        key=_newest_first,
    )
    if page.cursor is not None:
        after = _position(page.cursor)
        items = [item for item in items if _newest_first(item) > after]
    built = build_cursor_page(
        items[: page.limit + 1],
        limit=page.limit,
        key=lambda item: (item.service_date.isoformat(), str(item.class_session_id)),
    )
    accounts = await identity.get_account_references(
        session, {item.tutor_user_id for item in built.items}
    )
    return CursorPage[UnresolvedWorkItem](
        items=[
            UnresolvedWorkItem(
                class_session_id=item.class_session_id,
                tutor_user_id=item.tutor_user_id,
                tutor_display_name=accounts[item.tutor_user_id].display_name,
                service_date=item.service_date,
                class_kind=cast(ClassKind, item.class_kind),
                source=item.source,
                reason=item.reason,
                blocking=item.blocking,
            )
            for item in built.items
        ],
        next_cursor=built.next_cursor,
    )
