"""Versioned authoritative JD fact contracts shared by the main system and KG."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.extraction_v2 import JDExtractionResult
from jobgraph_contracts.normalization_v2 import JDNormalizedResult, JobClassification


PUBLISHED_JD_FACT_V3 = "published-jd-fact.v3"


class ValidationLineageV2(StrictContract):
    state: Literal["present", "absent"]
    data_validation_task_id: str | None = None
    validation_report_id: str | None = None
    validated_bundle_snapshot_id: str | None = None
    validation_policy_version: str | None = None
    validation_conclusion: Literal["pass", "warn"] | None = None
    absent_reason: Literal["validation_not_enforced"] | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ValidationLineageV2":
        present_values = (
            self.data_validation_task_id,
            self.validation_report_id,
            self.validated_bundle_snapshot_id,
            self.validation_policy_version,
            self.validation_conclusion,
        )
        if self.state == "present":
            if any(not value for value in present_values) or self.absent_reason is not None:
                raise ValueError("present validation lineage must be complete")
            return self
        if any(value is not None for value in present_values):
            raise ValueError("absent validation lineage cannot contain validation IDs")
        if self.absent_reason is None:
            raise ValueError("absent validation lineage requires absent_reason")
        return self


class CatalogSnapshotRefV1(StrictContract):
    source: Literal["main-system-skill-catalog"]
    catalog_version: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(
        min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"
    )
    effective_at: str = Field(min_length=1)
    status: Literal["active"]


class PositionCatalogSnapshotRefV1(StrictContract):
    source: Literal["main-system-position-catalog"]
    catalog_version: Literal["position-taxonomy.v3.0.0"]
    content_hash: str = Field(
        min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"
    )
    effective_at: str = Field(min_length=1)
    status: Literal["active"]


class PublishedJDFactV3(StrictContract):
    contract_version: Literal["published-jd-fact.v3"]
    schema_version: Literal["v2"]
    source_system: Literal["main-system"]
    source_jd_id: str = Field(min_length=1)
    source_fact_id: str = Field(min_length=1)
    source_fact_version: str = Field(min_length=1)
    review_status: Literal["published"]
    published_at: str = Field(min_length=1)
    position_fact: JobClassification
    skill_facts: list[dict[str, Any]]
    requirement_facts: list[dict[str, Any]]
    education_fact: str | None = None
    experience_fact: str | None = None
    industry_fact: str | None = None
    company_facts: list[dict[str, Any]]
    employment_facts: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    extraction_fact: JDExtractionResult
    normalized_fact: JDNormalizedResult
    trace_metadata: dict[str, Any]
    validation_lineage: ValidationLineageV2
    skill_catalog_snapshot: CatalogSnapshotRefV1
    position_catalog_snapshot: PositionCatalogSnapshotRefV1

    @model_validator(mode="after")
    def validate_timestamps(self) -> "PublishedJDFactV3":
        for field_name in ("published_at", "source_fact_version"):
            try:
                timestamp = datetime.fromisoformat(
                    getattr(self, field_name).replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError(f"{field_name} must be an ISO timestamp") from exc
            if timestamp.tzinfo is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        return self


def build_published_jd_fact_v3(payload: dict[str, Any]) -> PublishedJDFactV3:
    body = {
        key: value
        for key, value in payload.items()
        if key not in set()
    }
    body["contract_version"] = PUBLISHED_JD_FACT_V3
    unexpected_fields = sorted(set(body).difference(PublishedJDFactV3.model_fields))
    if unexpected_fields:
        raise ValueError(
            f"published JD fact contains unexpected fields: {', '.join(unexpected_fields)}"
        )
    body["validation_lineage"] = ValidationLineageV2.model_validate(
        body.get("validation_lineage")
    )
    body["skill_catalog_snapshot"] = CatalogSnapshotRefV1.model_validate(
        body.get("skill_catalog_snapshot")
    )
    body["position_catalog_snapshot"] = PositionCatalogSnapshotRefV1.model_validate(
        body.get("position_catalog_snapshot")
    )
    return PublishedJDFactV3.model_validate(body)
