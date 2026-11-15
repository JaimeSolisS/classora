"""Shared helpers of the package tests (#94 G1-G9).

**Isolation.** The session sweeper and the expiry job read **every**
candidate of the module database. :func:`reset` runs first in each test:
the #91 reset (billing off, earlier uncharged billable sessions cancelled)
and every earlier package with units left expired (raw ``expire`` rows, as a
fixture may: the append-only triggers refuse only ``UPDATE``/``DELETE``),
so a run's counts are the test's own.

**Dates** are center-local (Mexico City, UTC-6, no DST). Sales go through
the purchase workflow under a frozen clock (:func:`sell`), so
``purchased_on`` is the given day.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.money import Money
from app.modules.academics import service as academics
from app.modules.billing import service as billing
from app.modules.identity.permissions import OWNER, ROLE_PERMISSIONS, TUTOR
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import AttendanceMark
from app.wiring import register_approval_wiring
from app.workflows import package_expiry, packages, session_closeout
from app.workflows import session_closeout_review as review
from app.workflows.closeout_approval import on_closeout_approved
from app.workflows.package_schemas import PackagePurchase
from app.workflows.session_closeout_schemas import SessionCloseoutSubmit
from tests.conftest import frozen_clock
from tests.factories.billing import persist_service_price
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import persist_subject
from tests.integration import session_charge_support as scs
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import in_uow

ZONE = scs.ZONE
PRODUCTS = "/api/v1/billing/package-products"
PACKAGES = "/api/v1/billing/packages"
PRICE_FROM = date(2020, 1, 1)
SESSION_PRICE = "250.00"
SUMMARY = {"topics": "Fracciones", "performance": "Bien", "next_steps": "Decimales"}


def local(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return scs.local(day, hour, minute)


def reset(url: str) -> None:
    """The #91 reset, then every earlier package with units left expired."""
    scs.reset(url)
    run_sql(
        url,
        "INSERT INTO package_credit_transactions (package_id, kind, units)"
        " SELECT p.id, 'expire', -r.remaining FROM packages p"
        " JOIN (SELECT package_id, sum(units) AS remaining"
        "  FROM package_credit_transactions GROUP BY package_id) r"
        "  ON r.package_id = p.id"
        " WHERE r.remaining > 0 AND NOT EXISTS ("
        "  SELECT 1 FROM package_credit_transactions e"
        "  WHERE e.package_id = p.id AND e.kind = 'expire')",
    )


@dataclass(frozen=True)
class World:
    """A student (with the subject's level) in a family, the subject, and a
    ``per_session`` price for it (so the sweeper can charge)."""

    student: uuid.UUID
    family: uuid.UUID
    subject: uuid.UUID
    level: uuid.UUID


async def new_family(session: AsyncSession, *students: Any) -> uuid.UUID:
    family = FamilyFactory.build()
    session.add(family)
    await session.flush()
    await add_to_family(session, family, *students)
    return family.id


def world(url: str, *, session_price: str | None = SESSION_PRICE) -> World:
    async def _make(session: AsyncSession) -> World:
        subject = await persist_subject(session)
        if session_price is not None:
            await persist_service_price(
                session, subject_id=subject.id, versions=[(PRICE_FROM, session_price)]
            )
        student = await persist_student(
            session, contacts=0, education_level_id=subject.education_level_id
        )
        family = await new_family(session, student)
        return World(
            student=student.id,
            family=family,
            subject=subject.id,
            level=subject.education_level_id,
        )

    return in_uow(url, _make)


def sibling(url: str, w: World) -> uuid.UUID:
    """Another student in ``w``'s family (the subject's level)."""

    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Family

        found = await session.get(Family, w.family)
        assert found is not None
        student = await persist_student(session, contacts=0, education_level_id=w.level)
        await add_to_family(session, found, student)
        return student.id

    return in_uow(url, _add)


