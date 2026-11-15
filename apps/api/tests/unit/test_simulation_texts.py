"""The simulation's text pools (#543 E11 (a))."""

from collections.abc import Iterator

from tests.factories.base import RESERVED_EMAIL_DOMAINS
from tests.factories.simulation.texts import (
    POOLS,
    RECEIPT_MESSAGES,
    SAME_IN_BOTH_LANGUAGES,
)

ACCENTED = set("áéíóúñ¿¡ÁÉÍÓÚÑ")


def leaves(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, f"{path}[{index}]")
    else:  # pragma: no cover - the pools hold strings, dicts and lists only
        raise AssertionError(f"unexpected value at {path}")


def test_both_pools_have_the_same_keys_and_list_lengths():
    es = dict(leaves(POOLS["es"]))
    en = dict(leaves(POOLS["en"]))

    assert es.keys() == en.keys()


def test_no_spanish_value_is_identical_to_its_english_value():
    es = dict(leaves(POOLS["es"]))
    en = dict(leaves(POOLS["en"]))

    same = {path for path in es if es[path] == en[path]}
    assert same <= SAME_IN_BOTH_LANGUAGES


def test_english_values_have_no_spanish_accents():
    for path, value in leaves(POOLS["en"]):
        assert not ACCENTED & set(value), path
    assert not ACCENTED & set(RECEIPT_MESSAGES["en"])


def test_no_value_is_blank_and_lists_hold_distinct_values():
    for language in ("es", "en"):
        for path, value in leaves(POOLS[language]):
            assert value.strip() == value and value, path
        for key, value in POOLS[language].items():
            if isinstance(value, list):
                assert len(set(value)) == len(value), key


def test_contact_emails_use_reserved_domains():
    for language in ("es", "en"):
        email = POOLS[language]["contact_email"]
        assert isinstance(email, str)
        assert email.rsplit("@", 1)[1] in RESERVED_EMAIL_DOMAINS


def test_the_experience_pool_covers_every_full_profile_tutor():
    for language in ("es", "en"):
        experience = POOLS[language]["professional_experience"]
        assert isinstance(experience, list) and len(experience) >= 12
