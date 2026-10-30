"""Notification types and params rules (#41 B1, B2, D1, D2)."""

from collections.abc import Iterator

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.openapi import build_document
from app.main import build_app
from app.modules.administration import service as administration
from app.modules.administration.notifications import (
    PARAM_STRING_MAX_LENGTH,
    PARAMS_MAX_BYTES,
    InvalidNotificationParamsError,
    validated_params,
)
from app.modules.administration.notifications_dev import (
    DEV_DEMO_TYPE,
    DevDemoParams,
)

SAMPLE = "test.sample_notice"


class SampleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    count: int | None = None
    flag: bool | None = None
    nested: dict[str, str] | None = None
    ratio: float | None = None
    items: list[str] | None = None


class SecretParams(BaseModel):
    password: str


@pytest.fixture
def sample_type() -> Iterator[str]:
    administration.register_notification_type(SAMPLE, SampleParams)
    yield SAMPLE
    administration.unregister_notification_type(SAMPLE)


# --- Registry (B1) -------------------------------------------------------------------


def test_a_registered_type_is_listed_and_registering_it_again_changes_nothing(
    sample_type,
):
    administration.register_notification_type(SAMPLE, SampleParams)

    assert SAMPLE in administration.registered_notification_types()


def test_another_model_for_a_registered_code_raises(sample_type):
    with pytest.raises(ValueError, match="already registered"):
        administration.register_notification_type(SAMPLE, SecretParams)


@pytest.mark.parametrize(
    "code", ["sample", "Test.Sample", "test.sample.", "test sample.x", "x." + "a" * 70]
)
def test_a_badly_formed_code_raises(code):
    with pytest.raises(ValueError, match="invalid notification type code"):
        administration.register_notification_type(code, SampleParams)


def test_the_params_model_must_be_a_pydantic_model():
    with pytest.raises(ValueError, match="Pydantic model"):
        administration.register_notification_type(SAMPLE, dict)  # type: ignore[arg-type]


def test_an_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown notification type"):
        validated_params("test.never_registered", {})


# --- Params rules (B2) ---------------------------------------------------------------


def test_flat_primitive_params_are_kept(sample_type):
    data = validated_params(
        SAMPLE,
        {"name": "Álgebra", "count": 3, "flag": False, "ratio": None},
    )

    assert data == {
        "name": "Álgebra",
        "count": 3,
        "flag": False,
        "nested": None,
        "ratio": None,
        "items": None,
    }


def test_a_model_instance_is_accepted(sample_type):
    assert validated_params(SAMPLE, SampleParams(name="Ana"))["name"] == "Ana"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"name": "x" * (PARAM_STRING_MAX_LENGTH + 1)}, "longer than"),
        ({"nested": {"a": "b"}}, "must be a string"),
        ({"ratio": 1.5}, "must be a string"),
        ({"items": ["a"]}, "must be a string"),
        ({"unknown": 1}, "don't match the model"),
        ({"count": "many"}, "don't match the model"),
    ],
)
def test_params_that_break_the_rules_raise(sample_type, params, message):
    with pytest.raises(InvalidNotificationParamsError, match=message):
        validated_params(SAMPLE, params)


def test_a_string_of_exactly_the_limit_is_accepted(sample_type):
    value = "x" * PARAM_STRING_MAX_LENGTH

    assert validated_params(SAMPLE, {"name": value})["name"] == value


def test_params_larger_than_2_kb_raise():
    class Wide(BaseModel):
        a: str
        b: str
        c: str
        d: str
        e: str
        f: str
        g: str
        h: str
        i: str
        j: str
        k: str

    administration.register_notification_type("test.wide", Wide)
    try:
        values = {key: "é" * 100 for key in "abcdefghijk"}  # 2 bytes per char
        with pytest.raises(InvalidNotificationParamsError, match="larger than"):
            validated_params("test.wide", values)
        # Under the limit: 11 x 80 ASCII characters plus the keys.
        small = {key: "x" * 80 for key in "abcdefghijk"}
        assert validated_params("test.wide", small) == small
    finally:
        administration.unregister_notification_type("test.wide")
    assert PARAMS_MAX_BYTES == 2048


def test_a_sensitive_key_raises():
    administration.register_notification_type("test.secret", SecretParams)
    try:
        with pytest.raises(InvalidNotificationParamsError, match="sensitive key"):
            validated_params("test.secret", {"password": "hunter2"})
    finally:
        administration.unregister_notification_type("test.secret")


def test_the_error_never_echoes_a_value(sample_type):
    with pytest.raises(InvalidNotificationParamsError) as caught:
        validated_params(SAMPLE, {"name": "visible-" + "x" * 300})

    assert "visible-" not in str(caught.value)


def test_the_app_registers_the_payment_reminder():
    # #97 F6: billing.payment_reminder, wired by app.wiring; flat data only.
    administration.unregister_notification_type("billing.payment_reminder")
    build_app()

    assert "billing.payment_reminder" in administration.registered_notification_types()
    assert validated_params(
        "billing.payment_reminder",
        {
            "family_id": "0190f5a0-0000-7000-8000-000000000001",
            "kind": "due_soon",
            "count": 3,
            "open_total": "900.00",
            "due_on": "2031-03-13",
        },
    ) == {
        "family_id": "0190f5a0-0000-7000-8000-000000000001",
        "kind": "due_soon",
        "count": 3,
        "open_total": "900.00",
        "due_on": "2031-03-13",
    }


# --- The dev type (D1, D2) -----------------------------------------------------------


def test_dev_demo_is_registered_only_with_the_dev_routes():
    administration.unregister_notification_type(DEV_DEMO_TYPE)
    app = build_app()

    assert DEV_DEMO_TYPE not in administration.registered_notification_types()
    assert "/api/v1/dev/notifications" not in app.openapi()["paths"]

    dev_app = build_app(include_dev_routes=True)

    assert DEV_DEMO_TYPE in administration.registered_notification_types()
    assert "/api/v1/dev/notifications" in dev_app.openapi()["paths"]
    # Another dev app build registers the same model again: no error.
    build_app(include_dev_routes=True)


def test_the_exported_contract_has_no_dev_notification_route():
    document = build_document()

    assert "/api/v1/dev/notifications" not in document["paths"]
    assert "DevNotificationBody" not in document["components"]["schemas"]


@pytest.mark.parametrize("label", ["", "x" * 101])
def test_the_dev_label_is_1_to_100_characters(label):
    build_app(include_dev_routes=True)

    with pytest.raises(InvalidNotificationParamsError):
        validated_params(DEV_DEMO_TYPE, {"label": label})
    assert validated_params(DEV_DEMO_TYPE, DevDemoParams(label="x" * 100))
