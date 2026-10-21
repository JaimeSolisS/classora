"""Module boundary rule (ADR-0002, D2 (a)): enforced with the stdlib ``ast``.

Code under ``app/modules/<x>/`` may import another module ``y`` only through
``app.modules.y.service`` or ``app.modules.y.schemas`` (or their submodules),
and never ``app.workflows``. Imports inside functions and ``if TYPE_CHECKING:``
blocks count; relative imports are resolved first. Modules are discovered from
the directory, so every new module is checked without editing this test. The
scanned code is parsed, never imported.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
MODULES = ("app", "modules")
PUBLIC_SUBMODULES = frozenset({"service", "schemas"})

# ADR-0002 (D3): the read-only reporting module (M8) may also import other
# modules' models for cross-module read joins. Nothing else is exempt; any
# other exception needs a new ADR.
READS_OTHER_MODULES_MODELS = frozenset({"reporting"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: imports {self.target}"


def module_name(api_root: Path, path: Path) -> list[str]:
    parts = list(path.relative_to(api_root).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def is_forbidden(module: str, target: str) -> bool:
    """Whether code in module ``module`` may not import ``target``."""
    parts = target.split(".")
    if parts[:2] == ["app", "workflows"]:
        return True
    if parts[:2] != list(MODULES) or len(parts) < 3 or parts[2] == module:
        return False
    submodule = parts[3] if len(parts) > 3 else None
    if submodule in PUBLIC_SUBMODULES:
        return False
    return not (submodule == "models" and module in READS_OTHER_MODULES_MODELS)


def import_targets(node: ast.AST, package: list[str]) -> list[str]:
    """The dotted names an import statement reaches (empty for other nodes).

    ``from a.b import c`` reaches ``a.b.c`` (a submodule or a name in ``a.b``),
    so ``from app.modules import y`` is the bare package ``app.modules.y``.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level:
        base = package[: len(package) - (node.level - 1)]
        prefix = ".".join([*base, *([node.module] if node.module else [])])
    else:
        prefix = node.module or ""
    return [f"{prefix}.{alias.name}" for alias in node.names]


def find_violations(api_root: Path) -> list[Violation]:
    """Every forbidden import under ``<api_root>/app/modules/<x>/``."""
    violations: list[Violation] = []
    modules_dir = api_root.joinpath(*MODULES)
    for module_dir in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        if module_dir.name == "__pycache__":
            continue
        for path in sorted(module_dir.rglob("*.py")):
            name = module_name(api_root, path)
            package = name if path.name == "__init__.py" else name[:-1]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for target in import_targets(node, package):
                    if is_forbidden(module_dir.name, target):
                        violations.append(
                            Violation(
                                path=path.relative_to(api_root).as_posix(),
                                line=node.lineno,
                                target=target,
                            )
                        )
    return sorted(violations, key=lambda v: (v.path, v.line, v.target))


def failure_message(violations: list[Violation]) -> str:
    return (
        "Module boundary violations (ADR-0002: import other modules only through"
        " their service or schemas, never app.workflows):\n"
        + "\n".join(str(v) for v in violations)
    )


def test_real_modules_respect_the_boundary():
    violations = find_violations(API_ROOT)

    assert not violations, failure_message(violations)


def test_the_real_tree_is_scanned():
    # Guards against a checker that silently scans nothing.
    assert (API_ROOT / "app" / "modules" / "identity" / "service.py").is_file()
    assert module_name(API_ROOT, API_ROOT / "app/modules/identity/__init__.py") == [
        "app",
        "modules",
        "identity",
    ]


