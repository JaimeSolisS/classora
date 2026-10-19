"""Ages and the center date (#33 B1-B3; ADR-0007 D17)."""

from datetime import UTC, date, datetime

import pytest

from app.core import clock as app_clock
from app.core.clock import DEFAULT_CENTER_TIME_ZONE, center_today
from app.modules.families import service
from app.modules.families.ages import is_minor


def test_the_center_zone_is_mexico_city_and_resolves_without_tzdata():
    from zoneinfo import ZoneInfo

    assert DEFAULT_CENTER_TIME_ZONE == "America/Mexico_City"
    assert (
        ZoneInfo(DEFAULT_CENTER_TIME_ZONE).utcoffset(datetime(2026, 10, 19)) is not None
    )


def test_center_today_is_the_mexico_city_date(clock):
    clock.set(datetime(2026, 10, 18, 5, 30, tzinfo=UTC))

    assert center_today() == date(2026, 10, 17)
    clock.set(datetime(2026, 10, 18, 6, 0, tzinfo=UTC))
    assert center_today() == date(2026, 10, 18)


def test_the_18th_birthday_starts_at_midnight_in_mexico_city(clock):
    born = date(2008, 10, 19)

    # 23:59:59 on the 18th in Mexico City; the UTC date is already the 19th.
    clock.set(datetime(2026, 10, 19, 5, 59, 59, tzinfo=UTC))
    assert app_clock.now().date() == date(2026, 10, 19)
    assert is_minor(born, center_today()) is True
    clock.set(datetime(2026, 10, 19, 6, 0, 0, tzinfo=UTC))
    assert is_minor(born, center_today()) is False


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
