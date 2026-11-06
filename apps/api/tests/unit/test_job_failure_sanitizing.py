"""``sanitize_job_error`` (#128 A2): only the class name, an application code
and the SQLSTATE of a failed run are kept; never the message, the arguments
or the traceback."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.core.jobs import sanitize_job_error

NAME = "Ana Lucía Pérez"
EMAIL = "ana.perez@example.com"
SECRET_ID = str(uuid.UUID("0190f5a8-2c1e-7d3b-9a4f-5e6d7c8b9a01"))
PERSONAL = f"{NAME} <{EMAIL}> {SECRET_ID}"


class ChargeRunError(AppError):
    status = 409
    code = "charge_run_failed"
    detail = "The run failed."


class _DriverError(Exception):
    """An asyncpg-like error: a message quoting key values plus a sqlstate."""

    sqlstate = "23505"


class _PgcodeError(Exception):
    pgcode = "40001"


def _all_values(result: tuple[str, str | None, str | None]) -> str:
    return " ".join(value for value in result if value is not None)


def test_a_plain_exception_keeps_only_its_class_name():
    assert sanitize_job_error(RuntimeError(PERSONAL)) == ("RuntimeError", None, None)


def test_an_app_error_keeps_its_code():
    error = ChargeRunError()

    assert sanitize_job_error(error) == ("ChargeRunError", "charge_run_failed", None)


def test_a_driver_error_keeps_its_sqlstate_and_not_its_message():
    error = _DriverError(f"Key (student_id, email)=({SECRET_ID}, {EMAIL}) exists")

    result = sanitize_job_error(error)

    assert result == ("_DriverError", None, "23505")


def test_the_sqlstate_comes_from_orig_or_the_cause():
    wrapped = IntegrityError(
        f"INSERT ... {NAME}", {"name": NAME}, _DriverError(PERSONAL)
    )
    chained = RuntimeError(PERSONAL)
    chained.__cause__ = _PgcodeError(PERSONAL)

    assert sanitize_job_error(wrapped) == ("IntegrityError", None, "23505")
    assert sanitize_job_error(chained) == ("RuntimeError", None, "40001")


@pytest.mark.parametrize(
    "code",
    [PERSONAL, "Has_Upper", "1starts_with_digit", "a" * 65, 42, b"bytes_code", None],
)
def test_a_code_that_is_not_an_app_code_is_dropped(code):
    error = ChargeRunError()
    error.code = code  # type: ignore[assignment]
    plain = RuntimeError(PERSONAL)
    plain.code = "looks_like_a_code"  # type: ignore[attr-defined]

    assert sanitize_job_error(error)[1] is None
    # Only an AppError's code counts (SQLAlchemy's ``code`` is a docs link id).
    assert sanitize_job_error(plain)[1] is None


@pytest.mark.parametrize("sqlstate", ["2350", "235050", "ab123", EMAIL, 23505])
def test_a_sqlstate_that_is_not_five_characters_is_dropped(sqlstate):
    error = RuntimeError(PERSONAL)
    error.sqlstate = sqlstate  # type: ignore[attr-defined]

    assert sanitize_job_error(error)[2] is None


def test_the_class_name_is_the_qualname_truncated_to_200():
    long_class = type("E" * 250, (Exception,), {})

    error_class, _, _ = sanitize_job_error(long_class(PERSONAL))

    assert error_class == "E" * 200


def test_no_personal_value_reaches_any_column():
    errors: list[BaseException] = [
        RuntimeError(PERSONAL),
        ValueError(NAME, EMAIL, SECRET_ID),
        _DriverError(PERSONAL),
        IntegrityError(PERSONAL, {"email": EMAIL}, _DriverError(PERSONAL)),
    ]
    for error in errors:
        values = _all_values(sanitize_job_error(error))
        for personal in (NAME, EMAIL, SECRET_ID, "Ana", "example.com"):
            assert personal not in values