def move_to_new_family(url: str, student: uuid.UUID) -> uuid.UUID:
    run_sql(url, "DELETE FROM family_students WHERE student_id = :s", {"s": student})

    async def _add(session: AsyncSession) -> uuid.UUID:
        from app.modules.families.models import Student

        loaded = await session.get(Student, student)
        assert loaded is not None
        return await new_family(session, loaded)

    return in_uow(url, _add)


def product(
    url: str,
    *,
    unit: str = "sessions",
    quantity: int = 10,
    validity_days: int = 90,
    subject: uuid.UUID | None = None,
    amount: str = "1000.00",
    price_from: date = PRICE_FROM,
    name: str | None = None,
) -> uuid.UUID:
    """A product written through the billing layer (no route rules)."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        return await billing.create_product(
            session,
            name=name or f"Paquete {uuid.uuid4().hex[:6]}",
            unit=unit,  # type: ignore[arg-type]
            quantity=quantity,
            validity_days=validity_days,
            subject_id=subject,
            amount=Money(amount),
            effective_from=price_from,
        )

    return in_uow(url, _make)


def service_price_of(url: str, product_id: uuid.UUID) -> uuid.UUID:
    [row] = run_sql(
        url,
        "SELECT service_price_id FROM package_products WHERE id = :p",
        {"p": product_id},
    )
    return uuid.UUID(str(row.service_price_id))


def sell(url: str, student: uuid.UUID, product_id: uuid.UUID, on: date) -> uuid.UUID:
    """A purchase through the workflow, with the clock on ``on`` (noon)."""
    with frozen_clock(local(on, 12)):
        detail = in_uow(
            url,
            lambda s: packages.purchase_package(
                s, PackagePurchase(student_id=student, package_product_id=product_id)
            ),
        )
    return detail.id


def session_for(
    url: str,
    w: World,
    day: date,
    *,
    minutes: int = 60,
    state: str = "confirmed",
    kind: str = "individual",
    student: uuid.UUID | None = None,
    subject: uuid.UUID | None = None,
) -> uuid.UUID:
    """One session of ``w``'s subject (or ``subject``) on ``day`` at 10:00
    with ``w.student`` (or ``student``) as its participant."""
    made = scs.item(
        url,
        starts_at=local(day),
        minutes=minutes,
        state=state,
        kind=kind,
        family=False,
        subject=subject or w.subject,
        price=None,
        student=student or w.student,
    )
    return made.session


def remaining(url: str, package_id: uuid.UUID) -> int:
    [row] = run_sql(
        url,
        "SELECT coalesce(sum(units), 0) AS n FROM package_credit_transactions"
        " WHERE package_id = :p",
        {"p": package_id},
    )
    return int(row.n)


def transactions(url: str, package_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM package_credit_transactions WHERE package_id = :p"
        " ORDER BY created_at, id",
        {"p": package_id},
    )


def consumes(url: str, class_session: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM package_credit_transactions WHERE kind = 'consume'"
        " AND class_session_id = :s ORDER BY created_at, id",
        {"s": class_session},
    )


def session_charges(url: str, class_session: uuid.UUID) -> list[Any]:
    return scs.charges_of(url, class_session)


def consume(
    url: str, class_session: uuid.UUID, student: uuid.UUID
) -> billing.ConsumeResult:
    """``consume_session_credit`` for the session as it is stored."""

    async def _run(session: AsyncSession) -> billing.ConsumeResult:
        locked = await scheduling.lock_session_for_billing(session, class_session)
        assert locked is not None
        return await billing.consume_session_credit(
            session,
            billing.SessionCreditClaim(
                class_session_id=locked.id,
                student_id=student,
                subject_id=locked.subject_id,
                service_date=locked.starts_at.astimezone(ZONE).date(),
                scheduled_minutes=locked.scheduled_minutes,
            ),
        )

    return in_uow(url, _run)


def hook(
    url: str,
    class_session: uuid.UUID,
    *,
    supersedes: uuid.UUID | None = None,
) -> None:
    """``on_closeout_approved`` alone (no approval row), as revision 1 or as
    a later revision when ``supersedes`` is given."""

    async def _run(session: AsyncSession) -> None:
        await on_closeout_approved(
            session,
            approval_id=uuid.uuid7(),
            supersedes_approval_id=supersedes,
            class_session_id=class_session,
        )

    in_uow(url, _run)


def _principal(user_id: uuid.UUID, role: str) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


@dataclass(frozen=True)
class Closed:
    """A session closed out by its tutor (submitted), with a reviewer."""

    session: uuid.UUID
    reviewer: uuid.UUID
    closeout_version: int


def submit(
    url: str, class_session: uuid.UUID, student: uuid.UUID, *, status: str = "present"
) -> Closed:
    """The tutor marks the participant (``present`` by default) and submits
    the closeout (the clock after the session's end)."""
    register_approval_wiring()
    [row] = run_sql(
        url,
        "SELECT tutor_user_id, starts_at, ends_at, version FROM class_sessions"
        " WHERE id = :s",
        {"s": class_session},
    )
    tutor = uuid.UUID(str(row.tutor_user_id))
    after = row.ends_at.astimezone(UTC) + timedelta(minutes=5)
    with frozen_clock(after):

        async def _mark(session: AsyncSession) -> None:
            await scheduling.record_attendance(
                session,
                actor=_principal(tutor, TUTOR),
                class_session_id=class_session,
                version=row.version,
                marks=[AttendanceMark(student_id=student, status=status)],
            )

        in_uow(url, _mark)

        async def _submit(session: AsyncSession) -> None:
            await session_closeout.submit_session_closeout(
                session,
                principal=_principal(tutor, TUTOR),
                class_session_id=class_session,
                body=SessionCloseoutSubmit.model_validate(
                    {
                        "version": row.version + 1,
                        "actual_minutes": 50,
                        "summary": SUMMARY,
                    }
                ),
            )

        in_uow(url, _submit)

    async def _reviewer(session: AsyncSession) -> uuid.UUID:
        return (await persist_user(session, roles=(OWNER,))).id

    return Closed(
        session=class_session, reviewer=in_uow(url, _reviewer), closeout_version=1
    )


def approve(url: str, closed: Closed, *, version: int | None = None) -> None:
    """The reviewer approves (revision 1 unless already approved)."""
    register_approval_wiring()

    async def _run(session: AsyncSession) -> None:
        await review.approve_closeout(
            session,
            principal=_principal(closed.reviewer, OWNER),
            class_session_id=closed.session,
            body=academics.CloseoutApprove(
                version=closed.closeout_version if version is None else version
            ),
        )

    with frozen_clock(datetime(2099, 1, 1, tzinfo=UTC)):
        in_uow(url, _run)


def correct(url: str, closed: Closed, version: int, minutes: int) -> None:
    """A staff correction of an approved closeout (a new revision)."""

    async def _run(session: AsyncSession) -> None:
        await review.correct_closeout(
            session,
            principal=_principal(closed.reviewer, OWNER),
            class_session_id=closed.session,
            body=academics.CloseoutCorrection.model_validate(
                {"version": version, "actual_minutes": minutes, "reason": "Ajuste"}
            ),
        )

    with frozen_clock(datetime(2099, 1, 1, tzinfo=UTC)):
        in_uow(url, _run)


def closeout_version(url: str, class_session: uuid.UUID) -> int:
    [row] = run_sql(
        url,
        "SELECT version FROM session_closeouts WHERE class_session_id = :s",
        {"s": class_session},
    )
    return int(row.version)


def approved(url: str, class_session: uuid.UUID) -> None:
    """Submit and approve the closeout (revision 1)."""
    [participant] = run_sql(
        url,
        "SELECT student_id FROM class_session_participants WHERE class_session_id = :s",
        {"s": class_session},
    )
    approve(url, submit(url, class_session, uuid.UUID(str(participant.student_id))))


def expire(url: str, today: date) -> package_expiry.PackageExpiryRunResult:
    return asyncio.run(
        package_expiry.run_expire_packages(settings_for(url), now=local(today, 12))
    )


def sweep(url: str) -> Any:
    return scs.run(url)


def set_floor(url: str, floor: date | None) -> None:
    scs.set_floor(url, floor)
