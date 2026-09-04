from dataclasses import asdict
from datetime import date
from typing import Any

from app.application.contracts import (
    AlgorithmComparisonResult,
    DiscoveryConfig,
    HistoricalTimeWindow,
    DiscoveryResult,
    DiscoveryTimeWindow,
    RunDiscoveryCommand,
)
from app.application.discovery_mapping import algorithm_metadata_contract
from app.application.lifecycle_survival import LifecycleSurvivalResult
from app.application.promotion_distance import PromotionDistanceCertificate
from app.domain.discovery import (
    JDSnapshot,
    JDStructuredData,
    PositionReference,
    SkillReference,
)
from app.domain.values import FrozenDict, freeze, thaw


def lifecycle_survival_data(item: LifecycleSurvivalResult) -> dict[str, object]:
    return {
        "candidate_id": item.candidate_id,
        "start_window": item.start_window,
        "event_window": item.event_window,
        "duration": item.duration,
        "event_type": item.event_type,
        "censored": item.censored,
        "last_observed_window": item.last_observed_window,
        "observation_end_window": item.observation_end_window,
        "start_run_id": item.start_run_id,
        "event_run_id": item.event_run_id,
        "observation_end_run_id": item.observation_end_run_id,
        "start_request_id": item.start_request_id,
        "algorithm_version": item.algorithm_version,
        "formula_version": item.formula_version,
    }


def promotion_distance_data(item: PromotionDistanceCertificate) -> dict[str, object]:
    condition_data = {
        condition.name: {
            "required": condition.required,
            "current": condition.current,
            "missing": condition.missing,
            "satisfied": condition.satisfied,
        }
        for condition in item.conditions
    }
    return {
        "candidate_id": item.candidate_id,
        "current_state": item.current_state,
        "target_state": item.target_state,
        "outcome": item.outcome,
        "eligible_state": item.eligible_state,
        "gate_satisfied": item.gate_satisfied,
        "windows": condition_data["windows"],
        "support": condition_data["support"],
        "company_coverage": condition_data["companies"],
        "emergence": condition_data["emergence"],
        "identity_stability": condition_data["identity_stability"],
        "conditions": condition_data,
        "missing_conditions": list(item.missing_conditions),
        "gate_identity": {
            "lifecycle_version": item.lifecycle_version,
            "config_snapshot_id": item.config_snapshot_id,
            "run_id": item.config_run_id,
            "request_id": item.config_request_id,
            "algorithm_version": item.algorithm_version,
            "formula_version": item.formula_version,
        },
    }


def _generated_definition_data(value: Any) -> dict[str, Any]:
    return {
        "position_name": value.position_name,
        "core_responsibilities": list(value.core_responsibilities),
        "required_skills": [asdict(item) for item in value.required_skills],
        "bonus_skills": [asdict(item) for item in value.bonus_skills],
        "industry_scenarios": list(value.industry_scenarios),
        "generation_mode": value.generation_mode,
        "field_evidence": thaw(value.field_evidence),
        "position_summary": value.position_summary,
        "distinguishing_features": list(value.distinguishing_features),
        "representative_enterprises": thaw(value.representative_enterprises),
        "growth_trajectory": [thaw(item) for item in value.growth_trajectory],
    }


def _skill(value: dict[str, Any]) -> SkillReference:
    return SkillReference(
        raw_skill=str(value["raw_skill"]) if value.get("raw_skill") else None,
        normalized_skill_id=(
            str(value["normalized_skill_id"]) if value.get("normalized_skill_id") else None
        ),
    )


def structured_data_from_api(value: dict[str, Any]) -> JDStructuredData:
    known = {
        "responsibilities",
        "required_skills",
        "bonus_skills",
        "business_scenarios",
        "position_title",
        "industry",
    }
    extensions = freeze(
        {key: item for key, item in value.items() if key not in known and item is not None}
    )
    if not isinstance(extensions, FrozenDict):
        raise TypeError("structured-data extensions must be a JSON object")
    return JDStructuredData(
        responsibilities=tuple(str(item) for item in value.get("responsibilities", ())),
        required_skills=tuple(_skill(item) for item in value.get("required_skills", ())),
        bonus_skills=tuple(_skill(item) for item in value.get("bonus_skills", ())),
        business_scenarios=tuple(str(item) for item in value.get("business_scenarios", ())),
        position_title=str(value["position_title"]) if value.get("position_title") else None,
        industry=str(value["industry"]) if value.get("industry") else None,
        extensions=extensions,
    )


