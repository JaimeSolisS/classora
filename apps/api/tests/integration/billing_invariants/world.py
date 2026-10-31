"""The shared world of the billing invariant suite (#103 D) and its
databases.

:func:`build_template` creates one database per pytest session: migrated to
the Alembic head (the triggers, the ledger view and the predicates exist
only there), every reference seed applied, then the world below, built
under the clock frozen at :data:`NOW` (Sunday 2041-01-13, noon in Mexico
City). :func:`clone_database` copies it (``CREATE DATABASE … TEMPLATE``),
so every seed, race attempt and job case starts from the same world with an
empty ledger.

The world (labels in brackets are the stable names the traces use):

- billing on from :data:`FLOOR`; center zone ``America/Mexico_City``;
- three families with a guardian login each: ``F1`` (students ``A1``,
  ``A2``, so the sibling discount applies), ``F2`` (``B1``, who holds the
  scholarship) and ``F3`` (``C1``, ``C2``, ``C3``), plus ``X1``, a student
  with no family (unbilled: nothing may ever be posted for them);
- catalog prices: individual ``per_session`` for ``IND`` (250.00 any
  duration, 375.00 at 90 minutes, 200.00 at 45 minutes) and ``PKG``
  (300.00); a package product of ``PKG`` (4 sessions, 30 days, 1000.00);
  group ``G1`` (``GRP1``, 10 sessions, flat fee 2000.00); group ``G2``
  (``GRP2``, 10 sessions, 150.00 per session and a monthly ``per_period``
  item of 1200.00 for a linked tuition plan); cohort ``CO1`` (``COH``, fee
  3000.00); a monthly individual ``per_period`` item of ``TUI`` (1000.00);
- a 10 % sibling discount (every family) and a 25 % scholarship assigned
  to ``B1``;
- staff: an Owner and a Finance user (``TEST_USER_PASSWORD``), and the
  Owner is the actor of every Unit of Work.

Everything here is test support (raw rows, factories and the real routes for
groups and cohorts), never production code.
"""

import asyncio
import time as walltime
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.identity.permissions import FINANCE_STAFF, GUARDIAN, OWNER
from tests.conftest import frozen_clock
from tests.factories import billing as billing_factories
from tests.factories.base import persist
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    persist_student,
)
from tests.factories.identity import persist_user
from tests.factories.scheduling import persist_subject
from tests.integration import cohort_support, group_support
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow
from tests.integration.test_migrations import alembic_config

ZONE_NAME = "America/Mexico_City"
ZONE = ZoneInfo(ZONE_NAME)
TODAY = date(2041, 1, 13)
"""A Sunday; the next day starts the groups' and the cohort's schedules."""
NOW = datetime.combine(TODAY, time(12), tzinfo=ZONE)
MONDAY = TODAY + timedelta(days=1)
FLOOR = date(2041, 1, 1)
PRICE_FROM = date(2030, 1, 1)
PACKAGE_UNITS = 4
PACKAGE_VALIDITY_DAYS = 30


def local(day: date, hour: int = 10, minute: int = 0) -> datetime:
    """The UTC instant of a center-local wall time."""
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


@dataclass(frozen=True)
class Person:
    id: uuid.UUID
    username: str


@dataclass(frozen=True)
class FamilyInfo:
    label: str
    id: uuid.UUID
    account: uuid.UUID
    students: tuple[str, ...]
    """Student labels."""
    guardian: Person


@dataclass(frozen=True)
class World:
    """Ids of the shared world (module docstring) and their labels."""

    owner: Person
    finance: Person
    families: dict[str, FamilyInfo]
    students: dict[str, uuid.UUID]
    subjects: dict[str, uuid.UUID]
    product: uuid.UUID
    tuition_item: uuid.UUID
    group_tuition_item: uuid.UUID
    groups: dict[str, uuid.UUID]
    cohort: uuid.UUID
    labels: dict[uuid.UUID, str] = field(default_factory=dict)

    def family_of(self, student_label: str) -> FamilyInfo | None:
        for info in self.families.values():
            if student_label in info.students:
                return info
        return None

    @property
    def billed_students(self) -> list[str]:
        return [s for info in self.families.values() for s in info.students]


