"""reimport_extraction_audit_v2.py — 用 run-cleaning.v2 重清洗包更新已导入的 JD 文本。

`position-cleaned-publishable-v3-recleaned-20260814.zip` 是修复了「BOSS直聘」水印
粘入中文词的 v2 重清洗包（3501 条，document_id 与库中 bundles_all:* 记录重叠）。

本脚本把 v2 的 cleaned_text 写回 `jd_documents.raw_text`（可更新列）。

注意：`jd_extraction_records.payload` 是 KG 服务的不可变审计副本，不更新；
denormalized 的 extraction_evidence / extracted_candidate_requirements 等也不重放
（v2 仅 550 条 evidence 重新对齐，占比极小，属已知残留）。
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

KG_SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KG_SERVICE))

from sqlalchemy import select

from app.config import Settings
from app.database import create_database
from app.models import JDDocument

AUDIT_PACKAGE = (
    KG_SERVICE.parent.parent / "data" / "extraction-audit"
    / "position-cleaned-publishable-v3-recleaned-20260814.zip"
)


def _load_cleaned_texts(package_path: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    with zipfile.ZipFile(package_path) as zf:
        name = next(
            n for n in zf.namelist() if n.endswith("annotations_nested.json")
        )
        for record in json.loads(zf.read(name).decode("utf-8")):
            texts[record["document_id"]] = (
                record.get("cleaned_text") or record.get("raw_text") or ""
            )
    return texts


def update_v2(package_path: Path, dry_run: bool = False) -> dict:
    if not package_path.exists():
        raise FileNotFoundError(f"包不存在: {package_path}")

    settings = Settings.from_env()
    if settings.environment.casefold() == "production":
        raise RuntimeError("禁止在生产环境执行此脚本")

    texts = _load_cleaned_texts(package_path)
    database = create_database(settings)
    result = {"in_package": len(texts), "updated": 0, "changed": 0, "missing": 0}

    try:
        with database.session_factory() as session:
            for document_id, cleaned in sorted(texts.items()):
                row = session.scalar(
                    select(JDDocument).where(JDDocument.document_id == document_id)
                )
                if row is None:
                    result["missing"] += 1
                    continue
                if row.raw_text != cleaned:
                    result["changed"] += 1
                    if not dry_run:
                        row.raw_text = cleaned
                result["updated"] += 1

            if dry_run:
                session.rollback()
            else:
                session.commit()

        return result

    finally:
        database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="用 v2 重清洗包更新 jd_documents.raw_text"
    )
    parser.add_argument("--package", default=str(AUDIT_PACKAGE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = update_v2(Path(args.package), dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
