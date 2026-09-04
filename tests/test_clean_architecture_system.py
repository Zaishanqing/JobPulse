from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPOSITORY_ROOT / "apps" / "api"
SERVICES = (
    REPOSITORY_ROOT / "services" / "emerging-discovery",
    REPOSITORY_ROOT / "services" / "knowledge-graph",
)
EXTERNAL_SERVICE_ROOTS = (
    REPOSITORY_ROOT / "services" / "crawler",
    REPOSITORY_ROOT / "services" / "jd-extraction",
    REPOSITORY_ROOT / "services" / "cv-extraction",
)
# Historical dependency frozen by integration batch 1. Moving these mapping
# value objects would affect the publication/KG business path, so this batch
# permits only the exact existing edge and rejects every new domain violation.
FROZEN_DOMAIN_DEPENDENCIES = {
    ("app/domain/jd_publication.py", "app.integrations.knowledge_graph.mappings"),
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def _files(root: Path, layer: str) -> list[Path]:
    target = root / "app" / layer
    return list(target.rglob("*.py")) if target.exists() else []


def _relative(path: Path) -> str:
    return str(path.relative_to(REPOSITORY_ROOT))


def test_main_domain_dependency_direction_is_inward_only():
    forbidden = (
        "fastapi",
        "sqlalchemy",
        "app.api",
        "app.application",
        "app.ports",
        "app.infrastructure",
        "app.integrations",
        "app.models",
        "app.services",
    )
    violations = {
        _relative(path): module
        for path in _files(ROOT, "domain")
        for module in _imports(path)
        if module.startswith(forbidden)
        and (path.relative_to(ROOT).as_posix(), module) not in FROZEN_DOMAIN_DEPENDENCIES
    }
    assert violations == {}, (
        "domain must not depend on application, ports, infrastructure, API, "
        f"ORM, or HTTP frameworks: {violations}"
    )


def test_ports_do_not_depend_on_concrete_infrastructure():
    forbidden = (
        "sqlalchemy",
        "app.api",
        "app.infrastructure",
        "app.integrations",
        "app.models",
        "app.services",
    )
    violations = {
        _relative(path): module
        for root in (ROOT, *SERVICES)
        for path in _files(root, "ports")
        for module in _imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}, f"ports must not import concrete adapters: {violations}"


def test_main_backend_does_not_import_other_service_runtime_packages():
    forbidden = (
        "multi_company_scraper",
        "unified_api",
        "crawler",
        "jdextraction",
        "cvextraction",
        "services.emerging_discovery",
        "services.emerging-discovery",
        "services.knowledge_graph",
        "services.knowledge-graph",
    )
    violations = {
        _relative(path): module
        for path in (ROOT / "app").rglob("*.py")
        for module in _imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}, (
        "main backend may use HTTP ports/clients and stable contracts, but not "
        f"another service's runtime package: {violations}"
    )


def test_crawler_and_extraction_do_not_import_main_persistence():
    forbidden = (
        "app.models",
        "app.infrastructure",
        "app.core.database",
        "app.repositories",
    )
    violations = {
        _relative(path): module
        for root in EXTERNAL_SERVICE_ROOTS
        for path in root.rglob("*.py")
        if not any(part in {".venv", "__pycache__", ".pytest_cache"} for part in path.parts)
        for module in _imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}, (
        "extraction services must publish through contracts or HTTP and must not "
        f"import main persistence: {violations}"
    )


def test_crawler_is_materialized_as_an_independent_service_boundary():
    crawler_root = REPOSITORY_ROOT / "services" / "crawler"
    assert crawler_root.is_dir()
    assert (crawler_root / "unified_api").is_dir()
    assert (crawler_root / "pyproject.toml").is_file()


def test_shared_contract_package_is_runtime_neutral():
    forbidden = (
        "app",
        "fastapi",
        "sqlalchemy",
        "httpx",
        "requests",
        "multi_company_scraper",
        "unified_api",
    )
    violations = {
        _relative(path): module
        for path in (REPOSITORY_ROOT / "packages" / "contracts" / "jobgraph_contracts").rglob("*.py")
        for module in _imports(path)
        if module == "app" or module.startswith(tuple(f"{name}." for name in forbidden))
    }
    assert violations == {}, (
        "jobgraph_contracts must contain stable DTOs only, not service runtime "
        f"dependencies: {violations}"
    )


def test_inner_layers_are_framework_and_adapter_independent_across_all_services():
    forbidden = (
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "app.api",
        "app.config",
        "app.core.config",
        "app.models",
        "app.infrastructure",
        "app.services",
        "src.adapters",
    )
    violations = {}
    for root in (ROOT, *SERVICES):
        for layer in ("domain", "application"):
            for path in _files(root, layer):
                bad = [module for module in _imports(path) if module.startswith(forbidden)]
                if bad:
                    violations[str(path.relative_to(ROOT))] = bad
    assert violations == {}


def test_p1_11a_task_records_enter_through_the_current_context_uow():
    targets = {
        "talent_acquisition/_applications/candidates.py": "_match_one",
        "matching_learning/_applications/matching.py": "run",
        "talent_acquisition/_applications/resumes.py": "parse",
        "market_intelligence/_applications/trends.py": "run",
        "evaluation/_applications/evaluation.py": "run_cluster",
    }
    for filename, method_name in targets.items():
        source = (ROOT / "app" / "contexts" / filename).read_text("utf-8")
        tree = ast.parse(source)
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        calls = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        names = [node.func.attr for node in calls]
        assert "create_succeeded" not in names
        if filename in {
            "matching_learning/_applications/matching.py",
            "talent_acquisition/_applications/candidates.py",
            "market_intelligence/_applications/trends.py",
        }:
            assert "prepare_succeeded" not in names
            if filename != "market_intelligence/_applications/trends.py":
                assert "create_task" in names
            continue
        prepare_line = next(node.lineno for node in calls if node.func.attr == "prepare_succeeded")
        add_line = next(node.lineno for node in calls if node.func.attr == "add_task")
        commit_line = next(node.lineno for node in calls if node.func.attr == "commit")
        assert prepare_line < add_line < commit_line


def test_infrastructure_never_delegates_to_legacy_services():
    violations = {
        str(path.relative_to(ROOT)): module
        for root in (ROOT, *SERVICES)
        for path in _files(root, "infrastructure")
        for module in _imports(path)
        if module.startswith("app.services")
    }
    assert violations == {}


def test_http_adapters_do_not_execute_orm_or_transaction_operations():
    violations = []
    for root in (ROOT, *SERVICES):
        paths = _files(root, "api")
        bootstrap = root / "app" / "bootstrap"
        if bootstrap.exists():
            paths.extend(bootstrap.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"db", "session"}
                    and node.func.attr
                    in {
                        "query",
                        "get",
                        "execute",
                        "scalar",
                        "scalars",
                        "add",
                        "add_all",
                        "delete",
                        "commit",
                        "rollback",
                        "flush",
                        "refresh",
                    }
                ):
                    violations.append((str(path.relative_to(ROOT)), node.lineno, node.func.attr))
    assert violations == []


