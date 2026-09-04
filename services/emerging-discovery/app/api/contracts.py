import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


StrictText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SUPPORTED_ALGORITHMS = frozenset({"emerge_v3_2"})
COMPARISON_ALGORITHMS = frozenset(
    {
        "baseline",
        "fused_agglomerative",
        "density_noise",
        "semantic_agglomerative",
        "semantic_fused_agglomerative",
        "multi_view",
    }
)


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillReference(StrictRequestModel):
    raw_skill: str | None = None
    normalized_skill_id: str | None = None


class JDStructuredDataV2(StrictRequestModel):
    responsibilities: list[str]
    required_skills: list[SkillReference]
    bonus_skills: list[SkillReference]
    business_scenarios: list[str]
    position_title: str | None = None
    industry: str | None = None
    enterprise_id: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    source_platform: str | None = None
    source_record_id: str | None = None
    bundle_id: str | None = None
    date_source: Literal["publish_date", "crawl_date"] | None = None
    evidence_ids: list[StrictText] | None = None


class JDSnapshotV2(StrictRequestModel):
    source_fact_id: StrictText
    source_fact_version: StrictText
    jd_id: StrictText
    schema_version: Literal["v2"]
    review_status: Literal["approved", "reviewed", "published"]
    consumption_path: Literal["legacy_reviewed", "published"] | None = None
    title: StrictText
    source_name: str | None = None
    publish_date: date | None = None
    content_hash: str | None = None
    structured_data: JDStructuredDataV2

    @field_validator("content_hash")
    @classmethod
    def valid_content_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
            raise ValueError("content_hash must be a sha256: digest")
        return normalized



class PositionReference(StrictRequestModel):
    position_id: StrictText
    graph_version_id: StrictText
    required_skills: list[SkillReference] = Field(default_factory=list)


class HistoricalTimeWindow(StrictRequestModel):
    window_id: StrictText
    start: date
    end: date

    @model_validator(mode="after")
    def valid_range(self) -> "HistoricalTimeWindow":
        if self.start > self.end:
            raise ValueError("historical window start must not be after end")
        return self


