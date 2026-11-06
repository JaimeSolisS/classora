"""#129 A1, E2: exactly the five academic models are soft-deletable.

A new ``SoftDeleteMixin`` model fails this test: it must be added here, to
``academics.deleted_records.DeletedRecordKind`` and to the recovery and
purge paths on purpose. Money, payroll, expenses, families, identity,
scheduling and staff never soft-delete (master data is deactivated).
"""

import app.core.model_registry  # noqa: F401  (every model is mapped)
from app.core.models import Base, SoftDeleteMixin
from app.modules.academics.deleted_records import MODELS, DeletedRecordKind
from app.modules.academics.models import (
    Assignment,
    Evaluation,
    LearningPlan,
    ProgressReport,
    SessionNote,
)

NEVER_SOFT_DELETED = (
    "billing",
    "payroll",
    "finance",
    "families",
    "identity",
    "scheduling",
    "staff",
)


def soft_deletable() -> set[type]:
    return {
        mapper.class_
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, SoftDeleteMixin)
    }


def test_exactly_the_five_academic_models_are_soft_deletable():
    assert soft_deletable() == {
        SessionNote,
        Evaluation,
        LearningPlan,
        Assignment,
        ProgressReport,
    }


def test_every_soft_deletable_model_is_a_recoverable_kind():
    assert set(MODELS.values()) == soft_deletable()
    assert set(MODELS) == set(DeletedRecordKind)
    assert {kind.value for kind in DeletedRecordKind} == {
        "session_note",
        "evaluation",
        "learning_plan",
        "assignment",
        "progress_report",
    }


def test_no_money_payroll_master_or_schedule_model_is_soft_deletable():
    for model in soft_deletable():
        module = model.__module__.split(".")
        assert module[:3] == ["app", "modules", "academics"], model
        assert module[2] not in NEVER_SOFT_DELETED


def test_every_soft_deletable_model_records_who_deleted_it():
    for model in soft_deletable():
        columns = model.__table__.columns
        assert "deleted_at" in columns, model
        assert "deleted_by_user_id" in columns, model
