from app.contexts.catalog import (
    CatalogDraftPreview,
    DownstreamSkillProjection,
    MergePreview,
    NormalizationCandidateRecord,
    NormalizationResult,
    NormalizationSuggestion,
    RenormalizationSummary,
    SkillAliasRecord,
    SkillClassificationRecord,
    SkillCatalogVersionRecord,
    SkillRecord,
    SkillTaxonomyNodeRecord,
)
from app.domain.json_types import thaw_json_object


def skill_data(item: SkillRecord) -> dict[str, object]:
    return {
        "skill_id": item.skill_id, "skill_name": item.skill_name,
        "catalog_code": item.catalog_code,
        "category": item.category, "description": item.description,
        "parent_skill_id": item.parent_skill_id,
        "status": item.status,
        "redirect_target_skill_id": item.redirect_target_skill_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def alias_data(item: SkillAliasRecord) -> dict[str, object]:
    return {"alias_id": item.alias_id, "skill_id": item.skill_id, "alias": item.alias}


def taxonomy_node_data(item: SkillTaxonomyNodeRecord) -> dict[str, object]:
    return {
        "node_id": item.node_id,
        "facet": item.facet,
        "code": item.code,
        "name_zh": item.name_zh,
        "name_en": item.name_en,
        "parent_id": item.parent_id,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def classification_data(
    item: SkillClassificationRecord,
) -> dict[str, object]:
    return {
        "classification_id": item.classification_id,
        "skill_id": item.skill_id,
        "taxonomy_node_id": item.taxonomy_node_id,
        "facet": item.facet,
        "code": item.code,
        "name_zh": item.name_zh,
        "name_en": item.name_en,
        "is_primary": item.is_primary,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def normalization_data(item: NormalizationResult) -> dict[str, object]:
    return {
        "raw_skill": item.raw_skill,
        "candidates": [
            {"skill_id": row.skill_id, "skill_name": row.skill_name,
             "category": row.category, "confidence": row.confidence,
             "redirected_from_skill_id": row.redirected_from_skill_id,
             "redirected_from_skill_name": row.redirected_from_skill_name}
            for row in item.candidates
        ],
        "need_review": item.need_review,
        "candidate_id": item.candidate_id,
    }


def normalization_suggestion_data(
    item: NormalizationSuggestion,
) -> dict[str, object]:
    return {
        "skill_id": item.skill_id,
        "skill_name": item.skill_name,
        "category": item.category,
        "rank": item.rank,
        "lexical_score": item.lexical_score,
        "semantic_score": item.semantic_score,
        "combined_score": item.combined_score,
        "matched_alias": item.matched_alias,
        "reasons": list(item.reasons),
        "semantic_available": item.semantic_available,
    }


def merge_preview_data(item: MergePreview) -> dict[str, object]:
    def summary_data(summary) -> dict[str, object]:
        return {
            "skill": skill_data(summary.skill),
            "alias_count": summary.alias_count,
            "classifications": [
                classification_data(row) for row in summary.classifications
            ],
            "related_candidate_count": summary.related_candidate_count,
        }

    return {
        "source": summary_data(item.source),
        "target": summary_data(item.target),
        "impact_by_source": item.impact_by_source,
        "classification_conflicts": list(item.classification_conflicts),
    }


def catalog_draft_preview_data(item: CatalogDraftPreview) -> dict[str, object]:
    return {
        "based_on_catalog_version": item.based_on_catalog_version,
        "change_summary": thaw_json_object(item.change_summary),
        "validation_issues": list(item.validation_issues),
        "publishable": not item.validation_issues,
    }


def catalog_version_data(
    item: SkillCatalogVersionRecord,
) -> dict[str, object]:
    return {
        "version_id": item.version_id,
        "version_number": item.version_number,
        "catalog_version": item.catalog_version,
        "snapshot": thaw_json_object(item.snapshot),
        "change_summary": thaw_json_object(item.change_summary),
        "published_by": item.published_by,
        "published_at": (
            item.published_at.isoformat() if item.published_at else None
        ),
    }


def candidate_data(item: NormalizationCandidateRecord) -> dict[str, object]:
    normalization_state = {
        "pending": "unresolved",
        "mapped_existing": "resolved",
        "created_new": "resolved",
        "excluded_non_skill": "excluded_non_skill",
        "deferred": "deferred",
    }[item.status]
    return {
        "candidate_id": item.candidate_id, "raw_skill": item.raw_skill,
        "normalized_skill": item.normalized_skill,
        "candidate_skill_id": item.candidate_skill_id,
        "candidate_skill_name": item.candidate_skill_name,
        "confidence": item.confidence, "context": item.context,
        "occurrence_count": item.occurrence_count,
        "source_type": item.source_type,
        "representative_evidence": item.context,
        "evidence_samples": list(item.evidence_samples),
        "status": item.status,
        "normalization_state": normalization_state,
        "catalog_version": item.normalization_catalog_version,
        "normalized_at": (
            item.normalized_at.isoformat() if item.normalized_at else None
        ),
        "first_seen_at": (
            item.first_seen_at.isoformat() if item.first_seen_at else None
        ),
        "last_seen_at": (
            item.last_seen_at.isoformat() if item.last_seen_at else None
        ),
        "reviewer_id": item.reviewer_id,
        "reviewed_at": (
            item.reviewed_at.isoformat() if item.reviewed_at else None
        ),
        "decision_reason": item.decision_reason,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def renormalization_summary_data(
    item: RenormalizationSummary,
) -> dict[str, object]:
    return {
        "catalog_version": item.catalog_version,
        "resolved_candidate_count": item.resolved_candidate_count,
        "unresolved_candidate_count": item.unresolved_candidate_count,
        "excluded_non_skill_count": item.excluded_non_skill_count,
        "affected_jd_count": item.affected_jd_count,
        "affected_cv_count": item.affected_cv_count,
    }


def downstream_skill_projection_data(
    item: DownstreamSkillProjection,
) -> dict[str, object]:
    return {
        "catalog_version": item.catalog_version,
        "resolved_skill_ids": list(item.resolved_skill_ids),
        "unresolved_candidates": [
            candidate_data(candidate) for candidate in item.unresolved_candidates
        ],
    }
