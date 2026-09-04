from __future__ import annotations

import ast
from pathlib import Path

from app.application.contracts import AlgorithmSelection
from app.application.discovery_identity import DiscoveryIdentityResult


ROOT = Path(__file__).resolve().parents[1] / "app"


def test_deprecated_application_dto_module_is_removed() -> None:
    assert not (ROOT / "application" / "dto.py").exists()
    assert not any(
        "app.application.dto" in path.read_text(encoding="utf-8")
        for path in ROOT.rglob("*.py")
    )


def test_identity_api_returns_named_types() -> None:
    tree = ast.parse(
        (ROOT / "application" / "discovery_identity.py").read_text(encoding="utf-8")
    )
    functions = {
        node.name: ast.unparse(node.returns)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    assert functions["normalize_algorithm"] == AlgorithmSelection.__name__
    assert functions["discovery_identity"] == DiscoveryIdentityResult.__name__
    for signature in functions.values():
        assert "Any" not in signature
        assert "dict" not in signature
        assert "object" not in signature


def test_domain_values_do_not_own_boundary_serializers() -> None:
    tree = ast.parse((ROOT / "domain" / "discovery.py").read_text(encoding="utf-8"))
    methods = {
        child.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, ast.FunctionDef)
    }
    assert methods.isdisjoint({"from_mapping", "as_dict", "model_dump", "to_external"})
