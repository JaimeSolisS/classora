"""Catalog database constraints with raw INSERTs (#32 A3)."""

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.catalog_support import insert_level, insert_subject
from tests.integration.conftest import run_sql_async

pytestmark = pytest.mark.anyio


async def test_a_valid_level_gets_the_defaults(head_url):
    level_id = await insert_level(head_url, "Primaria", 10)

    rows = await run_sql_async(
        head_url,
        "SELECT is_active, version, seed_key FROM education_levels WHERE id = :id",
        {"id": level_id},
    )
    assert tuple(rows[0]) == (True, 1, None)


@pytest.mark.parametrize("name", [" Álgebra", "Álgebra ", "", "   ", "\t"])
async def test_untrimmed_or_blank_level_names_are_rejected(head_url, name):
    with pytest.raises(IntegrityError, match="ck_education_levels_name"):
        await insert_level(head_url, name)


@pytest.mark.parametrize("name", [" Álgebra", "", "   "])
async def test_untrimmed_or_blank_subject_names_are_rejected(head_url, name):
    level_id = await insert_level(head_url, "Secundaria")

    with pytest.raises(IntegrityError, match="ck_subjects_name"):
        await insert_subject(head_url, level_id, name)


async def test_level_names_are_unique_ignoring_case(head_url):
    await insert_level(head_url, "Primaria")

    with pytest.raises(IntegrityError, match="uq_education_levels_name_ci"):
        await insert_level(head_url, "PRIMARIA", is_active=False)


async def test_accents_are_not_folded(head_url):
    await insert_level(head_url, "Fisica")

    await insert_level(head_url, "Física")


async def test_a_subject_name_is_unique_per_level_ignoring_case(head_url):
    secundaria = await insert_level(head_url, "Secundaria")
    preparatoria = await insert_level(head_url, "Preparatoria")
    await insert_subject(head_url, secundaria, "Matemáticas")

    # The same name under another level is a different subject.
    await insert_subject(head_url, preparatoria, "Matemáticas")
    with pytest.raises(IntegrityError, match="uq_subjects_level_name_ci"):
        await insert_subject(head_url, secundaria, "MATEMÁTICAS", is_active=False)


async def test_a_level_with_a_subject_cannot_be_deleted(head_url):
    level_id = await insert_level(head_url, "Secundaria")
    await insert_subject(head_url, level_id, "Historia")

    with pytest.raises(
        IntegrityError, match="fk_subjects_education_level_id_education_levels"
    ):
        await run_sql_async(
            head_url, "DELETE FROM education_levels WHERE id = :id", {"id": level_id}
        )


async def test_a_subject_needs_an_existing_level(head_url):
    with pytest.raises(IntegrityError, match="fk_subjects_education_level_id"):
        await insert_subject(
            head_url, "00000000-0000-4000-8000-000000000000", "Historia"
        )


@pytest.mark.parametrize("sort_order", [-1, 10000])
async def test_sort_order_outside_0_to_9999_is_rejected(head_url, sort_order):
    with pytest.raises(IntegrityError, match="ck_education_levels_sort_order_range"):
        await insert_level(head_url, "Primaria", sort_order)


@pytest.mark.parametrize("seed_key", ["Elementary", "1st", "high-school", ""])
async def test_seed_key_format(head_url, seed_key):
    with pytest.raises(IntegrityError, match="ck_education_levels_seed_key_format"):
        await insert_level(head_url, "Primaria", seed_key=seed_key)


async def test_seed_key_is_unique(head_url):
    await insert_level(head_url, "Primaria", seed_key="elementary")

    with pytest.raises(IntegrityError, match="uq_education_levels_seed_key"):
        await insert_level(head_url, "Otra", seed_key="elementary")


async def test_names_longer_than_100_fail(head_url):
    with pytest.raises(DBAPIError, match="value too long"):
        await insert_level(head_url, "x" * 101)
