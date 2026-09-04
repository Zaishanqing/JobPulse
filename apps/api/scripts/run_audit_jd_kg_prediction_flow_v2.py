#!/usr/bin/env python3
"""Replay a complete audited JD extraction batch through JobPulse and KG.

Designed for audit.zip-style output containing:
    final/annotations_nested.json
    final/normalized_annotations.json
    final/failed_cases.jsonl              (optional)
    audit/*.json                           (optional but preferred)

The script:
1. Loads post-reviewed extraction and normalization output.
2. Rebuilds raw JD text from audit source_blocks and realigns every Evidence span.
3. Imports the repository's strict V2 extraction/normalization contracts.
4. Confirms and publishes the JD facts in the main backend.
5. Synchronizes authoritative published facts into the KG service.
6. Resolves each v2 position code to the authoritative catalog and builds one graph per position.
7. Reviews/publishes graph versions and checks that real records entered samples.
8. Optionally exercises the existing mock predicted-position workflow.

Place this file at:
    JobPulse/apps/api/scripts/run_audit_jd_kg_prediction_flow_v2.py

Recommended input path:
    JobPulse/apps/api/data/audit.zip
"""

from __future__ import annotations

import argparse
import copy
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx


ROOT = Path(__file__).resolve().parents[1]
MAIN_SKILL_CATALOG = ROOT / "config" / "skill_taxonomy_catalog.v1.json"


class FlowError(RuntimeError):
    """Raised when the end-to-end verification cannot continue safely."""


