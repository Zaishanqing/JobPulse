import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tree(relative_path: str) -> ast.AST:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _used_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _called_attributes(node: ast.AST) -> set[str]:
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def test_jd_application_and_ports_do_not_depend_on_http_or_persistence_frameworks():
    modules = _imported_modules(_tree("app/ports/jd_repository.py"))
    for path in (ROOT / "app" / "application").glob("jd*.py"):
        modules |= _imported_modules(ast.parse(path.read_text(encoding="utf-8")))

    forbidden_prefixes = ("fastapi", "sqlalchemy", "app.models", "app.infrastructure")
    assert not any(module.startswith(forbidden_prefixes) for module in modules)


def test_jd_parse_task_query_is_read_only_at_the_application_boundary():
    method = _function(
        _tree("app/contexts/jd_lifecycle/_applications/jd_parsing.py"),
        "get_parse_task",
    )

    assert "commit" not in _called_attributes(method)
    assert "rollback" not in _called_attributes(method)


def test_jd_facade_is_composed_from_single_responsibility_use_case_groups():
    assert len((ROOT / "app/application/jd.py").read_text(encoding="utf-8").splitlines()) < 80
    expected = {
        "jd_management.py",
        "jd_parsing.py",
        "jd_review.py",
        "jd_quality.py",
        "jd_support.py",
    }
    assert expected <= {path.name for path in (ROOT / "app/application").glob("jd*.py")}


def test_jd_router_does_not_call_legacy_services_or_persistence_adapters():
    modules = _imported_modules(_tree("app/api/v1/jds.py"))

    assert not any(module.startswith("app.services") for module in modules)
    assert not any(module.startswith("app.models") for module in modules)
    assert "app.infrastructure.jd_repository" not in modules


def test_reserved_jd_routes_only_map_http_and_call_application_use_cases():
    tree = _tree("app/api/v1/reserved.py")
    routes = {
        name: _function(tree, name)
        for name in (
            "create_jd_image",
            "get_jd_parse_task",
            "duplicate_check_batch",
            "inflation_check_batch",
            "mark_jd_skill_abnormal",
        )
    }
    forbidden_names = {
        "Session",
        "get_db",
        "get_authorized_task",
        "serialize_task",
        "_ensure_internal",
        "SqlAlchemyJDRepository",
        "TaskRecord",
    }

    for route in routes.values():
        assert _used_names(route).isdisjoint(forbidden_names)

    for name in ("duplicate_check_batch", "inflation_check_batch"):
        route = routes[name]
        assert not any(
            isinstance(node, (ast.For, ast.AsyncFor, ast.ListComp, ast.SetComp, ast.DictComp))
            for node in ast.walk(route)
        )

    assert "duplicate_check_batch" in _called_attributes(routes["duplicate_check_batch"])
    assert "duplicate_check" not in _called_attributes(routes["duplicate_check_batch"])
    assert "inflation_check_batch" in _called_attributes(routes["inflation_check_batch"])
    assert "inflation_check" not in _called_attributes(routes["inflation_check_batch"])
    assert "get_parse_task" in _called_attributes(routes["get_jd_parse_task"])
    assert "isinstance" not in _used_names(routes["mark_jd_skill_abnormal"])


def test_jd_repositories_never_commit_transactions():
    tree = _tree("app/infrastructure/jd_repository.py")
    repository_classes = {
        "SqlAlchemyJDRepository",
        "SqlAlchemyFileRepository",
        "SqlAlchemyTaskRepository",
    }
    commit_calls: list[tuple[str, int]] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in repository_classes:
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "commit"
            ):
                commit_calls.append((node.name, child.lineno))

    assert commit_calls == []


def test_jd_task_adapter_does_not_delegate_transaction_control_to_task_service():
    modules = _imported_modules(_tree("app/infrastructure/jd_repository.py"))

    assert "app.services.task_service" not in modules


def test_jd_application_error_has_no_http_status_semantics():
    tree = _tree("app/contexts/jd_lifecycle/_applications/jd_common.py")
    error = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "JDApplicationError"
    )
    attributes = {
        node.attr
        for node in ast.walk(error)
        if isinstance(node, ast.Attribute)
    }

    assert "status_code" not in attributes
    assert "fastapi" not in _imported_modules(tree)


def test_jd_application_does_not_construct_compatibility_responses():
    forbidden_fragments = ('"compatibility"', '"legacy_fields"')
    violations = {}
    for path in (ROOT / "app" / "application").glob("jd*.py"):
        source = path.read_text(encoding="utf-8")
        found = [fragment for fragment in forbidden_fragments if fragment in source]
        if found:
            violations[path.name] = found

    assert violations == {}


def test_jd_domain_policy_has_no_framework_or_adapter_dependencies():
    modules = set()
    for name in ("jd.py", "jd_policies.py"):
        modules |= _imported_modules(_tree(f"app/domain/{name}"))

    forbidden = ("fastapi", "pydantic", "sqlalchemy", "app.models", "app.infrastructure", "app.core.config")
    assert not any(module.startswith(forbidden) for module in modules)


def test_jd_application_public_methods_use_typed_boundaries():
    violations = []
    for path in (ROOT / "app" / "application").glob("jd*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            annotations = [
                argument.annotation
                for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if argument.annotation is not None
            ]
            if node.returns is not None:
                annotations.append(node.returns)
            rendered = [ast.unparse(annotation) for annotation in annotations]
            if any("Any" in value or "dict[" in value for value in rendered):
                violations.append((path.name, node.name, rendered))

    assert violations == []
