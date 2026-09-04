from __future__ import annotations

import ast
import subprocess
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

CONTEXTS = (
    "access",
    "catalog",
    "discovery",
    "emerging_positions",
    "evaluation",
    "governance_feedback",
    "jd_lifecycle",
    "knowledge_graph",
    "market_intelligence",
    "matching_learning",
    "platform",
    "talent_acquisition",
    "tasks",
)
LEGACY_MODULES = (
    "app.application.account_management",
    "app.application.candidates",
    "app.application.embeddings",
    "app.application.evaluation",
    "app.application.feedback",
    "app.application.files",
    "app.application.governance",
    "app.application.jd",
    "app.application.knowledge_graph",
    "app.application.matching",
    "app.application.ocr",
    "app.application.positions",
    "app.application.recruitment",
    "app.application.resumes",
    "app.application.skills",
    "app.application.system",
    "app.application.trend_reports",
    "app.application.trends",
    "app.application.discovery",
    "app.application.discovery_queries",
    "app.application.emerging_positions",
    "app.application.tasks",
    "app.ports.discovery",
    "app.ports.emerging_position",
    "app.ports.tasks",
)
LEGACY_AUTHORITIES = {
    Path("application/discovery.py"),
    Path("application/discovery_queries.py"),
    Path("application/emerging_positions.py"),
    Path("application/tasks.py"),
    Path("ports/discovery.py"),
    Path("ports/emerging_position.py"),
    Path("ports/tasks.py"),
}

LEGACY_AUTHORITIES.update(
    path.relative_to(APP)
    for root in (APP / "application", APP / "ports")
    for path in root.glob("*.py")
    if path.name != "__init__.py"
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_contexts_expose_application_contracts_and_ports() -> None:
    for context in CONTEXTS:
        package = APP / "contexts" / context
        assert (package / "__init__.py").is_file()
        assert (package / "application.py").is_file()
        assert (package / "contracts.py").is_file()
        assert (package / "ports.py").is_file()


def test_context_facades_are_explicit_and_match_their_all_contracts() -> None:
    technical_names = {"dataclass", "Callable", "Mapping", "FrozenJsonObject"}
    for context in CONTEXTS:
        for filename in ("__init__.py", "application.py", "contracts.py", "ports.py"):
            path = APP / "contexts" / context / filename
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            assert not any(
                isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
                for node in ast.walk(tree)
            ), str(path.relative_to(ROOT))
            module_name = f"app.contexts.{context}" + (
                "" if filename == "__init__.py" else f".{path.stem}"
            )
            module = __import__(module_name, fromlist=["*"])
            exported = set(module.__all__)
            assert all(hasattr(module, name) for name in exported)
            public = {
                name
                for name, value in vars(module).items()
                if not name.startswith("_") and not isinstance(value, types.ModuleType)
            }
            assert public <= exported, f"{module_name}: {sorted(public - exported)}"
            if filename == "__init__.py":
                assert not technical_names & exported


def test_old_imports_are_identity_preserving_compatibility_paths() -> None:
    from app.application.discovery import RunDiscoveryCommand as OldDiscoveryCommand
    from app.application.emerging_positions import (
        EmergingPositionHandlers as OldEmergingHandlers,
    )
    from app.application.tasks import ManageTasks as OldManageTasks
    from app.contexts.discovery import RunDiscoveryCommand
    from app.contexts.emerging_positions import EmergingPositionHandlers
    from app.contexts.tasks import ManageTasks, TaskPayload
    from app.ports.tasks import TaskPayload as OldTaskPayload

    assert RunDiscoveryCommand is OldDiscoveryCommand
    assert EmergingPositionHandlers is OldEmergingHandlers
    assert ManageTasks is OldManageTasks
    assert TaskPayload is OldTaskPayload


def test_production_consumers_do_not_bypass_context_public_entries() -> None:
    violations: dict[str, list[str]] = {}
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP)
        if relative in LEGACY_AUTHORITIES or relative.parts[:1] == ("contexts",):
            continue
        bad = [module for module in _imports(path) if module.startswith(LEGACY_MODULES)]
        if bad:
            violations[str(relative)] = bad
    assert violations == {}


def test_api_and_composition_root_do_not_import_context_internals() -> None:
    internal_prefixes = tuple(
        f"app.contexts.{context}.{layer}"
        for context in CONTEXTS
        for layer in ("_applications", "_ports", "domain", "infrastructure")
    )
    violations: dict[str, list[str]] = {}
    paths = [
        *list((APP / "api").rglob("*.py")),
        *list((APP / "bootstrap").rglob("*.py")),
        *list((APP / "infrastructure").rglob("*.py")),
    ]
    for path in paths:
        bad = [module for module in _imports(path) if module.startswith(internal_prefixes)]
        if bad:
            violations[str(path.relative_to(APP))] = bad
    assert violations == {}


def test_contexts_do_not_import_another_context_internals() -> None:
    violations: dict[str, list[str]] = {}
    for context in CONTEXTS:
        package = APP / "contexts" / context
        forbidden = tuple(
            f"app.contexts.{other}.{layer}"
            for other in CONTEXTS
            if other != context
            for layer in ("_applications", "_ports", "domain", "infrastructure")
        )
        for path in package.rglob("*.py"):
            bad = [module for module in _imports(path) if module.startswith(forbidden)]
            if bad:
                violations[str(path.relative_to(APP))] = bad
    assert violations == {}


def test_composition_root_assembles_handlers_from_public_contexts() -> None:
    from app.contexts.discovery import PositionDiscoveryHandlers
    from app.contexts.emerging_positions import EmergingPositionHandlers
    from app.contexts.tasks import ManageTasks
    from app.main import app

    container = app.state.container
    assert isinstance(container.discovery, PositionDiscoveryHandlers)
    assert isinstance(container.emerging_positions, EmergingPositionHandlers)
    assert isinstance(container.tasks, ManageTasks)


def test_public_contexts_have_no_circular_imports_in_a_clean_interpreter() -> None:
    statement = "; ".join(
        [*(f"import app.contexts.{context}" for context in CONTEXTS), "import app.main"]
    )
    result = subprocess.run(
        [sys.executable, "-c", statement],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
