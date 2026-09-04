#!/usr/bin/env python3
"""Run all JobPulse Python module suites and build one weighted coverage report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


MODULES = (
    ("main", "Main"),
    ("knowledge-graph", "KnowledgeGraph"),
    ("matching", "MatchingService"),
    ("emerging", "EmergingDiscovery"),
    ("trend", "TrendIntelligence"),
    ("crawler", "Crawler"),
    ("jd-extraction", "JDExtraction"),
    ("cv-extraction", "CVExtraction"),
    ("embedding", "EmbeddingService"),
)

MODULE_DIRS = {
    "main": "apps/api",
    "knowledge-graph": "services/knowledge-graph",
    "matching": "services/matching-service",
    "emerging": "services/emerging-discovery",
    "trend": "services/trend-intelligence",
    "crawler": "services/crawler",
    "jd-extraction": "services/jd-extraction",
    "cv-extraction": "services/cv-extraction",
    "embedding": "services/embedding-service",
}

MODULE_SOURCES = {
    "main": ["app"],
    "knowledge-graph": ["app"],
    "matching": ["app"],
    "emerging": ["app"],
    "trend": ["app"],
    "crawler": [
        "patches.scheduler",
        "multi_company_scraper.adapters.crawler_jd_envelope",
        "multi_company_scraper.collector",
        "multi_company_scraper.models.company_config",
        "multi_company_scraper.models.job_data",
        "multi_company_scraper.normalizer",
        "multi_company_scraper.scrapers.base",
        "multi_company_scraper.scrapers.dispatcher",
        "multi_company_scraper.scrapers.liepin_scraper",
        "multi_company_scraper.scrapers.playwright_scraper",
        "unified_api.database",
        "unified_api.offline_export.staging",
        "unified_api.services.boss_detail",
        "unified_api.services.boss_service",
        "unified_api.services.company_service",
        "unified_api.services.liepin_service",
        "unified_api.services.persistence",
        "unified_api.services.task_manager",
    ],
    "jd-extraction": ["src"],
    "cv-extraction": ["src", "api"],
    "embedding": ["app"],
}


def _tail(text: str, lines: int = 80) -> str:
    values = text.rstrip().splitlines()
    return "\n".join(values[-lines:])


def _console_safe(text: str) -> str:
    """Keep captured UTF-8 test output printable on legacy Windows consoles."""

    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="backslashreplace").decode(encoding)


def _coverage_args(slug: str, artifact_dir: Path) -> list[str]:
    args: list[str] = []
    args.extend(f"--cov={source}" for source in MODULE_SOURCES[slug])
    if slug == "main":
        # The materialized main application tree currently has a reproducible
        # 78.80% branch-aware baseline; keep the per-module gate just below it.
        args.append("--cov-fail-under=78")
    elif slug in {"knowledge-graph", "matching"}:
        args.append("--cov-fail-under=85")
    args.extend(
        [
            "--cov-branch",
            "--cov-report=term-missing",
            f"--cov-report=json:{artifact_dir.as_posix()}/{slug}.json",
            f"--cov-report=xml:{artifact_dir.as_posix()}/{slug}.xml",
            f"--junitxml={artifact_dir.as_posix()}/{slug}.junit.xml",
        ]
    )
    return args


def _portable_commands(
    slug: str, artifact_dir: Path, jobpulse_root: Path, use_xdist: bool
) -> list[tuple[list[str], Path]]:
    """Mirror test-all.sh's Python commands for hosts without bash."""

    # Keep pytest's disposable files outside the report directory.  This
    # mirrors test-all.sh's system-temp location and avoids Windows ACLs or
    # report consumers preventing pytest from removing its basetemp.
    pytest_basetemp = Path(tempfile.mkdtemp(prefix=f"jobpulse-{slug}-"))
    pytest_args = ["--basetemp", str(pytest_basetemp)]
    pytest_args.extend(_coverage_args(slug, artifact_dir))
    if use_xdist and slug == "main":
        pytest_args = ["-n", "4", "--dist", "loadfile", *pytest_args]
    elif use_xdist and slug == "knowledge-graph":
        pytest_args = ["-n", "4", "--dist", "loadfile", *pytest_args]
    elif use_xdist and slug == "jd-extraction":
        pytest_args = ["-n", "4", "--dist", "loadfile", *pytest_args]

    if slug == "crawler":
        pytest_args = [
            "tests",
            "unified_api/tests",
            "multi_company_scraper/tests",
            *pytest_args,
        ]
    if slug == "embedding":
        pytest_args = ["tests/test_api.py", *pytest_args]

    module_root = jobpulse_root / MODULE_DIRS[slug]
    commands = [([sys.executable, "-m", "pytest", *pytest_args], module_root)]
    if slug == "embedding":
        commands.append(
            ([sys.executable, "-c", "from app.config import Settings; Settings()"], module_root)
        )
    return commands