def test_http_adapters_do_not_import_private_application_symbols():
    violations = []
    for root in (ROOT, *SERVICES):
        paths = _files(root, "api")
        bootstrap = root / "app" / "bootstrap"
        if bootstrap.exists():
            paths.extend(bootstrap.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and (node.module or "").startswith("app.application")
                ):
                    violations.extend(
                        (str(path.relative_to(ROOT)), alias.name)
                        for alias in node.names
                        if alias.name.startswith("_")
                    )
    assert violations == []


def test_repositories_never_control_transactions():
    violations = []
    for root in (ROOT, *SERVICES):
        for path in _files(root, "infrastructure"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for class_node in (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and "Repository" in node.name
            ):
                for node in ast.walk(class_node):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"commit", "rollback"}
                    ):
                        violations.append(
                            (str(path.relative_to(ROOT)), class_node.name, node.lineno)
                        )
    assert violations == []


def test_candidate_update_contract_excludes_algorithm_owned_values():
    source = (ROOT / "app" / "schemas" / "emerging_position.py").read_text("utf-8")
    update = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "EmergingPositionUpdate"
    )
    fields = {
        node.target.id
        for node in update.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert fields.isdisjoint({"germination_score", "score_dimensions", "evidence_jd_ids"})
    assert "extra=\"forbid\"" in source


def test_discovery_ports_are_explicit_and_no_implicit_capability_checks_remain():
    port_source = (
        SERVICES[0] / "app" / "ports" / "providers.py"
    ).read_text("utf-8")
    for name in (
        "EmbeddingPort",
        "ClusteringPort",
        "ReferencePort",
        "DefinitionPort",
        "LineagePort",
        "GerminationPort",
    ):
        assert f"class {name}" in port_source
    for root in (ROOT / "app", *(service / "app" for service in SERVICES)):
        assert all("hasattr(" not in path.read_text("utf-8") for path in root.rglob("*.py"))


def test_database_resources_are_created_only_by_factories():
    database_files = [
        ROOT / "app" / "core" / "database.py",
        SERVICES[0] / "app" / "infrastructure" / "database.py",
        SERVICES[1] / "app" / "database.py",
    ]
    forbidden_names = {"engine", "SessionLocal", "_default_database"}
    violations = []
    for path in database_files:
        tree = ast.parse(path.read_text("utf-8"))
        for node in tree.body:
            names = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
            violations.extend((str(path.relative_to(ROOT)), name) for name in names if name in forbidden_names)
    assert violations == []


def test_cross_service_contracts_do_not_import_foreign_entities():
    violations = {
        str(path.relative_to(ROOT)): module
        for path in (ROOT / "app").rglob("*.py")
        for module in _imports(path)
        if module.startswith(("services.emerging_discovery", "services.knowledge_graph"))
    }
    assert violations == {}


def test_each_backend_has_one_declared_composition_root():
    roots = {
        ROOT: ROOT / "app" / "bootstrap" / "container.py",
        SERVICES[0]: SERVICES[0] / "app" / "bootstrap" / "application.py",
        SERVICES[1]: SERVICES[1] / "app" / "bootstrap" / "application.py",
    }
    assert all(path.is_file() for path in roots.values())
    for service, composition_root in roots.items():
        bootstrap_files = {
            path
            for path in (service / "app" / "bootstrap").glob("*.py")
            if path.name not in {"__init__.py", "settings.py"}
        }
        assert bootstrap_files == {composition_root}


def test_api_never_constructs_use_cases_or_runtime_resources():
    forbidden_suffixes = ("UseCase", "UseCases", "UnitOfWork", "Repository", "Provider", "Session")
    violations = []
    for root in (ROOT, *SERVICES):
        for path in _files(root, "api"):
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                if name.startswith("Manage") or name.endswith(forbidden_suffixes):
                    violations.append((str(path.relative_to(ROOT)), node.lineno, name))
    assert violations == []


def test_api_never_imports_database_or_concrete_adapters():
    forbidden = (
        "sqlalchemy",
        "app.database",
        "app.models",
        "app.infrastructure",
        "app.integrations",
        "app.services",
    )
    violations = {
        str(path.relative_to(ROOT)): module
        for root in (ROOT, *SERVICES)
        for path in _files(root, "api")
        for module in _imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}


def test_production_code_never_imports_legacy_services():
    violations = {
        str(path.relative_to(ROOT)): module
        for root in (ROOT, *SERVICES)
        for path in (root / "app").rglob("*.py")
        for module in _imports(path)
        if module.startswith("app.services")
    }
    assert violations == {}


def test_main_application_cross_context_imports_stay_within_owned_module_families():
    families = (
        frozenset({"discovery", "discovery_queries"}),
        frozenset({"jd", "jd_common", "jd_extraction_postprocessor", "jd_management", "jd_parsing", "jd_pipeline", "jd_quality", "jd_review", "jd_schema", "jd_support"}),
        frozenset({"config_defaults", "system"}),
    )
    ownership = {module: family for family in families for module in family}
    violations = []
    for path in _files(ROOT, "application"):
        owner = path.stem
        for module in _imports(path):
            prefix = "app.application."
            if not module.startswith(prefix):
                continue
            target = module.removeprefix(prefix).split(".", 1)[0]
            owner_family = ownership.get(owner)
            if owner == target or (owner_family is not None and owner_family == ownership.get(target)):
                continue
            violations.append((str(path.relative_to(ROOT)), module))
    assert violations == []


def test_typed_core_domains_do_not_regress_to_mapping_contracts():
    core_domains = ("matching.py", "positions.py", "trend_analysis.py")
    violations = {}
    for name in core_domains:
        path = ROOT / "app" / "domain" / name
        source = path.read_text("utf-8")
        if "Mapping[str, object]" in source or "dict[str, Any]" in source:
            violations[name] = "untyped core contract"
    assert violations == {}


def test_new_core_calculations_return_explicit_result_types():
    expected_types = {
        ROOT / "app" / "domain" / "evaluation.py": {
            "EvaluationMetrics",
            "EvaluationErrorCase",
            "EvaluationConfigSnapshot",
            "EvaluationOutcome",
        },
        SERVICES[0] / "app" / "domain" / "germination.py": {
            "GerminationDimensions",
            "GerminationAssessmentResult",
        },
        SERVICES[0] / "app" / "domain" / "lineage.py": {
            "LineageScore",
            "LineageRelation",
        },
        SERVICES[1] / "app" / "domain" / "publishing.py": {
            "PublishGateFacts",
            "PublishGateResult",
            "GateViolation",
        },
        SERVICES[1] / "app" / "domain" / "policies.py": {
            "EvidenceAlignment",
            "QualityAssessment",
        },
    }
    for path, names in expected_types.items():
        classes = {
            node.name for node in ast.parse(path.read_text("utf-8")).body
            if isinstance(node, ast.ClassDef)
        }
        assert names <= classes, path


def test_production_does_not_import_knowledge_graph_service_compatibility_layer():
    compatibility_module = "app.integrations.knowledge_graph.service"
    violations = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "app").rglob("*.py")
        if path != ROOT / "app" / "integrations" / "knowledge_graph" / "service.py"
        and compatibility_module in _imports(path)
    }
    assert violations == set()


