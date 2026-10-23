"""Business settings: request bodies, contact patterns and the time-zone
dependency registry (#37 C2-C4, D2). Pure, no database."""

import re

import pytest
from pydantic import ValidationError

from app.modules.administration import schemas, settings_models
from app.modules.administration import service as administration
from app.modules.administration.schemas import BusinessSettingsUpdate
from app.modules.identity import models as identity_models
from app.modules.identity import schemas as identity_schemas


def errors_of(body: dict[str, object]) -> list[tuple[tuple[object, ...], str]]:
    with pytest.raises(ValidationError) as caught:
        BusinessSettingsUpdate.model_validate(body)
    return [(tuple(e["loc"]), e["type"]) for e in caught.value.errors()]


def locs(body: dict[str, object]) -> list[tuple[object, ...]]:
    return [loc for loc, _ in errors_of(body)]


# --- C2: the body ---------------------------------------------------------------------


def test_only_the_version_is_required():
    body = BusinessSettingsUpdate.model_validate({"version": 1})

    assert body.model_fields_set == {"version"}


@pytest.mark.parametrize("version", [None, 0, -1, "x"])
def test_the_version_is_required_and_positive(version):
    assert locs({"version": version}) == [("version",)]
    assert locs({}) == [("version",)]


def test_unknown_fields_are_refused():
    assert errors_of({"version": 1, "logo": "x"}) == [(("logo",), "extra_forbidden")]


@pytest.mark.parametrize("field", ["center_name", "time_zone", "operating_hours"])
def test_null_is_refused_for_name_zone_and_hours(field):
    assert locs({"version": 1, field: None}) == [(field,)]


def test_the_center_name_is_trimmed_and_never_blank():
    body = BusinessSettingsUpdate.model_validate(
        {"version": 1, "center_name": "  Centro Norte  "}
    )
    assert body.center_name == "Centro Norte"
    assert locs({"version": 1, "center_name": "   "}) == [("center_name",)]
    assert locs({"version": 1, "center_name": "x" * 101}) == [("center_name",)]
    assert BusinessSettingsUpdate.model_validate(
        {"version": 1, "center_name": "x" * 100}
    ).center_name == ("x" * 100)


@pytest.mark.parametrize("field", ["contact_phone", "contact_email", "address"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_null_or_blank_clears_a_contact_field(field, value):
    body = BusinessSettingsUpdate.model_validate({"version": 1, field: value})

    assert field in body.model_fields_set
    assert getattr(body, field) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contact_email", "no-at-sign"),
        ("contact_email", "a@b"),
        ("contact_email", "a b@c.mx"),
        ("contact_email", f"{'a' * 250}@b.mx"),
        ("contact_phone", "12345"),
        ("contact_phone", "phone 555"),
        ("contact_phone", "+52 55 1234 5678 9012 3456 7890 12"),
        ("address", "x" * 301),
    ],
)
def test_contact_formats(field, value):
    assert locs({"version": 1, field: value}) == [(field,)]


def test_valid_contact_values_are_trimmed():
    body = BusinessSettingsUpdate.model_validate(
        {
            "version": 1,
            "contact_email": " hola@classora.mx ",
            "contact_phone": " +52 (55) 1234-5678 ",
            "address": "  Av. Reforma 1, CDMX  ",
        }
    )

    assert body.contact_email == "hola@classora.mx"
    assert body.contact_phone == "+52 (55) 1234-5678"
    assert body.address == "Av. Reforma 1, CDMX"


def test_the_contact_patterns_are_identitys():
    assert settings_models.EMAIL_PATTERN == identity_models.EMAIL_PATTERN
    assert settings_models.PHONE_PATTERN == identity_models.PHONE_PATTERN
    assert schemas.EMAIL_REGEX == identity_schemas.EMAIL_REGEX
    assert schemas.PHONE_REGEX == identity_schemas.PHONE_REGEX


# --- C3: operating hours --------------------------------------------------------------


def hours(*entries: tuple[int, str, str]) -> list[dict[str, object]]:
    return [{"weekday": w, "opens_at": o, "closes_at": c} for w, o, c in entries]


