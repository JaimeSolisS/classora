"""``post_charge`` (#90 E4; C3; #86 E4, E11; J4).

- Each kind of #90 posts on the student's family account with its amounts,
  its due date (the base plus ``due_offset_days``, or the base alone with
  ``apply_family_offset=False``), ``posted_at`` from the clock and
  ``posted_by_user_id`` = the Unit of Work's actor (``NULL`` in a job).
- No family → ``StudentHasNoFamilyError``, nothing written.
- A second post of a natural key → ``created = False``, no row, no event.
- A charge keeps its account when the student moves to another family; new
  charges go to the new family's account.
- **Concurrency:** two connections post the same ``(class_session_id,
  student_id)`` for a family without an account yet: one row, both succeed,
  exactly one ``created = True``. Repeated 5 times.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.schemas import ChargeDraft
from app.modules.courses.models import CohortEnrollment
from app.modules.families.models import Family, FamilyStudent, Student
from app.modules.scheduling.models import GroupEnrollment
from tests.conftest import frozen_clock
from tests.factories import billing as factories
from tests.factories.courses import persist_course_cohort
from tests.factories.families import add_to_family, persist_student
from tests.factories.scheduling import (
    persist_class_group,
    persist_class_session,
    persist_subject,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import events, in_uow

NOW = datetime(2031, 3, 10, 18, 30, tzinfo=UTC)
DAY = date(2031, 3, 10)
HOLD_SECONDS = 0.5
REPEATS = 5


@dataclass(frozen=True)
class World:
    user: uuid.UUID
    family: uuid.UUID
    student: uuid.UUID
    level: uuid.UUID
    session: uuid.UUID
    group_enrollment: uuid.UUID
    cohort_enrollment: uuid.UUID


def _world(url: str) -> World:
    async def _make(session: AsyncSession) -> World:
        user = await factories.staff_user_id(session)
        family, [student] = await factories.family_with_students(session)
        subject = await persist_subject(session)
        class_session = await persist_class_session(session, subject_id=subject.id)
        group = await persist_class_group(session, subject_id=subject.id)
        cohort = await persist_course_cohort(session, subject_id=subject.id)
        group_enrollment = GroupEnrollment(
            group_id=group.id,
            student_id=student,
            enrolled_at=NOW - timedelta(days=30),
            enrolled_by_user_id=user,
        )
        cohort_enrollment = CohortEnrollment(
            cohort_id=cohort.id,
            student_id=student,
            enrolled_at=NOW - timedelta(days=30),
            enrolled_by_user_id=user,
        )
        session.add_all([group_enrollment, cohort_enrollment])
        await session.flush()
        return World(
            user=user,
            family=family,
            student=student,
            level=subject.education_level_id,
            session=class_session.id,
            group_enrollment=group_enrollment.id,
            cohort_enrollment=cohort_enrollment.id,
        )

    return in_uow(url, _make)


def _draft(world: World, kind: str, **overrides: Any) -> ChargeDraft:
    sources: dict[str, dict[str, Any]] = {
        "session": {"class_session_id": world.session, "scheduled_minutes": 60},
        "group_enrollment": {"group_enrollment_id": world.group_enrollment},
        "cohort_enrollment": {"cohort_enrollment_id": world.cohort_enrollment},
        "manual": {"description": "Material", "apply_family_offset": False},
    }
    values: dict[str, Any] = {
        "student_id": world.student,
        "kind": kind,
        "service_date": DAY,
        "list_amount": Money("400.00"),
        "discount_amount": Money("40.00"),
        "due_date_base": DAY + timedelta(days=7),
        "education_level_id": world.level,
        **sources[kind],
        **overrides,
    }
    return ChargeDraft(**values)


def _charge(url: str, charge_id: uuid.UUID) -> Any:
    [row] = run_sql(url, "SELECT * FROM charges WHERE id = :id", {"id": charge_id})
    return row


def _set_offset(url: str, family_id: uuid.UUID, days: int) -> uuid.UUID:
    account_id = in_uow(url, lambda s: billing.ensure_billing_account(s, family_id))
    run_sql(
        url,
        "UPDATE billing_accounts SET due_offset_days = :d WHERE id = :id",
        {"d": days, "id": account_id},
    )
    return account_id


@pytest.mark.parametrize(
    "kind", ["session", "group_enrollment", "cohort_enrollment", "manual"]
)
def test_each_kind_posts_on_the_family_account(module_seeded_url, kind):
    url = module_seeded_url
    world = _world(url)
    account_id = _set_offset(url, world.family, 10)

    with frozen_clock(NOW):
        posted = in_uow(
            url,
            lambda s: billing.post_charge(s, _draft(world, kind)),
            actor_id=world.user,
        )

    assert (posted.billing_account_id, posted.created) == (account_id, True)
    row = _charge(url, posted.charge_id)
    assert row.kind == kind
    assert row.billing_account_id == account_id
    assert row.student_id == world.student
    assert (row.list_amount, row.discount_amount, row.amount) == (
        Money("400.00").amount,
        Money("40.00").amount,
        Money("360.00").amount,
    )
    offset = 0 if kind == "manual" else 10
    assert row.due_date == DAY + timedelta(days=7 + offset)
    assert row.posted_at == NOW
    assert row.posted_by_user_id == world.user
    assert row.service_date == DAY
    assert row.education_level_id == world.level
    assert row.scheduled_minutes == (60 if kind == "session" else None)
    assert row.description == ("Material" if kind == "manual" else None)


def test_without_an_offset_the_due_date_is_the_base(module_seeded_url):
    url = module_seeded_url
    world = _world(url)

    posted = in_uow(url, lambda s: billing.post_charge(s, _draft(world, "session")))

    assert _charge(url, posted.charge_id).due_date == DAY + timedelta(days=7)


def test_apply_family_offset_false_ignores_the_offset(module_seeded_url):
    url = module_seeded_url
    world = _world(url)
    _set_offset(url, world.family, 30)

    posted = in_uow(
        url,
        lambda s: billing.post_charge(
            s, _draft(world, "group_enrollment", apply_family_offset=False)
        ),
    )

    assert _charge(url, posted.charge_id).due_date == DAY + timedelta(days=7)


def test_a_job_posts_without_an_actor(module_seeded_url):
    url = module_seeded_url
    world = _world(url)

    posted = in_uow(url, lambda s: billing.post_charge(s, _draft(world, "session")))

    assert _charge(url, posted.charge_id).posted_by_user_id is None
    [event] = events(url, record_type="charges", record_id=posted.charge_id)
    assert (event.action, event.actor_user_id) == ("create", None)


def test_a_student_without_a_family_writes_nothing(module_seeded_url):
    url = module_seeded_url

    async def _student(session: AsyncSession) -> uuid.UUID:
        return (await persist_student(session, contacts=0)).id

    student = in_uow(url, _student)

    with pytest.raises(billing.StudentHasNoFamilyError) as raised:
        in_uow(
            url,
            lambda s: billing.post_charge(s, factories.manual_draft(student)),
        )

    assert raised.value.student_id == student
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM charges WHERE student_id = :s",
            {"s": student},
        )[0].n
        == 0
    )


@pytest.mark.parametrize("kind", ["session", "group_enrollment", "cohort_enrollment"])
def test_a_natural_key_is_posted_once(module_seeded_url, kind):
    url = module_seeded_url
    world = _world(url)
    first = in_uow(url, lambda s: billing.post_charge(s, _draft(world, kind)))
    audit_before = len(events(url, record_type="charges"))

    again = in_uow(
        url,
        lambda s: billing.post_charge(
            s, _draft(world, kind, list_amount=Money("999.00"))
        ),
    )

    assert again == billing.PostedCharge(
        charge_id=first.charge_id,
        billing_account_id=first.billing_account_id,
        created=False,
    )
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM charges WHERE student_id = :s",
            {"s": world.student},
        )[0].n
        == 1
    )
    assert len(events(url, record_type="charges")) == audit_before


def test_manual_charges_have_no_natural_key(module_seeded_url):
    url = module_seeded_url
    world = _world(url)
    first = in_uow(url, lambda s: billing.post_charge(s, _draft(world, "manual")))
    second = in_uow(url, lambda s: billing.post_charge(s, _draft(world, "manual")))

    assert first.created and second.created
    assert first.charge_id != second.charge_id


def test_a_charge_keeps_its_account_when_the_student_moves(module_seeded_url):
    url = module_seeded_url
    world = _world(url)
    old = in_uow(url, lambda s: billing.post_charge(s, _draft(world, "session")))

    async def _move(session: AsyncSession) -> uuid.UUID:
        member = await session.scalar(
            select(FamilyStudent).where(FamilyStudent.student_id == world.student)
        )
        await session.delete(member)
        await session.flush()
        new_family, _ = await factories.family_with_students(session, 0)
        family = await session.get(Family, new_family)
        student = await session.get(Student, world.student)
        assert family is not None and student is not None
        await add_to_family(session, family, student)
        return new_family

    new_family = in_uow(url, _move)
    new = in_uow(url, lambda s: billing.post_charge(s, _draft(world, "manual")))

    new_account = in_uow(url, lambda s: billing.ensure_billing_account(s, new_family))
    assert new.billing_account_id == new_account != old.billing_account_id
    assert _charge(url, old.charge_id).billing_account_id == old.billing_account_id


async def _race(url: str, draft: ChargeDraft) -> list[billing.PostedCharge]:
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    flushed = asyncio.Event()

    async def run_a() -> billing.PostedCharge:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a)) as uow:
                posted = await billing.post_charge(uow.session, draft)
                flushed.set()
                await asyncio.sleep(HOLD_SECONDS)
                return posted
        finally:
            flushed.set()

    async def run_b() -> billing.PostedCharge:
        await flushed.wait()
        async with UnitOfWork(create_sessionmaker(engine_b)) as uow:
            return await billing.post_charge(uow.session, draft)

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_connections_posting_one_session_charge_store_one_row(
    module_seeded_url, attempt
):
    url = module_seeded_url
    world = _world(url)

    first, second = asyncio.run(_race(url, _draft(world, "session")))

    assert first.charge_id == second.charge_id
    assert first.billing_account_id == second.billing_account_id
    assert sorted([first.created, second.created]) == [False, True]
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM charges WHERE student_id = :s",
            {"s": world.student},
        )[0].n
        == 1
    )
    assert len(events(url, record_type="charges", record_id=first.charge_id)) == 1