def targets(module: str) -> set[str]:
    """Every ``app.*`` name the files of ``app/modules/<module>`` import."""
    found: set[str] = set()
    for path in (API_ROOT / "app" / "modules" / module).rglob("*.py"):
        name = module_name(API_ROOT, path)
        package = name if path.name == "__init__.py" else name[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            found.update(import_targets(node, package))
    return {t for t in found if t.startswith("app.")}


def test_families_imports_only_core_and_the_catalog_and_identity_public_api():
    # #33 H3, #34 H3: families uses app.core, its own files, catalog.service /
    # catalog.schemas, identity.service / identity.schemas and (#37 E6)
    # administration.service only; neither catalog nor identity imports
    # anything from families.
    allowed = (
        "app.core.",
        "app.modules.families.",
        "app.modules.catalog.service",
        "app.modules.catalog.schemas",
        "app.modules.identity.service",
        "app.modules.identity.schemas",
        "app.modules.administration.service",
    )
    families = targets("families")
    assert "app.modules.catalog.service" in families  # the scan found imports
    assert "app.modules.identity.service" in families
    assert not {t for t in families if not t.startswith(allowed)}
    assert not {t for t in targets("catalog") if "families" in t}
    assert not {t for t in targets("identity") if "families" in t}


def test_administration_imports_no_other_module():
    # #37 E6: administration (business settings, the time-zone registry)
    # sits below the other modules; identity and families reach it only
    # through administration.service. #40 E4: the approval inbox keeps it so
    # (handlers and the account-reference resolver are registered by the
    # composition roots).
    administration = targets("administration")
    # The scan found imports.
    assert any(t.startswith("app.modules.administration.") for t in administration)
    assert not {
        t
        for t in administration
        if t.startswith("app.modules.")
        and not t.startswith("app.modules.administration.")
    }
    for module in ("identity", "families", "catalog"):
        reached = {t for t in targets(module) if "administration" in t}
        assert reached == {"app.modules.administration.service"}, module


def test_catalog_imports_only_core_and_the_administration_service():
    # #38 D4: the catalog (levels, subjects, periods, closures) uses app.core,
    # its own files and administration.service (the center time zone of
    # is_center_closed_at) only.
    allowed = (
        "app.core.",
        "app.modules.catalog.",
        "app.modules.administration.service",
    )
    catalog = targets("catalog")
    assert "app.modules.administration.service" in catalog
    assert not {t for t in catalog if not t.startswith(allowed)}


def test_staff_imports_only_core_and_public_apis():
    # #43 A1: staff (tutor profiles) uses app.core, its own files,
    # identity.service / identity.schemas and (#44) catalog.service /
    # catalog.schemas and administration.service (the approval inbox and
    # notifications); no module imports staff (app.wiring registers it).
    allowed = (
        "app.core.",
        "app.modules.staff.",
        "app.modules.identity.service",
        "app.modules.identity.schemas",
        "app.modules.catalog.service",
        "app.modules.catalog.schemas",
        "app.modules.administration.service",
    )
    staff = targets("staff")
    assert "app.modules.identity.service" in staff  # the scan found imports
    assert not {t for t in staff if not t.startswith(allowed)}
    for module in ("identity", "catalog", "administration", "families"):
        assert not {t for t in targets(module) if "staff" in t}, module


def test_courses_imports_only_core_and_public_apis():
    # #64 A1: courses (templates and snapshots) uses app.core, its own files,
    # catalog.service / catalog.schemas and the identity public modules
    # only; no module imports courses yet (#65 will, through its service).
    allowed = (
        "app.core.",
        "app.modules.courses.",
        "app.modules.catalog.service",
        "app.modules.catalog.schemas",
        "app.modules.identity.service",
        "app.modules.identity.schemas",
    )
    courses = targets("courses")
    assert "app.modules.catalog.service" in courses  # the scan found imports
    assert not {t for t in courses if not t.startswith(allowed)}
    for module in ("identity", "catalog", "administration", "families", "staff"):
        assert not {t for t in targets(module) if "courses" in t}, module


# --- The checker on a synthetic tree (B5) ----------------------------------------

FORBIDDEN_SOURCE = """\
import app.modules.billing.models
from app.modules.billing.models import Invoice
from app.modules.billing import repository
from ..billing import models
from ..billing.router import router
import app.modules.billing
from app.modules import billing
import app.workflows.enrollment
from app.workflows import enrollment
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.modules.billing.tasks import run
def later():
    from app.modules.billing.permissions import CAN_PAY
    return CAN_PAY
from ... import workflows
"""

FORBIDDEN = [
    (1, "app.modules.billing.models"),
    (2, "app.modules.billing.models.Invoice"),
    (3, "app.modules.billing.repository"),
    (4, "app.modules.billing.models"),
    (5, "app.modules.billing.router.router"),
    (6, "app.modules.billing"),
    (7, "app.modules.billing"),
    (8, "app.workflows.enrollment"),
    (9, "app.workflows.enrollment"),
    (12, "app.modules.billing.tasks.run"),
    (14, "app.modules.billing.permissions.CAN_PAY"),
    (16, "app.workflows"),
]

ALLOWED_SOURCE = """\
import os
import sqlalchemy
from fastapi import APIRouter
from app.core.uow import UnitOfWork
import app.core.models
from app.modules.billing import service
from app.modules.billing import schemas
from app.modules.billing.service import charge
from app.modules.billing.schemas import InvoiceOut
from app.modules.billing.service.helpers import fee
import app.modules.billing.service
from ..billing import service as billing_service
from ..billing.schemas import InvoiceOut as Out
from . import models
from .models import Student
from app.modules.students.repository import find
"""


def write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_checker_reports_exactly_the_forbidden_imports(tmp_path):
    write(tmp_path, "app/modules/students/service.py", FORBIDDEN_SOURCE)
    write(tmp_path, "app/modules/students/api.py", ALLOWED_SOURCE)
    # One package deeper, the same relative imports need one more dot.
    write(
        tmp_path,
        "app/modules/students/sub/__init__.py",
        ALLOWED_SOURCE.replace("..", "..."),
    )
    write(tmp_path, "app/modules/billing/__init__.py", "")
    write(tmp_path, "app/modules/__init__.py", "from app.workflows import x\n")

    violations = find_violations(tmp_path)

    assert [(v.line, v.target) for v in violations] == FORBIDDEN
    assert {v.path for v in violations} == {"app/modules/students/service.py"}


def test_relative_bare_package_import_is_forbidden(tmp_path):
    write(tmp_path, "app/modules/students/service.py", "from .. import billing\n")

    assert [str(v) for v in find_violations(tmp_path)] == [
        "app/modules/students/service.py:1: imports app.modules.billing"
    ]


def test_reporting_may_import_models_but_nothing_else(tmp_path):
    source = (
        "from app.modules.billing.models import Invoice\n"
        "from app.modules.students import models\n"
        "from app.modules.billing.repository import find\n"
        "from app.workflows import x\n"
        "from app.modules.billing import service\n"
    )
    write(tmp_path, "app/modules/reporting/queries.py", source)
    write(tmp_path, "app/modules/other/queries.py", source)

    violations = find_violations(tmp_path)

    assert [str(v) for v in violations] == [
        "app/modules/other/queries.py:1: imports app.modules.billing.models.Invoice",
        "app/modules/other/queries.py:2: imports app.modules.students.models",
        "app/modules/other/queries.py:3: imports app.modules.billing.repository.find",
        "app/modules/other/queries.py:4: imports app.workflows.x",
        "app/modules/reporting/queries.py:3: imports "
        "app.modules.billing.repository.find",
        "app/modules/reporting/queries.py:4: imports app.workflows.x",
    ]


def test_failure_message_lists_every_violation_and_cites_the_adr(tmp_path):
    write(
        tmp_path,
        "app/modules/zz_probe/__init__.py",
        "from app.modules.identity import models\nimport app.workflows\n",
    )

    message = failure_message(find_violations(tmp_path))

    assert "ADR-0002" in message
    assert (
        "app/modules/zz_probe/__init__.py:1: imports app.modules.identity.models"
        in (message)
    )
    assert "app/modules/zz_probe/__init__.py:2: imports app.workflows" in message
