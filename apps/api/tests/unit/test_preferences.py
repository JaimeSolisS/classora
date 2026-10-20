"""User preferences without a database (#36 A3, B2, D2).

The upsert's SQL is compiled for PostgreSQL from a recording session, so these
tests show which columns a call writes; the integration tests
(``tests/integration/test_user_preferences.py``) run it for real.
"""

import asyncio
import uuid
from typing import Any, get_args

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.modules.identity import service as identity
from app.modules.identity.models import UserPreference
from app.modules.identity.preferences import (
    LANGUAGES,
    THEMES,
    UNSET,
    Language,
    Theme,
    UserPreferences,
    UserPreferencesUpdate,
    get_user_preferences,
    update_user_preferences,
)

pytestmark = pytest.mark.unit

USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000036")


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def one(self) -> tuple[Any, ...]:
        assert self._row is not None
        return self._row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self._row


class RecordingSession:
    """Records each statement as PostgreSQL SQL and answers with ``row``."""

    def __init__(self, row: tuple[Any, ...] | None = ("es", None)) -> None:
        self.row = row
        self.sql: list[str] = []

    async def execute(self, statement: Any) -> _Result:
        self.sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return _Result(self.row)


def run_update(session: RecordingSession, **fields: Any) -> UserPreferences:
    return asyncio.run(update_user_preferences(session, USER_ID, **fields))  # type: ignore[arg-type]


def set_clause(sql: str) -> str:
    return sql.split("DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]


# --- A3: the codes --------------------------------------------------------------------


def test_the_codes_match_the_openapi_enums():
    assert LANGUAGES == ("es", "en")
    assert THEMES == ("light", "dark", "system")
    assert get_args(Language.__value__) == LANGUAGES
    assert get_args(Theme.__value__) == THEMES


def test_the_check_constraints_are_generated_from_the_codes():
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in UserPreference.__table__.constraints
        if constraint.name and "valid" in str(constraint.name)
    }

    assert checks == {
        "ck_user_preferences_language_valid": "language IN ('es', 'en')",
        "ck_user_preferences_theme_valid": "theme IN ('light', 'dark', 'system')",
    }


def test_identity_service_re_exports_the_functions():
    assert identity.get_user_preferences is get_user_preferences
    assert identity.update_user_preferences is update_user_preferences
    assert identity.LANGUAGES is LANGUAGES
    assert identity.THEMES is THEMES


# --- B2: UNSET ------------------------------------------------------------------------


def test_only_the_language_is_written_when_only_it_is_passed():
    session = RecordingSession(("en", None))

    result = run_update(session, language="en")

    [sql] = session.sql
    assert sql.startswith("INSERT INTO user_preferences (user_id, language")
    assert "theme)" not in sql.split("VALUES", 1)[0]
    assert "ON CONFLICT (user_id) DO UPDATE SET" in sql
    assert "language =" in set_clause(sql)
    assert "theme" not in set_clause(sql)
    assert "updated_at = now()" in set_clause(sql)
    assert "RETURNING user_preferences.language, user_preferences.theme" in sql
    assert result == UserPreferences(language="en", theme=None)


def test_only_the_theme_is_written_when_only_it_is_passed():
    session = RecordingSession(("es", "dark"))

    result = run_update(session, theme="dark")

    [sql] = session.sql
    assert "language" not in set_clause(sql)
    assert "theme =" in set_clause(sql)
    assert result == UserPreferences(language="es", theme="dark")


def test_both_fields_are_written_when_both_are_passed():
    session = RecordingSession(("es", "system"))

    run_update(session, language="es", theme="system")

    clause = set_clause(session.sql[0])
    assert "language =" in clause
    assert "theme =" in clause


def test_explicit_unset_is_the_same_as_not_passing():
    session = RecordingSession(("en", None))

    run_update(session, language="en", theme=UNSET)

    assert "theme" not in set_clause(session.sql[0])


@pytest.mark.parametrize(
    "fields",
    [
        {"language": None},
        {"theme": None},
        {"language": "en", "theme": None},
        {"language": "fr"},
        {"language": "EN"},
        {"theme": "blue"},
        {},
        {"language": UNSET, "theme": UNSET},
    ],
)
def test_none_unknown_values_and_no_field_are_refused_before_any_sql(fields):
    session = RecordingSession()

    with pytest.raises(ValueError):
        run_update(session, **fields)

    assert session.sql == []


def test_get_returns_nulls_without_a_row():
    session = RecordingSession(row=None)

    result = asyncio.run(get_user_preferences(session, USER_ID))  # type: ignore[arg-type]

    assert result == UserPreferences(language=None, theme=None)
    [sql] = session.sql
    assert sql.startswith("SELECT user_preferences.language, user_preferences.theme")


# --- D2: the request body -------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"language": "en"},
        {"theme": "dark"},
        {"language": "es", "theme": "system"},
    ],
)
def test_valid_bodies(body):
    update = UserPreferencesUpdate.model_validate(body)

    assert update.model_fields_set == set(body)
    for name, value in body.items():
        assert getattr(update, name) == value


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"language": None},
        {"theme": None},
        {"language": "en", "theme": None},
        {"language": "fr"},
        {"language": "EN"},
        {"language": ""},
        {"theme": "blue"},
        {"theme": "Dark"},
        {"language": "en", "user_id": str(USER_ID)},
        {"user_id": str(USER_ID)},
    ],
)
def test_invalid_bodies_are_refused(body):
    with pytest.raises(ValidationError):
        UserPreferencesUpdate.model_validate(body)


def test_the_update_schema_has_no_null_and_no_other_field():
    schema = UserPreferencesUpdate.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"language", "theme"}
    assert "required" not in schema
    for prop in schema["properties"].values():
        assert "anyOf" not in prop
        assert "default" not in prop or prop["default"] is None


def test_the_read_schema_requires_both_nullable_fields():
    schema = UserPreferences.model_json_schema()

    assert schema["required"] == ["language", "theme"]
    for prop in schema["properties"].values():
        assert {"type": "null"} in prop["anyOf"]
