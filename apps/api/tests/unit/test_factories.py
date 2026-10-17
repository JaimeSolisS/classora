"""Factory base without a database (M1-17 C1-C3)."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import factory
import pytest

from tests.factories.base import (
    RESERVED_EMAIL_DOMAINS,
    ClassoraFactory,
    FactoryCreateError,
    get_faker,
    money,
    reseed,
)
from tests.integration.samples import SampleProfileFactory

MEXICAN_STATES = {
    "Aguascalientes", "Baja California", "Baja California Sur", "Campeche",
    "Chiapas", "Chihuahua", "Coahuila de Zaragoza", "Colima", "Distrito Federal",
    "Durango", "Guanajuato", "Guerrero", "Hidalgo", "Jalisco",
    "Michoacán de Ocampo", "Morelos", "México", "Nayarit", "Nuevo León",
    "Oaxaca", "Puebla", "Querétaro", "Quintana Roo", "San Luis Potosí",
    "Sinaloa", "Sonora", "Tabasco", "Tamaulipas", "Tlaxcala",
    "Veracruz de Ignacio de la Llave", "Yucatán", "Zacatecas",
}  # fmt: skip


class Address:
    def __init__(self, state: str) -> None:
        self.state = state


class AddressFactory(ClassoraFactory):
    """No ``locale=`` argument: the base default must apply."""

    class Meta:
        model = Address

    state = factory.Faker("state")


def test_faker_locale_is_es_mx():
    assert get_faker().locales == ["es_MX"]


def test_faker_declarations_default_to_es_mx():
    states = {AddressFactory.build().state for _ in range(100)}

    assert states <= MEXICAN_STATES
    assert len(states) > 5


def test_same_seed_gives_identical_builds():
    reseed(7)
    first = [vars_of(SampleProfileFactory.build()) for _ in range(3)]
    reseed(7)
    second = [vars_of(SampleProfileFactory.build()) for _ in range(3)]

    assert first == second


def test_different_seed_gives_different_builds():
    reseed(7)
    first = vars_of(SampleProfileFactory.build())
    reseed(8)
    second = vars_of(SampleProfileFactory.build())

    assert first != second


def test_create_is_refused():
    with pytest.raises(FactoryCreateError, match="persist"):
        SampleProfileFactory.create()
    with pytest.raises(FactoryCreateError):
        SampleProfileFactory.create_batch(2)


def test_emails_use_reserved_domains_only():
    emails = [profile.email for profile in SampleProfileFactory.build_batch(200)]

    for email in emails:
        local, _, domain = email.partition("@")
        assert local
        assert domain in RESERVED_EMAIL_DOMAINS, email


def test_sample_object_follows_adr_0004_types():
    profile = SampleProfileFactory.build()

    assert type(profile.amount) is Decimal
    assert profile.amount.as_tuple().exponent == -2
    assert type(profile.starts_on) is date
    assert type(profile.occurred_at) is datetime
    assert profile.occurred_at.utcoffset() == timedelta(0)


def test_money_bounds_and_scale():
    class Price:
        def __init__(self, amount: Decimal) -> None:
            self.amount = amount

    class PriceFactory(ClassoraFactory):
        class Meta:
            model = Price

        amount = money("0.00", "0.00")

    assert repr(PriceFactory.build().amount) == "Decimal('0.00')"


def vars_of(instance: object) -> dict[str, object]:
    return {k: v for k, v in vars(instance).items() if not k.startswith("_sa_")}
