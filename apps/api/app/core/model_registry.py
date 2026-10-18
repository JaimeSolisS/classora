"""The one place that imports every model module (spec §10: migration-first).

Alembic's ``env.py`` and the metadata/migration comparison test use
:data:`target_metadata` and :func:`include_name` from here, so autogenerate and
the check see every table. When an issue adds a model module, import it below,
for example::

    import app.modules.students.models  # noqa: F401
"""

from app.core.models import Base

target_metadata = Base.metadata

# Procrastinate's tables and indexes are created by their own Alembic revisions
# from committed SQL (ADR-0003), not from models.
PROCRASTINATE_PREFIX = "procrastinate_"


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Autogenerate filter: leave Procrastinate's ``procrastinate_*`` objects out.

    Passed as ``include_name`` to Alembic's ``context.configure`` and
    ``MigrationContext.configure``. Everything else is compared as usual.
    """
    if name is not None and name.startswith(PROCRASTINATE_PREFIX):
        return type_ not in {
            "table",
            "index",
            "unique_constraint",
            "foreign_key_constraint",
        }
    return True


from sqlalchemy.orm import Mapped, mapped_column  # noqa: E402

from app.core.models import Model  # noqa: E402


class H2Throwaway(Model):
    __tablename__ = "h2_throwaway"
    note: Mapped[str] = mapped_column()
