"""A-DATA-01: 回填 jd_documents.published_at。

此前 `import_extraction_audit.py` 以 `published_at=None` 导入（审计包无时间字段），
导致 bind_evidence_refs 生成 relation_claim 时 `observed_at` 回退为 document.created_at
（导入时间，非真实观测时间）。

本脚本用 `duplicate_mapping.csv`（Extraction `bundles_all_unique_v3` 去重映射）的
`crawl_time`（抓取时间，对应快照 bundle 日期与时间窗口）回填 `published_at`。
`crawl_time` 是 selected_success 行的字段，覆盖全部 3597 条 bundles_all 文档。

回填后，`bind_evidence_refs.py` 的 `_build_claim` 会用真实 crawl_time 生成 observed_at。

幂等：重复执行只会再次更新相同值。
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

KG_SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KG_SERVICE))

from sqlalchemy import text

from app.config import Settings
from app.database import create_database

DUPLICATE_MAPPING_CSV = (
    KG_SERVICE.parent
    / "jd-extraction"
    / "output"
    / "bundles_all_unique_v3"
    / "duplicate_mapping.csv"
)


def _parse_crawl_time(value: str) -> datetime:
    """把 ISO 时间字符串解析为 timezone-aware datetime。

    crawl_time 形如 '2026-07-27T08:02:45.053748Z'。带 'Z' 表示 UTC。
    """
    v = value.strip()
    if not v:
        raise ValueError("empty crawl_time")
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def backfill_published_at(csv_path: Path, dry_run: bool = False) -> dict:
    if not csv_path.exists():
        raise FileNotFoundError(f"映射 CSV 不存在: {csv_path}")

    settings = Settings.from_env()
    if settings.environment.casefold() == "production":
        raise RuntimeError("禁止在生产环境执行此脚本")

    # document_id -> crawl_time (datetime)
    doc_time: dict[str, datetime] = {}
    skipped: list[str] = []
    with open(csv_path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            did = r["document_id"]
            if not did.startswith("bundles_all:"):
                continue
            # 只取去重后保留的文档（selected_success 或 recovered_unique_failure），
            # 跳过 duplicate_omitted 等被去重合并的重复行。
            if r["row_outcome"] not in ("selected_success", "recovered_unique_failure"):
                continue
            try:
                doc_time[did] = _parse_crawl_time(r["crawl_time"])
            except (ValueError, KeyError):
                skipped.append(did)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "csv_documents": len(doc_time),
        "parse_skipped": len(skipped),
        "updated": 0,
        "matched": 0,
        "unmatched": 0,
    }

    database = create_database(settings)
    try:
        with database.session_factory() as session:
            if dry_run:
                rows = session.execute(
                    text(
                        "SELECT document_id, published_at FROM jd_documents "
                        "WHERE document_id LIKE 'bundles_all:%'"
                    )
                ).mappings().all()
                for row in rows:
                    did = row["document_id"]
                    if did in doc_time:
                        report["matched"] += 1
                        if row["published_at"] is None:
                            report["updated"] += 1
                    else:
                        report["unmatched"] += 1
            else:
                for did, ct in doc_time.items():
                    result = session.execute(
                        text(
                            "UPDATE jd_documents SET published_at = :ct "
                            "WHERE document_id = :did"
                        ),
                        {"ct": ct, "did": did},
                    )
                    if result.rowcount:
                        report["updated"] += 1
                        report["matched"] += 1
                    else:
                        report["unmatched"] += 1
                session.commit()

        return report
    finally:
        database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="回填 jd_documents.published_at（来自 duplicate_mapping.csv 的 crawl_time）"
    )
    parser.add_argument(
        "--csv",
        default=str(DUPLICATE_MAPPING_CSV),
        help=f"duplicate_mapping.csv 路径（默认: {DUPLICATE_MAPPING_CSV}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写数据库")
    args = parser.parse_args()

    report = backfill_published_at(Path(args.csv), dry_run=args.dry_run)
    print(report)


if __name__ == "__main__":
    main()
