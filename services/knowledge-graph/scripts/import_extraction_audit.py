"""import_extraction_audit.py — 把 B 侧 JD 抽取审计包导入 KG 库。

读取 full-reviewed-jd-package-v3-all-20260812.zip（schema
`jobgraph-extraction-audit-package.v2`），把 5754 条真实 JD 抽取结果回流到
knowledge-graph 库，补齐此前阻塞 A-DATA-01 / A14「逐条 Evidence refs 绑定」的
空表：

  jd_documents / jd_extraction_records / extracted_candidate_requirements /
  extraction_evidence / jd_normalized_records / normalized_job_classifications /
  normalized_requirement_records / normalized_skill_records

来源是 B 侧正式抽取链（模型抽取 + 校验 + 审查 + 归一化），多平台、去重、证据级
（每条技能/要求带 exact quote + start/end + source_id）。缺少主系统 Validation，
按 KG 服务约定标记 fact_authority=legacy_local。

幂等：跳过已存在 document_id 的记录。
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
from app.models import (
    ExtractedCandidateRequirement,
    ExtractionEvidence,
    JDDocument,
    JDExtractionRecord,
    JDNormalizedRecord,
    NormalizedJobClassification,
    NormalizedRequirementRecord,
    NormalizedSkillRecord,
)

AUDIT_PACKAGE = (
    KG_SERVICE.parent.parent / "data" / "extraction-audit"
    / "full-reviewed-jd-package-v3-all-20260812.zip"
)


def _load_package(package_path: Path) -> dict:
    """读取审计包，返回 {annotations, normalized, doc_source}。"""
    annotations: dict[str, dict] = {}
    normalized: dict[str, dict] = {}
    doc_source: dict[str, str] = {}

    with zipfile.ZipFile(package_path) as zf:
        # run_id -> source_platform
        manifest = json.loads(
            zf.read("package_manifest.json").decode("utf-8")
        )
        run_platform: dict[str, str] = {
            run["run_id"]: run["source_platform"]
            for run in manifest.get("source_runs", [])
        }

        for item in json.loads(
            zf.read("final/annotations_nested.json").decode("utf-8")
        ):
            annotations[item["document_id"]] = item

        for item in json.loads(
            zf.read("final/normalized_annotations.json").decode("utf-8")
        ):
            normalized[item["document_id"]] = item

        # 从 audit 记录恢复 document -> source_platform（审计文件是抽样子集）
        for name in zf.namelist():
            if not name.startswith("audit/") or not name.endswith(".json"):
                continue
            audit = json.loads(zf.read(name).decode("utf-8"))
            jd_id = audit.get("jd_id")
            run_id = audit.get("run_id")
            platform = run_platform.get(run_id)
            if jd_id and platform:
                doc_source[jd_id] = platform

    return {
        "annotations": annotations,
        "normalized": normalized,
        "doc_source": doc_source,
    }


def _first_company(annotation: dict) -> str | None:
    for fact in annotation.get("company_facts") or []:
        if isinstance(fact, dict) and fact.get("kind") == "company_name":
            return fact.get("value")
    return None


def import_extraction_audit(
    package_path: Path, dry_run: bool = False
) -> dict:
    if not package_path.exists():
        raise FileNotFoundError(f"审计包不存在: {package_path}")

    settings = Settings.from_env()
    if settings.environment.casefold() == "production":
        raise RuntimeError("禁止在生产环境执行此脚本")

    data = _load_package(package_path)
    annotations = data["annotations"]
    normalized = data["normalized"]
    doc_source = data["doc_source"]

    database = create_database(settings)
    result = {
        "documents": 0,
        "extraction_records": 0,
        "candidate_requirements": 0,
        "evidence": 0,
        "normalized_records": 0,
        "job_classifications": 0,
        "normalized_requirements": 0,
        "normalized_skills": 0,
        "skipped_documents": 0,
    }

    try:
        with database.session_factory() as session:
            for document_id, annotation in sorted(annotations.items()):
                existing = session.scalar(
                    select(JDDocument.id).where(
                        JDDocument.document_id == document_id
                    )
                )
                if existing is not None:
                    result["skipped_documents"] += 1
                    continue

                norm = normalized.get(document_id) or {}
                raw_text = annotation.get("cleaned_text") or annotation.get("raw_text") or ""
                platform = doc_source.get(document_id)

                session.add(JDDocument(
                    document_id=document_id,
                    raw_text=raw_text,
                    source_type="crawler",
                    source_name=platform,
                    enterprise_name=_first_company(annotation),
                    published_at=None,
                    source_credibility=1.0,
                    is_synthetic=False,
                    fact_authority="legacy_local",
                ))
                session.flush()

                session.add(JDExtractionRecord(
                    document_id=document_id,
                    payload=annotation,
                    status="confirmed",
                    confirmed=True,
                ))

                req_id_map: dict[str, int] = {}

                for req in annotation.get("requirements") or []:
                    if not isinstance(req, dict):
                        continue
                    requirement_id = req.get("requirement_id") or ""
                    kind = req.get("kind") or "other"
                    modality = req.get("modality") or "required"
                    evidence = req.get("evidence") or {}

                    session.add(ExtractedCandidateRequirement(
                        document_id=document_id,
                        requirement_id=requirement_id,
                        kind=kind,
                        modality=modality,
                        payload=req,
                    ))
                    result["candidate_requirements"] += 1

                    if isinstance(evidence, dict) and evidence.get("quote"):
                        session.add(ExtractionEvidence(
                            document_id=document_id,
                            owner_type="requirement",
                            owner_ref=requirement_id,
                            quote=evidence.get("quote", ""),
                            start=evidence.get("start"),
                            end=evidence.get("end"),
                            alignment=evidence.get("alignment", "exact"),
                            occurrence_index=evidence.get("occurrence_index"),
                        ))
                        result["evidence"] += 1

                session.add(JDNormalizedRecord(
                    document_id=document_id,
                    payload=norm,
                    map_version=(
                        (norm.get("job_classification") or {}).get("taxonomy_version")
                        or "position-taxonomy.v3.0.0"
                    ),
                ))
                session.flush()
                normalized_record_id = session.scalar(
                    select(JDNormalizedRecord.id).where(
                        JDNormalizedRecord.document_id == document_id
                    )
                )
                result["normalized_records"] += 1

                job_classification = norm.get("job_classification") or {}
                position_code = job_classification.get("position_code")
                if position_code:
                    session.add(NormalizedJobClassification(
                        normalized_record_id=normalized_record_id,
                        position_id=position_code,
                        source_title=job_classification.get("source_title"),
                        resolution_status=job_classification.get("classification_status") or "resolved",
                    ))
                    result["job_classifications"] += 1

                for nreq in norm.get("normalized_requirements") or []:
                    if not isinstance(nreq, dict):
                        continue
                    requirement_id = nreq.get("requirement_id") or ""
                    kind = nreq.get("kind") or "other"

                    session.add(NormalizedRequirementRecord(
                        normalized_record_id=normalized_record_id,
                        requirement_id=requirement_id,
                        kind=kind,
                    ))
                    session.flush()
                    nreq_db_id = session.scalar(
                        select(NormalizedRequirementRecord.id).where(
                            NormalizedRequirementRecord.normalized_record_id == normalized_record_id,
                            NormalizedRequirementRecord.requirement_id == requirement_id,
                        )
                    )
                    result["normalized_requirements"] += 1

                    for skill in nreq.get("skills") or []:
                        if not isinstance(skill, dict):
                            continue
                        session.add(NormalizedSkillRecord(
                            normalized_requirement_id=nreq_db_id,
                            source_name=skill.get("source_name") or "",
                            skill_id=skill.get("skill_id"),
                            canonical_name=skill.get("canonical_name"),
                            category_code=None,
                            subcategory_code=None,
                            resolution_status=(
                                skill.get("identity_resolution_status")
                                or skill.get("classification_resolution_status")
                                or "unresolved"
                            ),
                            resolution_source=(
                                "resolved"
                                if skill.get("identity_resolution_status") == "resolved"
                                else "unresolved"
                            ),
                        ))
                        result["normalized_skills"] += 1

                result["documents"] += 1
                result["extraction_records"] += 1

                if not dry_run and result["documents"] % 500 == 0:
                    session.commit()
                    print(f"  已导入 {result['documents']} 份文档...")

            if dry_run:
                session.rollback()
            else:
                session.commit()

        return result

    finally:
        database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="导入 B 侧 JD 抽取审计包到 KG 库"
    )
    parser.add_argument(
        "--package",
        default=str(AUDIT_PACKAGE),
        help=f"审计包路径（默认: {AUDIT_PACKAGE}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览，不写数据库",
    )
    args = parser.parse_args()

    result = import_extraction_audit(Path(args.package), dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
