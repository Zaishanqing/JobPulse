#!/usr/bin/env python3
"""Aggregate per-module pytest-cov JSON reports without averaging percentages."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LINE_GATE = 60.0
MODULE_BRANCH_GATES = {
    "crawler": 60.0,
    "jd-extraction": 60.0,
}


@dataclass(frozen=True)
class ModuleSpec:
    slug: str
    display_name: str


MODULES = (
    ModuleSpec("main", "Main"),
    ModuleSpec("knowledge-graph", "Knowledge Graph"),
    ModuleSpec("matching", "Matching"),
    ModuleSpec("emerging", "Emerging"),
    ModuleSpec("trend", "Trend"),
    ModuleSpec("crawler", "Crawler"),
    ModuleSpec("jd-extraction", "JD Extraction"),
    ModuleSpec("cv-extraction", "CV Extraction"),
    ModuleSpec("embedding", "Embedding"),
)


SOURCE_POLICY = {
    "included": {
        "main": ["apps/api/app"],
        "knowledge-graph": ["services/knowledge-graph/app"],
        "matching": ["services/matching-service/app"],
        "emerging": ["services/emerging-discovery/app"],
        "trend": ["services/trend-intelligence/app"],
        "crawler": [
            "services/crawler/patches/scheduler.py",
            "services/crawler/multi_company_scraper/adapters/crawler_jd_envelope.py",
            "services/crawler/multi_company_scraper/collector.py",
            "services/crawler/multi_company_scraper/models/company_config.py",
            "services/crawler/multi_company_scraper/models/job_data.py",
            "services/crawler/multi_company_scraper/normalizer.py",
            "services/crawler/multi_company_scraper/scrapers/base.py",
            "services/crawler/multi_company_scraper/scrapers/dispatcher.py",
            "services/crawler/multi_company_scraper/scrapers/liepin_scraper.py",
            "services/crawler/multi_company_scraper/scrapers/playwright_scraper.py",
            "services/crawler/unified_api/database.py",
            "services/crawler/unified_api/offline_export/staging.py",
            "services/crawler/unified_api/services/boss_detail.py",
            "services/crawler/unified_api/services/boss_service.py",
            "services/crawler/unified_api/services/company_service.py",
            "services/crawler/unified_api/services/liepin_service.py",
            "services/crawler/unified_api/services/persistence.py",
            "services/crawler/unified_api/services/task_manager.py",
        ],
        "jd-extraction": ["services/jd-extraction/src"],
        "cv-extraction": ["services/cv-extraction/src", "services/cv-extraction/api"],
        "embedding": ["services/embedding-service/app"],
    },
    "excluded": [
        "tests/ and test_*.py files",
        "third-party site-packages and installed dependencies",
        "alembic/ and migrations/ directories",
        "generated code directories, if present",
        "apps/web Vitest coverage (reported separately, never merged here)",
    ],
    "rule": (
        "Only the formal source package roots listed above are measured. "
        "Migration files live outside those roots and are uniformly excluded; "
        "no generated-code root is present in the measured source packages."
    ),
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _percentage(covered: int, total: int) -> float | None:
    return round(covered * 100.0 / total, 2) if total else None


def _is_formal_source(slug: str, raw_path: str) -> bool:
    normalized = raw_path.replace("\\", "/").lower().lstrip("./")
    included_roots = [root.lower().strip("/") for root in SOURCE_POLICY["included"][slug]]
    for root in included_roots:
        if root.endswith(".py"):
            if normalized == root or root.endswith(f"/{normalized}"):
                return True
            continue
        package = root.rsplit("/", 1)[-1]
        if (
            normalized == root
            or normalized.startswith(f"{root}/")
            or f"/{root}/" in f"/{normalized}/"
            or normalized == package
            or normalized.startswith(f"{package}/")
        ):
            return True
    return False


def _coverage_totals(
    slug: str, path: Path, started_at: float | None
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    if started_at is not None:
        try:
            if path.stat().st_mtime < started_at:
                return None
        except OSError:
            return None

    report = _read_json(path)
    if not report:
        return None
    totals = report.get("totals")
    if not isinstance(totals, dict):
        return None

    statements = _as_int(totals.get("num_statements"))
    covered_statements = min(statements, _as_int(totals.get("covered_lines")))
    branches = _as_int(totals.get("num_branches"))
    covered_branches = min(branches, _as_int(totals.get("covered_branches")))

    unexpected: list[str] = []
    files = report.get("files")
    if isinstance(files, dict):
        for raw_path in files:
            normalized = str(raw_path).replace("\\", "/").lower()
            if (
                not _is_formal_source(slug, str(raw_path))
                or "/tests/" in f"/{normalized}"
                or normalized.startswith("tests/")
                or "/migrations/" in f"/{normalized}"
                or "/alembic/" in f"/{normalized}"
                or "/generated/" in f"/{normalized}"
            ):
                unexpected.append(str(raw_path))

    return {
        "statements": statements,
        "covered_statements": covered_statements,
        "missing_statements": statements - covered_statements,
        "line_percent": _percentage(covered_statements, statements),
        "branches": branches,
        "covered_branches": covered_branches,
        "missing_branches": branches - covered_branches,
        "branch_percent": _percentage(covered_branches, branches),
        "unexpected_source_files": unexpected,
    }


def _parse_junit(path: Path, started_at: float | None) -> dict[str, int]:
    empty = {"total": 0, "passed": 0, "skipped": 0, "failed": 0}
    if not path.is_file():
        return empty
    if started_at is not None:
        try:
            if path.stat().st_mtime < started_at:
                return empty
        except OSError:
            return empty
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return empty

    cases = list(root.iter("testcase"))
    if not cases:
        total = _as_int(root.attrib.get("tests"))
        skipped = _as_int(root.attrib.get("skipped"))
        failed = _as_int(root.attrib.get("failures")) + _as_int(root.attrib.get("errors"))
        return {
            "total": total,
            "passed": max(0, total - skipped - failed),
            "skipped": skipped,
            "failed": failed,
        }

    skipped = 0
    failed = 0
    for case in cases:
        children = {child.tag.rsplit("}", 1)[-1] for child in case}
        if "skipped" in children:
            skipped += 1
        elif "failure" in children or "error" in children:
            failed += 1
    return {
        "total": len(cases),
        "passed": len(cases) - skipped - failed,
        "skipped": skipped,
        "failed": failed,
    }


def _is_fresh_file(path: Path, started_at: float | None) -> bool:
    if not path.is_file():
        return False
    if started_at is None:
        return True
    try:
        return path.stat().st_mtime >= started_at
    except OSError:
        return False


def _load_execution(artifact_dir: Path) -> dict[str, Any]:
    value = _read_json(artifact_dir / "execution.json")
    if value:
        return value

    modules: list[dict[str, Any]] = []
    git_shas: set[str] = set()
    fragment_count = 0
    fragment_sha_count = 0
    for path in sorted(artifact_dir.glob("execution-*.json")):
        fragment = _read_json(path)
        if not fragment:
            continue
        fragment_count += 1
        sha = fragment.get("git_sha")
        if isinstance(sha, str) and sha:
            git_shas.add(sha)
            fragment_sha_count += 1
        fragment_modules = fragment.get("modules")
        if isinstance(fragment_modules, list):
            modules.extend(item for item in fragment_modules if isinstance(item, dict))
    return {
        "git_sha": next(iter(git_shas)) if len(git_shas) == 1 else None,
        "git_shas": sorted(git_shas),
        "fragment_count": fragment_count,
        "fragment_sha_count": fragment_sha_count,
        "modules": modules,
    }


def _format_percent(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "n/a"


def _format_ratio(covered: int, total: int) -> str:
    return f"{covered}/{total} ({_format_percent(_percentage(covered, total))})"


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build_summary(artifact_dir: Path) -> dict[str, Any]:
    execution = _load_execution(artifact_dir)
    current_git_sha = _git_sha()
    execution_git_shas = execution.get("git_shas")
    if not isinstance(execution_git_shas, list):
        execution_sha = execution.get("git_sha")
        execution_git_shas = [execution_sha] if execution_sha else []
    fragment_count = execution.get("fragment_count")
    fragment_sha_count = execution.get("fragment_sha_count")
    fragments_valid = (
        fragment_count == len(MODULES) and fragment_sha_count == len(MODULES)
        if isinstance(fragment_count, int)
        else True
    )
    sha_consistent = (
        fragments_valid
        and len(execution_git_shas) == 1
        and execution_git_shas[0] == current_git_sha
    )
    execution_by_slug = {
        item.get("slug"): item
        for item in execution.get("modules", [])
        if isinstance(item, dict) and item.get("slug")
    }
    module_rows: list[dict[str, Any]] = []

    for spec in MODULES:
        run = execution_by_slug.get(spec.slug, {})
        started_at = run.get("started_at")
        if not isinstance(started_at, (int, float)):
            started_at = None
        coverage = _coverage_totals(
            spec.slug, artifact_dir / f"{spec.slug}.json", started_at
        )
        tests = _parse_junit(artifact_dir / f"{spec.slug}.junit.xml", started_at)
        return_code = run.get("exit_code")
        if not run:
            run_status = "not_run"
        else:
            run_status = "passed" if return_code == 0 else "failed"
        reason = run.get("reason")
        if run_status == "failed" and not reason:
            reason = f"test runner exit code {return_code}"
        if coverage is None:
            reason = reason or "coverage JSON was not produced"
        unexpected = coverage.get("unexpected_source_files", []) if coverage else []
        if unexpected:
            reason = reason or "coverage source contains an excluded path"
        coverage_xml = artifact_dir / f"{spec.slug}.xml"
        junit_xml = artifact_dir / f"{spec.slug}.junit.xml"
        log_name = run.get("log") if isinstance(run.get("log"), str) else None
        log_path = artifact_dir / log_name if log_name else None
        required_outputs_present = (
            coverage is not None
            and _is_fresh_file(coverage_xml, started_at)
            and _is_fresh_file(junit_xml, started_at)
            and log_path is not None
            and _is_fresh_file(log_path, started_at)
        )
        if not required_outputs_present:
            reason = reason or "one or more required coverage artifacts were not produced"
        branch_gate_minimum = MODULE_BRANCH_GATES.get(spec.slug)
        branch_percent = coverage.get("branch_percent") if coverage else None
        branch_gate_passed = (
            branch_percent is not None and branch_percent >= branch_gate_minimum
            if branch_gate_minimum is not None
            else None
        )
        module_rows.append(
            {
                "slug": spec.slug,
                "module": spec.display_name,
                "run_status": run_status,
                "exit_code": return_code,
                "reason": reason,
                "tests": tests,
                "coverage": coverage,
                "branch_gate_minimum_percent": branch_gate_minimum,
                "branch_gate_passed": branch_gate_passed,
                "required_outputs_present": required_outputs_present,
                "coverage_json": f"{spec.slug}.json" if coverage else None,
                "coverage_xml": coverage_xml.name if _is_fresh_file(coverage_xml, started_at) else None,
                "junit_xml": junit_xml.name if _is_fresh_file(junit_xml, started_at) else None,
                "log": log_name if log_path and _is_fresh_file(log_path, started_at) else None,
            }
        )

    total_statements = sum((row["coverage"] or {}).get("statements", 0) for row in module_rows)
    covered_statements = sum((row["coverage"] or {}).get("covered_statements", 0) for row in module_rows)
    total_branches = sum((row["coverage"] or {}).get("branches", 0) for row in module_rows)
    covered_branches = sum((row["coverage"] or {}).get("covered_branches", 0) for row in module_rows)
    total_tests = sum(row["tests"]["total"] for row in module_rows)
    total_passed = sum(row["tests"]["passed"] for row in module_rows)
    total_skipped = sum(row["tests"]["skipped"] for row in module_rows)
    total_failed = sum(row["tests"]["failed"] for row in module_rows)
    run_failures = [row for row in module_rows if row["run_status"] != "passed"]
    missing_reports = [row for row in module_rows if row["coverage"] is None]
    missing_outputs = [row for row in module_rows if not row["required_outputs_present"]]
    policy_violations = [
        {"module": row["module"], "files": row["coverage"]["unexpected_source_files"]}
        for row in module_rows
        if row["coverage"] and row["coverage"]["unexpected_source_files"]
    ]
    overall_line = _percentage(covered_statements, total_statements)
    overall_branch = _percentage(covered_branches, total_branches)
    line_gate_passed = overall_line is not None and overall_line >= LINE_GATE
    module_branch_gate_passed = all(
        row["branch_gate_passed"] is True
        for row in module_rows
        if row["branch_gate_minimum_percent"] is not None
    )
    complete = (
        not missing_reports
        and not missing_outputs
        and len(execution_by_slug) == len(MODULES)
        and sha_consistent
    )
    execution_passed = complete and not run_failures and not policy_violations
    status = "COMPLETE" if complete else "INCOMPLETE"

    return {
        "git_sha": current_git_sha,
        "execution_git_shas": execution_git_shas,
        "status": status,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "thresholds": {
            "overall_line_coverage_minimum_percent": LINE_GATE,
            "module_branch_coverage_minimum_percent": MODULE_BRANCH_GATES,
        },
        "source_policy": SOURCE_POLICY,
        "modules": module_rows,
        "overall": {
            "tests": {
                "total": total_tests,
                "passed": total_passed,
                "skipped": total_skipped,
                "failed": total_failed,
            },
            "line": {
                "covered": covered_statements,
                "total": total_statements,
                "percent": overall_line,
            },
            "branch": {
                "covered": covered_branches,
                "total": total_branches,
                "percent": overall_branch,
            },
        },
        "gates": {
            "overall_line_coverage": line_gate_passed,
            "module_branch_coverage": module_branch_gate_passed,
            "module_test_runs": execution_passed,
            "complete": complete,
            "same_git_sha": sha_consistent,
            "passed": (
                complete
                and line_gate_passed
                and module_branch_gate_passed
                and execution_passed
            ),
        },
    }


def _markdown(summary: dict[str, Any], artifact_dir: Path) -> str:
    lines = [
        "# JobPulse Python Coverage",
        "",
        f"- Git SHA: `{summary.get('git_sha') or 'unknown'}`",
        f"- Coverage status: **{summary['status']}**",
        "- Coverage is weighted by covered/total statements and covered/total branches; module percentages are not averaged.",
        f"- Overall line gate: `{LINE_GATE:.0f}%` minimum.",
        "- Module branch gates: "
        + ", ".join(
            f"`{slug}` >= `{minimum:.0f}%`"
            for slug, minimum in MODULE_BRANCH_GATES.items()
        )
        + ".",
        "",
        "| Module | Tests | Passed | Skipped | Failed | Line Coverage | Branch Coverage | Branch Gate | Run |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in summary["modules"]:
        coverage = row["coverage"] or {}
        lines.append(
            f"| {row['module']} | {row['tests']['total']} | {row['tests']['passed']} | "
            f"{row['tests']['skipped']} | {row['tests']['failed']} | "
            f"{_format_percent(coverage.get('line_percent'))} | "
            f"{_format_percent(coverage.get('branch_percent'))} | "
            f"{('PASS' if row['branch_gate_passed'] else 'FAIL') if row['branch_gate_minimum_percent'] is not None else 'n/a'} | "
            f"{row['run_status']} |"
        )
    overall = summary["overall"]
    lines.extend(
        [
            "| **Overall** | **{}** | **{}** | **{}** | **{}** | **{}** | **{}** | **{}** | **{}** |".format(
                overall["tests"]["total"],
                overall["tests"]["passed"],
                overall["tests"]["skipped"],
                overall["tests"]["failed"],
                _format_ratio(overall["line"]["covered"], overall["line"]["total"]),
                _format_ratio(overall["branch"]["covered"], overall["branch"]["total"]),
                "PASS" if summary["gates"]["module_branch_coverage"] else "FAIL",
                (
                    "passed"
                    if summary["gates"]["passed"]
                    else ("INCOMPLETE" if summary["status"] == "INCOMPLETE" else "failed")
                ),
            ),
            "",
            "## Source policy",
            "",
            summary["source_policy"]["rule"],
            "",
        ]
    )
    for item in summary["source_policy"]["excluded"]:
        lines.append(f"- Excluded: {item}")
    if summary["status"] == "INCOMPLETE":
        lines.extend(
            [
                "",
                "The Overall ratios above are partial diagnostics only. They are not a complete repository Coverage PASS.",
            ]
        )
    lines.extend(["", f"Raw reports: `{artifact_dir}`", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("artifacts/coverage"),
        help="directory containing per-module coverage JSON/XML and execution.json",
    )
    args = parser.parse_args()
    artifact_dir = args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(artifact_dir)
    (artifact_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (artifact_dir / "summary.md").write_text(_markdown(summary, artifact_dir), encoding="utf-8")

    print(_markdown(summary, artifact_dir))
    return 0 if summary["gates"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
