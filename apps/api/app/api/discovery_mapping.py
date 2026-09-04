from typing import Any

from app.api.task_mapping import task_data
from app.domain.values import thaw
from app.contexts.discovery import (
    CandidateObservation,
    CandidateTrajectory,
    ClusterJDRecord,
    ClusterProjection,
    DiscoveryCandidate,
    DiscoveryCandidateDetail,
    RecentPositionSignal,
)
from app.contexts.tasks import TaskRecord


def cluster_data(cluster: ClusterProjection) -> dict[str, Any]:
    assessment = thaw(cluster.discovery_assessment)
    return {
        "cluster_id": cluster.cluster_id,
        "cluster_name": cluster.cluster_name,
        "algorithm": cluster.algorithm_version,
        "time_window_start": cluster.time_window_start.isoformat() if cluster.time_window_start else None,
        "time_window_end": cluster.time_window_end.isoformat() if cluster.time_window_end else None,
        "sample_count": cluster.sample_count,
        "core_skills": thaw(cluster.core_skills),
        "representative_titles": list(cluster.representative_titles),
        "representative_jd_ids": list(cluster.representative_jd_ids),
        "stability_score": cluster.stability_score,
        "growth_score": cluster.growth_score,
        "distance_from_existing_positions": cluster.distance_from_existing_positions,
        "discovery_run_id": cluster.discovery_run_id,
        "evolution_relations": thaw(cluster.discovery_lineages),
        "emergence_assessment": assessment,
        "generated_definition": thaw(cluster.generated_definition),
        "input_quality_report": assessment.get("input_quality_report", {}),
        "run_context": assessment.get("run_context", {}),
        "standard_position_comparison": assessment.get("stage1", {}),
        "explainability": assessment.get("temporal_layers", {}),
        "lineage_relations": assessment.get("lineage_relations", []),
        "request_id": assessment.get("request_id"),
        "input_fingerprint": assessment.get("input_fingerprint"),
        "status": cluster.status,
        "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
        "updated_at": cluster.updated_at.isoformat() if cluster.updated_at else None,
        "concept_note": "emerging_position 来自招聘市场 JD 稳定簇；predicted_position 来自政策、论文、报告等趋势信号，本批次不实现。",
    }


def cluster_jd_data(row: ClusterJDRecord) -> dict[str, Any]:
    return {
        "jd_id": row.jd_id,
        "source_type": row.source_type,
        "source_name": row.source_name,
        "enterprise_id": row.enterprise_id,
        "title": row.title,
        "raw_text": row.raw_text,
        "publish_date": row.publish_date.isoformat() if row.publish_date else None,
        "url": row.url,
        "file_id": row.file_id,
        "parse_status": row.parse_status,
        "input_extraction_status": row.input_extraction_status,
        "input_provider": row.input_provider,
        "input_error_code": row.input_error_code,
        "input_error_message": row.input_error_message,
        "implementation_status": (
            "adapter_extracted_input"
            if row.input_extraction_status == "completed"
            else "adapter_extraction_failed"
            if row.input_extraction_status == "failed"
            else "direct_text_input"
        ),
        "copy_risk_score": row.copy_risk_score,
        "inflation_score": row.inflation_score,
        "is_downweighted": row.is_downweighted,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def observation_data(observation: CandidateObservation) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "candidate_id": observation.candidate_id,
        "run_id": observation.run_id,
        "cluster_id": observation.cluster_id,
        "cluster_name": observation.cluster_name,
        "window_id": observation.window_id,
        "title": observation.title,
        "status": observation.status,
        "emergence_score": observation.emergence_score,
        "support_count": observation.support_count,
        "company_count": observation.company_count,
        "identity_similarity": observation.identity_similarity,
        "skill_similarity": observation.skill_similarity,
        "responsibility_similarity": observation.responsibility_similarity,
        "title_similarity": observation.title_similarity,
        "membership_overlap": observation.membership_overlap,
        "semantic_similarity": observation.semantic_similarity,
        "evidence": thaw(observation.evidence),
        "match_evidence": thaw(observation.match_evidence),
        "created_at": observation.created_at,
    }


def candidate_data(candidate: DiscoveryCandidate) -> dict[str, Any]:
    # 高维 semantic_centroid 仅用于上游身份匹配，浏览器端不需要，不随列表/详情下发。
    identity_profile = thaw(candidate.identity_profile)
    identity_profile.pop("semantic_centroid", None)
    return {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "first_seen_window_id": candidate.first_seen_window_id,
        "last_seen_window_id": candidate.last_seen_window_id,
        "age": candidate.age,
        "current_cluster_id": candidate.current_cluster_id,
        "previous_cluster_ids": list(candidate.previous_cluster_ids),
        "canonical_title": candidate.canonical_title,
        "display_title": candidate.display_title,
        "definition": thaw(candidate.definition),
        "identity_profile": identity_profile,
        "evidence": thaw(candidate.evidence),
        "support_count": candidate.support_count,
        "company_coverage": candidate.company_coverage,
        "skill_similarity": candidate.skill_similarity,
        "responsibility_similarity": candidate.responsibility_similarity,
        "title_similarity": candidate.title_similarity,
        "membership_overlap": candidate.membership_overlap,
        "identity_similarity": candidate.identity_similarity,
        "novelty_score": candidate.novelty_score,
        "emergence_score": candidate.emergence_score,
        "identity_stability": candidate.identity_stability,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
    }


def recent_signal_data(signal: RecentPositionSignal) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "position_name": signal.position_name,
        "representative_title": signal.representative_title,
        "skills": list(signal.skills),
        "observed_at": signal.observed_at.isoformat() if signal.observed_at else None,
        "source_jd_ids": list(signal.source_jd_ids),
        "source_count": signal.source_count,
        "projection_version": signal.projection_version,
    }


def candidate_detail_data(detail: DiscoveryCandidateDetail) -> dict[str, Any]:
    return {
        "candidate": candidate_data(detail.candidate),
        "latest_observation": (
            observation_data(detail.latest_observation)
            if detail.latest_observation is not None
            else None
        ),
    }


def trajectory_data(trajectory: CandidateTrajectory) -> dict[str, Any]:
    return {
        "candidate_id": trajectory.candidate_id,
        "trajectory": [observation_data(item) for item in trajectory.trajectory],
    }


def discovery_data(value: Any) -> Any:
    if isinstance(value, TaskRecord):
        return task_data(value)
    if isinstance(value, ClusterProjection):
        return cluster_data(value)
    if isinstance(value, ClusterJDRecord):
        return cluster_jd_data(value)
    if isinstance(value, DiscoveryCandidate):
        return candidate_data(value)
    if isinstance(value, DiscoveryCandidateDetail):
        return candidate_detail_data(value)
    if isinstance(value, CandidateTrajectory):
        return trajectory_data(value)
    if isinstance(value, CandidateObservation):
        return observation_data(value)
    if isinstance(value, (list, tuple)):
        return [discovery_data(item) for item in value]
    return value