@dataclass(frozen=True)
class Template:
    url: str
    world: World


# --- Databases -----------------------------------------------------------------------


def _url_of(admin_url: str, name: str) -> str:
    return make_url(admin_url).set(database=name).render_as_string(False)


def _name_of(url: str) -> str:
    name = make_url(url).database
    assert name is not None
    return name


def _terminate(admin_url: str, name: str) -> None:
    run_sql(
        admin_url,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
        " WHERE datname = :n AND pid <> pg_backend_pid()",
        {"n": name},
    )


def clone_database(admin_url: str, template_url: str) -> str:
    """A new database copied from ``template_url`` (its rate-limit counters
    emptied, so each copy has its own login budget)."""
    source = _name_of(template_url)
    name = f"binv_{uuid.uuid4().hex}"
    for attempt in range(20):
        _terminate(admin_url, source)
        try:
            run_sql(
                admin_url,
                f'CREATE DATABASE "{name}" TEMPLATE "{source}" STRATEGY FILE_COPY',
                autocommit=True,
            )
            break
        except DBAPIError as error:  # "being accessed by other users"
            if getattr(error.orig, "sqlstate", None) != "55006" or attempt == 19:
                raise
            walltime.sleep(0.1)
    url = _url_of(admin_url, name)
    run_sql(url, "DELETE FROM rate_limit_counters")
    return url


def drop_database(admin_url: str, url: str) -> None:
    run_sql(
        admin_url, f'DROP DATABASE IF EXISTS "{_name_of(url)}" WITH (FORCE)', autocommit=True
    )


