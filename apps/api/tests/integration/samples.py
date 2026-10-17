"""Sample factory and test seeds for the test-only models (M1-17).

They show how domain tasks use the framework: a factory extends
``ClassoraFactory`` with the base declarations, and a reference seed is one
``UpsertSeed`` with fixed literal rows. Every value is synthetic.
"""

import factory

from app.seeds.registry import UpsertSeed
from tests.factories.base import (
    ClassoraFactory,
    calendar_date,
    instant,
    money,
    reserved_email,
)
from tests.integration.models import SampleProfile, SeedReference

# Fixed literals, as every reference seed (no Faker, no dates).
TEST_REFERENCE_ROWS = (
    {"code": "alpha", "label": "Alfa"},
    {"code": "beta", "label": "Beta"},
    {"code": "gamma", "label": "Gamma"},
)

TEST_REFERENCE_SEED = UpsertSeed(
    name="test_references", model=SeedReference, rows=TEST_REFERENCE_ROWS
)


class SampleProfileFactory(ClassoraFactory):
    class Meta:
        model = SampleProfile

    full_name = factory.Faker("name")
    email = reserved_email()
    amount = money("100.00", "5000.00")
    starts_on = calendar_date()
    occurred_at = instant()