@contextlib.contextmanager
def project_tempdir(work_dir: Path) -> Iterable[Path]:
    """Use inherited project ACLs instead of Windows' restrictive tempfile ACL."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / f"audit-jd-flow-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


@dataclass
class PreparedRecord:
    source_document_id: str
    position_code: str
    position_name: str | None
    family_code: str
    family_name: str | None
    skill_domain_codes: tuple[str, ...]
    title: str
    raw_text: str
    raw_source: str
    extraction_source: dict[str, Any]
    normalization_source: dict[str, Any]
    classification_resolved: bool
    normalization_resolved: bool
    publishable_projection: bool
    source_raw_text: str
    cleaned_text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class RecordOutcome:
    source_document_id: str
    position_code: str
    family_code: str
    main_position_id: str | None = None
    knowledge_graph_position_id: str | None = None
    main_jd_id: str | None = None
    title: str | None = None
    raw_source: str | None = None
    status: str = "pending"
    sync_status: str | None = None
    payload_hash: str | None = None
    error: str | None = None
    error_type: str | None = None
    error_stage: str | None = None
    retryable: bool = False
    attempt_count: int = 1
    attempt_errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


COMPANY_KIND_MAP = {
    "company_name": "company_name",
    "industry": "industry",
    "business_domain": "industry",
    "company_size": "company_size",
    "ownership": "ownership",
    "location": "location",
    "product_service": "business",
    "development_stage": "business",
    "qualification": "business",
    "team": "business",
    "technical_resource": "business",
    "target_market": "business",
}

EMPLOYMENT_KIND_MAP = {
    "salary": "salary",
    "location": "work_location",
    "work_location": "work_location",
    "employment_type": "employment_type",
    "work_mode": "employment_type",
    "work_schedule": "work_schedule",
    "training": "training",
    "bonus": "benefit",
    "allowance": "benefit",
    "team_activity": "benefit",
    "social_security": "benefit",
    "leave": "benefit",
    "health_check": "benefit",
    "meal": "benefit",
    "equipment": "benefit",
    "equity": "benefit",
    "accommodation": "benefit",
    "commission": "benefit",
    "other": "benefit",
}

REQUIREMENT_FIELDS = {
    "skill": {"items", "proficiency"},
    "education": {
        "minimum_degree",
        "majors",
        "school_constraints",
        "admission_type",
        "graduation_year",
        "student_cohort",
    },
    "experience": {
        "minimum_years",
        "maximum_years",
        "domain",
        "role",
        "duration_text",
        "experience_unlimited",
    },
    "certificate": {"certificates"},
    "soft_skill": {"skills"},
    "other": {"label", "value"},
}

VALID_ITEM_TYPES = {
    "programming_language",
    "framework",
    "library",
    "database",
    "tool",
    "platform",
    "methodology",
    "domain_knowledge",
    "other",
}

SALARY_PERIOD_MAP = {
    "时": "hour",
    "小时": "hour",
    "日": "day",
    "天": "day",
    "月": "month",
    "年": "year",
}
VALID_MODALITIES = {"required", "preferred", "bonus", "unknown"}


def log(message: str) -> None:
    print(f"[AUDIT-JD-FLOW] {message}", flush=True)


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def locate_batch_root(input_path: Path, temp_dir: Path) -> Path:
    if input_path.is_dir():
        candidates = [input_path, *[p.parent for p in input_path.rglob("final") if p.is_dir()]]
        for root in candidates:
            if (root / "final" / "annotations_nested.json").exists():
                return root
        raise FlowError(f"Cannot find final/annotations_nested.json under {input_path}")

    if not input_path.exists():
        raise FlowError(f"Input does not exist: {input_path}")
    if input_path.suffix.lower() != ".zip":
        raise FlowError("--input must be an extracted batch directory or ZIP file")

    extract_dir = temp_dir / "batch"
    with zipfile.ZipFile(input_path) as archive:
        archive.extractall(extract_dir)
    for path in extract_dir.rglob("annotations_nested.json"):
        if path.parent.name == "final":
            return path.parent.parent
    raise FlowError("ZIP does not contain final/annotations_nested.json")


def iter_evidence_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        evidence = value.get("evidence")
        if isinstance(evidence, dict):
            yield evidence
        for child in value.values():
            yield from iter_evidence_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_evidence_nodes(child)


def validate_evidence_slices(extraction: dict[str, Any], raw_text: str) -> None:
    evidence_count = 0
    for evidence in iter_evidence_nodes(extraction):
        evidence_count += 1
        start = evidence.get("start")
        end = evidence.get("end")
        quote_text = evidence.get("quote")
        if not isinstance(start, int) or not isinstance(end, int):
            raise FlowError("evidence coordinates are not integers")
        if start < 0 or end < start or raw_text[start:end] != quote_text:
            raise FlowError(
                f"evidence slice mismatch at {start}:{end}: expected {quote_text!r}, "
                f"got {raw_text[start:end]!r}"
            )
        if evidence.get("alignment") != "exact":
            raise FlowError("non-exact evidence cannot enter the formal flow")
    if evidence_count == 0:
        raise FlowError("extraction contains no evidence")


def normalized_evidence(evidence: dict[str, Any], main_jd_id: str) -> dict[str, Any]:
    return {
        "source_id": main_jd_id,
        "quote": str(evidence["quote"]),
        "start": int(evidence["start"]),
        "end": int(evidence["end"]),
        "alignment": "exact",
        "occurrence_index": int(evidence.get("occurrence_index") or 0),
    }


def deduplicate_extraction_facts(extraction: dict[str, Any]) -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.infrastructure.jd_validation_projection import (
        reviewed_fact_duplicate_value,
    )

    collections = {
        "responsibilities": "responsibility",
        "requirements": "requirement",
        "company_facts": "company_fact",
        "employment_facts": "employment_fact",
    }
    for collection, fact_type in collections.items():
        unique_items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in extraction[collection]:
            value = reviewed_fact_duplicate_value(collection, item)
            identity = json.dumps(
                {"fact_type": fact_type, "value": value},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if identity in seen:
                continue
            seen.add(identity)
            unique_items.append(item)
        extraction[collection] = unique_items


def convert_extraction(source: dict[str, Any], main_jd_id: str) -> dict[str, Any]:
    converted: dict[str, Any] = {
        "schema_version": "v2",
        "document_id": main_jd_id,
        "job_title": None,
        "responsibilities": [],
        "requirements": [],
        "company_facts": [],
        "employment_facts": [],
    }

    title = source.get("job_title")
    if isinstance(title, dict) and title.get("value") and title.get("evidence"):
        converted["job_title"] = {
            "value": str(title["value"]),
            "evidence": normalized_evidence(title["evidence"], main_jd_id),
        }

    for item in source.get("responsibilities", []):
        converted["responsibilities"].append(
            {
                "requirement_id": str(item["requirement_id"]),
                "kind": "task",
                "modality": item.get("modality")
                if item.get("modality") in VALID_MODALITIES
                else "unknown",
                "action": str(item.get("action") or item["evidence"]["quote"]),
                "evidence": normalized_evidence(item["evidence"], main_jd_id),
            }
        )

    for item in source.get("requirements", []):
        kind = item.get("kind")
        if kind not in REQUIREMENT_FIELDS:
            continue
        payload: dict[str, Any] = {
            "requirement_id": str(item["requirement_id"]),
            "kind": kind,
            "modality": item.get("modality")
            if item.get("modality") in VALID_MODALITIES
            else "unknown",
            "evidence": normalized_evidence(item["evidence"], main_jd_id),
        }
        for key in REQUIREMENT_FIELDS[kind]:
            if key in item and item[key] is not None:
                payload[key] = copy.deepcopy(item[key])
        if kind == "skill":
            payload["items"] = [
                {
                    "name": str(skill.get("name") or "").strip(),
                    "item_type": skill.get("item_type")
                    if skill.get("item_type") in VALID_ITEM_TYPES
                    else "other",
                }
                for skill in item.get("items", [])
                if str(skill.get("name") or "").strip()
            ]
            if not payload["items"]:
                continue
        elif kind == "certificate" and not payload.get("certificates"):
            payload["certificates"] = [str(item["evidence"]["quote"])]
        elif kind == "soft_skill" and not payload.get("skills"):
            payload["skills"] = [str(item["evidence"]["quote"])]
        elif kind == "other":
            payload.setdefault("label", "other")
            payload.setdefault("value", str(item["evidence"]["quote"]))
        converted["requirements"].append(payload)

    for item in source.get("company_facts", []):
        converted["company_facts"].append(
            {
                "fact_id": str(item["fact_id"]),
                "kind": COMPANY_KIND_MAP.get(str(item.get("kind")), "business"),
                "value": str(item.get("value") or item["evidence"]["quote"]),
                "evidence": normalized_evidence(item["evidence"], main_jd_id),
            }
        )

    for item in source.get("employment_facts", []):
        converted["employment_facts"].append(
            {
                "fact_id": str(item["fact_id"]),
                "kind": EMPLOYMENT_KIND_MAP.get(str(item.get("kind")), "benefit"),
                "value": str(item.get("value") or item["evidence"]["quote"]),
                "evidence": normalized_evidence(item["evidence"], main_jd_id),
            }
        )
    deduplicate_extraction_facts(converted)
    return converted


def source_classification_is_resolved(classification: dict[str, Any]) -> bool:
    status = classification.get("classification_status")
    confidence = classification.get("confidence")
    return (
        classification.get("schema_version") == "job-position-classification.v3"
        and status in {"resolved", "manually_confirmed"}
        and (
            status == "manually_confirmed"
            or confidence is None
            or float(confidence) >= 0.75
        )
        and bool(classification.get("position_code"))
        and bool(classification.get("family_code"))
    )


def convert_normalization(
    source: dict[str, Any], extraction: dict[str, Any], main_jd_id: str,
    catalog_skills: dict[str, dict[str, Any]], *,
    standard_position_id: str | None = None,
    standard_position_code: str | None = None,
    standard_position_name: str | None = None,
    standard_family_code: str | None = None,
    standard_family_name: str | None = None,
    standard_skill_domain_codes: Iterable[str] = (),
) -> dict[str, Any]:
    classification = source.get("job_classification") or {}
    source_classification_resolved = source_classification_is_resolved(classification)
    title = str(
        ((extraction.get("job_title") or {}).get("value"))
        or source.get("document_id")
        or main_jd_id
    )
    result: dict[str, Any] = {
        "schema_version": "v2",
        "document_id": main_jd_id,
        "job_classification": ({
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": title,
            "position_id": standard_position_id,
            "position_code": standard_position_code,
            "position_name": standard_position_name,
            "family_code": standard_family_code,
            "family_name": standard_family_name,
            "candidate_positions": [{
                "position_code": standard_position_code,
                "score": float(classification.get("confidence") or 1.0),
            }],
            "career_level": None,
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": list(standard_skill_domain_codes),
            "confidence": float(classification.get("confidence") or 1.0),
            "classification_status": classification.get(
                "classification_status", "resolved"
            ),
            "review_reason_codes": [],
            "evidence_refs": ["job_title"],
            "classification_policy_version": "audit-position-classifier.v3.0",
        } if source_classification_resolved and standard_position_id else {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": title,
            "position_id": None,
            "position_code": None,
            "position_name": None,
            "family_code": None,
            "family_name": None,
            "candidate_positions": [],
            "career_level": None,
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": [],
            "confidence": None,
            "classification_status": "catalog_gap",
            "review_reason_codes": ["SOURCE_JOB_CLASSIFICATION_UNRESOLVED"],
            "evidence_refs": ["job_title"],
            "classification_policy_version": "audit-position-classifier.v3.0",
        }),
        "normalized_requirements": [],
        "salary": None,
        "unresolved_items": [],
    }
    if not source_classification_resolved:
        result["unresolved_items"].append({
            "item_type": "job_title",
            "source_value": title,
            "reason": "source_job_classification_unresolved",
            "severity": "blocking",
            "source": "normalization",
            "code": "audit_batch_unresolved_position",
        })

    seen_skills: set[tuple[str, str | None, str]] = set()
    normalized_by_requirement: dict[tuple[str, str], dict[str, Any]] = {}
    for requirement in source.get("normalized_requirements", []):
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        requirement_kind = str(requirement.get("kind") or "skill").strip()
        if not requirement_id:
            raise FlowError("normalized requirement lacks requirement_id")
        for skill in requirement.get("skills", []):
            source_name = str(skill.get("source_name") or "").strip()
            if not source_name:
                continue
            identity_status = skill.get("identity_resolution_status")
            classification_status = skill.get("classification_resolution_status")
            if identity_status is None or classification_status is None:
                raise FlowError(
                    f"skill {source_name!r} lacks v2 identity/classification resolution status"
                )
            source_skill_id = str(skill.get("skill_id") or "")
            source_canonical_name = str(skill.get("canonical_name") or "").strip()
            classifications = skill.get("classifications") or []
            source_resolved = (
                identity_status == "resolved"
                and classification_status == "resolved"
            )
            if source_resolved and (
                not source_skill_id
                or not source_canonical_name
                or not classifications
            ):
                raise FlowError(
                    f"resolved skill {source_name!r} lacks canonical identity or classifications"
                )
            catalog_skill = catalog_skills.get(source_skill_id) if source_resolved else None
            # A reviewed package may refer to an older catalog identity. Preserve
            # the source identity for audit and route it to normalization review.
            if source_resolved and catalog_skill is None:
                source_resolved = False
            resolution = "resolved" if source_resolved else "unresolved"
            skill_id = str(catalog_skill["skill_id"]) if catalog_skill else None
            key = (source_name, skill_id, requirement_id)
            if key in seen_skills:
                continue
            seen_skills.add(key)
            converted_skill = {
                "source_name": source_name,
                "requirement_id": requirement_id,
                "requirement_kind": requirement_kind,
                "skill_id": skill_id,
                "canonical_name": (
                    str(catalog_skill["skill_name"]) if catalog_skill else None
                ),
                "category_code": catalog_skill.get("category") if catalog_skill else None,
                "subcategory_code": None,
                "resolution_status": resolution,
                "resolution_source": (
                    "main_capability_catalog" if source_resolved else "unresolved"
                ),
                "source_skill_id": source_skill_id or None,
                "source_canonical_name": source_canonical_name or None,
                "source_category_code": next(
                    (
                        item.get("code")
                        for item in classifications
                        if item.get("facet") == "concept_class"
                        and item.get("is_primary") is True
                    ),
                    None,
                ),
                "source_subcategory_code": next(
                    (
                        item.get("code")
                        for item in classifications
                        if item.get("facet") == "technology_kind"
                        and item.get("is_primary") is True
                    ),
                    None,
                ),
                "source_resolution_status": (
                    f"identity={identity_status};classification={classification_status}"
                ),
                "source_resolution_source": "extraction_skill_taxonomy_v2",
            }
            result["normalized_requirements"].append(converted_skill)
            normalized_by_requirement.setdefault(
                (requirement_id, source_name.casefold()), converted_skill
            )
            if resolution == "unresolved":
                result["unresolved_items"].append(
                    {
                        "item_type": "skill",
                        "source_value": source_name,
                        "reason": "source_normalization_unresolved",
                        "severity": "warning",
                        "source": "normalization",
                        "code": "audit_batch_unresolved_skill",
                        "details": {
                            "requirement_id": requirement_id,
                            "requirement_kind": requirement_kind,
                        },
                    }
                )

    # Every extracted skill spelling must have an exact normalization row.
    # Preserve missing source normalization as an explicit review item.
    extracted_skills = [
        (
            str(requirement.get("requirement_id") or "").strip(),
            str(requirement.get("kind") or "skill").strip(),
            str(item.get("name") or "").strip(),
        )
        for requirement in extraction.get("requirements", [])
        if requirement.get("kind") == "skill"
        for item in requirement.get("items", [])
    ]
    existing_requirement_skills = {
        (str(item["requirement_id"]), str(item["source_name"]))
        for item in result["normalized_requirements"]
    }
    for requirement_id, requirement_kind, source_name in extracted_skills:
        if not requirement_id:
            raise FlowError(f"extracted skill {source_name!r} lacks requirement_id")
        if not source_name or (requirement_id, source_name) in existing_requirement_skills:
            continue
        matched = normalized_by_requirement.get(
            (requirement_id, source_name.casefold())
        )
        if matched is not None:
            result["normalized_requirements"].append({**matched, "source_name": source_name})
        else:
            result["normalized_requirements"].append({
                "source_name": source_name,
                "requirement_id": requirement_id,
                "requirement_kind": requirement_kind,
                "skill_id": None,
                "canonical_name": None,
                "category_code": None,
                "subcategory_code": None,
                "resolution_status": "unresolved",
            })
            result["unresolved_items"].append({
                "item_type": "skill",
                "source_value": source_name,
                "reason": "source_normalization_missing",
                "severity": "warning",
                "source": "normalization",
                "code": "audit_batch_missing_skill_normalization",
                "details": {
                    "requirement_id": requirement_id,
                    "requirement_kind": requirement_kind,
                },
            })
        existing_requirement_skills.add((requirement_id, source_name))

    salary = source.get("salary")
    if isinstance(salary, dict):
        period = salary.get("period")
        result["salary"] = {
            "raw_value": salary.get("raw_text") or salary.get("raw_value"),
            "minimum": salary.get("minimum"),
            "maximum": salary.get("maximum"),
            "currency": salary.get("currency"),
            "period": SALARY_PERIOD_MAP.get(str(period), period if period in {"hour", "day", "month", "year", "unknown"} else "unknown"),
        }
    return result


def ensure_main_catalog_skills(
    main: API, token: str, records: list[PreparedRecord]
) -> dict[str, dict[str, Any]]:
    """Bind resolved Extraction IDs to the reviewed main capability catalog."""
    catalog = read_json(MAIN_SKILL_CATALOG)
    catalog_entries = catalog.get("skills") if isinstance(catalog, dict) else None
    if not isinstance(catalog_entries, dict) or not catalog_entries:
        raise FlowError("main capability catalog snapshot is missing or empty")
    existing = main.request("GET", "/api/v1/skills", token=token)["data"]
    by_name = {
        str(item.get("skill_name") or "").casefold(): item
        for item in existing
        if item.get("skill_id") and item.get("skill_name")
    }
    resolved: dict[str, dict[str, Any]] = {}
    for record in records:
        for requirement in record.normalization_source.get("normalized_requirements", []):
            for skill in requirement.get("skills", []):
                if not (
                    skill.get("identity_resolution_status") == "resolved"
                    and skill.get("classification_resolution_status") == "resolved"
                ):
                    continue
                source_skill_id = str(skill.get("skill_id") or "")
                source_name = str(skill.get("canonical_name") or "").strip()
                source_entry = catalog_entries.get(source_skill_id)
                if not isinstance(source_entry, dict):
                    raise FlowError(
                        f"Extraction skill {source_skill_id!r} is absent from the reviewed catalog"
                    )
                if source_entry.get("canonical_name") != source_name:
                    raise FlowError(
                        f"Extraction skill {source_skill_id!r} conflicts with the reviewed catalog"
                    )
                source_classifications = sorted(
                    (
                        str(item.get("facet")),
                        str(item.get("code")),
                        item.get("is_primary") is True,
                    )
                    for item in skill.get("classifications") or []
                )
                catalog_classifications = sorted(
                    (
                        str(item.get("facet")),
                        str(item.get("code")),
                        item.get("is_primary") is True,
                    )
                    for item in source_entry.get("classifications") or []
                )
                if source_classifications != catalog_classifications:
                    raise FlowError(
                        f"Extraction skill {source_skill_id!r} classifications "
                        "conflict with the reviewed catalog"
                    )
                main_skill = by_name.get(source_name.casefold())
                if main_skill is None:
                    raise FlowError(
                        f"main capability catalog is not synchronized for {source_skill_id!r}"
                    )
                resolved[source_skill_id] = main_skill
    return resolved


def validate_structural_contracts(
    extraction: dict[str, Any], normalization: dict[str, Any], raw_text: str
) -> None:
    if extraction.get("schema_version") != "v2":
        raise FlowError("converted extraction is not v2")
    if normalization.get("schema_version") != "v2":
        raise FlowError("converted normalization is not v2")
    if extraction.get("document_id") != normalization.get("document_id"):
        raise FlowError("converted document IDs do not match")
    validate_evidence_slices(extraction, raw_text)
    for item in extraction.get("requirements", []):
        if item.get("kind") not in REQUIREMENT_FIELDS:
            raise FlowError(f"unsupported converted requirement kind: {item.get('kind')}")
        if item.get("modality") not in VALID_MODALITIES:
            raise FlowError(f"unsupported modality: {item.get('modality')}")
    classification = normalization.get("job_classification") or {}
    classification_status = classification.get("classification_status")
    if classification_status in {"resolved", "manually_confirmed"}:
        required = (
            "taxonomy_version",
            "position_id",
            "position_code",
            "position_name",
            "family_code",
            "family_name",
        )
        if any(not classification.get(field) for field in required):
            raise FlowError("resolved job classification lacks taxonomy or position identity")
    elif classification_status in {"ambiguous", "out_of_scope", "catalog_gap"}:
        if classification.get("position_id"):
            raise FlowError("unresolved job classification cannot have a position identity")
    else:
        raise FlowError(f"unsupported job classification status: {classification_status}")


def validate_with_repository_contracts(
    extraction: dict[str, Any], normalization: dict[str, Any]
) -> str:
    """Validate exactly the contracts consumed by the main backend."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.contracts.jd import JDExtractionResult, JDNormalizedResult

    JDExtractionResult.model_validate(extraction)
    JDNormalizedResult.model_validate(normalization)
    return "main_backend_pydantic_v2"


