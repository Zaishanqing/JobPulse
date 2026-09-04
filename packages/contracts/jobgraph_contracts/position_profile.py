from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from jobgraph_contracts.base import StrictContract


class PositionProfileEvidenceSummary(StrictContract):
    evidence_id: int
    document_id: str
    requirement_id: str
    skill_id: str | None = None
    quote: str | None = None
    alignment: str | None = None


class PositionProfileQuality(StrictContract):
    included_samples: int = 0
    excluded_samples: int = 0
    relation_count: int = 0
    evidence_count: int = 0
    unresolved_count: int = 0
    non_exact_evidence_count: int = 0
    publication_gate_passed: bool = True
    skill_retained_by_level: dict[str, int] = Field(default_factory=dict)
    skill_filtered_by_level: dict[str, int] = Field(default_factory=dict)
    responsibility_retained_by_level: dict[str, int] = Field(default_factory=dict)
    responsibility_filtered_by_level: dict[str, int] = Field(default_factory=dict)


class PositionProfileSkillRelation(StrictContract):
    skill_id: str
    skill_name: str
    category_code: str | None = None
    taxonomy_version: str | None = None
    weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    importance_level: str
    modality: str | None = None
    profile_tier: Literal["market_core", "specialty", "observed"]
    support_ratio: float = Field(ge=0, le=1)
    supporting_jd_count: int = Field(ge=0)
    required_supporting_jd_count: int = Field(ge=0)
    required_prevalence: float = Field(ge=0, le=1)
    required_purity: float = Field(ge=0, le=1)
    enterprise_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    requirement_market_status: Literal[
        "market_supported",
        "enterprise_specific",
        "inflation_risk",
        "not_applicable",
    ] = "not_applicable"
    inflation_risk: bool = False
    inflation_reason_codes: list[
        Literal[
            "LOW_MARKET_REQUIRED_PREVALENCE",
            "INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT",
            "LOW_REQUIRED_PURITY",
            "INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT",
        ]
    ] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)


class RequirementInflationMarketEvidence(StrictContract):
    support_ratio: float = Field(ge=0, le=1)
    supporting_jd_count: int = Field(ge=0)
    required_supporting_jd_count: int = Field(ge=0)
    required_prevalence: float = Field(ge=0, le=1)
    required_purity: float = Field(ge=0, le=1)
    enterprise_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    leave_one_out_required_jd_count: int = Field(ge=0)
    leave_one_out_enterprise_count: int = Field(ge=0)
    leave_one_out_source_count: int = Field(ge=0)


class RequirementInflationItem(StrictContract):
    requirement_id: str
    skill_id: str
    skill_name: str
    evidence_id: int
    jd_modality: Literal["required"]
    market_status: Literal[
        "market_supported", "enterprise_specific", "inflation_risk"
    ]
    inflation_risk: bool
    reason_codes: list[
        Literal[
            "LOW_MARKET_REQUIRED_PREVALENCE",
            "INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT",
            "LOW_REQUIRED_PURITY",
            "INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT",
        ]
    ]
    market: RequirementInflationMarketEvidence


class JDRequirementInflationDiagnostic(StrictContract):
    document_id: str
    enterprise_name: str | None = None
    source_name: str | None = None
    required_skill_count: int = Field(ge=0)
    inflation_risk_skill_count: int = Field(ge=0)
    inflation_ratio: float = Field(ge=0, le=1)
    risk_level: Literal["low", "medium", "high"]
    requirements: list[RequirementInflationItem]


class RequirementInflationSummary(StrictContract):
    jd_count: int = Field(ge=0)
    total_required_requirement_count: int = Field(ge=0)
    market_supported_count: int = Field(ge=0)
    enterprise_specific_count: int = Field(ge=0)
    inflation_risk_count: int = Field(ge=0)
    jd_risk_level_counts: dict[str, int] = Field(default_factory=dict)


class PositionProfileRequirementInflation(StrictContract):
    algorithm_version: Literal["requirement-strength-calibration.v1"]
    scope: Literal["required_skills"]
    summary: RequirementInflationSummary
    jd_diagnostics: list[JDRequirementInflationDiagnostic]


class PositionProfileDependencies(StrictContract):
    published_fact_versions: list[str]
    skill_catalog_version: str
    mapping_snapshot_version: str
    normalization_algorithm_version: str
    build_config_version: str
    source_time_window: dict[str, str | int | None]
    position_profile_thresholds: dict[str, Any] = Field(default_factory=dict)


class PositionProfileV1(StrictContract):
    contract_version: Literal["position-profile.v1"]
    position_id: str
    position_name: str
    graph_version: str
    profile_state: Literal["published", "draft", "experimental"]
    taxonomy_version: str
    responsibilities: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    skill_relations: list[PositionProfileSkillRelation]
    evidence_summary: list[PositionProfileEvidenceSummary]
    quality: PositionProfileQuality
    requirement_inflation: PositionProfileRequirementInflation | None = None


class PositionProfileV2(PositionProfileV1):
    contract_version: Literal["position-profile.v2"]
    graph_version_id: int
    published_at: datetime | None
    dependencies: PositionProfileDependencies


class PositionProfileV3(PositionProfileV2):
    contract_version: Literal["position-profile.v3"]
    position_code: str
    classification_status: Literal["resolved", "manually_confirmed"]
    career_level: str | None = None
    leadership_scope: str | None = None
    sample_support_status: Literal["sufficient"]
