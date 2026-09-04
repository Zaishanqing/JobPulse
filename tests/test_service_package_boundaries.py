from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


JOBPULSE_ROOT = Path(__file__).resolve().parents[1]
MAIN_ROOT = JOBPULSE_ROOT / "apps" / "api"


@dataclass(frozen=True)
class PackageRoot:
    service: str
    module: str
    path: Path

    @property
    def parent(self) -> Path:
        return self.path.parent


# These are import roots used by the real service processes, not guessed names
# derived from repository folder strings.  Duplicate "app" and "src" package
# names are intentionally retained so imports are resolved local-first.
PACKAGE_ROOTS = (
    PackageRoot("main", "app", MAIN_ROOT / "app"),
    PackageRoot(
        "knowledge-graph",
        "app",
        JOBPULSE_ROOT / "services" / "knowledge-graph" / "app",
    ),
    PackageRoot(
        "emerging-discovery",
        "app",
        JOBPULSE_ROOT / "services" / "emerging-discovery" / "app",
    ),
    PackageRoot(
        "matching-service",
        "app",
        JOBPULSE_ROOT / "services" / "matching-service" / "app",
    ),
    PackageRoot(
        "trend-intelligence",
        "app",
        JOBPULSE_ROOT / "services" / "trend-intelligence" / "app",
    ),
    PackageRoot(
        "embedding-service",
        "app",
        JOBPULSE_ROOT / "services" / "embedding-service" / "app",
    ),
    PackageRoot(
        "jd-extraction",
        "src",
        JOBPULSE_ROOT / "services" / "jd-extraction" / "src",
    ),
    PackageRoot(
        "cv-extraction",
        "src",
        JOBPULSE_ROOT / "services" / "cv-extraction" / "src",
    ),
    PackageRoot(
        "crawler",
        "unified_api",
        JOBPULSE_ROOT / "services" / "crawler" / "unified_api",
    ),
    PackageRoot(
        "crawler",
        "multi_company_scraper",
        JOBPULSE_ROOT / "services" / "crawler" / "multi_company_scraper",
    ),
    PackageRoot(
        "crawler",
        "historical_jd",
        JOBPULSE_ROOT / "services" / "crawler" / "historical_jd",
    ),
    PackageRoot(
        "crawler",
        "patches",
        JOBPULSE_ROOT / "services" / "crawler" / "patches",
    ),
)
SHARED_CONTRACT_ROOT = JOBPULSE_ROOT / "packages" / "contracts" / "jobgraph_contracts"
IGNORED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "tests",
    }
)


def _production_files(package: PackageRoot) -> Iterable[Path]:
    return (
        path
        for path in package.path.rglob("*.py")
        if not IGNORED_PARTS.intersection(path.parts)
    )


def _module_name(package: PackageRoot, path: Path) -> str:
    relative = path.relative_to(package.path)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((package.module, *parts))


