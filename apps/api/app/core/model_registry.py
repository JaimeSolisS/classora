"""The one place that imports every model module (spec §10: migration-first).

Alembic's ``env.py`` and the metadata/migration comparison test use
:data:`target_metadata` and :func:`include_name` from here, so autogenerate and
the check see every table. When an issue adds a model module, import it below,
for example::

    import app.modules.students.models  # noqa: F401
"""

import app.core.audit  # noqa: F401  (audit_events)
import app.core.idempotency_models  # noqa: F401  (idempotency_keys)
import app.core.numbering_models  # noqa: F401  (number_series)
import app.core.ratelimit_models  # noqa: F401  (rate_limit_counters)
import app.modules.academics.models  # noqa: F401
import app.modules.administration.models  # noqa: F401
import app.modules.administration.settings_models  # noqa: F401
import app.modules.billing.models  # noqa: F401
import app.modules.catalog.models  # noqa: F401
import app.modules.courses.models  # noqa: F401
import app.modules.families.models  # noqa: F401
import app.modules.identity.models  # noqa: F401
import app.modules.payroll.models  # noqa: F401
import app.modules.scheduling.models  # noqa: F401
import app.modules.staff.models  # noqa: F401
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
