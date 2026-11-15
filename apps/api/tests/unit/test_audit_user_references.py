"""The user-reference columns of an audited table (#120 B2).

``user_reference_columns`` reads SQLAlchemy metadata only: the included
columns whose foreign key targets ``users.id``, never a masked, excluded or
always-left-out one.
"""

import uuid
from typing import ClassVar

from sqlalchemy import ForeignKey, MetaData, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import app.core.model_registry  # noqa: F401  (maps every application model)
from app.core.audit import Audited, user_reference_columns


class ReferenceBase(DeclarativeBase):
    metadata = MetaData()


class ReferenceThing(Audited, ReferenceBase):
    __tablename__ = "user_reference_things"
    __audit_exclude__: ClassVar[frozenset[str]] = frozenset({"hidden_user_id"})
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"masked_user_id"})

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid7
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    masked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    hidden_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    other_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), nullable=True
    )
    plain_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    label: Mapped[str] = mapped_column(Text)


class NotAudited(ReferenceBase):
    __tablename__ = "user_reference_plain"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )


def test_only_included_foreign_keys_onto_users_count():
    assert user_reference_columns("user_reference_things") == {
        "owner_user_id",
        "reviewer_user_id",
    }


def test_an_unknown_or_unaudited_table_has_none():
    assert user_reference_columns("no_such_table") == frozenset()
    assert user_reference_columns("user_reference_plain") == frozenset()


def test_the_real_models():
    # #118: an assignment is an update of assignee_user_id (B5).
    assert {
        "assignee_user_id",
        "requester_user_id",
        "resolved_by_user_id",
    } <= user_reference_columns("approval_requests")
    assert "user_id" in user_reference_columns("user_roles")
    # Audit events themselves are not an audited table.
    assert user_reference_columns("audit_events") == frozenset()


def test_no_real_masked_or_excluded_column_is_ever_returned():
    from app.core.audit import audited_table_names

    for table in audited_table_names():
        for cls in _classes_of(table):
            hidden = cls.__audit_mask__ | cls.__audit_exclude__
            assert not hidden & user_reference_columns(table), table


def _classes_of(table: str) -> list[type[Audited]]:
    from sqlalchemy import inspect

    found: list[type[Audited]] = []
    pending: list[type[Audited]] = list(Audited.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        mapper = inspect(cls, raiseerr=False)
        if mapper is not None and mapper.local_table.name == table:
            found.append(cls)
    return found