def test_a_whole_week_and_an_empty_week_are_accepted():
    week = hours(*((day, "08:00", "21:30") for day in range(1, 8)))

    full = BusinessSettingsUpdate.model_validate(
        {"version": 1, "operating_hours": week}
    )
    empty = BusinessSettingsUpdate.model_validate({"version": 1, "operating_hours": []})

    assert [item.weekday for item in full.operating_hours or []] == list(range(1, 8))
    assert empty.operating_hours == []


@pytest.mark.parametrize(
    ("entry", "loc"),
    [
        ((0, "09:00", "10:00"), ("operating_hours", 0, "weekday")),
        ((8, "09:00", "10:00"), ("operating_hours", 0, "weekday")),
        ((1, "9:00", "10:00"), ("operating_hours", 0, "opens_at")),
        ((1, "09:00:00", "10:00"), ("operating_hours", 0, "opens_at")),
        ((1, "24:00", "10:00"), ("operating_hours", 0, "opens_at")),
        ((1, "09:60", "10:00"), ("operating_hours", 0, "opens_at")),
        ((1, "10:00", "10:00"), ("operating_hours", 0, "closes_at")),
        ((1, "10:00", "09:59"), ("operating_hours", 0, "closes_at")),
    ],
)
def test_invalid_hours(entry, loc):
    assert loc in locs({"version": 1, "operating_hours": hours(entry)})


def test_a_weekday_appears_once():
    week = hours((1, "09:00", "10:00"), (1, "11:00", "12:00"))

    assert locs({"version": 1, "operating_hours": week}) == [("operating_hours",)]


def test_at_most_seven_entries():
    week = hours(*((1 + day % 7, "09:00", "10:00") for day in range(8)))

    assert ("operating_hours",) in locs({"version": 1, "operating_hours": week})


def test_unknown_fields_of_an_entry_are_refused():
    entry = {"weekday": 1, "opens_at": "09:00", "closes_at": "10:00", "note": "x"}

    assert errors_of({"version": 1, "operating_hours": [entry]}) == [
        (("operating_hours", 0, "note"), "extra_forbidden")
    ]


# --- C4: the time zone ----------------------------------------------------------------


@pytest.mark.parametrize(
    "zone", ["America/Mexico_City", "America/Cancun", "Pacific/Kiritimati", "UTC"]
)
def test_any_iana_zone_is_accepted(zone):
    body = BusinessSettingsUpdate.model_validate({"version": 1, "time_zone": zone})

    assert body.time_zone == zone


@pytest.mark.parametrize(
    "zone", ["Mexico City", "america/mexico_city", "", "Mars/Olympus", "x" * 65]
)
def test_unknown_zones_are_refused(zone):
    assert locs({"version": 1, "time_zone": zone}) == [("time_zone",)]


def test_the_zones_come_from_the_system_zone_data():
    zones = schemas.available_time_zones()

    assert "America/Mexico_City" in zones
    assert all(re.fullmatch(r"[A-Za-z0-9_+\-/]+", zone) for zone in zones)


# --- D2: the registry -----------------------------------------------------------------


async def _never(session: object) -> bool:
    return False


async def _other(session: object) -> bool:
    return True


def test_a_name_is_registered_once():
    name = "tests.registry_once"
    try:
        administration.register_time_zone_dependency(name, _never)
        # The same check again (an app built twice in one process): no change.
        administration.register_time_zone_dependency(name, _never)
        assert administration.time_zone_dependencies()[name] is _never
        with pytest.raises(ValueError, match="already registered"):
            administration.register_time_zone_dependency(name, _other)
        assert administration.time_zone_dependencies()[name] is _never
    finally:
        administration.unregister_time_zone_dependency(name)
    assert name not in administration.time_zone_dependencies()


def test_the_app_registers_only_the_scheduling_checks():
    from app.main import build_app
    from app.modules.scheduling import service as scheduling

    build_app()
    # Building the app again (tests, the OpenAPI export) does not raise.
    build_app()

    assert dict(administration.time_zone_dependencies()) == {
        "scheduling.class_sessions": scheduling.has_class_sessions,
        "scheduling.class_series": scheduling.has_class_series,
        "scheduling.session_requests": scheduling.has_pending_session_requests,
    }
