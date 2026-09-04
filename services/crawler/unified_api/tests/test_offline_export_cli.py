from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from jobgraph_contracts.offline_bundle import BundleMode

from unified_api.offline_export import cli
from unified_api.offline_export.exporter import BundleExporter
from unified_api.tests.bundle_test_support import FakeExportRepository, records


def _run_cli_without_database_module(
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    project_root = Path(__file__).resolve().parents[2]
    python_path = os.pathsep.join(
        str(Path(item or os.getcwd()).resolve()) for item in sys.path
    )
    script = """
import builtins
import runpy
import sys

original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "unified_api.database":
        raise AssertionError("read-only CLI imported unified_api.database")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
sys.argv = ["unified_api.offline_export.cli", *sys.argv[1:]]
runpy.run_module("unified_api.offline_export.cli", run_name="__main__")
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = python_path
    return subprocess.run(
        [sys.executable, "-c", script, *arguments],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_does_not_import_database_module():
    result = _run_cli_without_database_module("--help")

    assert result.returncode == 0, result.stderr
    assert "{export,verify,inspect}" in result.stdout
    assert "dbutils" not in result.stderr


def test_verify_does_not_import_database_module(tmp_path):
    bundle = BundleExporter(FakeExportRepository(records(1))).export(
        output=tmp_path,
        mode=BundleMode.FULL,
    )

    result = _run_cli_without_database_module(
        "verify",
        str(bundle.output_path),
    )

    assert result.returncode == 0, result.stderr
    assert "status: verified" in result.stdout


def test_inspect_does_not_import_database_module(tmp_path):
    bundle = BundleExporter(FakeExportRepository(records(1))).export(
        output=tmp_path,
        mode=BundleMode.FULL,
    )

    result = _run_cli_without_database_module(
        "inspect",
        str(bundle.output_path),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["bundle_id"] == bundle.bundle_id


def test_export_initializes_database_before_export(monkeypatch, tmp_path):
    calls: list[str] = []
    database_module = ModuleType("unified_api.database")

    def ensure_schema() -> None:
        calls.append("ensure_schema")

    database_module.ensure_schema = ensure_schema  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "unified_api.database", database_module)

    from unified_api.offline_export import repository

    repository_instance = object()
    monkeypatch.setattr(
        repository,
        "MySQLExportRepository",
        lambda: repository_instance,
    )

    def validate_export_request(*, mode, limit) -> None:
        assert mode is BundleMode.INCREMENTAL
        assert limit == 1
        calls.append("validate_export_request")

    monkeypatch.setattr(cli, "validate_export_request", validate_export_request)

    class FakeBundleExporter:
        def __init__(self, value) -> None:
            assert value is repository_instance

        def export(self, **kwargs):
            assert kwargs["mode"] is BundleMode.INCREMENTAL
            assert kwargs["limit"] == 1
            calls.append("export")
            return SimpleNamespace(
                bundle_id="bundle-test",
                record_count=1,
                output_path=tmp_path / "bundle.zip",
            )

    monkeypatch.setattr(cli, "BundleExporter", FakeBundleExporter)

    result = cli.main(
        [
            "export",
            "--output",
            str(tmp_path),
            "--mode",
            "incremental",
            "--limit",
            "1",
        ]
    )

    assert result == 0
    assert calls == ["validate_export_request", "ensure_schema", "export"]
