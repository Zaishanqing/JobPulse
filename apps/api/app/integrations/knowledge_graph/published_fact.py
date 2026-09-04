from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

from jobgraph_contracts.published_jd import (
    PUBLISHED_JD_FACT_V3,
    PublishedJDFactV3,
    ValidationLineageV2,
    build_published_jd_fact_v3,
)


CONTRACT_VERSION = "published-jd-fact.v1"
CONTRACT_VERSION_V3 = PUBLISHED_JD_FACT_V3


def _source_observed_at(value: date | datetime | str | None) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str) or value is None:
        return value
    raise TypeError("publish_date must be a date, datetime, string, or null")


@dataclass(frozen=True)
class PublishedJDFactV1:
    payload: dict[str, Any]

    def to_contract(self) -> dict[str, Any]:
        return dict(self.payload)


def publication_snapshot_views(snapshot: dict[str, Any]) -> tuple[Any, Any]:
    """Expose an immutable publication snapshot through the existing mapper shape."""

    jd_payload = snapshot.get("jd")
    legacy = snapshot.get("legacy")
    if not isinstance(jd_payload, dict) or not isinstance(legacy, dict):
        raise ValueError("jd_publication_snapshot_invalid")
    published_at = snapshot.get("published_at")
    if not isinstance(published_at, str) or not published_at:
        raise ValueError("jd_publication_snapshot_invalid")
    try:
        source_version = datetime.fromisoformat(published_at)
    except ValueError as exc:
        raise ValueError("jd_publication_snapshot_invalid") from exc
    jd = SimpleNamespace(
        id=snapshot.get("jd_id"),
        source_type=jd_payload.get("source_type"),
        source_name=jd_payload.get("source_name"),
        enterprise_id=jd_payload.get("enterprise_id"),
        publish_date=jd_payload.get("publish_date"),
    )
    parsed = SimpleNamespace(
        id=snapshot.get("parse_result_id"),
        workflow_status="published",
        schema_version=snapshot.get("schema_version"),
        normalization_schema_version=snapshot.get(
            "normalization_schema_version"
        ),
        extraction_result=snapshot.get("extraction_result"),
        normalized_result=snapshot.get("normalized_result"),
        education=legacy.get("education"),
        experience=legacy.get("experience"),
        industry=legacy.get("industry"),
        created_at=source_version,
        updated_at=source_version,
    )
    return jd, parsed


def map_published_jd_fact(
    *,
    jd: Any,
    parsed: Any,
    extraction_fact: dict[str, Any],
    normalized_fact: dict[str, Any],
    mapping_revision_at: datetime | None = None,
) -> PublishedJDFactV1:
    if parsed.workflow_status != "published":
        raise ValueError("Only published JD facts can enter authoritative KG sync")
    if parsed.schema_version != "v2" or parsed.normalization_schema_version != "v2":
        raise ValueError("Only schema_version v2 is supported")
    skills = [
        skill
        for requirement in normalized_fact.get("normalized_requirements", [])
        for skill in requirement.get("normalized_skills", [])
    ]
    evidence = []
    for group in (
        [extraction_fact.get("job_title")] if extraction_fact.get("job_title") else [],
        extraction_fact.get("responsibilities", []),
        extraction_fact.get("requirements", []),
        extraction_fact.get("company_facts", []),
        extraction_fact.get("employment_facts", []),
    ):
        for item in group:
            if item and item.get("evidence"):
                evidence.append(item["evidence"])
    source_version = parsed.updated_at or parsed.created_at
    if source_version is None:
        raise ValueError("published JD fact has no version timestamp")
    if source_version.tzinfo is None:
        source_version = source_version.replace(tzinfo=timezone.utc)
    if mapping_revision_at is not None:
        if mapping_revision_at.tzinfo is None:
            mapping_revision_at = mapping_revision_at.replace(tzinfo=timezone.utc)
        source_version = max(source_version, mapping_revision_at)
    published_at = source_version.isoformat()
    return PublishedJDFactV1(
        {
            "contract_version": CONTRACT_VERSION,
            "schema_version": "v2",
            "source_system": "main-system",
            "source_jd_id": str(jd.id),
            "source_fact_id": str(parsed.id),
            "source_fact_version": published_at,
            "review_status": "published",
            "published_at": published_at,
            "position_fact": normalized_fact.get("job_classification") or {},
            "skill_facts": skills,
            "requirement_facts": normalized_fact.get("normalized_requirements") or [],
            "education_fact": parsed.education,
            "experience_fact": parsed.experience,
            "industry_fact": parsed.industry,
            "company_facts": extraction_fact.get("company_facts") or [],
            "employment_facts": extraction_fact.get("employment_facts") or [],
            "evidence": evidence,
            "extraction_fact": extraction_fact,
            "normalized_fact": normalized_fact,
            "trace_metadata": {
                "source_type": jd.source_type,
                "source_name": jd.source_name,
                "enterprise_id": jd.enterprise_id,
                "source_observed_at": _source_observed_at(jd.publish_date),
            },
        }
    )


def map_published_jd_fact_v3(
    *,
    jd: Any,
    parsed: Any,
    extraction_fact: dict[str, Any],
    normalized_fact: dict[str, Any],
    publication_snapshot: dict[str, Any],
    mapping_revision_at: datetime | None = None,
) -> PublishedJDFactV3:
    if publication_snapshot.get("contract_version") != "jd-publication-snapshot.v3":
        raise ValueError("jd_publication_v3_snapshot_required")
    validation = publication_snapshot.get("validation_lineage")
    skill_catalog = publication_snapshot.get("skill_catalog_snapshot")
    position_catalog = publication_snapshot.get("position_catalog_snapshot")
    if not all(
        isinstance(value, dict)
        for value in (validation, skill_catalog, position_catalog)
    ):
        raise ValueError("jd_publication_v3_lineage_missing")
    v1_payload = map_published_jd_fact(
        jd=jd,
        parsed=parsed,
        extraction_fact=extraction_fact,
        normalized_fact=normalized_fact,
        mapping_revision_at=mapping_revision_at,
    ).payload
    v1_payload["validation_lineage"] = {
        key: value
        for key, value in validation.items()
        if key in ValidationLineageV2.model_fields
    }
    position_fact = dict(v1_payload["position_fact"])
    position_fact.pop("position_id", None)
    v1_payload["position_fact"] = position_fact
    normalized_fact_payload = dict(v1_payload["normalized_fact"])
    normalized_classification = dict(
        normalized_fact_payload.get("job_classification") or {}
    )
    normalized_classification.pop("position_id", None)
    normalized_fact_payload["job_classification"] = normalized_classification
    v1_payload["normalized_fact"] = normalized_fact_payload
    v1_payload["skill_catalog_snapshot"] = skill_catalog
    v1_payload["position_catalog_snapshot"] = position_catalog
    return build_published_jd_fact_v3(v1_payload)
