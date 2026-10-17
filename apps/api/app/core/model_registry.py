"""The one place that imports every model module (spec §10: migration-first).

Alembic's ``env.py`` and the metadata/migration comparison test use
:data:`target_metadata` from here, so autogenerate and the check see every
table. When an issue adds a model module, import it below, for example::

    import app.modules.students.models  # noqa: F401
"""

from app.core.models import Base

target_metadata = Base.metadata