def discovery_command_from_api(
    *,
    contract_version: str,
    request_id: str,
    algorithm: str,
    snapshots: list[dict[str, Any]],
    position_references: list[dict[str, Any]],
    config: dict[str, Any],
    time_windows: list[dict[str, Any]],
    current_observation_window_id: str | None = None,
) -> RunDiscoveryCommand:
    frozen_config = freeze(config)
    if not isinstance(frozen_config, FrozenDict):
        raise TypeError("discovery config must be a JSON object")
    windows = tuple(
        HistoricalTimeWindow(
            window_id=str(item["window_id"]),
            start=date.fromisoformat(str(item["start"])),
            end=date.fromisoformat(str(item["end"])),
        )
        for item in sorted(time_windows, key=lambda value: str(value["start"]))
    )

    def window_id_for(item: dict[str, Any]) -> str:
        published = date.fromisoformat(str(item["publish_date"]))
        return next(
            window.window_id for window in windows if window.start <= published <= window.end
        )

    return RunDiscoveryCommand(
        contract_version=contract_version,
        request_id=request_id,
        algorithm=algorithm,
        snapshots=tuple(
            JDSnapshot(
                source_fact_id=str(item["source_fact_id"]),
                source_fact_version=str(item["source_fact_version"]),
                window_id=window_id_for(item),
                jd_id=str(item["jd_id"]),
                schema_version=str(item["schema_version"]),
                review_status=str(item["review_status"]),
                title=str(item["title"]),
                source_name=str(item["source_name"]) if item.get("source_name") else None,
                publish_date=(
                    date.fromisoformat(str(item["publish_date"]))
                    if item.get("publish_date")
                    else None
                ),
                structured_data=structured_data_from_api(item["structured_data"]),
                consumption_path=(
                    str(item["consumption_path"]) if item.get("consumption_path") else None
                ),
                content_hash=(
                    str(item["content_hash"]) if item.get("content_hash") else None
                ),
                source_record_id=(
                    str(item["structured_data"]["source_record_id"])
                    if item["structured_data"].get("source_record_id")
                    else None
                ),
                bundle_id=(
                    str(item["structured_data"]["bundle_id"])
                    if item["structured_data"].get("bundle_id")
                    else None
                ),
                date_source=(
                    str(item["structured_data"]["date_source"])
                    if item["structured_data"].get("date_source")
                    else None
                ),
            )
            for item in snapshots
        ),
        position_references=tuple(
            PositionReference(
                position_id=str(item["position_id"]),
                required_skills=tuple(_skill(skill) for skill in item["required_skills"]),
                graph_version_id=str(item["graph_version_id"]),
            )
            for item in position_references
        ),
        config=DiscoveryConfig(frozen_config),
        time_window=DiscoveryTimeWindow(
            windows[0].start,
            windows[-1].end,
            windows,
            current_observation_window_id or windows[-1].window_id,
        ),
    )


