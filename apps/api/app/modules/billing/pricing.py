"""The catalog price lookup (#89 C; #86 E9; data-model §3.5) and the
shared price calculation (#95 B2-B4; :func:`quote`, at the end).

:func:`lookup_price` answers "which catalog price applies to this service on
this local date". The billing issues that post charges call it through
``app.modules.billing.service`` (#91 sessions, #92 enrollments); #95 adds
the custom student or family price as a step **above** it (precedence step
1) without changing these ranks.

**Candidates** are the items with the query's class type, pricing model and
subject, ranked (lower wins; #89 E4):

- **0:** the item scoped to the query's cohort or group (only when the
  query has a scope; scoped items have no level and no duration);
- **1:** exact level and exact duration;
- **2:** exact level and any duration;
- **3:** any level and exact duration;
- **4:** any level and any duration.

A ``None`` query level matches only the any-level items (ranks 1 and 2 are
skipped), and a ``None`` query duration only the any-duration items (ranks
1 and 3).

**The most specific candidate that has a version in effect on ``on``
decides.** The version in effect is the one with the greatest
``effective_from <= on``; a candidate whose versions all start later (or
that has none) is skipped. If the deciding version's amount is ``NULL``
("not offered"), the result is ``None``: the lookup does **not** fall
through to a less specific item, so a mistake never charges a fallback
price silently. No candidate at all is also ``None`` (the caller's
``no_price``).

**Historical truth** (spec §6, §16): the function reads only the catalog.
A posted charge stores its ``price_version_id`` and its amount (#90, #91),
so a new version never changes a charge that was already posted.

``on`` is a local calendar date: the caller converts an instant with the
center zone (ADR-0004). One ``SELECT`` in the caller's Unit of Work; no
lock, no commit.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, case, exists, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing.discounts import (
    ChargeFacts,
    DiscountEligibility,
    DiscountKind,
    DiscountLine,
    DiscountSpec,
    matches,
    select_discounts,
    total,
)
from app.modules.billing.models import (
    CustomPrice,
    Discount,
    DiscountAssignment,
    PriceVersion,
    ServicePrice,
)
from app.modules.billing.schemas import BasePrice, PriceInEffect, PriceQuery, Quote
from app.modules.families import service as families

PACKAGE = "package"
PER_PERIOD = "per_period"


def _unscoped() -> ColumnElement[bool]:
    return and_(ServicePrice.cohort_id.is_(None), ServicePrice.group_id.is_(None))


def _rank_conditions(
    *,
    education_level_id: uuid.UUID | None,
    duration_minutes: int | None,
    cohort_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
) -> list[tuple[ColumnElement[bool], int]]:
    level, duration = ServicePrice.education_level_id, ServicePrice.duration_minutes
    ranks: list[tuple[ColumnElement[bool], int]] = []
    if cohort_id is not None:
        ranks.append((ServicePrice.cohort_id == cohort_id, 0))
    if group_id is not None:
        ranks.append((ServicePrice.group_id == group_id, 0))
    if education_level_id is not None and duration_minutes is not None:
        ranks.append(
            (
                and_(
                    _unscoped(),
                    level == education_level_id,
                    duration == duration_minutes,
                ),
                1,
            )
        )
    if education_level_id is not None:
        ranks.append(
            (and_(_unscoped(), level == education_level_id, duration.is_(None)), 2)
        )
    if duration_minutes is not None:
        ranks.append(
            (and_(_unscoped(), level.is_(None), duration == duration_minutes), 3)
        )
    ranks.append((and_(_unscoped(), level.is_(None), duration.is_(None)), 4))
    return ranks


def _check_arguments(
    *,
    class_type: str,
    pricing_model: str,
    cohort_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
    pinned: bool = False,
) -> None:
    if pricing_model == PACKAGE and not pinned:
        raise ValueError("package prices are read through their product (#94)")
    if pricing_model == PER_PERIOD and not pinned:
        raise ValueError("per_period prices are read through the plan (#93)")
    if cohort_id is not None and group_id is not None:
        raise ValueError("give a cohort_id or a group_id, not both")
    if cohort_id is not None and class_type != "course":
        raise ValueError("cohort_id is only for class_type 'course'")
    if group_id is not None and class_type != "group":
        raise ValueError("group_id is only for class_type 'group'")


async def lookup_price(
    session: AsyncSession,
    *,
    class_type: str,
    pricing_model: str,
    subject_id: uuid.UUID,
    on: date,
    education_level_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    cohort_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
) -> PriceInEffect | None:
    """The catalog price in effect on the local date ``on``, or ``None``
    (see the module docstring for the ranks and the ``NULL`` rule).

    Raises :class:`ValueError` for ``pricing_model='package'`` (read
    through the product: a pinned :func:`quote`, #94 L8) and
    ``'per_period'`` (read through the plan, #93: a plan pins its item,
    ``PriceQuery.service_price_id``; a weekly and a monthly item share
    every other key column, so a rank search could pick either), for a
    ``cohort_id`` with a class type other than ``course`` or a ``group_id``
    with one other than ``group``, and for both scopes at once. One statement."""
    _check_arguments(
        class_type=class_type,
        pricing_model=pricing_model,
        cohort_id=cohort_id,
        group_id=group_id,
    )
    ranks = _rank_conditions(
        education_level_id=education_level_id,
        duration_minutes=duration_minutes,
        cohort_id=cohort_id,
        group_id=group_id,
    )
    rank = case(*ranks).label("rank")
    in_effect = (
        select(PriceVersion.id, PriceVersion.effective_from, PriceVersion.amount)
        .where(
            PriceVersion.service_price_id == ServicePrice.id,
            PriceVersion.effective_from <= on,
        )
        .order_by(PriceVersion.effective_from.desc())
        .limit(1)
        .lateral("in_effect")
    )
    query: Any = (
        select(ServicePrice.id, in_effect.c.id, in_effect.c.effective_from)
        .add_columns(in_effect.c.amount, rank)
        .join(in_effect, true())
        .where(
            ServicePrice.class_type == class_type,
            ServicePrice.pricing_model == pricing_model,
            ServicePrice.subject_id == subject_id,
            or_(*(condition for condition, _ in ranks)),
        )
        .order_by(rank)
        .limit(1)
    )
    row = (await session.execute(query)).first()
    if row is None or row[3] is None:
        return None
    return PriceInEffect(
        service_price_id=row[0],
        price_version_id=row[1],
        effective_from=row[2],
        amount=row[3],
        rank=int(row[4]),
    )


def group_fee_item_columns() -> Any:
    """``SELECT subject_id, group_id FROM service_prices WHERE class_type =
    'group' AND pricing_model = 'full_course'`` (``group_id`` ``NULL``: an
    unscoped item of the subject), **not executed**: the #92 enrollment
    sweeper reads a group period only when such an item could price it
    (D3), so session-priced groups aren't re-read on every tick."""
    return select(ServicePrice.subject_id, ServicePrice.group_id).where(
        ServicePrice.class_type == "group",
        ServicePrice.pricing_model == "full_course",
    )


# --- The price calculation (#95 B2-B4) -----------------------------------------------
#
# **Order** (ADR-0006 E9; #95 H4-H6): (1) a custom price of the **student**,
# then one of the **family**, whose catalog item is one of the #89 candidates
# of the query and which is in effect on ``on`` (``effective_from <= on <=
# expires_on``); among one target's prices the item with the lowest #89 rank
# wins. A matching custom price beats the catalog even when the catalog says
# "not offered" (``NULL``) or has no version in effect. (2) With no custom
# price, exactly :func:`lookup_price` (its ranks and ``NULL`` rule). (3) The
# discounts on top of that base price (:func:`discounts_for`, H4/H5: at most
# one per kind, scholarship → sibling → discount → promotion, each on the
# running remainder). #92 prorates between :func:`base_price` and
# :func:`discounts_for` (proration before discounts: ``quote(…, prorate=)``).
#
# Everything runs in the caller's Unit of Work: no commit, no lock.


def _target_condition(
    student_id: uuid.UUID, family_id: uuid.UUID | None
) -> ColumnElement[bool]:
    if family_id is None:
        return CustomPrice.student_id == student_id
    return or_(CustomPrice.student_id == student_id, CustomPrice.family_id == family_id)


def _check_query(query: PriceQuery) -> None:
    if query.subject_id is None and query.service_price_id is None:
        raise ValueError("an unpinned query needs a subject_id")
    _check_arguments(
        class_type=query.class_type,
        pricing_model=query.pricing_model,
        cohort_id=query.cohort_id,
        group_id=query.group_id,
        pinned=query.service_price_id is not None,
    )


def _version_in_effect(
    versions: Sequence[tuple[date, uuid.UUID, Money | None]], on: date
) -> tuple[uuid.UUID, Money | None] | None:
    """The version in effect on ``on`` among ``(effective_from, id,
    amount)`` rows (the greatest ``effective_from <= on``), or ``None``."""
    best: tuple[date, uuid.UUID, Money | None] | None = None
    for version in versions:
        if version[0] <= on and (best is None or version[0] > best[0]):
            best = version
    return None if best is None else (best[1], best[2])


async def _pinned_base_price(
    session: AsyncSession, query: PriceQuery, service_price_id: uuid.UUID
) -> BasePrice | None:
    """#93 B4: the pinned item's custom price of the student, then of the
    family, in effect on ``on`` (one statement), otherwise the item's
    version in effect on ``on`` (one more; none or a ``NULL`` amount →
    ``None``). ``rank`` is 0 (no rank search)."""
    by_student = case((CustomPrice.student_id.is_not(None), 0), else_=1)
    row = (
        await session.execute(
            select(CustomPrice.id, CustomPrice.amount, CustomPrice.student_id)
            .where(
                CustomPrice.service_price_id == service_price_id,
                CustomPrice.effective_from <= query.on,
                or_(
                    CustomPrice.expires_on.is_(None), CustomPrice.expires_on >= query.on
                ),
                _target_condition(query.student_id, query.family_id),
            )
            .order_by(by_student, CustomPrice.id)
            .limit(1)
        )
    ).first()
    if row is not None:
        return BasePrice(
            list_amount=row.amount,
            price_version_id=None,
            custom_price_id=row.id,
            source="custom_student" if row.student_id is not None else "custom_family",
            rank=0,
        )
    version = (
        await session.execute(
            select(PriceVersion.id, PriceVersion.amount)
            .where(
                PriceVersion.service_price_id == service_price_id,
                PriceVersion.effective_from <= query.on,
            )
            .order_by(PriceVersion.effective_from.desc())
            .limit(1)
        )
    ).first()
    if version is None or version.amount is None:
        return None
    return BasePrice(
        list_amount=version.amount,
        price_version_id=version.id,
        custom_price_id=None,
        source="catalog",
        rank=0,
    )


async def pinned_base_prices(
    session: AsyncSession, queries: Sequence[PriceQuery]
) -> dict[PriceQuery, BasePrice | None]:
    """The :func:`base_price` of many **pinned** queries (#93 G1: the
    unbilled tuition read) in two statements (none for no query): their
    custom prices (:func:`custom_prices_for`) and every version of their
    items. Raises :class:`ValueError` for an unpinned query."""
    if not queries:
        return {}
    if any(q.service_price_id is None for q in queries):
        raise ValueError("pinned_base_prices takes pinned queries only")
    custom = await custom_prices_for(session, queries)
    rows = (
        await session.execute(
            select(
                PriceVersion.service_price_id,
                PriceVersion.effective_from,
                PriceVersion.id,
                PriceVersion.amount,
            ).where(
                PriceVersion.service_price_id.in_({q.service_price_id for q in queries})
            )
        )
    ).all()
    versions: dict[uuid.UUID, list[tuple[date, uuid.UUID, Money | None]]] = {}
    for row in rows:
        versions.setdefault(row.service_price_id, []).append(
            (row.effective_from, row.id, row.amount)
        )
    found: dict[PriceQuery, BasePrice | None] = {}
    for query in queries:
        if query in custom:
            found[query] = custom[query]
            continue
        assert query.service_price_id is not None  # noqa: S101 - checked above
        in_effect = _version_in_effect(
            versions.get(query.service_price_id, []), query.on
        )
        if in_effect is None or in_effect[1] is None:
            found[query] = None
        else:
            found[query] = BasePrice(
                list_amount=in_effect[1],
                price_version_id=in_effect[0],
                custom_price_id=None,
                source="catalog",
                rank=0,
            )
    return found


async def base_price(session: AsyncSession, query: PriceQuery) -> BasePrice | None:
    """The base price of ``query`` (#95 B2, H6): the matching custom price
    (one statement), otherwise :func:`lookup_price` (one more). ``None`` is
    ``no_price``. At most two statements.

    With a pin (``query.service_price_id``, #93 B4): the custom-price step
    for that item only, then that item's version in effect on ``on``; the
    rank search is skipped (still at most two statements). A ``package``
    item (#94 L8: a package product's purchase price) is only priced
    pinned; its ``subject_id`` may be ``None``."""
    _check_query(query)
    if query.service_price_id is not None:
        return await _pinned_base_price(session, query, query.service_price_id)
    ranks = _rank_conditions(
        education_level_id=query.education_level_id,
        duration_minutes=query.duration_minutes,
        cohort_id=query.cohort_id,
        group_id=query.group_id,
    )
    rank = case(*ranks).label("rank")
    by_student = case((CustomPrice.student_id.is_not(None), 0), else_=1)
    row = (
        await session.execute(
            select(CustomPrice.id, CustomPrice.amount, CustomPrice.student_id, rank)
            .join(ServicePrice, ServicePrice.id == CustomPrice.service_price_id)
            .where(
                ServicePrice.class_type == query.class_type,
                ServicePrice.pricing_model == query.pricing_model,
                ServicePrice.subject_id == query.subject_id,
                or_(*(condition for condition, _ in ranks)),
                CustomPrice.effective_from <= query.on,
                or_(
                    CustomPrice.expires_on.is_(None), CustomPrice.expires_on >= query.on
                ),
                _target_condition(query.student_id, query.family_id),
            )
            .order_by(by_student, rank, CustomPrice.id)
            .limit(1)
        )
    ).first()
    if row is not None:
        return BasePrice(
            list_amount=row.amount,
            price_version_id=None,
            custom_price_id=row.id,
            source="custom_student" if row.student_id is not None else "custom_family",
            rank=int(row.rank),
        )
    assert query.subject_id is not None  # noqa: S101 - _check_query
    price = await lookup_price(
        session,
        class_type=query.class_type,
        pricing_model=query.pricing_model,
        subject_id=query.subject_id,
        on=query.on,
        education_level_id=query.education_level_id,
        duration_minutes=query.duration_minutes,
        cohort_id=query.cohort_id,
        group_id=query.group_id,
    )
    return None if price is None else BasePrice.of_catalog(price)


@dataclass(frozen=True)
class _ItemKey:
    class_type: str
    pricing_model: str
    subject_id: uuid.UUID | None
    education_level_id: uuid.UUID | None
    duration_minutes: int | None
    cohort_id: uuid.UUID | None
    group_id: uuid.UUID | None


def candidate_rank(item: _ItemKey, query: PriceQuery) -> int | None:
    """The #89 rank of a catalog item for ``query`` (the Python twin of the
    ranks :func:`lookup_price` computes in SQL), or ``None`` when the item is
    not a candidate."""
    if (item.class_type, item.pricing_model, item.subject_id) != (
        query.class_type,
        query.pricing_model,
        query.subject_id,
    ):
        return None
    if item.cohort_id is not None or item.group_id is not None:
        scoped = (
            query.cohort_id is not None and item.cohort_id == query.cohort_id
        ) or (query.group_id is not None and item.group_id == query.group_id)
        return 0 if scoped else None
    level, duration = item.education_level_id, item.duration_minutes
    level_ok = level is None or level == query.education_level_id
    duration_ok = duration is None or duration == query.duration_minutes
    if not (level_ok and duration_ok):
        return None
    if level is not None:
        return 1 if duration is not None else 2
    return 3 if duration is not None else 4


async def custom_prices_for(
    session: AsyncSession, queries: Sequence[PriceQuery]
) -> dict[PriceQuery, BasePrice]:
    """The matching custom price of each query that has one (the
    :func:`base_price` rule, H6), in **one** statement for all of them (none
    for no query): the #91 unbilled list reads a page's custom prices with
    it. Queries without a custom price are left out (the caller then asks
    the catalog). A pinned query (#93) matches only its item's custom
    prices (rank 0)."""
    if not queries:
        return {}
    for query in queries:
        _check_query(query)
    students = {q.student_id for q in queries}
    families_ = {q.family_id for q in queries if q.family_id is not None}
    target: ColumnElement[bool] = CustomPrice.student_id.in_(students)
    if families_:
        target = or_(target, CustomPrice.family_id.in_(families_))
    rows = (
        await session.execute(
            select(
                CustomPrice.id,
                CustomPrice.service_price_id,
                CustomPrice.amount,
                CustomPrice.student_id,
                CustomPrice.family_id,
                CustomPrice.effective_from,
                CustomPrice.expires_on,
                ServicePrice.class_type,
                ServicePrice.pricing_model,
                ServicePrice.subject_id,
                ServicePrice.education_level_id,
                ServicePrice.duration_minutes,
                ServicePrice.cohort_id,
                ServicePrice.group_id,
            )
            .join(ServicePrice, ServicePrice.id == CustomPrice.service_price_id)
            .where(
                target,
                ServicePrice.subject_id.in_({q.subject_id for q in queries}),
                ServicePrice.pricing_model.in_({q.pricing_model for q in queries}),
                CustomPrice.effective_from <= max(q.on for q in queries),
                or_(
                    CustomPrice.expires_on.is_(None),
                    CustomPrice.expires_on >= min(q.on for q in queries),
                ),
            )
        )
    ).all()
    found: dict[PriceQuery, BasePrice] = {}
    for query in queries:
        best: tuple[int, int, uuid.UUID, Any] | None = None
        for row in rows:
            if not (
                row.effective_from <= query.on
                and (row.expires_on is None or row.expires_on >= query.on)
            ):
                continue
            if row.student_id == query.student_id:
                order = 0
            elif query.family_id is not None and row.family_id == query.family_id:
                order = 1
            else:
                continue
            rank = 0 if row.service_price_id == query.service_price_id else None
            if query.service_price_id is None:
                rank = candidate_rank(
                    _ItemKey(
                        class_type=row.class_type,
                        pricing_model=row.pricing_model,
                        subject_id=row.subject_id,
                        education_level_id=row.education_level_id,
                        duration_minutes=row.duration_minutes,
                        cohort_id=row.cohort_id,
                        group_id=row.group_id,
                    ),
                    query,
                )
            if rank is None:
                continue
            candidate = (order, rank, row.id, row)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
        if best is not None:
            order, rank, _, row = best
            found[query] = BasePrice(
                list_amount=row.amount,
                price_version_id=None,
                custom_price_id=row.id,
                source="custom_student" if order == 0 else "custom_family",
                rank=rank,
            )
    return found


def _spec(row: Discount) -> DiscountSpec:
    return DiscountSpec(
        discount_id=row.id,
        kind=cast(DiscountKind, row.kind),
        name=row.name,
        percent=row.percent,
        fixed_amount=row.amount,
        created_at=row.created_at,
    )


async def discounts_for(
    session: AsyncSession, query: PriceQuery, list_amount: Money
) -> tuple[DiscountLine, ...]:
    """The discount lines of ``query`` on ``list_amount`` (#95 B3; H2, H3,
    H4, H9): the active discounts whose window contains ``on`` and whose
    audience is ``all`` or has an assignment of the student or the family
    (one statement); the criteria (:func:`~app.modules.billing.discounts.
    matches`); the family's active students, read only when a ``sibling``
    candidate remains (one more); then the H4 selection. ``family_id``
    ``None``: no family assignment and no sibling discount. At most two
    statements."""
    assigned = [DiscountAssignment.student_id == query.student_id]
    if query.family_id is not None:
        assigned.append(DiscountAssignment.family_id == query.family_id)
    audience = or_(
        Discount.audience == "all",
        exists().where(DiscountAssignment.discount_id == Discount.id, or_(*assigned)),
    )
    conditions: list[ColumnElement[bool]] = [
        Discount.is_active.is_(True),
        or_(Discount.starts_on.is_(None), Discount.starts_on <= query.on),
        or_(Discount.expires_on.is_(None), Discount.expires_on >= query.on),
        audience,
    ]
    if query.family_id is None:
        conditions.append(Discount.kind != "sibling")
    rows = (await session.scalars(select(Discount).where(*conditions))).all()
    facts = ChargeFacts(
        charge_kind=query.charge_kind,
        class_type=query.class_type,
        subject_id=query.subject_id,
        education_level_id=query.education_level_id,
    )
    pending: list[tuple[Discount, DiscountEligibility]] = []
    eligible: list[DiscountSpec] = []
    for row in rows:
        criteria = DiscountEligibility.model_validate(row.eligibility)
        if criteria.min_family_students is None:
            if matches(criteria, facts):
                eligible.append(_spec(row))
        elif matches(criteria.model_copy(update={"min_family_students": None}), facts):
            pending.append((row, criteria))
    if pending and query.family_id is not None:
        count = await families.active_student_count(session, query.family_id)
        counted = ChargeFacts(
            charge_kind=facts.charge_kind,
            class_type=facts.class_type,
            subject_id=facts.subject_id,
            education_level_id=facts.education_level_id,
            family_active_students=count,
        )
        eligible.extend(
            _spec(row) for row, criteria in pending if matches(criteria, counted)
        )
    return select_discounts(list_amount, eligible)


def make_quote(base: BasePrice, lines: Iterable[DiscountLine]) -> Quote:
    """A :class:`Quote` from a base price and its discount lines."""
    applied = tuple(lines)
    discount = total(applied)
    return Quote(
        list_amount=base.list_amount,
        price_version_id=base.price_version_id,
        custom_price_id=base.custom_price_id,
        source=base.source,
        rank=base.rank,
        discounts=applied,
        discount_amount=discount,
        amount=base.list_amount - discount,
    )


async def quote(
    session: AsyncSession,
    query: PriceQuery,
    *,
    prorate: tuple[int, int] | None = None,
) -> Quote | None:
    """The price of ``query`` (#95 B4): :func:`base_price` then
    :func:`discounts_for` (the order at the top of this section). ``None``
    ⇔ no base price (``no_price``). At most four statements; no lock, no
    commit. Every charge a sweeper or a billing workflow posts is priced
    here; post it with ``ChargeDraft.priced(quote, …)``.

    ``prorate = (remaining, total)`` (#92 B3, J3): the base price's list
    amount becomes ``list_amount.prorate(remaining, total)`` (one half-up
    rounding) **before** the discounts are computed on it, so proration
    comes before discounts. ``None`` is exactly the #95 behaviour."""
    base = await base_price(session, query)
    if base is None:
        return None
    if prorate is not None:
        remaining, total = prorate
        base = replace(base, list_amount=base.list_amount.prorate(remaining, total))
    return make_quote(base, await discounts_for(session, query, base.list_amount))
