"""Strict main-system boundary checks for emerging discovery."""

from __future__ import annotations

import ast
from pathlib import Path

from app.integrations.emerging_discovery.client import EmergingDiscoveryClient

ROOT = Path(__file__).resolve().parents[1]


def test_main_system_has_no_discovery_algorithm_implementation_or_runtime_import():
    forbidden_names = {
        "legacy_clustering.py",
        "legacy_assessment.py",
        "germination.py",
        "providers.py",
    }
    integration_files = set((ROOT / "app" / "integrations" / "emerging_discovery").glob("*.py"))
    assert not ({path.name for path in integration_files} & forbidden_names)

    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif node.module:
                    modules = [node.module]
                assert not any(
                    module.startswith("services.emerging-discovery")
                    or module.startswith("services.emerging_discovery")
                    for module in modules
                ), f"cross-service runtime import in {path}"


def test_cluster_entrypoint_depends_on_stable_http_client_only():
    source = (ROOT / "app" / "infrastructure" / "discovery.py").read_text(encoding="utf-8")
    assert "EmergingDiscoveryClient" in source
    assert "legacy_clustering" not in source
    assert "legacy_assessment" not in source
    assert "EMERGING_DISCOVERY_ENABLED" not in source
    assert EmergingDiscoveryClient.__doc__ and "HTTP-only" in EmergingDiscoveryClient.__doc__


def test_emerging_position_modules_cannot_import_main_embedding_or_discovery_algorithms():
    emerging_modules = (
        ROOT / "app" / "api" / "v1" / "emerging_positions.py",
        ROOT / "app" / "application" / "emerging_positions.py",
        ROOT / "app" / "infrastructure" / "emerging_positions.py",
        ROOT / "app" / "integrations" / "emerging_discovery" / "client.py",
    )
    forbidden_prefixes = (
        "app.services.embedding_service",
        "app.integrations.local",
        "app.integrations.registry",
        "services.emerging_discovery",
        "services.emerging-discovery",
    )
    for path in emerging_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        assert not any(
            module.startswith(forbidden_prefixes) for module in imported_modules
        ), f"emerging-position runtime crosses the discovery boundary in {path}"