def _run_commands(
    commands: list[tuple[list[str], Path]], env: dict[str, str]
) -> tuple[int, str]:
    output_parts: list[str] = []
    for command, cwd in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            output_parts.append(f"could not start command {command!r}: {exc}")
            return 127, "\n".join(output_parts)
        output_parts.append(completed.stdout or "")
        if completed.returncode != 0:
            return completed.returncode, "\n".join(output_parts)
    return 0, "\n".join(output_parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("artifacts/coverage"),
        help="directory for per-module coverage and aggregate reports",
    )
    parser.add_argument(
        "--module",
        choices=[slug for slug, _suite in MODULES],
        help="run one module (used by the GitHub Actions matrix)",
    )
    parser.add_argument(
        "--no-aggregate",
        action="store_true",
        help="write execution metadata without aggregating (used by matrix jobs)",
    )
    args = parser.parse_args()

    jobpulse_root = Path(__file__).resolve().parents[1]
    artifact_dir = (jobpulse_root / args.artifact_dir).resolve() if not args.artifact_dir.is_absolute() else args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    test_result_dir = artifact_dir
    test_all = jobpulse_root / "scripts" / "test-all.sh"
    aggregate = jobpulse_root / "scripts" / "aggregate-coverage.py"
    use_xdist = importlib.util.find_spec("xdist") is not None
    bash = shutil.which("bash")
    # Windows ships a WSL launcher named bash.exe.  It is not a POSIX shell
    # when WSL is unavailable; prefer the portable Python path in that case.
    if bash and os.name == "nt" and "\\system32\\" in bash.lower():
        bash = None

    base_env = os.environ.copy()
    base_env.update(
        {
            "TEST_TREE": "JobPulse",
            "MODULE_FULL": "true",
            "MODULE_BASE_SHA": "",
            "JOBPULSE_COVERAGE_DIR": artifact_dir.as_posix(),
            "JOBPULSE_JUNIT_DIR": test_result_dir.as_posix(),
        }
    )

    selected_modules = (
        tuple(item for item in MODULES if item[0] == args.module)
        if args.module
        else MODULES
    )
    results: list[dict[str, object]] = []
    for slug, suite in selected_modules:
        started_at = time.time()
        env = base_env | {"JOBPULSE_COVERAGE_SLUG": slug}
        if bash:
            command = [bash, str(test_all), suite, "JobPulse"]
            run_cwd = jobpulse_root
        else:
            command = None
            run_cwd = None
        print(f"\n=== Coverage module: {suite} ({slug}) ===", flush=True)
        if command is not None:
            try:
                completed = subprocess.run(
                    command,
                    cwd=run_cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                output = completed.stdout or ""
                exit_code = completed.returncode
            except OSError as exc:
                output = f"could not start test runner: {exc}"
                exit_code = 127
        else:
            output = "[coverage-all] bash unavailable; using equivalent Python test commands\n"
            exit_code, portable_output = _run_commands(
                _portable_commands(slug, artifact_dir, jobpulse_root, use_xdist), env
            )
            output += portable_output

        log_path = artifact_dir / f"{slug}.log"
        log_path.write_text(output, encoding="utf-8")
        print(_console_safe(_tail(output)), flush=True)
        results.append(
            {
                "slug": slug,
                "suite": suite,
                "started_at": started_at,
                "finished_at": time.time(),
                "exit_code": exit_code,
                "log": log_path.name,
                "reason": None if exit_code == 0 else f"test runner exited with code {exit_code}; see {log_path.name}",
            }
        )

    execution = {
        "git_sha": _git_sha(jobpulse_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "modules": results,
    }
    execution_name = f"execution-{args.module}.json" if args.module else "execution.json"
    (artifact_dir / execution_name).write_text(
        json.dumps(execution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    test_exit_code = next(
        (int(result["exit_code"]) for result in results if result["exit_code"] != 0),
        0,
    )
    if args.no_aggregate:
        return test_exit_code

    aggregate_result = subprocess.run(
        [sys.executable, str(aggregate), "--artifact-dir", str(artifact_dir)],
        cwd=jobpulse_root,
        env=base_env,
        text=True,
    )
    return aggregate_result.returncode if aggregate_result.returncode else test_exit_code


def _git_sha(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


if __name__ == "__main__":
    sys.exit(main())