def build_template(admin_url: str) -> Template:
    """The session's world template (module docstring)."""
    from alembic import command

    from app.seeds import runner

    name = f"binv_tpl_{uuid.uuid4().hex}"
    run_sql(admin_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = _url_of(admin_url, name)
    command.upgrade(alembic_config(url), "head")

    async def _seed() -> None:
        engine = runner.create_seed_engine(settings_for(url))
        try:
            await runner.run_reference_seeds(engine)
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    with frozen_clock(NOW.astimezone(UTC)):
        world = _build_world(url)
    _terminate(admin_url, name)
    return Template(url=url, world=world)


# --- The world -----------------------------------------------------------------------


def _price(session: AsyncSession, subject: uuid.UUID, amount: str, **key: Any) -> Any:
    return billing_factories.persist_service_price(
        session, subject_id=subject, versions=[(PRICE_FROM, amount)], **key
    )


def _build_world(url: str) -> World:
    run_sql(
        url,
        "UPDATE billing_settings SET billing_starts_on = :d, version = version + 1",
        {"d": FLOOR},
    )
    run_sql(url, "UPDATE business_settings SET time_zone = :z", {"z": ZONE_NAME})

    async def _rows(session: AsyncSession) -> dict[str, Any]:
        owner = await persist_user(session, roles=(OWNER,))
        finance = await persist_user(session, roles=(FINANCE_STAFF,))
        subjects = {
            code: (await persist_subject(session)).id
            for code in ("IND", "PKG", "GRP1", "GRP2", "COH", "TUI")
        }
        await _price(session, subjects["IND"], "250.00")
        await _price(session, subjects["IND"], "375.00", duration_minutes=90)
        await _price(session, subjects["IND"], "200.00", duration_minutes=45)
        await _price(session, subjects["PKG"], "300.00")
        tuition_item = (
            await _price(
                session, subjects["TUI"], "1000.00", pricing_model="per_period"
            )
        ).id
        product = await billing.create_product(
            session,
            name="Paquete de 4 sesiones",
            unit="sessions",
            quantity=PACKAGE_UNITS,
            validity_days=PACKAGE_VALIDITY_DAYS,
            subject_id=subjects["PKG"],
            amount=Money("1000.00"),
            effective_from=PRICE_FROM,
        )
        students: dict[str, uuid.UUID] = {}
        families: dict[str, FamilyInfo] = {}
        for label, members in (
            ("F1", ("A1", "A2")),
            ("F2", ("B1",)),
            ("F3", ("C1", "C2", "C3")),
        ):
            family = FamilyFactory.build()
            session.add(family)
            await session.flush()
            rows: list[Any] = []
            for member in members:
                student = await persist_student(session, contacts=0)
                students[member] = student.id
                rows.append(student)
            user = await persist_user(session, roles=(GUARDIAN,))
            rows.append(await persist(session, GuardianFactory, user_id=user.id))
            await add_to_family(session, family, *rows)
            account = await billing.ensure_billing_account(session, family.id)
            families[label] = FamilyInfo(
                label=label,
                id=family.id,
                account=account,
                students=members,
                guardian=Person(user.id, user.username),
            )
        students["X1"] = (await persist_student(session, contacts=0)).id
        await billing_factories.persist_discount(
            session, kind="sibling", name="Hermanos 10 %", percent="10"
        )
        scholarship = await billing_factories.persist_discount(
            session,
            kind="scholarship",
            audience="assigned",
            name="Beca 25 %",
            percent="25",
        )
        await billing_factories.assign_discount(
            session, scholarship.id, student_id=students["B1"]
        )
        return {
            "owner": Person(owner.id, owner.username),
            "finance": Person(finance.id, finance.username),
            "subjects": subjects,
            "students": students,
            "families": families,
            "product": product,
            "tuition_item": tuition_item,
        }

    rows = in_uow(url, _rows)
    subjects: dict[str, uuid.UUID] = rows["subjects"]

    with auth_client(url) as client:
        cookie = sign_in(client, rows["owner"].username)
        groups: dict[str, uuid.UUID] = {}
        for label, subject, weekdays in (("G1", "GRP1", [1, 3]), ("G2", "GRP2", [2, 4])):
            response = group_support.create_group(
                client,
                cookie,
                group_support.group_body(
                    subject=subjects[subject],
                    starts_on=MONDAY,
                    count=10,
                    weekdays=weekdays,
                    max_students=12,
                    name=f"Grupo {label}",
                ),
            )
            assert response.status_code == 201, response.text
            groups[label] = uuid.UUID(response.json()["id"])
        template = cohort_support.new_template(
            client, cookie, [subjects["COH"]], session_count=10
        )
        response = cohort_support.create_cohort(
            client,
            cookie,
            cohort_support.cohort_body(
                template=template["id"],
                subject=subjects["COH"],
                starts_on=MONDAY,
                weekdays=[1, 3, 5],
                name="Cohorte CO1",
            ),
        )
        assert response.status_code == 201, response.text
        cohort = uuid.UUID(response.json()["id"])

    async def _scoped(session: AsyncSession) -> uuid.UUID:
        await _price(
            session,
            subjects["GRP1"],
            "2000.00",
            class_type="group",
            pricing_model="full_course",
            group_id=groups["G1"],
        )
        await _price(
            session,
            subjects["GRP2"],
            "150.00",
            class_type="group",
            group_id=groups["G2"],
        )
        await _price(
            session,
            subjects["COH"],
            "3000.00",
            class_type="course",
            pricing_model="full_course",
            cohort_id=cohort,
        )
        item = await _price(
            session,
            subjects["GRP2"],
            "1200.00",
            class_type="group",
            pricing_model="per_period",
            group_id=groups["G2"],
        )
        return item.id

    group_tuition_item = in_uow(url, _scoped)
    labels: dict[uuid.UUID, str] = {sid: label for label, sid in rows["students"].items()}
    for info in rows["families"].values():
        labels[info.id] = info.label
        labels[info.account] = f"acct:{info.label}"
    for label, gid in groups.items():
        labels[gid] = label
        sessions = run_sql(
            url,
            "SELECT id FROM class_sessions WHERE group_id = :g ORDER BY starts_at",
            {"g": gid},
        )
        for index, row in enumerate(sessions, start=1):
            labels[row.id] = f"{label}s{index}"
    labels[cohort] = "CO1"
    return World(
        owner=rows["owner"],
        finance=rows["finance"],
        families=rows["families"],
        students=rows["students"],
        subjects=subjects,
        product=rows["product"],
        tuition_item=rows["tuition_item"],
        group_tuition_item=group_tuition_item,
        groups=groups,
        cohort=cohort,
        labels=labels,
    )