def test_legacy_src_package_is_removed():
    assert not (ROOT / "src").exists()


def test_frontend_cache_and_typescript_build_state_are_not_tracked():
    import subprocess

    repository = Path(
        subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    tracked = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository.as_posix()}",
            "-C",
            str(repository),
            "ls-files",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any("/.pnpm-store/" in f"/{path}" for path in tracked)
    assert not any(path.endswith(".tsbuildinfo") for path in tracked)
    ignore_rules = (repository / ".gitignore").read_text(encoding="utf-8")
    assert ".pnpm-store/" in ignore_rules
    assert "**/.pnpm-store/" in ignore_rules
    assert "*.tsbuildinfo" in ignore_rules


def test_python_packaging_discovers_app_and_neutral_contract_packages():
    from setuptools import find_packages

    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    contracts_root = REPOSITORY_ROOT / "packages" / "contracts"
    contracts_configuration = (contracts_root / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.setuptools.packages.find]' in configuration
    assert 'include = ["app*"]' in configuration
    assert 'include = ["jobgraph_contracts*"]' in contracts_configuration
    assert 'exclude = ["tests*", "scripts*"]' in configuration
    assert '"src"' not in configuration
    discovered = set(find_packages(where=ROOT, include=("app*",)))
    expected = {
        ".".join(path.parent.relative_to(ROOT).parts)
        for path in (ROOT / "app").rglob("__init__.py")
    }
    assert discovered == expected
    contract_packages = set(
        find_packages(where=contracts_root, include=("jobgraph_contracts*",))
    )
    expected_contracts = {
        ".".join(path.parent.relative_to(contracts_root).parts)
        for path in (contracts_root / "jobgraph_contracts").rglob("__init__.py")
    }
    assert contract_packages == expected_contracts


def test_production_startup_never_creates_schema_from_metadata():
    violations = {
        str(path.relative_to(ROOT))
        for root in (ROOT, *SERVICES)
        for path in (root / "app").rglob("*.py")
        if "metadata.create_all" in path.read_text("utf-8")
    }
    assert violations == set()
