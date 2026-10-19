"""Base for every Classora model factory (Factory Boy + Faker; spec §10).

Factories and Faker are **dev dependencies only**: nothing under ``app/``
imports them, and the runtime image does not contain them.

Locale and determinism
    Every ``factory.Faker(...)`` declaration uses the ``es_MX`` locale by
    default (no per-field ``locale=``). :func:`reseed` seeds Factory Boy's and
    Faker's shared random generator, so the same seed gives the same data. An
    autouse fixture in ``tests/conftest.py`` reseeds before every test.
    Declarations that need randomness outside Faker use
    ``factory.random.randgen``, which :func:`reseed` also seeds. Avoid values
    relative to "today": use fixed date ranges.

Persistence
    Factory Boy's ``create`` strategy cannot use an ``AsyncSession``, so
    ``FactoryCls.create()`` (and ``FactoryCls()``) raise
    :class:`FactoryCreateError`. Use ``await persist(session, FactoryCls)`` or
    ``await persist_batch(session, FactoryCls, n)``: they build, add and flush,
    and never commit (only the Unit of Work commits). ``FactoryCls.build()``
    builds without the database.

Privacy (architecture ISSUE-I7, spec §16)
    All generated data is synthetic. Emails use :func:`reserved_email`, which
    yields only the reserved domains in :data:`RESERVED_EMAIL_DOMAINS`
    (RFC 2606); never use Faker's ``email``/``free_email`` providers, which
    yield real domains such as ``gmail.com``. Factories and seeds contain no
    hard-coded real names, emails, phone numbers or identifiers, and no data
    copied from DEV or PROD. A domain factory that adds phone numbers or
    national identifiers (for example CURP or RFC) must document, next to the
    declaration, how its values are fictitious.

Types (ADR-0004)
    - Money: a ``Decimal`` with exactly 2 places (``Decimal("1250.00")``),
      never built from a ``float``: use :func:`money`.
    - Calendar values: ``datetime.date``: use :func:`calendar_date`.
    - Instants: timezone-aware UTC ``datetime``: use :func:`instant`.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import factory
import factory.random
import faker
from sqlalchemy.ext.asyncio import AsyncSession

LOCALE = "es_MX"
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

# The class-level default that factory.Faker.override_default_locale() sets;
# applies to every factory.Faker declaration that does not pass locale=.
factory.Faker._DEFAULT_LOCALE = LOCALE


class FactoryCreateError(RuntimeError):
    """``FactoryCls.create()`` was called; use :func:`persist` instead."""


def reseed(seed: int) -> None:
    """Seed Factory Boy's and Faker's random generator (reproducible data)."""
    factory.random.reseed_random(seed)


def get_faker() -> faker.Faker:
    """The Faker instance behind ``factory.Faker`` declarations (``es_MX``)."""
    instance: faker.Faker = factory.Faker._get_faker()
    return instance


class ClassoraFactory(factory.Factory):
    """Abstract base of every Classora model factory."""

    class Meta:
        abstract = True

    @classmethod
    def _create(cls, model_class: type[Any], *args: Any, **kwargs: Any) -> Any:
        raise FactoryCreateError(
            f"{cls.__name__}.create() cannot use the async session: use "
            f"`await persist(session, {cls.__name__})` or "
            f"`{cls.__name__}.build()`."
        )


async def persist(
    session: AsyncSession, factory_cls: type[ClassoraFactory], **overrides: Any
) -> Any:
    """Build one object, add it to ``session`` and flush (never commits).

    The returned object has its database-set ``id``, ``created_at`` and
    ``updated_at`` loaded.
    """
    instance = factory_cls.build(**overrides)
    session.add(instance)
    await session.flush()
    return instance


async def persist_batch(
    session: AsyncSession,
    factory_cls: type[ClassoraFactory],
    n: int,
    **overrides: Any,
) -> list[Any]:
    """Build ``n`` objects, add them to ``session`` and flush (never commits)."""
    instances: list[Any] = factory_cls.build_batch(n, **overrides)
    session.add_all(instances)
    await session.flush()
    return instances


@dataclass(frozen=True)
class GeneratorSkipped:
    """What a demo generator returns when it writes nothing on purpose.

    ``make seed-demo`` prints ``<name>: skipped (<reason>)``.
    """

    reason: str


# --- Declarations --------------------------------------------------------------


def reserved_email() -> factory.Faker:
    """An email address on a reserved domain only (RFC 2606)."""
    return factory.Faker("safe_email")


def money(low: str = "0.00", high: str = "9999.99") -> factory.LazyFunction:
    """A ``Decimal`` amount with exactly 2 places in ``[low, high]``.

    Built from an integer number of cents, never from a ``float``.
    """
    low_cents = int(Decimal(low).scaleb(2))
    high_cents = int(Decimal(high).scaleb(2))

    def _amount() -> Decimal:
        return Decimal(factory.random.randgen.randint(low_cents, high_cents)).scaleb(-2)

    return factory.LazyFunction(_amount)


def calendar_date(
    start: date = date(2024, 1, 1), end: date = date(2026, 12, 31)
) -> factory.Faker:
    """A ``datetime.date`` in ``[start, end]`` (fixed range, not relative to today)."""
    return factory.Faker("date_between_dates", date_start=start, date_end=end)


def instant(
    start: datetime = datetime(2024, 1, 1, tzinfo=UTC),
    end: datetime = datetime(2026, 12, 31, tzinfo=UTC),
) -> factory.Faker:
    """A timezone-aware UTC ``datetime`` in ``[start, end]``."""
    return factory.Faker(
        "date_time_between_dates", datetime_start=start, datetime_end=end, tzinfo=UTC
    )


__all__ = [
    "LOCALE",
    "RESERVED_EMAIL_DOMAINS",
    "ClassoraFactory",
    "FactoryCreateError",
    "GeneratorSkipped",
    "calendar_date",
    "get_faker",
    "instant",
    "money",
    "persist",
    "persist_batch",
    "reseed",
    "reserved_email",
]
