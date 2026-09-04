from __future__ import annotations

from app.contexts.matching_learning.matching_service import (
    MatchingServiceReferenceRecord,
    RemoteEvaluation,
)
from app.api.matching_bff_mapping import (
    algorithm_versions_response,
    canonical_status,
    data_versions_response,
    matching_method_from_evaluation,
    enrich_report,
    match_versions_response,
    report_result_status,
)


def evaluation_report_data(
    item: RemoteEvaluation, *, position_name: str | None = None
) -> dict[str, object]:
    enriched = enrich_report(item)
    return {
        "evaluation_id": item.evaluation_id,
        "task_id": item.task_id,
        "status": "stale" if item.stale else "current",
        "result_status": report_result_status(
            enriched["evaluation"],
            enriched["gap_analysis"],
        ),
        "matching_method": (
            item.matching_method
            if item.matching_method in {"rule", "semantic_verified"}
            else matching_method_from_evaluation(enriched["evaluation"])
        ),
        "degraded": item.degraded,
        "stale": item.stale,
        "stale_reason_codes": list(item.stale_reason_codes),
        "evaluation": enriched["evaluation"],
        "gap_analysis": enriched["gap_analysis"],
        "versions": match_versions_response(item.versions) or {},
        "lineage": {
            "resume_id": item.resume_id,
            "position_id": item.position_id,
            "position_name": position_name,
            "validated_cv_snapshot_id": item.validated_cv_snapshot_id,
            "target_type": item.target_type,
            "provider": item.provider,
            "method": item.method,
            "algorithm_versions": algorithm_versions_response(item.algorithm_versions),
            "data_versions": data_versions_response(item.data_versions),
        },
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def evaluation_reference_data(
    item: MatchingServiceReferenceRecord,
) -> dict[str, object]:
    return {
        "evaluation_id": item.evaluation_id,
        "task_id": item.task_id,
        "resume_id": item.resume_id,
        "position_id": item.position_id,
        "target_type": item.target_type,
        "status": canonical_status(item.status),
        "provider": item.provider,
        "matching_method": item.matching_method,
        "degraded": item.degraded,
        "overall_score": item.overall_score,
        "origin": (
            "auto_ranking"
            if item.idempotency_key.startswith(
                ("ranking-v1:", "ranking-v2:", "ranking-v3:")
            )
            else "manual"
        ),
        "error_code": item.error_code,
        "error_message": item.error_message,
        "lineage": {
            "algorithm_version": item.algorithm_version,
            "source_version": item.source_version,
            "taxonomy_version": item.taxonomy_version,
            "graph_version": item.graph_version,
            "cv_profile_version": item.cv_profile_version,
            "position_profile_version": item.position_profile_version,
        },
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
