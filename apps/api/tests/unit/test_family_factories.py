"""The families factories without a database (#33 H6, #34 H6, #172 G4)."""

import re
from datetime import date

from app.modules.families.ages import is_minor
from app.modules.families.models import FAMILY_NAME_MAX_LENGTH, PHONE_PATTERN
from tests.factories.families import (
    FamilyFactory,
    GuardianFactory,
    StudentEmergencyContactFactory,
    StudentFactory,
    fictitious_phone,
)

PHONE = re.compile(PHONE_PATTERN.replace("[:space:]", r"\s"))


def test_built_students_are_active_minors_with_names():
    students = StudentFactory.build_batch(5)

    assert all(s.is_active for s in students)
    assert all(s.given_names.strip() and s.family_names.strip() for s in students)
    assert all(len(s.family_names.split()) >= 2 for s in students)
    assert all(is_minor(s.birthdate, date(2029, 12, 31)) for s in students)


def test_contact_phones_are_valid_and_fictitious():
    contacts = StudentEmergencyContactFactory.build_batch(3, position=1)

    assert all(PHONE.fullmatch(c.phone) for c in contacts)
    assert all(c.phone.startswith("+52 00 ") for c in contacts)
    assert len({c.phone for c in contacts}) == 3
    assert fictitious_phone(123456789) == "+52 00 2345 6789"


def test_built_guardians_are_active_without_an_account_or_contact():
    guardians = GuardianFactory.build_batch(3)

    assert all(g.is_active for g in guardians)
    assert all(g.user_id is None for g in guardians)
    assert all((g.email, g.phone) == (None, None) for g in guardians)
    assert all(g.given_names.strip() and g.family_names.strip() for g in guardians)


def test_built_families_are_active_with_a_valid_name():
    families = FamilyFactory.build_batch(3)

    assert all(f.is_active for f in families)
    assert all(f.name.startswith("Familia ") for f in families)
    assert all(f.name == f.name.strip() for f in families)
    assert all(len(f.name) <= FAMILY_NAME_MAX_LENGTH for f in families)