def require_exact_published_replay(
    existing_parse: dict[str, Any],
    extraction: dict[str, Any],
    normalization: dict[str, Any],
) -> None:
    """Published facts are immutable and may only be replayed byte-for-byte."""
    differing = [
        field
        for field, expected in (
            ("extraction_result", extraction),
            ("normalized_result", normalization),
        )
        if existing_parse.get(field) != expected
    ]
    if differing:
        raise FlowError(
            "published replay differs from the current v2 source; "
            "create a clean JD version before synchronization: "
            + ", ".join(differing)
        )


def prepare_records(
    batch_root: Path,
    *,
    max_records: int,
    only_families: set[str],
    skipped_source_ids: set[str],
    only_source_ids: set[str] | None = None,
) -> tuple[list[PreparedRecord], list[dict[str, Any]], dict[str, Any]]:
    annotations = read_json(batch_root / "final" / "annotations_nested.json")
    normalized_rows = read_json(batch_root / "final" / "normalized_annotations.json")
    failed_cases = read_jsonl(batch_root / "final" / "failed_cases.jsonl")
    if not isinstance(annotations, list) or not isinstance(normalized_rows, list):
        raise FlowError("annotations_nested.json and normalized_annotations.json must be lists")

    normalized_by_id = {str(row["document_id"]): row for row in normalized_rows}
    prepared: list[PreparedRecord] = []
    skipped: list[dict[str, Any]] = []
    validation_modes: Counter[str] = Counter()
    raw_modes: Counter[str] = Counter()

    for extraction_source in annotations:
        source_id = str(extraction_source.get("document_id") or "")
        if only_source_ids is not None and source_id not in only_source_ids:
            continue
        if source_id in skipped_source_ids:
            skipped.append(
                {"source_document_id": source_id, "reason": "explicitly_skipped"}
            )
            continue
        normalization_source = normalized_by_id.get(source_id)
        if not source_id or normalization_source is None:
            skipped.append({"source_document_id": source_id, "reason": "missing_normalization"})
            continue
        classification = normalization_source.get("job_classification") or {}
        if classification.get("schema_version") != "job-position-classification.v3":
            raise FlowError(
                f"record {source_id} does not use job-position-classification.v3"
            )
        position_code = str(classification.get("position_code") or "UNRESOLVED")
        position_name = classification.get("position_name")
        family_code = str(classification.get("family_code") or "UNRESOLVED")
        family_name = classification.get("family_name")
        skill_domain_codes = tuple(
            str(code)
            for code in (classification.get("observed_skill_domain_codes") or [])
        )
        classification_resolved = source_classification_is_resolved(classification)
        if only_families and family_code not in only_families:
            continue
        cleaning_status = extraction_source.get("cleaning_status")
        cleaned_candidate = extraction_source.get("cleaned_text")
        source_raw_text = extraction_source.get("raw_text")
        if (
            cleaning_status != "ok"
            or not isinstance(cleaned_candidate, str)
            or not isinstance(source_raw_text, str)
            or not cleaned_candidate.strip()
            or not source_raw_text.strip()
        ):
            raise FlowError(
                f"record {source_id} is not a cleaned run record "
                f"(cleaning_status={cleaning_status!r})"
            )
        extraction_copy = copy.deepcopy(extraction_source)
        raw_text = cleaned_candidate
        cleaned_text = cleaned_candidate
        raw_source = "cleaned_run_text"
        warnings: list[str] = []
        title = str(
            ((extraction_source.get("job_title") or {}).get("value"))
            or f"审计 JD {source_id}"
        )
        placeholder = f"DRY_{source_id}"
        converted_extraction = convert_extraction(extraction_copy, placeholder)
        preview_catalog = {
            str(skill["skill_id"]): {
                "skill_id": str(skill["skill_id"]),
                "skill_name": str(skill["canonical_name"]),
                "category": None,
            }
            for requirement in normalization_source.get("normalized_requirements", [])
            for skill in requirement.get("skills", [])
            if skill.get("identity_resolution_status") == "resolved"
            and skill.get("classification_resolution_status") == "resolved"
        }
        converted_normalization = convert_normalization(
            normalization_source, extraction_source, placeholder, preview_catalog
        )
        normalization_resolved = classification_resolved and all(
            item.get("resolution_status") == "resolved"
            for item in converted_normalization["normalized_requirements"]
        )
        publishable_projection = classification_resolved and (
            projection_has_available_facts(
                converted_extraction, converted_normalization
            )
        )
        validate_structural_contracts(converted_extraction, converted_normalization, raw_text)
        validation_modes[
            validate_with_repository_contracts(converted_extraction, converted_normalization)
        ] += 1
        raw_modes[raw_source] += 1
        prepared.append(
            PreparedRecord(
                source_document_id=source_id,
                position_code=position_code,
                position_name=str(position_name) if position_name is not None else None,
                family_code=family_code,
                family_name=str(family_name) if family_name is not None else None,
                skill_domain_codes=skill_domain_codes,
                title=title,
                raw_text=raw_text,
                raw_source=raw_source,
                source_raw_text=source_raw_text,
                cleaned_text=cleaned_text,
                extraction_source=extraction_copy,
                normalization_source=normalization_source,
                classification_resolved=classification_resolved,
                normalization_resolved=normalization_resolved,
                publishable_projection=publishable_projection,
                warnings=warnings,
            )
        )
        if max_records and len(prepared) >= max_records:
            break

    if not prepared:
        raise FlowError("no usable extraction/normalization records were prepared")
    metadata = {
        "cleaned_run_count": len(prepared),
        "raw_source_modes": dict(raw_modes),
        "validation_modes": dict(validation_modes),
        "skipped_records": skipped,
    }
    return prepared, failed_cases, metadata


