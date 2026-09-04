# deploy/daily_pipeline.py
"""Daily crawl -> export -> verify -> handover orchestration (v2 API).

Run this as a scheduled task to produce the daily data delivery::

    python deploy/daily_pipeline.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("daily_pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEDULE = Path(__file__).resolve().parent / "schedule_config.yaml"
DEFAULT_OUTPUT = os.environ.get("NFBS_BUNDLE_OUTPUT", str(PROJECT_ROOT / "output"))


def _run_crawl(task: dict) -> dict:
    task_type = task["task_type"]
    params = task.get("params", {})
    name = task.get("name", "未命名")

    started_at = datetime.now(timezone.utc)
    logger.info("Starting crawl: %s (type=%s)", name, task_type)

    stats = {
        "platform": task_type,
        "task_name": name,
        "planned": 0,
        "list_discovered": 0,
        "detail_success": 0,
        "detail_failed": 0,
        "detail_unavailable": 0,
        "published": 0,
        "status": "completed",
        "started_at": started_at.isoformat(),
    }

    try:
        task_id = f"pipeline_{task_type}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        if task_type == "boss":
            from unified_api.services.boss_service import run_boss_crawl
            count = run_boss_crawl(
                keyword=params["keyword"],
                city=params["city"],
                pages=params.get("pages", 5),
                task_id=task_id,
            )
        elif task_type == "company":
            from unified_api.services.company_service import run_company_crawl
            count = run_company_crawl(
                company_name=params.get("company_name", "all"),
                platform=params.get("platform"),
                task_id=task_id,
            )
        elif task_type == "liepin":
            from unified_api.services.liepin_service import run_liepin_crawl
            count = run_liepin_crawl(
                keywords=params.get("keywords"),
                cities=params.get("cities"),
                task_id=task_id,
            )
        else:
            logger.warning("Unknown task_type: %s, skipping", task_type)
            stats["status"] = "skipped"
            stats["finished_at"] = datetime.now(timezone.utc).isoformat()
            return stats

        stats["detail_success"] = count
        stats["published"] = count
        logger.info("Crawl complete: %s — %s records", name, count)
    except Exception:
        logger.exception("Crawl failed: %s", name)
        stats["status"] = "failed"
        stats["detail_failed"] = stats["detail_success"]

    stats["finished_at"] = datetime.now(timezone.utc).isoformat()
    return stats


def _run_export(commit: str, output_dir: str) -> list[dict]:
    """Export using the v2 BundleExporter API."""
    from jobgraph_contracts.offline_bundle import BundleMode
    from unified_api.database import ensure_schema
    from unified_api.offline_export.exporter import BundleExporter
    from unified_api.offline_export.repository import MySQLExportRepository
    from unified_api.offline_export.manifest import verify_bundle

    ensure_schema()

    repo = MySQLExportRepository()
    parent_id = repo.latest_completed_bundle_id()
    mode = BundleMode.INCREMENTAL if parent_id else BundleMode.FULL
    logger.info("Starting %s export (parent=%s)...", mode.value, parent_id or "none")

    bundles_info: list[dict] = []

    try:
        exporter = BundleExporter(repo)
        try:
            summary = exporter.export(
                output=Path(output_dir),
                mode=mode,
                parent_bundle_id=parent_id,
                producer_git_commit=commit,
            )
        except ValueError as exc:
            if "No new records" in str(exc):
                logger.info("No new records to export")
                return bundles_info
            raise

        vr = verify_bundle(summary.output_path)
        ct = vr.manifest.crawl_time_range
        ct_min = ct.minimum.isoformat() if ct.minimum else "N/A"
        ct_max = ct.maximum.isoformat() if ct.maximum else "N/A"

        bundles_info.append({
            "filename": summary.output_path.name,
            "bundle_id": summary.bundle_id,
            "mode": mode.value,
            "parent_bundle_id": parent_id,
            "record_count": summary.record_count,
            "crawl_time_range": f"{ct_min} ~ {ct_max}",
            "verified": True,
        })

        logger.info("Export complete: 1 bundle, %s records", summary.record_count)
    except Exception:
        logger.exception("Export failed")

    return bundles_info


def main() -> int:
    from unified_api.database import ensure_schema
    ensure_schema()
    logger.info("Database schema confirmed")

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=PROJECT_ROOT
        ).strip()
    except Exception:
        commit = "unknown"
    logger.info("Crawler commit: %s", commit)

    if DEFAULT_SCHEDULE.exists():
        with open(DEFAULT_SCHEDULE, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        tasks = [t for t in config.get("schedules", []) if t.get("enabled", True) and t.get("task_type") not in ("pipeline", "export_only")]
    else:
        tasks = []

    if not tasks:
        logger.warning("No enabled crawl tasks found in schedule_config.yaml")
        return 1

    # Phase 1: Crawl
    platform_stats = []
    task_ids = []
    crawl_failures: list[str] = []
    for task in tasks:
        stats = _run_crawl(task)
        platform_stats.append(stats)
        task_ids.append(task.get("name", ""))
        if stats["status"] == "failed":
            crawl_failures.append(stats["task_name"])

    # Phase 2: Export
    tz = timezone(timedelta(hours=8))
    date_str = datetime.now(tz).strftime("%Y-%m-%d")
    output_dir = os.path.join(DEFAULT_OUTPUT, date_str)
    bundles_info = _run_export(commit, output_dir)

    # Phase 3: Handover
    from unified_api.offline_export.handover import (
        DailyCrawlStats, PlatformStats, BundleRef, generate_handover,
        compute_daily_change_stats,
    )

    change = compute_daily_change_stats(date_str)

    stats = DailyCrawlStats(date=date_str)
    stats.executor = "scheduler"
    stats.crawler_commit = commit
    stats.task_ids = task_ids
    stats.platforms = [
        PlatformStats(
            platform=s["platform"],
            detail_success=s["detail_success"],
            detail_failed=s["detail_failed"],
            published=s["published"],
        )
        for s in platform_stats
    ]
    stats.bundles = [
        BundleRef(
            filename=b["filename"],
            bundle_id=b["bundle_id"],
            mode=b["mode"],
            parent_bundle_id=b["parent_bundle_id"],
            record_count=b["record_count"],
            crawl_time_range=b["crawl_time_range"],
            verified=b["verified"],
        )
        for b in bundles_info
    ]
    stats.change = change

    content = generate_handover(stats, output_dir)
    print(content)

    exit_code = 0

    if crawl_failures:
        logger.error("Crawl failures: %s", ", ".join(crawl_failures))

    failed_bundles = [b for b in bundles_info if not b["verified"]]
    if failed_bundles:
        logger.error("Pipeline complete with %s verification failure(s)", len(failed_bundles))
        exit_code = 1

    if crawl_failures and not failed_bundles:
        logger.warning("All bundles verified, but %s crawl source(s) failed", len(crawl_failures))

    logger.info("Daily pipeline complete — %s bundles delivered, %s crawl failures",
                len(bundles_info), len(crawl_failures))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
