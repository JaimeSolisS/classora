"""Fixtures of the #126 KPI tests: the worked examples B1-B5 of
``_docs/kpis.md``, each built on the database it is given.

Charges, payments, reversals, refunds and expenses go through their owners'
service functions (``post_charge``, ``record_payment``, ``reverse_payment``,
``refund_payment``, ``insert_expense`` and the expense payment). Sessions,
participants, attendance, rooms, closures and enrollments are model rows
(the ``tests.factories`` way). Payroll periods, closeout approvals, lines,
adjustments and payments are rows too, by SQL where the tables are
append-only, as the #106-#112 tests do (the KPIs only read them). Each
builder runs under a frozen clock at its example's ``now``.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.academics.models import SessionCloseout, SessionCloseoutApproval
from app.modules.billing import service as billing
from app.modules.billing.models import TuitionPlan
from app.modules.billing.schemas import AllocationRequest, ChargeDraft, RefundDraft
from app.modules.catalog.models import Classroom, ClassroomAvailabilityWindow, Closure
from app.modules.courses.models import CohortEnrollment
from app.modules.finance import service as finance
from app.modules.finance.schemas import ExpensePaymentCreate
from app.modules.payroll.models import PayrollAdjustment, PayrollPeriod
from app.modules.scheduling.models import ClassSession, ClassSessionAttendance
from tests.conftest import frozen_clock
from tests.factories.billing import persist_service_price
from tests.factories.courses import persist_course_cohort
from tests.factories.families import persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration import receipt_support as rs
from tests.integration.conftest import run_sql
from tests.integration.expense_support import category_of
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")


def d(text_: str) -> date:
    return date.fromisoformat(text_)


def local(day: date | str, hour: int = 10, minute: int = 0, zone: ZoneInfo = ZONE):
    """The UTC instant of a local wall-clock time."""
    if isinstance(day, str):
        day = d(day)
    return datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)


def noon(day: str) -> datetime:
    """Noon local of ``day``: the frozen ``now`` of an example."""
    return local(day, 12)


# --- Rows ---------------------------------------------------------------------------


async def user(session: AsyncSession, name: str) -> uuid.UUID:
    made = await persist_user(session, display_name=name)
    return made.id


async def class_session(
    session: AsyncSession,
    *,
    starts_at: datetime,
    minutes: int = 60,
    state: str = "completed",
    tutor: uuid.UUID | None = None,
    students: list[uuid.UUID] | tuple[uuid.UUID, ...] = (),
    confirmed_before: bool = True,
    **extra: Any,
) -> ClassSession:
    """A session (a new tutor unless given) with its participants. A
    ``cancelled`` one is cancelled after a confirmation unless
    ``confirmed_before=False``."""
    if tutor is not None:
        extra["tutor_user_id"] = tutor
    if state == "cancelled":
        extra["first_confirmed_at"] = (
            starts_at - timedelta(days=7) if confirmed_before else None
        )
        extra.setdefault("cancelled_at", starts_at - timedelta(days=1))
    made = await persist_class_session(
        session,
        state=state,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
        **extra,
    )
    for student in students:
        await add_participant(session, made, student)
    return made


async def mark(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    student: uuid.UUID,
    status: str,
    by: uuid.UUID,
    at: datetime,
) -> None:
    session.add(
        ClassSessionAttendance(
            class_session_id=class_session_id,
            student_id=student,
            status=status,
            marked_by_user_id=by,
            marked_at=at,
        )
    )
    await session.flush()


async def approval(
    session: AsyncSession, made: ClassSession, approver: uuid.UUID
) -> uuid.UUID:
    """An approved closeout of ``made`` (revision 1); returns the approval."""
    assert made.tutor_user_id is not None
    at = made.ends_at + timedelta(hours=1)
    closeout = SessionCloseout(
        class_session_id=made.id,
        tutor_user_id=made.tutor_user_id,
        status="submitted",
        submitted_at=at,
        last_submitted_at=at,
    )
    session.add(closeout)
    await session.flush()
    approved = SessionCloseoutApproval(
        closeout_id=closeout.id,
        revision=1,
        source="review",
        class_session_id=made.id,
        tutor_user_id=made.tutor_user_id,
        session_starts_at=made.starts_at,
        session_kind=made.kind,
        scheduled_minutes=made.scheduled_minutes,
        approved_minutes=made.scheduled_minutes,
        attendance={},
        approved_by_user_id=approver,
        approved_at=at,
    )
    session.add(approved)
    await session.flush()
    closeout.status = "approved"
    closeout.current_approval_id = approved.id
    await session.flush()
    return approved.id


async def period(
    session: AsyncSession,
    tutor: uuid.UUID,
    starts: date,
    ends: date,
    *,
    first: bool = True,
) -> uuid.UUID:
    """A monthly period (``first=False``: it follows the tutor's period that
    ends the day before)."""
    made = PayrollPeriod(
        tutor_user_id=tutor,
        starts_on=starts,
        ends_on=ends,
        pay_frequency="monthly",
        is_first_period=first,
    )
    session.add(made)
    await session.flush()
    return made.id


async def session_line(
    session: AsyncSession,
    period_id: uuid.UUID,
    made: ClassSession,
    amount: str,
    approver: uuid.UUID,
) -> None:
    """A ``session`` line of ``made`` on its local date, with a real
    approval."""
    approval_id = await approval(session, made, approver)
    await session.execute(
        text(
            "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind,"
            " amount, class_session_id, approval_id, service_date, class_kind,"
            " minutes) VALUES (:p, :u, 'session', :amount, :s, :a, :on, :kind,"
            " :minutes)"
        ),
        {
            "p": period_id,
            "u": made.tutor_user_id,
            "amount": Money(amount).amount,
            "s": made.id,
            "a": approval_id,
            "on": made.starts_at.astimezone(ZONE).date(),
            "kind": made.kind,
            "minutes": made.scheduled_minutes,
        },
    )


async def late_approval(
    session: AsyncSession,
    target: uuid.UUID,
    origin: uuid.UUID,
    made: ClassSession,
    amount: str,
) -> None:
    """A ``late_approval`` adjustment of ``made`` (from ``origin``) and its
    line in ``target``."""
    adjustment = PayrollAdjustment(
        kind="late_approval",
        tutor_user_id=made.tutor_user_id,
        target_period_id=target,
        amount=Money(amount),
        class_session_id=made.id,
        origin_period_id=origin,
        source_key=f"cancellation:{made.id}",
        line_kind="session",
        service_date=made.starts_at.astimezone(ZONE).date(),
        class_kind=made.kind,
        minutes=made.scheduled_minutes,
    )
    session.add(adjustment)
    await session.flush()
    await session.execute(
        text(
            "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind,"
            " amount, payroll_adjustment_id) VALUES (:p, :u, 'adjustment',"
            " :amount, :a)"
        ),
        {
            "p": target,
            "u": made.tutor_user_id,
            "amount": Money(amount).amount,
            "a": adjustment.id,
        },
    )


async def finalize(
    session: AsyncSession, period_id: uuid.UUID, total: str, by: uuid.UUID
) -> None:
    params = {"id": period_id, "by": by, "total": Money(total).amount}
    await session.execute(
        text(
            "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
            " prepared_by_user_id = :by WHERE id = :id"
        ),
        {"id": period_id, "by": by},
    )
    await session.execute(
        text(
            "UPDATE payroll_periods SET status = 'finalized', finalized_at = now(),"
            " finalized_by_user_id = :by, total_amount = :total WHERE id = :id"
        ),
        params,
    )


async def payroll_payment(
    session: AsyncSession, period_id: uuid.UUID, amount: str, on: date, by: uuid.UUID
) -> None:
    await session.execute(
        text(
            "INSERT INTO payroll_payments (payroll_period_id, paid_on, amount,"
            " method_code, reference, recorded_by_user_id) VALUES"
            " (:p, :on, :amount, 'cash', 'KPI', :u)"
        ),
        {"p": period_id, "on": on, "amount": Money(amount).amount, "u": by},
    )


def session_charge(made: ClassSession, student: uuid.UUID, amount: str) -> ChargeDraft:
    on = made.starts_at.astimezone(ZONE).date()
    return ChargeDraft(
        student_id=student,
        kind="session",
        class_session_id=made.id,
        service_date=on,
        list_amount=Money(amount),
        due_date_base=on,
        scheduled_minutes=made.scheduled_minutes,
    )


def payment(
    url: str, actor: uuid.UUID, account: uuid.UUID, amount: str, on: str
) -> uuid.UUID:
    """An unallocated payment received on ``on``."""
    draft = replace(
        rs.draft(account, amount, AllocationRequest(mode="none")), received_on=d(on)
    )
    return rs.record(url, actor, draft).payment_id


def expense(
    url: str, actor: uuid.UUID, amount: str, incurred: str, paid: str | None
) -> uuid.UUID:
    category = uuid.UUID(category_of(url, "rent"))

    async def _make(session: AsyncSession) -> uuid.UUID:
        made = await finance.insert_expense(
            session,
            category_id=category,
            vendor_id=None,
            description="Gasto KPI",
            amount=Money(amount),
            incurred_on=d(incurred),
            due_on=d(incurred),
            receipt_reference=None,
            note=None,
            created_by_user_id=actor,
        )
        if paid is not None:
            await finance.record_payment(
                session,
                actor,
                made.id,
                ExpensePaymentCreate(
                    paid_on=d(paid),
                    amount=Money(amount),
                    method_code="cash",
                    reference=None,
                ),
            )
        return made.id

    return in_uow(url, _make, actor_id=actor)


def actor(url: str) -> uuid.UUID:
    return in_uow(url, lambda s: user(s, "Dueña KPI"))


# --- B1 (K1) ------------------------------------------------------------------------

B1_NOW = noon("2031-03-12")
B1_RANGE = (d("2031-01-15"), d("2031-03-10"))


def build_b1(url: str) -> None:
    """Three 400.00 session charges (Jan 10, Jan 20, Feb 3), an expense of
    1,000.00 incurred Feb 1 and paid Feb 10, a finalized February period with
    the Feb 3 line of 250.00 paid on Mar 3, payments of 400.00 on Jan 25 and
    Mar 2 and a reversed one on Feb 5, and a refund of 100.00 on Mar 5."""
    with frozen_clock(B1_NOW):
        who = actor(url)
        _, account, [student] = rs.new_account(url)

        async def _sessions(session: AsyncSession) -> None:
            subject = await persist_subject(session)
            tutor = await user(session, "Tutora Tendencia")
            made = []
            for day in ("2031-01-10", "2031-01-20", "2031-02-03"):
                one = await class_session(
                    session,
                    starts_at=local(day),
                    tutor=tutor,
                    subject_id=subject.id,
                    students=[student],
                )
                await billing.post_charge(
                    session, session_charge(one, student, "400.00")
                )
                made.append(one)
            february = await period(session, tutor, d("2031-02-01"), d("2031-02-28"))
            await session_line(session, february, made[2], "250.00", who)
            await finalize(session, february, "250.00", who)
            await payroll_payment(session, february, "250.00", d("2031-03-03"), who)

        in_uow(url, _sessions, actor_id=who)
        expense(url, who, "1000.00", "2031-02-01", "2031-02-10")
        payment(url, who, account, "400.00", "2031-01-25")
        later = payment(url, who, account, "400.00", "2031-03-02")
        reversed_ = payment(url, who, account, "400.00", "2031-02-05")
        in_uow(
            url,
            lambda s: billing.reverse_payment(s, reversed_, "Pago duplicado"),
            actor_id=who,
        )
        refund = RefundDraft(
            payment_id=later,
            amount=Money("100.00"),
            refunded_on=d("2031-03-05"),
            payment_method_code="cash",
            reason="Devolución",
        )
        in_uow(url, lambda s: billing.refund_payment(s, refund), actor_id=who)


# --- B2 (K2) ------------------------------------------------------------------------

B2_NOW = noon("2031-04-02")
B2_RANGE = (d("2031-03-01"), d("2031-03-31"))


@dataclass
class B2:
    c1: uuid.UUID
    c2: uuid.UUID
    c1_name: str
    c2_name: str


def build_b2(url: str, suffix: str = "") -> B2:
    """C1 (3,000.00 over 2 Feb + 2 Mar + 2 Apr occurrences; the two March
    sessions at 300.00 in a finalized March period, plus a late approval of
    150.00 for a February one), C2 (1,000.00 over Mar 10, Mar 17, Apr 20; no
    payroll) and a tuition-plan charge of 800.00 priced as a course on
    Mar 15."""
    with frozen_clock(B2_NOW):
        who = actor(url)
        _, account, [s1, s2, s3] = rs.new_account(url, 3)

        async def _make(session: AsyncSession) -> B2:
            subject = await persist_subject(session)
            c1 = await persist_course_cohort(session, subject_id=subject.id)
            c2 = await persist_course_cohort(session, subject_id=subject.id)
            c1.name = f"Curso Uno {suffix}{uuid.uuid4().hex[:6]}"
            c2.name = f"Curso Dos {suffix}{uuid.uuid4().hex[:6]}"
            t1 = await user(session, "Tutor Curso Uno")
            t2 = await user(session, "Tutor Curso Dos")
            sessions = []
            for day in (
                "2031-02-10",
                "2031-02-17",
                "2031-03-03",
                "2031-03-24",
                "2031-04-07",
                "2031-04-21",
            ):
                sessions.append(
                    await class_session(
                        session,
                        starts_at=local(day),
                        state="completed" if day < "2031-04-02" else "confirmed",
                        tutor=t1,
                        students=[s1],
                        kind="course",
                        cohort_id=c1.id,
                        subject_id=subject.id,
                    )
                )
            for day in ("2031-03-10", "2031-03-17", "2031-04-20"):
                await class_session(
                    session,
                    starts_at=local(day),
                    state="completed" if day < "2031-04-02" else "confirmed",
                    tutor=t2,
                    students=[s2],
                    kind="course",
                    cohort_id=c2.id,
                    subject_id=subject.id,
                )
            for cohort, student, fee, on in (
                (c1, s1, "3000.00", "2031-02-01"),
                (c2, s2, "1000.00", "2031-03-01"),
            ):
                enrollment = CohortEnrollment(
                    cohort_id=cohort.id,
                    student_id=student,
                    enrolled_at=local(on, 9),
                    enrolled_by_user_id=who,
                )
                session.add(enrollment)
                await session.flush()
                await billing.post_charge(
                    session,
                    ChargeDraft(
                        student_id=student,
                        kind="cohort_enrollment",
                        cohort_enrollment_id=enrollment.id,
                        service_date=d(on),
                        list_amount=Money(fee),
                        due_date_base=d(on),
                    ),
                )
            price = await persist_service_price(
                session,
                subject_id=subject.id,
                class_type="course",
                pricing_model="full_course",
                versions=[(d("2031-01-01"), "800.00")],
            )
            plan = TuitionPlan(
                billing_account_id=account,
                student_id=s3,
                service_price_id=price.id,
                frequency="monthly",
                starts_on=d("2031-03-15"),
            )
            session.add(plan)
            await session.flush()
            await billing.post_charge(
                session,
                ChargeDraft(
                    student_id=s3,
                    kind="tuition_period",
                    tuition_plan_id=plan.id,
                    service_date=d("2031-03-15"),
                    service_period_end=d("2031-04-14"),
                    list_amount=Money("800.00"),
                    due_date_base=d("2031-03-15"),
                ),
            )
            february = await period(session, t1, d("2031-02-01"), d("2031-02-28"))
            march = await period(
                session, t1, d("2031-03-01"), d("2031-03-31"), first=False
            )
            for one in sessions[2:4]:
                await session_line(session, march, one, "300.00", who)
            await late_approval(session, march, february, sessions[0], "150.00")
            await finalize(session, march, "750.00", who)
            return B2(c1=c1.id, c2=c2.id, c1_name=c1.name, c2_name=c2.name)

        return in_uow(url, _make, actor_id=who)


# --- B3 (K4) ------------------------------------------------------------------------

B3_NOW = noon("2031-03-10")
B3_RANGE = (d("2031-03-03"), d("2031-03-09"))


def set_hours(url: str, hours: dict[int, tuple[str, str]]) -> None:
    run_sql(url, "DELETE FROM operating_hours")
    for weekday, (opens, closes) in hours.items():
        run_sql(
            url,
            "INSERT INTO operating_hours (weekday, opens_at, closes_at)"
            " VALUES (:w, :o, :c)",
            {
                "w": weekday,
                "o": time.fromisoformat(opens),
                "c": time.fromisoformat(closes),
            },
        )


B3_HOURS = {
    **{weekday: ("09:00", "21:00") for weekday in range(1, 6)},
    6: ("09:00", "14:00"),
}


async def room(
    session: AsyncSession,
    name: str,
    windows: list[tuple[int, str, str]] | tuple[()] = (),
    *,
    active: bool = True,
) -> uuid.UUID:
    made = Classroom(
        name=f"{name} {uuid.uuid4().hex[:6]}", capacity=10, is_active=active
    )
    session.add(made)
    await session.flush()
    for weekday, starts, ends in windows:
        session.add(
            ClassroomAvailabilityWindow(
                classroom_id=made.id,
                weekday=weekday,
                starts_at=time.fromisoformat(starts),
                ends_at=time.fromisoformat(ends),
            )
        )
    await session.flush()
    return made.id


async def closure(session: AsyncSession, on: str, *, active: bool = True) -> None:
    session.add(
        Closure(
            name=f"Cierre {on}",
            closure_type="holiday",
            starts_on=d(on),
            ends_on=d(on),
            is_active=active,
        )
    )
    await session.flush()


@dataclass
class B3:
    a: uuid.UUID
    b: uuid.UUID
    c: uuid.UUID


def build_b3(url: str, *, hours: bool = True) -> B3:
    """Rooms A (no windows), B (Mon 16-20, Sat 08-10) and inactive C; a
    closure on Wed Mar 5 and an inactive one on Thu Mar 6; the sessions of
    the worked example."""
    if hours:
        set_hours(url, B3_HOURS)
    with frozen_clock(B3_NOW):

        async def _make(session: AsyncSession) -> B3:
            await closure(session, "2031-03-05")
            await closure(session, "2031-03-06", active=False)
            a = await room(session, "Aula A")
            b = await room(
                session, "Aula B", [(1, "16:00", "20:00"), (6, "08:00", "10:00")]
            )
            c = await room(session, "Aula C", active=False)
            for starts_at, minutes, state, where in (
                (local("2031-03-03"), 60, "completed", a),
                (local("2031-03-04"), 60, "completed", a),
                (local("2031-03-06"), 90, "confirmed", a),
                (local("2031-03-07"), 60, "cancelled", a),
                (local("2031-03-02", 23), 60, "completed", a),
                (local("2031-03-03", 16), 120, "completed", b),
                (local("2031-03-03"), 60, "completed", c),
            ):
                await class_session(
                    session,
                    starts_at=starts_at,
                    minutes=minutes,
                    state=state,
                    classroom_id=where,
                )
            return B3(a=a, b=b, c=c)

        return in_uow(url, _make)


# --- B4 (K5) ------------------------------------------------------------------------

B4_NOW = noon("2031-04-02")
B4_RANGE = (d("2031-03-01"), d("2031-03-31"))


def build_b4(url: str) -> list[uuid.UUID]:
    """S1-S8 of the worked example; returns their ids, in order."""
    with frozen_clock(B4_NOW):

        async def _make(session: AsyncSession) -> list[uuid.UUID]:
            students = [
                (await persist_student(session, contacts=0)).id for _ in range(8)
            ]
            marker = await user(session, "Marcadora")
            plan: list[tuple[int, datetime, str, str | None]] = [
                (0, local("2031-02-10"), "completed", None),
                (0, local("2031-03-05"), "completed", None),
                (1, local("2031-02-20"), "completed", None),
                (2, local("2031-03-12"), "completed", None),
                (3, local("2031-02-02"), "completed", None),
                (3, local("2031-03-20"), "cancelled", None),
                (4, local("2031-01-28"), "completed", None),
                (4, local("2031-03-03"), "completed", None),
                (5, local("2031-02-15"), "completed", "absent"),
                (5, local("2031-03-07"), "completed", "absent"),
                (6, local("2031-03-31", 23, 30), "completed", None),
                (7, local("2031-02-28", 23, 30), "completed", None),
            ]
            for index, starts_at, state, status in plan:
                made = await class_session(
                    session,
                    starts_at=starts_at,
                    state=state,
                    students=[students[index]],
                )
                if status is not None:
                    await mark(
                        session, made.id, students[index], status, marker, starts_at
                    )
            return students

        return in_uow(url, _make)


# --- B5 (K6/K7) ---------------------------------------------------------------------

B5_NOW = noon("2031-04-01")
B5_RANGE = (d("2031-03-01"), d("2031-03-31"))


@dataclass
class B5:
    t1: uuid.UUID
    t2: uuid.UUID
    students: list[uuid.UUID] = field(default_factory=list)


def build_b5(url: str, suffix: str = "") -> B5:
    """T1's completed individual (S1 present) and group (S2 present, S3
    late, S4 absent) sessions, an ended confirmed one (S2 not recorded), a
    cancellation after confirmation, one never confirmed, and the session
    substituted to T2 (S5 present)."""
    with frozen_clock(B5_NOW):

        async def _make(session: AsyncSession) -> B5:
            t1 = await user(session, f"Ana Tutora {suffix}".strip())
            t2 = await user(session, f"Beto Tutor {suffix}".strip())
            s = [(await persist_student(session, contacts=0)).id for _ in range(5)]
            one = await class_session(
                session, starts_at=local("2031-03-03"), tutor=t1, students=[s[0]]
            )
            await mark(session, one.id, s[0], "present", t1, one.ends_at)
            group = await class_session(
                session,
                starts_at=local("2031-03-05"),
                minutes=90,
                tutor=t1,
                kind="group",
                students=[s[1], s[2], s[3]],
            )
            for student, status in (
                (s[1], "present"),
                (s[2], "late"),
                (s[3], "absent"),
            ):
                await mark(session, group.id, student, status, t1, group.ends_at)
            await class_session(
                session,
                starts_at=local("2031-03-10"),
                state="confirmed",
                tutor=t1,
                students=[s[1]],
            )
            await class_session(
                session, starts_at=local("2031-03-12"), state="cancelled", tutor=t1
            )
            await class_session(
                session,
                starts_at=local("2031-03-14"),
                state="cancelled",
                tutor=t1,
                confirmed_before=False,
            )
            # The session T1 was assigned and T2 now teaches (the current
            # tutor is credited).
            substituted = await class_session(
                session, starts_at=local("2031-03-17"), tutor=t2, students=[s[4]]
            )
            await mark(
                session, substituted.id, s[4], "present", t2, substituted.ends_at
            )
            return B5(t1=t1, t2=t2, students=s)

        return in_uow(url, _make)
