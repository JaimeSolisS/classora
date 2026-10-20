"""User preferences: UI language and theme (#36; spec §10, §12 ``Preference``).

Each user has at most one ``user_preferences`` row (``UserPreference``). A
column that is ``NULL``, or no row at all, means the user has never stored that
value; the web app then fills it from the browser's local choice after
sign-in. Rows appear on the first save, so no user-creation path writes one.

- :data:`LANGUAGES` and :data:`THEMES` are the stored codes. The table's CHECK
  constraints are generated from them (as ``users.state`` from
  ``USER_STATES``), and the OpenAPI string enums :data:`Language` and
  :data:`Theme` list exactly the same codes (a unit test keeps them equal).
- :func:`get_user_preferences` reads both values in one statement.
- :func:`update_user_preferences` writes only the fields it is given, with
  one ``INSERT … ON CONFLICT (user_id) DO UPDATE``: two concurrent first saves
  of one user end with one row holding both values. The last write wins; there
  is no ``version`` column.

Not audited: a UI preference isn't an important record (spec §9), and a
click per audit event would flood the trail. Saving never touches ``users``
(no ``version`` bump, no ``users`` audit event).

Public through ``identity.service`` (other modules, for example server-rendered
texts in the recipient's language, ISSUE-X1, import only that). Every function
runs in the caller's Unit of Work and never commits.
"""

import enum
import uuid
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

# Named string enums in OpenAPI, so the generated web client gets the unions.
type Language = Literal["es", "en"]
type Theme = Literal["light", "dark", "system"]

LANGUAGES: Final[tuple[str, ...]] = ("es", "en")
"""The UI languages (spec §10: Spanish and English), in ``Language`` order."""
THEMES: Final[tuple[str, ...]] = ("light", "dark", "system")
"""The theme choices (spec §10), in ``Theme`` order."""


class UserPreferences(BaseModel):
    """The signed-in user's stored preferences (``SessionUser.preferences``).

    ``null`` means the user has never stored the value.
    """

    language: Language | None = Field(
        description="The UI language, or null when never stored."
    )
    theme: Theme | None = Field(
        description="The theme choice, or null when never stored."
    )


class UserPreferencesUpdate(BaseModel):
    """``PATCH /api/v1/auth/me/preferences``: one or both values.

    A field that is not sent stays as it is. ``null`` is refused (a stored
    value can't be cleared), so is an empty body and any other field.
    """

    model_config = ConfigDict(extra="forbid")

    # Not Optional, so OpenAPI shows no null and an explicit null fails the
    # enum. A missing field keeps the default None, which pydantic never
    # validates and the route never writes (model_fields_set); hence the
    # ignores.
    language: Language = Field(default=None, description="The new UI language.")  # type: ignore[arg-type]
    theme: Theme = Field(default=None, description="The new theme choice.")  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _at_least_one(self) -> UserPreferencesUpdate:
        if not self.model_fields_set:
            raise ValueError("Send language, theme or both.")
        return self


class _Unset(enum.Enum):
    UNSET = enum.auto()


UNSET: Final = _Unset.UNSET
"""The default of :func:`update_user_preferences`' fields: leave unchanged."""


def _checked(name: str, value: object, allowed: tuple[str, ...]) -> str | None:
    """``value`` when it is one of ``allowed``; ``None`` for :data:`UNSET`."""
    if value is UNSET:
        return None
    if value is None:
        raise ValueError(f"{name} can't be cleared once stored")
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed!r}")
    return str(value)


async def get_user_preferences(
    session: AsyncSession, user_id: uuid.UUID
) -> UserPreferences:
    """``user_id``'s language and theme; each ``None`` when never stored
    (no row, or a ``NULL`` column). One statement."""
    # Imported here: models.py builds its CHECK constraints from this
    # module's constants, so a module-level import would be circular.
    from app.modules.identity.models import UserPreference

    row = (
        await session.execute(
            select(UserPreference.language, UserPreference.theme).where(
                UserPreference.user_id == user_id
            )
        )
    ).one_or_none()
    if row is None:
        return UserPreferences(language=None, theme=None)
    return UserPreferences.model_validate({"language": row[0], "theme": row[1]})


async def update_user_preferences(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    language: Language | _Unset = UNSET,
    theme: Theme | _Unset = UNSET,
) -> UserPreferences:
    """Store the fields passed for ``user_id`` and return both values.

    A field left at :data:`UNSET` is never written; ``None`` is refused
    (``ValueError``), and so is a call without any field. One statement:
    ``INSERT … ON CONFLICT (user_id) DO UPDATE SET <passed columns>,
    updated_at = now() RETURNING language, theme``. A concurrent first save of
    the same user waits for the other one's row, then updates it.
    """
    # Imported here: models.py builds its CHECK constraints from this
    # module's constants, so a module-level import would be circular.
    from app.modules.identity.models import UserPreference

    changes: dict[str, str] = {}
    for name, value, allowed in (
        ("language", language, LANGUAGES),
        ("theme", theme, THEMES),
    ):
        checked = _checked(name, value, allowed)
        if checked is not None:
            changes[name] = checked
    if not changes:
        raise ValueError("update_user_preferences needs language, theme or both")
    statement = (
        insert(UserPreference)
        .values(user_id=user_id, **changes)
        .on_conflict_do_update(
            index_elements=[UserPreference.user_id],
            set_={**changes, "updated_at": func.now()},
        )
        .returning(UserPreference.language, UserPreference.theme)
    )
    row = (await session.execute(statement)).one()
    return UserPreferences.model_validate({"language": row[0], "theme": row[1]})
