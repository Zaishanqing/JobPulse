from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCOPES = (
    ROOT / "app" / "contexts",
    ROOT / "services" / "emerging-discovery" / "app" / "application",
    ROOT / "services" / "emerging-discovery" / "app" / "ports",
    ROOT / "services" / "knowledge-graph" / "app" / "application",
    ROOT / "services" / "knowledge-graph" / "app" / "ports",
)
OFFLINE_EVALUATION_MODULES = {
    Path("services/emerging-discovery/app/application/candidate_lifecycle_evaluation.py"),
    Path("services/knowledge-graph/app/application/evolution_evaluation.py"),
}


def _banned(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    rendered = ast.unparse(annotation)
    return (
        rendered == "dict"
        or rendered.startswith("dict[")
        or "Any" in rendered
        or rendered == "object"
        or "Mapping[str, object]" in rendered
        or "list[dict" in rendered
    )


def _bare_request_body(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return True
    rendered = ast.unparse(annotation)
    return _banned(annotation) or rendered.startswith(("list[", "set[", "tuple["))


def test_public_application_context_and_port_signatures_are_typed() -> None:
    violations: list[str] = []
    for scope in SCOPES:
        if not scope.exists():
            continue
        for path in scope.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if relative in OFFLINE_EVALUATION_MODULES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents = {
                child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("_") and not node.name.startswith("__"):
                        continue
                    annotations = [
                        *(argument.annotation for argument in node.args.args),
                        *(argument.annotation for argument in node.args.kwonlyargs),
                        node.returns,
                    ]
                    if any(_banned(item) for item in annotations):
                        violations.append(f"{relative}:{node.lineno}:{node.name}")
                elif (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and isinstance(parents.get(node), ast.ClassDef)
                ):
                    if not node.target.id.startswith("_") and _banned(node.annotation):
                        violations.append(f"{relative}:{node.lineno}:{node.target.id}")
    assert violations == [], "\n".join(violations)


def test_flat_application_and_port_modules_are_compatibility_only() -> None:
    violations: list[str] = []
    for package in (ROOT / "app" / "application", ROOT / "app" / "ports"):
        for path in package.glob("*.py"):
            if path.name == "__init__.py" or path.stem in {"discovery", "discovery_queries"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in tree.body):
                violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_all_router_request_parameters_do_not_use_bare_json_types() -> None:
    violations: list[str] = []
    roots = (
        ROOT / "app" / "api" / "v1",
        ROOT / "services" / "emerging-discovery" / "app" / "api",
        ROOT / "services" / "knowledge-graph" / "app" / "api",
    )
    for scope in roots:
        for path in scope.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                routed = node.name in {"endpoint", "route"} or any(
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "router"
                    for decorator in node.decorator_list
                )
                if not routed:
                    continue
                arguments = [*node.args.args, *node.args.kwonlyargs]
                positional_defaults = [None] * (
                    len(node.args.args) - len(node.args.defaults)
                ) + list(node.args.defaults)
                defaults = [*positional_defaults, *node.args.kw_defaults]
                for argument, default in zip(arguments, defaults):
                    if (
                        isinstance(default, ast.Call)
                        and isinstance(default.func, ast.Name)
                        and default.func.id == "Depends"
                    ):
                        continue
                    if _bare_request_body(argument.annotation):
                        relative = path.relative_to(ROOT)
                        violations.append(f"{relative}:{node.lineno}:{argument.arg}")
    assert violations == [], "\n".join(violations)


def test_new_request_models_are_visible_in_openapi() -> None:
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    for name in (
        "VectorSearchRequest",
        "EnterpriseCandidateMatchRequest",
        "OCRResultUpdateRequest",
        "JDBatchCreateRequest",
        "JDParseBatchRequest",
    ):
        assert name in schemas