def discovery_result_data(result: DiscoveryResult) -> dict[str, Any]:
    def lineage_data(item) -> dict[str, Any]:
        return {
            "relation_type": item.relation.relation_type,
            "predecessor_cluster_id": item.relation.predecessor_cluster_id,
            "successor_cluster_id": item.relation.successor_cluster_id,
            "similarity_score": item.relation.similarity_score,
            "evidence": {
                "evidence_cluster_ids": list(item.relation.evidence_cluster_ids),
                "score_components": (asdict(item.relation.score) if item.relation.score else {}),
                "threshold": item.relation.threshold,
                "decision_reason": item.relation.decision_reason,
                "predecessor_window_id": item.relation.predecessor_window_id,
                "successor_window_id": item.relation.successor_window_id,
            },
            "decision_version": item.relation.decision_version,
        }

    return {
        "contract_version": result.contract_version,
        "run_id": result.run_id,
        "request_id": result.request_id,
        "status": result.status,
        "algorithm_version": result.algorithm_version,
        "formula_version": result.formula_version,
        "created_at": result.created_at,
        "completed_at": result.completed_at,
        "payload_fingerprint": result.payload_fingerprint,
        "clusters": [
            {
                "cluster_id": item.cluster_id,
                "cluster_name": item.cluster_name,
                "sample_count": item.sample_count,
                "core_skills": [asdict(skill) for skill in item.core_skills],
                "representative_titles": list(item.representative_titles),
                "representative_jd_ids": list(item.representative_jd_ids),
                "representative_members": [thaw(value) for value in item.representative_members],
                "core_responsibilities": list(item.core_responsibilities),
                "semantic_centroid": list(item.semantic_centroid),
                "algorithm_sources": list(item.algorithm_sources),
                "merge_basis": thaw(item.merge_basis),
                "stability_score": item.stability_score,
                "growth_score": item.growth_score,
                "distance_from_existing_positions": item.distance_from_existing_positions,
                "feature_summary": {
                    **thaw(algorithm_metadata_contract(item.feature_summary.metadata)),
                    "centroid": list(item.feature_summary.centroid),
                },
                "emergence_assessment": thaw(
                    item.germination_assessment.evidence_package.get(
                        "emergence_v3_2", FrozenDict()
                    )
                ),
                "germination_assessment": {
                    "germination_score": item.germination_assessment.germination_score,
                    "score_dimensions": thaw(
                        item.germination_assessment.score_dimensions
                    ),
                    "level": item.germination_assessment.level,
                    "qualified_as_emerging": item.germination_assessment.qualified_as_emerging,
                    "decision_reason": item.germination_assessment.decision_reason,
                    "evidence_package": thaw(item.germination_assessment.evidence_package),
                },
                "generated_definition": _generated_definition_data(item.generated_definition),
                "explainability": thaw(
                    item.germination_assessment.evidence_package.get(
                        "cluster_explainability", FrozenDict()
                    )
                ),
                "standard_position_comparison": thaw(
                    item.germination_assessment.evidence_package.get(
                        "standard_position_comparison", FrozenDict()
                    )
                ),
                "lineage_relations": [
                    lineage_data(lineage)
                    for lineage in result.lineages
                    if lineage.relation.successor_cluster_id == item.cluster_id
                ],
            }
            for item in result.clusters
        ],
        "lineages": [lineage_data(item) for item in result.lineages],
        "input_quality_report": thaw(result.input_quality_report),
        "run_context": thaw(result.run_context),
    }


def algorithm_comparison_data(result: AlgorithmComparisonResult) -> dict[str, Any]:
    return {
        "contract_version": result.contract_version,
        "request_id": result.request_id,
        "input_quality_report": thaw(result.input_quality_report),
        "algorithms": [
            {
                "algorithm": item.algorithm,
                "feature_name": item.feature_name,
                "clustering_name": item.clustering_name,
                "parameters": thaw(item.parameters),
                "cluster_count": item.cluster_count,
                "noise_ratio": item.noise_ratio,
                "silhouette_coefficient": item.silhouette_coefficient,
                "intra_cluster_similarity": item.intra_cluster_similarity,
                "inter_cluster_difference": item.inter_cluster_difference,
                "runtime_ms": item.runtime_ms,
                "clusters": [asdict(cluster) for cluster in item.clusters],
                "noise_points": [thaw(point) for point in item.noise_points],
                "enterprise_debias": thaw(item.enterprise_debias),
                "stability_analysis": thaw(item.stability_analysis),
                "parameter_sensitivity": [thaw(value) for value in item.parameter_sensitivity],
                "recommendation_score": item.recommendation_score,
            }
            for item in result.algorithms
        ],
        "recommended_algorithm": result.recommended_algorithm,
        "recommendation_reason": result.recommendation_reason,
    }
