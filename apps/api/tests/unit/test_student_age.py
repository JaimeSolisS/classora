"""Ages and the date in a zone (#33 B1-B3, #37 E2; ADR-0007 D17).

The center's zone comes from the business settings (#37); the integration
tests (``test_business_settings.py``) prove ``administration.service.
center_today`` reads it. Here ``today_in`` gets the default zone."""

from datetime import UTC, date, datetime

import pytest

from app.core import clock as app_clock
from app.core.clock import DEFAULT_CENTER_TIME_ZONE, today_in
from app.modules.families import service
from app.modules.families.ages import is_minor


def test_the_center_zone_is_mexico_city_and_resolves_without_tzdata():
    from zoneinfo import ZoneInfo

    assert DEFAULT_CENTER_TIME_ZONE == "America/Mexico_City"
    assert (
        ZoneInfo(DEFAULT_CENTER_TIME_ZONE).utcoffset(datetime(2026, 10, 19)) is not None
    )


def test_today_in_mexico_city(clock):
    clock.set(datetime(2026, 10, 18, 5, 30, tzinfo=UTC))

    assert today_in(DEFAULT_CENTER_TIME_ZONE) == date(2026, 10, 17)
    clock.set(datetime(2026, 10, 18, 6, 0, tzinfo=UTC))
    assert today_in(DEFAULT_CENTER_TIME_ZONE) == date(2026, 10, 18)


def test_today_in_follows_the_zone_it_is_given(clock):
    clock.set(datetime(2026, 10, 17, 10, 30, tzinfo=UTC))

    assert today_in("Pacific/Kiritimati") == date(2026, 10, 18)
    assert today_in("America/Mexico_City") == date(2026, 10, 17)
    assert today_in("UTC") == date(2026, 10, 17)


def test_the_clock_has_no_zone_free_center_today():
    assert not hasattr(app_clock, "center_today")


def test_the_18th_birthday_starts_at_midnight_in_mexico_city(clock):
    born = date(2008, 10, 19)

    # 23:59:59 on the 18th in Mexico City; the UTC date is already the 19th.
    clock.set(datetime(2026, 10, 19, 5, 59, 59, tzinfo=UTC))
    assert app_clock.now().date() == date(2026, 10, 19)
    assert is_minor(born, today_in(DEFAULT_CENTER_TIME_ZONE)) is True
    clock.set(datetime(2026, 10, 19, 6, 0, 0, tzinfo=UTC))
    assert is_minor(born, today_in(DEFAULT_CENTER_TIME_ZONE)) is False


@pytest.mark.parametrize(
    ("today", "minor"),
    [
        (date(2026, 2, 28), True),
        (date(2026, 3, 1), False),
    ],
)
def test_born_on_29_february(today, minor):
    assert is_minor(date(2008, 2, 29), today) is minor


@pytest.mark.parametrize(
    ("born", "minor"),
    [
        (date(2008, 10, 18), False),
        (date(2008, 10, 19), False),
        (date(2008, 10, 20), True),
        (date(2026, 10, 19), True),
        (date(1950, 1, 1), False),
    ],
)
def test_is_minor_around_the_birthday(born, minor):
    assert is_minor(born, date(2026, 10, 19)) is minor


def test_the_service_re_exports_is_minor_for_other_modules():
    assert service.is_minor is is_minor
