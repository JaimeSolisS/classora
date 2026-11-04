"""Expense categories (#114 B, E; ADR-0017 E15; data-model §12.1).

Every function runs in the caller's Unit of Work and never commits.

**Names.** A name is unique ignoring case. The unique index decides that, in a
savepoint that maps it to ``409 expense_category_exists`` (no ``ON CONFLICT``:
the audit would miss the row). A new or changed name is also refused when it
equals, ignoring case and surrounding spaces, the Spanish or English label of
**another** row that still holds a ``system_key`` (#114 E3), so no list shows
the same text twice in one language. That check is in the application: the
labels are constants, and a row can only lose its key, so the only race is a
harmless false refusal.

**Keys.** A real change of name clears ``system_key`` in the same update.
Sending the current name keeps it. Nothing sets a key again.

**Deactivation** of an active row is refused while a live template uses it
(``409 expense_category_in_use`` with ``details.template_count``, #116 E2):
read after the row lock and the version check, with no lock on the templates
(:mod:`~app.modules.finance.template_usage`). A rename and a reactivation
never read templates.

**Usable** (:func:`usable_category`, E7): the row ``FOR SHARE``, so it takes
turns with a deactivation (``FOR UPDATE``) and nothing new uses a category that
has just gone inactive.
"""

import uuid
from collections.abc import Iterable
from typing import Protocol, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.i18n import LANGUAGES, Language, message
from app.core.models import ensure_version
from app.modules.finance.errors import (
    ExpenseCategoryExistsError,
    ExpenseCategoryInactiveError,
    ExpenseCategoryInUseError,
    ExpenseCategoryUnknownError,
)
from app.modules.finance.models import (
    CATEGORY_NAME_KEY,
    ExpenseCategory,
    ExpenseTemplate,
)
from app.modules.finance.schemas import (
    ExpenseCategoryCreate,
    ExpenseCategoryItem,
    ExpenseCategoryUpdate,
    SystemKey,
)
from app.modules.finance.template_usage import live_template_count


class LabelledCategory(Protocol):
    """What :func:`category_label` reads of a category."""

    @property
    def name(self) -> str: ...
    @property
    def system_key(self) -> str | None: ...


def system_label(key: str, language: Language) -> str:
    """The catalog label of a seeded category's key (``finance.json``)."""
    return message(language, f"finance.categories.system.{key}")


def category_label(row: LabelledCategory, language: Language) -> str:
    """What a document shows for a category: the catalog label while the row
    holds its key, else the stored name (#114 E3; #117's documents)."""
    if row.system_key is None:
        return row.name
    return system_label(row.system_key, language)


def _item(row: ExpenseCategory) -> ExpenseCategoryItem:
    return ExpenseCategoryItem(
        id=row.id,
        name=row.name,
        system_key=cast(SystemKey | None, row.system_key),
        is_active=row.is_active,
        version=row.version,
    )


def _folded(text: str) -> str:
    return text.strip().lower()


async def _refuse_a_label(
    session: AsyncSession, name: str, *, other_than: uuid.UUID | None = None
) -> None:
    """``409 expense_category_exists`` when ``name`` is a label of another
    keyed row. One statement."""
    query = select(ExpenseCategory.system_key).where(
        ExpenseCategory.system_key.is_not(None)
    )
    if other_than is not None:
        query = query.where(ExpenseCategory.id != other_than)
    keys: Iterable[str | None] = await session.scalars(query)
    taken = {
        _folded(system_label(key, language))
        for key in keys
        if key is not None
        for language in LANGUAGES
    }
    if _folded(name) in taken:
        raise ExpenseCategoryExistsError()


async def _flush(session: AsyncSession, row: ExpenseCategory) -> None:
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        if CATEGORY_NAME_KEY in str(exc.orig):
            raise ExpenseCategoryExistsError() from None
        raise


async def list_categories(session: AsyncSession) -> list[ExpenseCategoryItem]:
    """Every category, active first, then by name ignoring case, then id. One
    statement."""
    rows = await session.scalars(
        select(ExpenseCategory).order_by(
            ExpenseCategory.is_active.desc(),
            func.lower(ExpenseCategory.name),
            ExpenseCategory.id,
        )
    )
    return [_item(row) for row in rows]


async def add_category(
    session: AsyncSession, body: ExpenseCategoryCreate
) -> ExpenseCategoryItem:
    """A new category, with no key: ``409 expense_category_exists`` on a taken
    name or label."""
    await _refuse_a_label(session, body.name)
    row = ExpenseCategory(name=body.name)
    await _flush(session, row)
    await session.refresh(row, ["is_active", "version", "system_key"])
    return _item(row)


async def change_category(
    session: AsyncSession, category_id: uuid.UUID, body: ExpenseCategoryUpdate
) -> ExpenseCategoryItem:
    """The row ``FOR UPDATE`` (``404``) → ``409 version_conflict`` → a
    deactivation: ``409 expense_category_in_use`` while a live template uses
    the row (#116 E2) → a new name: the label rule, then the key cleared → the
    update (``409 expense_category_exists``). Unchanged: nothing written, no
    new version."""
    row = await session.scalar(
        select(ExpenseCategory)
        .where(ExpenseCategory.id == category_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise RecordNotFoundError()
    ensure_version(row, body.version)
    if body.is_active is False and row.is_active:
        count = await live_template_count(session, ExpenseTemplate.category_id, row.id)
        if count:
            raise ExpenseCategoryInUseError(count)
    if body.name is not None and body.name != row.name:
        await _refuse_a_label(session, body.name, other_than=row.id)
        row.name = body.name
        row.system_key = None
    if body.is_active is not None:
        row.is_active = body.is_active
    await _flush(session, row)
    return _item(row)


async def usable_category(
    session: AsyncSession, category_id: uuid.UUID
) -> ExpenseCategory:
    """The category a new expense or template may use, read ``FOR SHARE``:
    ``422 expense_category_unknown``, ``409 expense_category_inactive``."""
    row = await session.scalar(
        select(ExpenseCategory)
        .where(ExpenseCategory.id == category_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ExpenseCategoryUnknownError()
    if not row.is_active:
        raise ExpenseCategoryInactiveError()
    return row