class DiscoveryRunRequest(StrictRequestModel):
    contract_version: Literal["discovery.v2"]
    request_id: StrictText
    algorithm: Literal["emerge_v3_2"] = "emerge_v3_2"
    time_windows: list[HistoricalTimeWindow] = Field(min_length=3)
    current_observation_window_id: StrictText | None = None
    snapshots: list[JDSnapshotV2] = Field(min_length=1)
    position_references: list[PositionReference] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("algorithm")
    @classmethod
    def supported_algorithm(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported algorithm: {value}")
        return normalized

    @model_validator(mode="after")
    def valid_snapshots_and_window(self) -> "DiscoveryRunRequest":
        ids = [item.jd_id for item in self.snapshots]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot jd_id values must be unique")
        fact_ids = [item.source_fact_id for item in self.snapshots]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("snapshot source_fact_id values must be unique")
        windows = sorted(self.time_windows, key=lambda item: (item.start, item.end))
        if self.time_windows != windows:
            raise ValueError("historical time windows must be supplied in chronological order")
        if len({item.window_id for item in windows}) != len(windows):
            raise ValueError("historical window_id values must be unique")
        observation_window_id = self.current_observation_window_id or windows[-1].window_id
        if observation_window_id not in {item.window_id for item in windows}:
            raise ValueError(
                "current observation window must be declared in historical windows"
            )
        for previous, current in zip(windows, windows[1:], strict=False):
            if current.start != previous.end + timedelta(days=1):
                raise ValueError("historical time windows must be continuous and non-overlapping")
        for snapshot in self.snapshots:
            matches = [
                item
                for item in windows
                if snapshot.publish_date is not None
                and item.start <= snapshot.publish_date <= item.end
            ]
            if len(matches) != 1:
                raise ValueError(
                    "every JD publish_date must belong to exactly one historical window"
                )
        threshold_names = (
            "semantic_candidate_threshold",
            "skill_cooccurrence_threshold",
            "responsibility_similarity_threshold",
            "supporting_view_threshold",
        )
        for name in threshold_names:
            if name in self.config:
                try:
                    threshold = float(self.config[name])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{name} must be numeric") from exc
                if not 0 <= threshold <= 1:
                    raise ValueError(f"{name} must be between zero and one")
        failure_mode = self.config.get("semantic_failure_mode", "mark_unavailable")
        if failure_mode not in {"fail", "mark_unavailable"}:
            raise ValueError("semantic_failure_mode must be fail or mark_unavailable")
        for item in self.snapshots:
            if item.review_status == "reviewed" and item.consumption_path != "legacy_reviewed":
                raise ValueError(
                    "reviewed snapshots require the legacy_reviewed compatibility path"
                )
            if item.review_status == "published" and item.consumption_path != "published":
                raise ValueError("published snapshots require the published path")
        return self


class AlgorithmComparisonRequest(DiscoveryRunRequest):
    comparison_algorithms: list[str] = Field(
        default_factory=lambda: [
            "baseline",
            "fused_agglomerative",
            "density_noise",
        ],
        min_length=1,
    )
    algorithm_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("comparison_algorithms")
    @classmethod
    def supported_comparison_algorithms(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("comparison algorithm names must be unique")
        unknown = sorted(set(normalized) - COMPARISON_ALGORITHMS)
        if unknown:
            raise ValueError(f"unsupported comparison algorithm: {unknown[0]}")
        return normalized

    @model_validator(mode="after")
    def configs_reference_selected_algorithms(self) -> "AlgorithmComparisonRequest":
        unknown = sorted(set(self.algorithm_configs) - set(self.comparison_algorithms))
        if unknown:
            raise ValueError(f"algorithm config does not have a selected algorithm: {unknown[0]}")
        return self


class EmergingTargetAnchorRequest(StrictRequestModel):
    anchor_id: StrictText
    titles: list[StrictText]
    skills: list[StrictText]
    responsibilities: list[StrictText]
    member_jd_ids: list[StrictText]
    member_evidence_ids: list[StrictText] = Field(default_factory=list)
    member_template_cluster_ids: list[StrictText] = Field(default_factory=list)
    semantic_centroid: list[float] = Field(default_factory=list)


class EmergingConclusionRecomputeRequest(DiscoveryRunRequest):
    dataset_id: StrictText
    release_id: StrictText
    subject_ref: StrictText
    algorithm_version: StrictText
    config_hash: Annotated[
        str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    target_anchor: EmergingTargetAnchorRequest | None = None


class OfflineEvaluationRequest(AlgorithmComparisonRequest):
    labels: dict[str, str] = Field(default_factory=dict)
    positive_candidate_jd_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1, le=1000)
    labeling_basis: StrictText = "人工按岗位职责、技能组合和跨窗口身份标注"


class FixedCompetitionEvaluationRequest(StrictRequestModel):
    dataset_version: Literal["discovery-competition-fixed.v1"]


class DiscoveryRunSummary(BaseModel):
    contract_version: Literal["discovery.v2"]
    run_id: str
    request_id: str | None
    status: str
    algorithm_version: str
    formula_version: str
    created_at: datetime
    completed_at: datetime
    clusters: list[dict[str, Any]]
    lineages: list[dict[str, Any]]
    input_quality_report: dict[str, Any]
    run_context: dict[str, Any]
    payload_fingerprint: str = ""


class DiscoveryRunEnvelope(BaseModel):
    code: Literal[0]
    message: Literal["success"]
    data: DiscoveryRunSummary


class AlgorithmComparisonEnvelope(BaseModel):
    code: Literal[0]
    message: Literal["success"]
    data: dict[str, Any]


class CleanupRunRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=10, max_length=2000)


class ResolveAmbiguousIdentityRequest(StrictRequestModel):
    resolution: Literal["confirm_same", "confirm_new"]
    target_candidate_id: StrictText | None = None
    reviewer: StrictText
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: StrictText | None = None
    idempotency_key: StrictText | None = None

    @model_validator(mode="after")
    def valid_target(self) -> "ResolveAmbiguousIdentityRequest":
        if self.resolution == "confirm_same" and not self.target_candidate_id:
            raise ValueError("confirm_same requires target_candidate_id")
        if self.resolution == "confirm_new" and self.target_candidate_id:
            raise ValueError("confirm_new must not provide target_candidate_id")
        return self


def success(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "success", "data": data}