def _imports(package: PackageRoot, path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    current = _module_name(package, path).split(".")
    current_package = current if path.name == "__init__.py" else current[:-1]
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(current_package) - node.level + 1
                prefix = current_package[: max(0, keep)]
                module = ".".join((*prefix, *((node.module or "").split("."))))
            else:
                module = node.module or ""
            if module:
                result.append((node.lineno, module))
    return result


def _module_exists(package: PackageRoot, module: str) -> bool:
    parts = module.split(".")
    own_parts = package.module.split(".")
    if parts[: len(own_parts)] != own_parts:
        return False
    suffix = parts[len(own_parts) :]
    target = package.path.joinpath(*suffix)
    return target.is_dir() or target.with_suffix(".py").is_file()


def _local_package(importer: PackageRoot, module: str) -> PackageRoot | None:
    return next(
        (
            package
            for package in PACKAGE_ROOTS
            if package.service == importer.service and _module_exists(package, module)
        ),
        None,
    )


def _foreign_packages(importer: PackageRoot, module: str) -> tuple[PackageRoot, ...]:
    if _local_package(importer, module):
        return ()
    return tuple(
        package
        for package in PACKAGE_ROOTS
        if package.service != importer.service and _module_exists(package, module)
    )


def _display(path: Path) -> str:
    return path.relative_to(JOBPULSE_ROOT).as_posix()


def test_declared_package_roots_are_real_runtime_roots():
    assert all(package.path.is_dir() for package in PACKAGE_ROOTS)
    assert all(
        package.path.name == package.module.split(".")[-1]
        for package in PACKAGE_ROOTS
    )
    assert SHARED_CONTRACT_ROOT.is_dir()


def test_all_materialized_service_owners_are_declared_once():
    assert {package.service for package in PACKAGE_ROOTS} == {
        "main",
        "knowledge-graph",
        "emerging-discovery",
        "matching-service",
        "trend-intelligence",
        "embedding-service",
        "jd-extraction",
        "cv-extraction",
        "crawler",
    }


def test_crawler_is_materialized_with_its_service_package_roots():
    crawler_root = JOBPULSE_ROOT / "services" / "crawler"
    assert crawler_root.is_dir()
    assert all(
        (crawler_root / package).is_dir()
        for package in ("unified_api", "multi_company_scraper", "historical_jd", "patches")
    )


def test_kg_and_discovery_inner_layers_do_not_resolve_to_adapters_or_orm():
    services = {"knowledge-graph", "emerging-discovery"}
    forbidden_external = ("sqlalchemy", "fastapi", "sqlmodel")
    forbidden_local_parts = {
        "api",
        "bootstrap",
        "database",
        "infrastructure",
        "models",
        "repositories",
        "session",
    }
    violations = []
    for package in PACKAGE_ROOTS:
        if package.service not in services:
            continue
        for layer in ("domain", "application"):
            layer_root = package.path / layer
            for path in layer_root.rglob("*.py"):
                for lineno, module in _imports(package, path):
                    local = _local_package(package, module)
                    local_parts = module.split(".")
                    local_layer = local_parts[1] if local and len(local_parts) > 1 else ""
                    if module.startswith(forbidden_external) or (
                        local and local_layer in forbidden_local_parts
                    ):
                        violations.append((_display(path), lineno, module))
    assert violations == [], (
        "KG/Discovery domain and application must depend on domain/ports/contracts, "
        f"not infrastructure, ORM, sessions, or repositories: {violations}"
    )


def test_services_do_not_import_another_service_runtime_package():
    violations = []
    for package in PACKAGE_ROOTS:
        for path in _production_files(package):
            for lineno, module in _imports(package, path):
                foreign = _foreign_packages(package, module)
                if foreign:
                    violations.append(
                        (
                            package.service,
                            _display(path),
                            lineno,
                            module,
                            sorted({target.service for target in foreign}),
                        )
                    )
    assert violations == [], (
        "Crawler, extraction, KG, Discovery, and the main backend may cross service "
        "boundaries only through neutral contracts or external clients/messages; "
        f"runtime imports found: {violations}"
    )


def test_service_adapters_do_not_bypass_foreign_public_boundaries():
    adapter_markers = {"adapter", "adapters", "infrastructure"}
    violations = []
    for package in PACKAGE_ROOTS:
        for path in _production_files(package):
            relative_parts = set(path.relative_to(package.path).parts)
            if not adapter_markers.intersection(relative_parts):
                continue
            for lineno, module in _imports(package, path):
                foreign = _foreign_packages(package, module)
                if foreign:
                    violations.append(
                        (
                            package.service,
                            _display(path),
                            lineno,
                            module,
                            sorted({target.service for target in foreign}),
                        )
                    )
    assert violations == [], (
        "Adapters/infrastructure must use declared contracts, ports, clients, or "
        f"messages instead of foreign internals: {violations}"
    )


def test_shared_contract_package_remains_the_declared_python_message_boundary():
    violations = []
    shared = PackageRoot("shared-contracts", "jobgraph_contracts", SHARED_CONTRACT_ROOT)
    for path in _production_files(shared):
        for lineno, module in _imports(shared, path):
            foreign = tuple(
                package for package in PACKAGE_ROOTS if _module_exists(package, module)
            )
            if foreign:
                violations.append(
                    (_display(path), lineno, module, sorted({item.service for item in foreign}))
                )
    assert violations == [], (
        "jobgraph_contracts must stay runtime-neutral and cannot import service "
        f"internals: {violations}"
    )
