"""Procrastinate schema guard (M1-16 B2): the committed schema copy and the
newest Procrastinate Alembic revision match the installed package."""

import importlib.metadata
import importlib.util
from pathlib import Path
from types import ModuleType

from procrastinate.schema import SchemaManager

API_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = API_ROOT / "migrations" / "versions"
SCHEMA_COPIES = API_ROOT / "migrations" / "procrastinate"


def load_revision(path: Path) -> ModuleType:
    # Loaded as a plain module: Alembic's ``op`` is only used inside upgrade().
    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def procrastinate_revisions() -> list[ModuleType]:
    """Revisions that define ``PROCRASTINATE_VERSION``, oldest first."""
    modules = [load_revision(path) for path in sorted(VERSIONS.glob("[0-9]*.py"))]
    return [m for m in modules if hasattr(m, "PROCRASTINATE_VERSION")]


def test_committed_schema_copy_matches_the_package():
    committed = (SCHEMA_COPIES / "3.10.0" / "schema.sql").read_text(encoding="utf-8")

    assert committed == SchemaManager.get_schema()


def test_installed_procrastinate_has_an_alembic_revision():
    installed = importlib.metadata.version("procrastinate")
    newest = procrastinate_revisions()[-1]

    assert newest.PROCRASTINATE_VERSION == installed, (
        f"Procrastinate {installed} is installed, but the newest Procrastinate "
        f"Alembic revision ({Path(newest.__file__ or '').name}) is for "
        f"{newest.PROCRASTINATE_VERSION}. Add a new Alembic revision that "
        f"applies the migration SQL for {installed} (procrastinate/sql/migrations/) "
        "and sets PROCRASTINATE_VERSION (ADR-0003)."
    )


def test_revision_0002_reads_only_the_committed_copy():
    (first, *_) = procrastinate_revisions()
    source = Path(first.__file__ or "").read_text(encoding="utf-8")

    assert first.revision == "0002"
    assert first.SCHEMA_SQL == SCHEMA_COPIES / "3.10.0" / "schema.sql"
    assert first.SCHEMA_SQL.is_file()
    # Never the installed package: an upgrade must not change what 0002 creates.
    assert "import procrastinate" not in source
    assert "from procrastinate" not in source
    assert "SchemaManager" not in source
