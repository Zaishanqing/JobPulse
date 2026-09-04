"""Verify package imports without runtime path mutation (task 02 final)."""
import ast
from pathlib import Path
import subprocess
import sys

import pytest

IMPORT_CASES = [
    "import jobgraph_contracts",
    "import multi_company_scraper",
    "import unified_api",
    "from multi_company_scraper.normalizer import Normalizer",
    "from unified_api.services.boss_detail import fetch_boss_job_detail",
    "from unified_api.services.company_service import run_company_crawl",
    "from unified_api.services.liepin_service import run_liepin_crawl",
]


@pytest.mark.parametrize("code", IMPORT_CASES)
def test_external_imports(tmp_path, code):
    """Each import must succeed from a temporary empty directory."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Import failed: {code}\nstderr: {result.stderr}"
    )


def test_no_sys_path_mutation_in_crawler_tree():
    """Reject executable runtime search-path mutations via AST inspection.

    Parsing Python syntax avoids false positives from documentation and from
    this test's own explanation strings.
    """
    crawler_root = Path(__file__).resolve().parents[2]
    violations = []
    ignored_parts = {"__pycache__", "test-results", ".pytest_cache"}
    for path in crawler_root.rglob("*.py"):
        if ignored_parts.intersection(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            is_sys_path = (
                isinstance(owner, ast.Attribute)
                and owner.attr == "path"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "sys"
            )
            if is_sys_path and node.func.attr in {"insert", "append"}:
                violations.append(f"{path}:{node.lineno}: sys.path.{node.func.attr}")
    if violations:
        pytest.fail("sys.path injection found:\n" + "\n".join(violations))


def test_jobgraph_contracts_importable():
    import jobgraph_contracts
    assert jobgraph_contracts.__file__.endswith("__init__.py")
