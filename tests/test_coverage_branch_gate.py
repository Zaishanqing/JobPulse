from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="module")
def aggregate_coverage_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "aggregate-coverage.py"
    spec = importlib.util.spec_from_file_location("jobpulse_aggregate_coverage", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_complete_reports(
    aggregate_coverage_module,
    artifact_dir: Path,
    branch_coverage: dict[str, int],
) -> None:
    modules = []
    for spec in aggregate_coverage_module.MODULES:
        covered_branches = branch_coverage.get(spec.slug, 100)
        source_root = aggregate_coverage_module.SOURCE_POLICY["included"][spec.slug][0]
        source_path = source_root if source_root.endswith(".py") else f"{source_root}/module.py"
        coverage = {
            "totals": {
                "num_statements": 100,
                "covered_lines": 100,
                "num_branches": 100,
                "covered_branches": covered_branches,
            },
            "files": {source_path: {}},
        }
        (artifact_dir / f"{spec.slug}.json").write_text(
            json.dumps(coverage), encoding="utf-8"
        )
        (artifact_dir / f"{spec.slug}.xml").write_text(
            "<coverage />", encoding="utf-8"
        )
        (artifact_dir / f"{spec.slug}.junit.xml").write_text(
            '<testsuite><testcase name="passes" /></testsuite>', encoding="utf-8"
        )
        (artifact_dir / f"{spec.slug}.log").write_text("passed", encoding="utf-8")
        modules.append(
            {
                "slug": spec.slug,
                "exit_code": 0,
                "started_at": 0,
                "log": f"{spec.slug}.log",
            }
        )
    (artifact_dir / "execution.json").write_text(
        json.dumps({"git_sha": "test-sha", "modules": modules}), encoding="utf-8"
    )


@pytest.mark.parametrize(("slug", "covered_branches"), [("jd-extraction", 55), ("crawler", 14)])
def test_module_branch_regression_fails_final_gate(
    aggregate_coverage_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    slug: str,
    covered_branches: int,
):
    monkeypatch.setattr(aggregate_coverage_module, "_git_sha", lambda: "test-sha")
    _write_complete_reports(
        aggregate_coverage_module, tmp_path, {slug: covered_branches}
    )

    summary = aggregate_coverage_module.build_summary(tmp_path)

    row = next(row for row in summary["modules"] if row["slug"] == slug)
    assert row["branch_gate_passed"] is False
    assert summary["gates"]["module_branch_coverage"] is False
    assert summary["gates"]["passed"] is False


def test_current_module_branch_baselines_pass_final_gate(
    aggregate_coverage_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(aggregate_coverage_module, "_git_sha", lambda: "test-sha")
    _write_complete_reports(
        aggregate_coverage_module,
        tmp_path,
        {"jd-extraction": 60, "crawler": 60},
    )

    summary = aggregate_coverage_module.build_summary(tmp_path)
    markdown = aggregate_coverage_module._markdown(summary, tmp_path)

    assert summary["gates"]["module_branch_coverage"] is True
    assert summary["gates"]["passed"] is True
    assert "| Branch Coverage | Branch Gate | Run |" in markdown
    assert "`crawler` >= `60%`" in markdown
    assert "`jd-extraction` >= `60%`" in markdown


def test_branch_gate_policy_targets_jd_extraction_and_crawler(
    aggregate_coverage_module,
):
    assert aggregate_coverage_module.MODULE_BRANCH_GATES == {
        "crawler": 60.0,
        "jd-extraction": 60.0,
    }


def test_crawler_source_policy_accepts_only_active_action_modules(
    aggregate_coverage_module,
):
    assert aggregate_coverage_module._is_formal_source(
        "crawler", "multi_company_scraper/scrapers/playwright_scraper.py"
    )
    assert aggregate_coverage_module._is_formal_source(
        "crawler", "unified_api/services/boss_service.py"
    )
    assert not aggregate_coverage_module._is_formal_source(
        "crawler", "historical_jd/legacy_scraper.py"
    )