def projection_has_available_facts(
    extraction: dict[str, Any], normalization: dict[str, Any]
) -> bool:
    """A publishable projection needs at least one evidence-backed fact.

    Unresolved skills are excluded per-skill; they must not block an otherwise
    usable responsibility or non-skill requirement projection.
    """
    if any(
        item.get("resolution_status") == "resolved"
        for item in normalization.get("normalized_requirements", [])
    ):
        return True
    if any(item.get("evidence") for item in extraction.get("responsibilities", [])):
        return True
    return any(
        item.get("kind") != "skill" and item.get("evidence")
        for item in extraction.get("requirements", [])
    )


class API:
    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        *,
        max_connections: int | None = None,
    ):
        client_options: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "timeout": timeout,
        }
        if max_connections is not None:
            client_options["limits"] = httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=min(max_connections, 64),
            )
        self.client = httpx.Client(**client_options)

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        expected: int | tuple[int, ...] = 200,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.client.request(method, path, headers=headers, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise FlowError(
                f"{method} {path} returned HTTP {response.status_code} non-JSON: "
                f"{response.text[:300]}"
            ) from exc
        allowed = (expected,) if isinstance(expected, int) else expected
        if response.status_code not in allowed:
            raise FlowError(
                f"{method} {path}: expected {allowed}, got {response.status_code}: "
                f"{json.dumps(body, ensure_ascii=False)}"
            )
        if response.status_code < 400 and body.get("code") != 0:
            raise FlowError(f"{method} {path}: non-zero envelope: {body}")
        return body

    def ready(self, path: str = "/readiness", timeout: int = 120) -> None:
        deadline = time.monotonic() + timeout
        last_error = "not attempted"
        while time.monotonic() < deadline:
            try:
                response = self.client.get(path)
                body = response.json()
                if response.status_code == 200 and body.get("code") == 0:
                    return
                last_error = f"HTTP {response.status_code}: {body}"
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(2)
        raise FlowError(f"readiness timeout for {self.client.base_url}{path}: {last_error}")


def login_main(api: API, username: str, password: str) -> str:
    body = api.request(
        "POST", "/api/v1/auth/login", json={"username": username, "password": password}
    )
    return str(body["data"]["access_token"])


def login_kg(api: API, username: str, password: str) -> str:
    body = api.request(
        "POST", "/api/v1/auth/token", json={"username": username, "password": password}
    )
    return str(body["data"]["access_token"])


def publish_jd_after_validation(
    api: API,
    token: str,
    jd_id: str,
    *,
    approve_validation_warnings: bool,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Wait for the asynchronous validation gate, then publish the JD."""
    path = f"/api/v1/jds/{jd_id}/parse-result/publish"
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = api.client.post(
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise FlowError(
                f"POST {path} returned HTTP {response.status_code} non-JSON: "
                f"{response.text[:300]}"
            ) from exc
        if response.status_code == 200 and body.get("code") == 0:
            return body
        if (
            response.status_code == 409
            and body.get("message") == "validation_review_pending"
            and approve_validation_warnings
        ):
            jd = api.request("GET", f"/api/v1/jds/{jd_id}", token=token)["data"]
            source_jd_id = str(jd.get("source_jd_id") or "").strip()
            extraction_task_id = str(jd.get("extraction_task_id") or "").strip()
            if not source_jd_id or not extraction_task_id:
                raise FlowError(
                    f"POST {path}: JD validation lineage is incomplete"
                )
            pending: list[dict[str, Any]] = []
            page = 1
            while True:
                tasks = api.request(
                    "GET",
                    "/api/v1/review-tasks",
                    token=token,
                    params={
                        "page": page,
                        "page_size": 100,
                        "status": "pending",
                        "task_kind": "data_validation_report",
                        "source_system": "main-system",
                    },
                )["data"]
                pending.extend(tasks)
                if len(tasks) < 100:
                    break
                page += 1
            matching: list[dict[str, Any]] = []
            for task in pending:
                context = api.request(
                    "GET",
                    f"/api/v1/review-tasks/{task['task_id']}/context",
                    token=token,
                )["data"]
                lineage = (context.get("report") or {}).get("lineage") or {}
                if (
                    str(lineage.get("source_jd_id") or "") == source_jd_id
                    and str(lineage.get("extraction_task_id") or "")
                    == extraction_task_id
                ):
                    matching.append(task)
            if len(matching) != 1:
                raise FlowError(
                    f"POST {path}: expected one validation review for source JD "
                    f"{source_jd_id}, found {len(matching)}"
                )
            task = matching[0]
            api.request(
                "POST",
                f"/api/v1/review-tasks/{task['task_id']}/approve",
                token=token,
                json={
                    "review_comment": (
                        "Approved while importing the post-reviewed audited JD batch."
                    )
                },
            )
            continue
        if response.status_code != 409 or body.get("message") != "validation_pending":
            raise FlowError(
                f"POST {path}: expected validation completion, got "
                f"{response.status_code}: {json.dumps(body, ensure_ascii=False)}"
            )
        if time.monotonic() >= deadline:
            raise FlowError(f"POST {path}: validation did not complete within {timeout_seconds:g}s")
        time.sleep(0.2)


def verify_frontend_portal(
    frontend: API,
    *,
    username: str,
    password: str,
    expected_knowledge_graph_ids: set[str],
) -> dict[str, Any]:
    """Verify the browser's real API path through the frontend proxy.

    The React panorama reads ``/api/v1/portal/positions`` through Nginx, not
    from the knowledge-graph service directly.  Keeping this check here makes
    a successful audit run prove the same published positions are reachable by
    the frontend's data source.
    """
    token = login_main(frontend, username, password)
    listing = frontend.request("GET", "/api/v1/portal/positions", token=token)["data"]
    portal_id_by_kg_id = {
        str(item.get("knowledge_graph_id")): str(item.get("position_id"))
        for item in listing
        if item.get("knowledge_graph_id") and item.get("position_id")
    }
    missing = sorted(expected_knowledge_graph_ids - set(portal_id_by_kg_id))
    if missing:
        raise FlowError(
            "frontend portal does not expose published KG positions: "
            + ", ".join(missing)
        )
    graphs: dict[str, int] = {}
    for knowledge_graph_id in sorted(expected_knowledge_graph_ids):
        position_id = portal_id_by_kg_id[knowledge_graph_id]
        graph = frontend.request(
            "GET", f"/api/v1/portal/positions/{position_id}/graph", token=token
        )["data"]
        relation_count = len((graph or {}).get("skill_relations") or [])
        if relation_count == 0:
            raise FlowError(f"frontend portal graph {position_id} has no relations")
        graphs[position_id] = relation_count
    return {
        "portal_position_ids": sorted(portal_id_by_kg_id.values()),
        "verified_knowledge_graph_ids": sorted(expected_knowledge_graph_ids),
        "relation_counts": graphs,
    }


def ensure_taxonomy_positions(
    api: API, token: str, records: list[PreparedRecord]
) -> dict[str, dict[str, Any]]:
    """Resolve every audited v2 position code against the main catalog."""
    existing = api.request("GET", "/api/v1/positions", token=token)["data"]
    by_code = {
        str(item["position_code"]): item
        for item in existing
        if item.get("position_code")
    }
    result: dict[str, dict[str, Any]] = {}
    for position_code in sorted(
        {row.position_code for row in records if row.classification_resolved}
    ):
        position_records = [
            row for row in records if row.position_code == position_code
        ]
        identities = {
            (
                row.position_name,
                row.family_code,
                row.family_name,
            )
            for row in position_records
        }
        if len(identities) != 1:
            raise FlowError(f"position {position_code} has conflicting v2 identities")
        position_name, family_code, family_name = identities.pop()
        item = by_code.get(position_code)
        if item is None:
            raise FlowError(f"main position catalog lacks {position_code}")
        position_id = item.get("position_id") or item.get("id")
        actual_identity = (
            item.get("position_name"),
            item.get("taxonomy_family_code"),
            item.get("taxonomy_family_name"),
        )
        expected_identity = (
            position_name,
            family_code,
            family_name,
        )
        if not position_id or actual_identity != expected_identity:
            raise FlowError(f"position catalog conflict for {position_code}: {item}")
        result[position_code] = {
            "position_id": str(position_id),
            "position_code": position_code,
            "position_name": str(position_name),
            "family_code": family_code,
            "family_name": str(family_name),
            "skill_domain_codes": list(item.get("skill_domain_codes") or []),
        }
    return result


def resolve_kg_taxonomy_positions(
    taxonomy_positions: dict[str, dict[str, Any]],
    kg_positions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Match KG catalog rows by authoritative position code, not main UUID."""
    kg_by_code = {
        str(item["position_code"]): item
        for item in kg_positions
        if item.get("position_code")
    }
    missing = sorted(
        code for code in taxonomy_positions if code not in kg_by_code
    )
    if missing:
        raise FlowError(
            "knowledge-graph catalog lacks taxonomy-owned standard positions: "
            + ", ".join(missing)
        )
    return kg_by_code


def resolve_task_prediction_ids(task: dict[str, Any]) -> list[str]:
    for container in (task, task.get("result_payload") or {}, task.get("result") or {}):
        values = container.get("predicted_ids") if isinstance(container, dict) else None
        if isinstance(values, list):
            return [str(value) for value in values]
    return []


def sync_jd_to_knowledge_graph(main: API, token: str, main_jd_id: str) -> dict[str, Any]:
    """Sync one published JD, tolerating concurrent outbox delivery.

    The sync API claims the outbox message inline; while the background
    outbox worker holds the claim the API answers 409
    knowledge_graph_sync_in_progress until that delivery completes, so retry
    with a short backoff instead of failing the record.
    """
    last_error: FlowError | None = None
    for _ in range(20):
        try:
            return main.request(
                "POST",
                f"/api/v1/integrations/knowledge-graph/jds/{main_jd_id}/sync",
                token=token,
            )["data"]
        except FlowError as exc:
            if "knowledge_graph_sync_in_progress" not in str(exc):
                raise
            last_error = exc
            time.sleep(3)
    raise last_error


def resolve_build_run_id(
    kg: API, token: str, build_run: dict[str, Any], timeout_seconds: float = 300.0
) -> int:
    """Resolve the build_run_id from a graph build response.

    The knowledge-graph service queues builds as asynchronous jobs, so the
    initial response may only carry a job without a run id; poll the job
    until the runner finishes.
    """
    build_run_id = build_run.get("build_run_id")
    if build_run_id is not None:
        return int(build_run_id)
    job_id = build_run.get("job_id")
    if job_id is None:
        raise FlowError(
            "graph build response carries neither build_run_id nor job_id: "
            f"{build_run}"
        )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = kg.request(
            "GET", f"/api/v1/graph/build-jobs/{job_id}", token=token
        )["data"]
        if job.get("build_run_id") is not None:
            return int(job["build_run_id"])
        if str(job.get("status")) == "failed":
            raise FlowError(f"graph build job {job_id} failed: {job.get('error')}")
        time.sleep(2)
    raise FlowError(
        f"graph build job {job_id} did not finish within {timeout_seconds}s"
    )


def run_prediction(main: API, token: str, tag: str) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    payloads = [
        (
            "policy",
            "具身智能与低空经济流程验证政策",
            "政策提出发展具身智能和低空经济相关产业，用于岗位预测流程验证。",
        ),
        (
            "report",
            "AI 安全与多模态内容溯源流程验证报告",
            "行业报告关注 AI 安全 和 多模态内容溯源，用于岗位预测流程验证。",
        ),
    ]
    for source_type, title, raw_text in payloads:
        sources.append(
            main.request(
                "POST",
                f"/api/v1/trend-sources/{source_type}",
                token=token,
                json={
                    "title": f"{title}-{tag}",
                    "source_name": "audit-jd-e2e-script",
                    "raw_text": raw_text,
                    "credibility_score": 0.9,
                },
            )["data"]
        )
    source_ids = [str(item["source_id"]) for item in sources]
    task = main.request(
        "POST",
        "/api/v1/predicted-positions/tasks",
        token=token,
        json={"source_ids": source_ids},
    )["data"]
    predicted_ids = resolve_task_prediction_ids(task)
    if not predicted_ids:
        listing = main.request("GET", "/api/v1/predicted-positions", token=token)["data"]
        source_set = set(source_ids)
        predicted_ids = [
            str(item["predicted_id"])
            for item in listing
            if source_set.intersection(map(str, item.get("related_source_ids") or []))
        ]
    if not predicted_ids:
        raise FlowError("prediction task produced no predicted positions")
    predicted_id = predicted_ids[0]
    score = main.request(
        "POST",
        f"/api/v1/predicted-positions/{predicted_id}/confidence-score",
        token=token,
    )["data"]
    published = main.request(
        "POST", f"/api/v1/predicted-positions/{predicted_id}/publish", token=token
    )["data"]
    return {
        "source_ids": source_ids,
        "task": task,
        "predicted_ids": predicted_ids,
        "confidence": score,
        "published": published,
    }


def inspect_unresolved_items(
    kg: API,
    token: str,
    document_ids: list[str],
) -> list[dict[str, Any]]:
    """Read unresolved items without mutating authoritative published facts.

    Published facts imported from the main system are immutable in the KG.
    Any build-scoped review tasks generated from unresolved normalization are
    handled later through the review-task workflow.
    """
    target_ids = set(map(str, document_ids))
    rows = kg.request(
        "GET",
        "/api/v1/normalization/unresolved-items",
        token=token,
    )["data"]
    return [
        {
            "id": int(item["id"]),
            "document_id": str(item.get("document_id")),
            "item_type": item.get("item_type"),
            "source_name": item.get("source_name"),
            "status": item.get("status"),
            "reason": item.get("reason"),
        }
        for item in rows
        if str(item.get("document_id")) in target_ids
        and item.get("status") in {None, "open"}
    ]


def open_review_tasks_for_build(
    kg: API, token: str, build_run_id: int
) -> list[dict[str, Any]]:
    """List every open review task of one build run.

    The review-task list endpoint paginates (default 20, max 100 per page),
    so a single unfiltered request only sees the first page; sweep all pages.
    """
    open_tasks: list[dict[str, Any]] = []
    page = 1
    while True:
        tasks = kg.request(
            "GET",
            f"/api/v1/review-tasks?page={page}&page_size=100",
            token=token,
        )["data"]
        if not tasks:
            break
        open_tasks.extend(
            task
            for task in tasks
            if int(task.get("build_run_id") or -1) == build_run_id
            and task.get("status") in {"pending", "claimed", "modified"}
        )
        if len(tasks) < 100:
            break
        page += 1
    return open_tasks


def review_and_publish_graph(
    kg: API,
    token: str,
    build_run_id: int,
    tag: str,
    kg_position_id: str,
    *,
    publish: bool,
) -> dict[str, Any]:
    handled: list[int] = []
    for _ in range(12):
        open_tasks = open_review_tasks_for_build(kg, token, build_run_id)
        if not open_tasks:
            break
        for task in open_tasks:
            task_id = int(task["id"])
            if task.get("status") == "pending":
                kg.request(
                    "POST",
                    f"/api/v1/review-tasks/{task_id}/claim",
                    token=token,
                    json={"reason": "audited real JD end-to-end verification"},
                )
            kg.request(
                "POST",
                f"/api/v1/review-tasks/{task_id}/approve",
                token=token,
                json={"reason": "audited real JD end-to-end verification"},
            )
            handled.append(task_id)

    gate = kg.request(
        "GET", f"/api/v1/graph/build-runs/{build_run_id}/publish-gate", token=token
    )["data"]
    result: dict[str, Any] = {
        "handled_review_task_ids": handled,
        "publish_gate": gate,
    }
    if not publish:
        return result
    if not gate.get("allowed"):
        raise FlowError("graph publish gate is closed: " + json.dumps(gate, ensure_ascii=False))
    result["published_version"] = kg.request(
        "POST",
        f"/api/v1/graph/build-runs/{build_run_id}/publish",
        token=token,
        json={
            "version_name": f"audit-real-jd-{kg_position_id.lower()}-{tag}",
            "release_notes": "End-to-end verification using audited real JD extraction output",
            "reason": "audited real JD end-to-end verification",
        },
    )["data"]
    return result


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Report written: {path}")


def build_group_summary(records: list[PreparedRecord]) -> dict[str, Any]:
    by_family = Counter(record.family_code for record in records)
    by_position = Counter(record.position_code for record in records)
    return {
        "record_count": len(records),
        "classification_resolved_count": sum(
            record.classification_resolved for record in records
        ),
        "normalization_resolved_count": sum(
            record.normalization_resolved for record in records
        ),
        "by_family": dict(sorted(by_family.items())),
        "by_position": dict(sorted(by_position.items())),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    tag = now_tag()
    report_path = (
        Path(args.report)
        if args.report
        else Path("data/e2e_reports") / f"audit_jd_flow_{tag}.json"
    )
    only_families = set(args.only_family or [])

    work_dir = Path(args.work_dir)
    with project_tempdir(work_dir) as temp_dir:
        batch_root = locate_batch_root(Path(args.input), temp_dir)
        records, failed_cases, preparation = prepare_records(
            batch_root,
            max_records=args.max_records,
            only_families=only_families,
            skipped_source_ids=set(args.skip_source_document),
        )
        summary = build_group_summary(records)
        log(
            f"Prepared {summary['record_count']} records; families={summary['by_family']}"
        )

        if args.dry_run:
            previews = [
                {
                    "source_document_id": record.source_document_id,
                    "title": record.title,
                    "position_code": record.position_code,
                    "position_name": record.position_name,
                    "family_code": record.family_code,
                    "family_name": record.family_name,
                    "raw_source": record.raw_source,
                    "raw_text_length": len(record.raw_text),
                    "evidence_count": sum(
                        1 for _ in iter_evidence_nodes(record.extraction_source)
                    ),
                    "normalized_skill_count": sum(
                        len(item.get("skills") or [])
                        for item in record.normalization_source.get(
                            "normalized_requirements", []
                        )
                    ),
                    "warnings": record.warnings,
                }
                for record in records
            ]
            report = {
                "status": "dry_run_success",
                "input": str(args.input),
                "batch_root": str(batch_root),
                "summary": summary,
                "source_failed_count": len(failed_cases),
                "source_failed_cases": failed_cases,
                "preparation": preparation,
                "records": previews,
            }
            write_report(report_path, report)
            return report

        if not args.kg_username or not args.kg_password:
            raise FlowError(
                "knowledge graph service credentials are required; set "
                "KNOWLEDGE_GRAPH_SERVICE_USERNAME and "
                "KNOWLEDGE_GRAPH_SERVICE_PASSWORD or pass "
                "--kg-username/--kg-password"
            )
        main = API(args.main_url, args.timeout)
        kg = API(args.kg_url, args.timeout)
        frontend = API(args.frontend_url, args.timeout) if args.frontend_url else None
        outcomes: list[RecordOutcome] = []
        try:
            log("Checking main and knowledge-graph readiness")
            main.ready()
            kg.ready()
            main_token = login_main(main, args.main_username, args.main_password)
            kg_token = login_kg(kg, args.kg_username, args.kg_password)
            resolved_records = [record for record in records if record.classification_resolved]
            if not resolved_records:
                raise FlowError("no job-classification-resolved record is eligible for KG sync")
            catalog_skills = ensure_main_catalog_skills(main, main_token, resolved_records)
            taxonomy_positions = ensure_taxonomy_positions(main, main_token, resolved_records)
            # The public position list intentionally contains only published
            # graphs.  Mapping must use the integration catalog so a new
            # taxonomy-owned position can receive its first graph build.
            kg_positions = kg.request(
                "GET", "/api/v1/integrations/positions", token=kg_token
            )["data"]
            kg_by_code = resolve_kg_taxonomy_positions(taxonomy_positions, kg_positions)
            for position in taxonomy_positions.values():
                kg_position = kg_by_code[position["position_code"]]
                main.request(
                    "PUT",
                    f"/api/v1/integrations/knowledge-graph/mappings/position/{position['position_id']}",
                    token=main_token,
                    json={"knowledge_graph_id": str(kg_position["position_id"])},
                )
            log(f"Resolved {len(taxonomy_positions)} taxonomy-owned standard positions")

            existing_jds = main.request("GET", "/api/v1/jds", token=main_token)["data"]
            existing_by_source_name: dict[str, dict[str, Any]] = {}
            for item in existing_jds:
                source_name = str(item.get("source_name") or "")
                if source_name and source_name not in existing_by_source_name:
                    existing_by_source_name[source_name] = item

            synced_by_position: dict[str, list[str]] = defaultdict(list)
            for index, record in enumerate(records, start=1):
                standard_position = (
                    taxonomy_positions[record.position_code]
                    if record.classification_resolved else None
                )
                outcome = RecordOutcome(
                    source_document_id=record.source_document_id,
                    position_code=record.position_code,
                    family_code=record.family_code,
                    main_position_id=(standard_position["position_id"] if standard_position else None),
                    title=record.title,
                    raw_source=record.raw_source,
                    warnings=list(record.warnings),
                )
                outcomes.append(outcome)
                try:
                    log(
                        f"[{index}/{len(records)}] Importing {record.source_document_id} "
                        f"({record.position_code} -> "
                        f"{standard_position['position_name'] if standard_position else 'classification review'})"
                    )
                    source_name = f"{batch_root.name}:{record.source_document_id}"
                    created = existing_by_source_name.get(source_name)
                    if created is None:
                        created = main.request(
                            "POST",
                            "/api/v1/jds/text",
                            token=main_token,
                            json={
                                "source_type": "audited_real_extraction_replay",
                                "source_name": source_name,
                                "title": record.title,
                                "raw_text": record.raw_text,
                                "cleaned_text": record.cleaned_text,
                            },
                        )["data"]
                        existing_by_source_name[source_name] = created
                    main_jd_id = str(created.get("jd_id") or created["id"])
                    outcome.main_jd_id = main_jd_id
                    existing_parse = main.request(
                        "GET",
                        f"/api/v1/jds/{main_jd_id}/parse-result",
                        token=main_token,
                        expected=(200, 404),
                    ).get("data")
                    extraction = convert_extraction(
                        copy.deepcopy(record.extraction_source), main_jd_id
                    )
                    normalization = convert_normalization(
                        record.normalization_source,
                        record.extraction_source,
                        main_jd_id,
                        catalog_skills,
                        standard_position_id=(
                            standard_position["position_id"]
                            if standard_position else None
                        ),
                        standard_position_code=(
                            standard_position["position_code"]
                            if standard_position else None
                        ),
                        standard_position_name=(
                            standard_position["position_name"]
                            if standard_position else None
                        ),
                        standard_family_code=(
                            standard_position["family_code"]
                            if standard_position else None
                        ),
                        standard_family_name=(
                            standard_position["family_name"]
                            if standard_position else None
                        ),
                        standard_skill_domain_codes=(
                            standard_position["skill_domain_codes"]
                            if standard_position else ()
                        ),
                    )
                    validate_structural_contracts(
                        extraction, normalization, record.raw_text
                    )
                    validate_with_repository_contracts(extraction, normalization)
                    if existing_parse and existing_parse.get("workflow_status") == "published":
                        require_exact_published_replay(
                            existing_parse, extraction, normalization
                        )
                        synced = sync_jd_to_knowledge_graph(main, main_token, main_jd_id)
                        outcome.sync_status = str(synced.get("sync_status"))
                        outcome.payload_hash = synced.get("payload_hash")
                        outcome.status = "synced"
                        synced_by_position[standard_position["position_id"]].append(main_jd_id)
                        continue
                    main.request(
                        "PUT",
                        f"/api/v1/jds/{main_jd_id}/parse-result",
                        token=main_token,
                        json={
                            "extraction_result": extraction,
                            "normalized_result": normalization,
                        },
                    )
                    if not record.publishable_projection:
                        outcome.status = (
                            "awaiting_normalization_review"
                            if record.classification_resolved
                            else "awaiting_classification_review"
                        )
                        continue
                    main.request(
                        "POST",
                        f"/api/v1/jds/{main_jd_id}/parse-result/confirm",
                        token=main_token,
                    )
                    publish_jd_after_validation(
                        main,
                        main_token,
                        main_jd_id,
                        approve_validation_warnings=args.approve_validation_warnings,
                    )
                    synced = sync_jd_to_knowledge_graph(main, main_token, main_jd_id)
                    outcome.sync_status = str(synced.get("sync_status"))
                    outcome.payload_hash = synced.get("payload_hash")
                    outcome.status = "synced"
                    synced_by_position[standard_position["position_id"]].append(main_jd_id)
                except (FlowError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    outcome.status = "failed"
                    outcome.error = str(exc)
                    log(f"[{record.source_document_id}] FAILED: {exc}")
                    if not args.continue_on_error:
                        raise

            synced_ids = [
                outcome.main_jd_id
                for outcome in outcomes
                if outcome.status == "synced" and outcome.main_jd_id
            ]
            if not synced_ids:
                raise FlowError("no record reached KG synchronization")
            log(f"Synchronized {len(synced_ids)}/{len(records)} records")
            observed_unresolved = inspect_unresolved_items(
                kg,
                kg_token,
                synced_ids,
            )
            log(
                "Observed unresolved items without mutating authoritative facts: "
                f"{len(observed_unresolved)}"
            )

            graph_results: dict[str, Any] = {}

            def build_position_graph(
                main_position_id: str,
                expected_ids: list[str],
                position_name: str,
            ) -> dict[str, Any]:
                log(
                    f"Building {position_name} from {len(expected_ids)} synchronized real records"
                )
                group_result: dict[str, Any] = {
                    "main_position_id": main_position_id,
                    "main_position_name": position_name,
                    "expected_real_jd_ids": expected_ids,
                    "status": "pending",
                }
                try:
                    build = main.request(
                        "POST",
                        f"/api/v1/integrations/knowledge-graph/positions/{main_position_id}/build",
                        token=main_token,
                        json={
                            "minimum_effective_weight": args.minimum_effective_weight,
                            "minimum_valid_samples": args.minimum_valid_samples,
                        },
                    )["data"]
                    kg_position_id = str(build["knowledge_graph_position_id"])
                    build_run = build["build_run"]
                    build_run_id = resolve_build_run_id(kg, kg_token, build_run)
                    samples = kg.request(
                        "GET",
                        f"/api/v1/graph/build-runs/{build_run_id}/samples",
                        token=kg_token,
                    )["data"]
                    expected_set = set(map(str, expected_ids))
                    real_samples = [
                        row
                        for row in samples
                        if str(row.get("document_id")) in expected_set
                    ]
                    included_real = [row for row in real_samples if row.get("included")]
                    if not real_samples:
                        raise FlowError(
                            "none of this target's newly synchronized records appeared in build samples"
                        )
                    workflow = review_and_publish_graph(
                        kg,
                        kg_token,
                        build_run_id,
                        tag,
                        kg_position_id,
                        publish=not args.skip_graph_publish,
                    )
                    published_graph = None
                    if not args.skip_graph_publish:
                        published_graph = kg.request(
                            "GET",
                            f"/api/v1/positions/{kg_position_id}/graph",
                            token=kg_token,
                        )["data"]
                    group_result.update(
                        {
                            "status": "success",
                            "knowledge_graph_position_id": kg_position_id,
                            "build": build,
                            "build_run_id": build_run_id,
                            "real_samples": real_samples,
                            "real_sample_count": len(real_samples),
                            "included_real_sample_count": len(included_real),
                            "workflow": workflow,
                            "published_graph": published_graph,
                            "published_relation_count": len(
                                (published_graph or {}).get("skill_relations") or []
                            ),
                        }
                    )
                except (FlowError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    group_result["status"] = "failed"
                    group_result["error"] = str(exc)
                    log(f"[{position_name}] GRAPH FAILED: {exc}")
                return group_result

            graph_targets = [
                (
                    main_position_id,
                    expected_ids,
                    next(
                        item["position_name"]
                        for item in taxonomy_positions.values()
                        if item["position_id"] == main_position_id
                    ),
                )
                for main_position_id, expected_ids in sorted(synced_by_position.items())
                if expected_ids
            ]
            if args.graph_build_workers > 1:
                with ThreadPoolExecutor(
                    max_workers=args.graph_build_workers,
                    thread_name_prefix="kg-graph-build",
                ) as executor:
                    futures = {
                        executor.submit(
                            build_position_graph, position_id, ids, position_name
                        ): position_id
                        for position_id, ids, position_name in graph_targets
                    }
                    for future in as_completed(futures):
                        group_result = future.result()
                        graph_results[group_result["main_position_id"]] = group_result
            else:
                for position_id, ids, position_name in graph_targets:
                    group_result = build_position_graph(
                        position_id, ids, position_name
                    )
                    graph_results[group_result["main_position_id"]] = group_result
            if (
                not args.continue_on_error
                and any(row.get("status") == "failed" for row in graph_results.values())
            ):
                raise FlowError("one or more graph builds failed")

            frontend_verification = None
            if frontend is not None and not args.skip_graph_publish:
                successful_target_ids = {
                    str(group_result["knowledge_graph_position_id"])
                    for group_result in graph_results.values()
                    if group_result.get("status") == "success"
                }
                if not successful_target_ids:
                    raise FlowError("no published graph is available for frontend verification")
                log(
                    "Verifying published graphs through the main frontend proxy: "
                    + ", ".join(sorted(successful_target_ids))
                )
                frontend_verification = verify_frontend_portal(
                    frontend,
                    username=args.main_username,
                    password=args.main_password,
                    expected_knowledge_graph_ids=successful_target_ids,
                )

            prediction = None
            if not args.skip_prediction:
                log("Running mock predicted-position workflow")
                prediction = run_prediction(main, main_token, tag)

            graph_success_count = sum(
                row.get("status") == "success" for row in graph_results.values()
            )
            report_status = (
                "success"
                if len(synced_ids) == len(records) and graph_success_count > 0
                else "partial_success"
            )
            report = {
                "status": report_status,
                "run_tag": tag,
                "input": str(args.input),
                "batch_root": str(batch_root),
                "summary": summary,
                "taxonomy_positions": taxonomy_positions,
                "preparation": preparation,
                "source_failed_count": len(failed_cases),
                "source_failed_cases": failed_cases,
                "records": [asdict(outcome) for outcome in outcomes],
                "synced_count": len(synced_ids),
                "observed_unresolved_items": observed_unresolved,
                "graph_results": graph_results,
                "graph_success_count": graph_success_count,
                "frontend_verification": frontend_verification,
                "prediction": prediction,
            }
            write_report(report_path, report)
            return report
        finally:
            main.close()
            kg.close()
            if frontend is not None:
                frontend.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run audited real JD data through main backend, immutable KG facts, graph build, and prediction flows."
    )
    parser.add_argument(
        "--input",
        default="data/audit.zip",
        help="audit ZIP or extracted batch directory",
    )
    parser.add_argument("--main-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kg-url", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--frontend-url",
        default=None,
        help="optional main frontend base URL; verifies the Nginx portal path after publish",
    )
    parser.add_argument("--main-username", default="demo_admin")
    parser.add_argument("--main-password", default="password123")
    parser.add_argument(
        "--kg-username",
        default=os.environ.get("KNOWLEDGE_GRAPH_SERVICE_USERNAME"),
    )
    parser.add_argument(
        "--kg-password",
        default=os.environ.get("KNOWLEDGE_GRAPH_SERVICE_PASSWORD"),
    )
    parser.add_argument(
        "--only-family",
        action="append",
        default=[],
        help="process only one taxonomy family; repeat for multiple families",
    )
    parser.add_argument("--minimum-effective-weight", type=float, default=0.05)
    parser.add_argument("--minimum-valid-samples", type=int, default=1)
    parser.add_argument(
        "--graph-build-workers",
        type=int,
        default=1,
        help="build and publish KG graphs for multiple positions concurrently",
    )
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument(
        "--skip-source-document",
        action="append",
        default=[],
        help="skip an already completed Extraction document_id; repeatable",
    )
    parser.add_argument(
        "--work-dir",
        default="data/.audit-flow-tmp",
        help="project-local writable directory for temporary audit extraction",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--continue-on-error", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--skip-graph-publish", action="store_true")
    parser.add_argument("--skip-prediction", action="store_true")
    parser.add_argument(
        "--approve-validation-warnings",
        action="store_true",
        help=(
            "approve pending Validation WARN review tasks created while replaying "
            "a post-reviewed audited batch; BLOCK findings still fail closed"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        result = run(parse_args())
        print(
            json.dumps(
                {
                    "status": result.get("status"),
                    "prepared_count": (result.get("summary") or {}).get("record_count"),
                    "synced_count": result.get("synced_count"),
                    "graph_success_count": result.get("graph_success_count"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (FlowError, httpx.HTTPError) as exc:
        print(f"[FAILED] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
