"""SQLAlchemy fact loading and persistence mapping for graph publication."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.publish_gate_mapper import publish_gate_result
from app.config import settings
from app.domain.publishing import (
    EXCLUDED_OBJECT_STATUSES,
    PublishGateFacts,
    RelationGateFact,
    ReviewTaskGateFact,
    SupportIntegrityFact,
    evaluate_publish_gate,
)
from app.domain.versioning import (
    ExistingGraphVersion,
    GraphVersionDependencies,
    PublishVersionFacts,
)
from app.models import (
    AuditLog,
    ExtractedCandidateRequirement,
    ExtractionEvidence,
    GraphBuildRun,
    GraphBuildSample,
    GraphVersion,
    JDDocument,
    JDExtractionRecord,
    NormalizedSkillRecord,
    PositionRequirementAggregateDraft,
    PositionSkillRelationDraft,
    PositionSkillSupport,
    PositionTaskAggregateDraft,
    ReviewTask,
    Skill,
    SkillCategory,
    SkillClassification,
    SkillTaxonomyNode,
    StandardPosition,
    UnresolvedNormalizationItem,
)


def build_summary_status(db: Session, run: GraphBuildRun) -> dict:
    """Return the stored build summary with live manual-edit statistics."""
    summary = dict(run.summary or {})
    relation_ids = tuple(
        str(value)
        for value in db.scalars(
            select(PositionSkillRelationDraft.id).where(
                PositionSkillRelationDraft.build_run_id == run.id
            )
        ).all()
    )
    edits = (
        db.scalars(
            select(AuditLog).where(
                AuditLog.object_type == "relation",
                AuditLog.object_id.in_(relation_ids),
                AuditLog.action == "modify_relation",
            )
        ).all()
        if relation_ids
        else []
    )
    changed_fields = {
        field
        for edit in edits
        for field in (edit.after_snapshot or {})
        if field not in {
            "revision", "status", "modification_source", "review_task_id"
        }
    }
    summary["manual_modifications"] = {
        "relations": len({edit.object_id for edit in edits}),
        "fields": len(changed_fields),
        "events": len(edits),
    }
    return summary


def relation_explanation(
    db: Session, relation: PositionSkillRelationDraft
) -> dict:
    supports = db.scalars(
        select(PositionSkillSupport)
        .where(
            PositionSkillSupport.build_run_id == relation.build_run_id,
            PositionSkillSupport.skill_id == relation.skill_id,
        )
        .order_by(PositionSkillSupport.id)
    ).all()
    evidence_items = []
    sources: dict[str, dict] = {}
    for support in supports:
        evidence = db.get(ExtractionEvidence, support.evidence_id)
        document = db.scalar(
            select(JDDocument).where(
                JDDocument.document_id == support.document_id
            )
        )
        evidence_items.append(
            {
                "support_id": support.id,
                "evidence_id": support.evidence_id,
                "document_id": support.document_id,
                "requirement_id": support.requirement_id,
                "modality": support.modality,
                "quote": evidence.quote if evidence else None,
                "alignment": evidence.alignment if evidence else None,
                "start": evidence.start if evidence else None,
                "end": evidence.end if evidence else None,
            }
        )
        if document is not None:
            sources[document.document_id] = {
                "document_id": document.document_id,
                "source_name": document.source_name or document.source_type,
                "enterprise_name": document.enterprise_name,
                "published_at": (
                    document.published_at.isoformat()
                    if document.published_at else None
                ),
                "effective_weight": db.scalar(
                    select(GraphBuildSample.effective_weight).where(
                        GraphBuildSample.build_run_id == relation.build_run_id,
                        GraphBuildSample.document_id == document.document_id,
                    )
                ),
            }
    history = [
        {
            "event_id": event.id,
            "actor_id": event.actor_id,
            "action": event.action,
            "modification_source": (event.after_snapshot or {}).get(
                "modification_source", "relation_edit"
            ),
            "review_task_id": (event.after_snapshot or {}).get("review_task_id"),
            "before": event.before_snapshot,
            "after": event.after_snapshot,
            "reason": event.reason,
            "trace_id": event.trace_id,
            "created_at": event.created_at.isoformat(),
        }
        for event in db.scalars(
            select(AuditLog)
            .where(
                AuditLog.object_type == "relation",
                AuditLog.object_id == str(relation.id),
                AuditLog.action == "modify_relation",
            )
            .order_by(AuditLog.id)
        ).all()
    ]
    base = dict(relation.explanation or {})
    base.update(
        {
            "relation_id": relation.id,
            "position_id": relation.position_id,
            "skill_id": relation.skill_id,
            "sources": list(sources.values()),
            "evidence": evidence_items,
            "statistics": dict(relation.statistics or {}),
            "weight_basis": {
                **dict(base.get("weight_basis") or {}),
                "auto": relation.auto_weight,
                "manual": relation.manual_weight,
                "final": relation.final_weight,
            },
            "confidence_basis": {
                **dict(base.get("confidence_basis") or {}),
                "auto": relation.auto_confidence,
                "manual": relation.manual_confidence,
                "final": relation.final_confidence,
            },
            "importance_basis": {
                "auto": relation.auto_importance_level,
                "manual": relation.manual_importance_level,
                "final": relation.final_importance_level,
            },
            "manual_modification_history": history,
        }
    )
    return base


def _support_integrity_facts(
    db: Session, supports: list[PositionSkillSupport]
) -> tuple[SupportIntegrityFact, ...]:
    """Validate every support chain with batched lookups instead of N+1 reads."""
    if not supports:
        return ()
    skill_ids = {support.skill_id for support in supports}
    normalized_ids = {support.normalized_skill_id for support in supports}
    evidence_ids = {support.evidence_id for support in supports}
    source_ids = {support.source_requirement_id for support in supports}
    extraction_ids = {support.extraction_record_id for support in supports}
    document_ids = {support.document_id for support in supports}
    skills = {
        row.skill_id: row
        for row in db.scalars(
            select(Skill).where(Skill.skill_id.in_(skill_ids))
        ).all()
    }
    normalized = {
        row.id: row
        for row in db.scalars(
            select(NormalizedSkillRecord).where(
                NormalizedSkillRecord.id.in_(normalized_ids)
            )
        ).all()
    }
    evidence = {
        row.id: row
        for row in db.scalars(
            select(ExtractionEvidence).where(
                ExtractionEvidence.id.in_(evidence_ids)
            )
        ).all()
    }
    sources = {
        row.id: row
        for row in db.scalars(
            select(ExtractedCandidateRequirement).where(
                ExtractedCandidateRequirement.id.in_(source_ids)
            )
        ).all()
    }
    extractions = {
        row.id: row
        for row in db.scalars(
            select(JDExtractionRecord).where(
                JDExtractionRecord.id.in_(extraction_ids)
            )
        ).all()
    }
    documents = {
        row.document_id: row
        for row in db.scalars(
            select(JDDocument).where(JDDocument.document_id.in_(document_ids))
        ).all()
    }
    facts = []
    for support in supports:
        skill = skills.get(support.skill_id)
        normalized_row = normalized.get(support.normalized_skill_id)
        evidence_row = evidence.get(support.evidence_id)
        source = sources.get(support.source_requirement_id)
        extraction = extractions.get(support.extraction_record_id)
        document = documents.get(support.document_id)
        source_items = source.payload.get("items", []) if source else []
        facts.append(
            SupportIntegrityFact(
                support_id=support.id,
                support_document_id=support.document_id,
                support_requirement_id=support.requirement_id,
                support_skill_id=support.skill_id,
                skill_status=skill.status if skill else None,
                normalized_exists=normalized_row is not None,
                normalized_status=(
                    normalized_row.resolution_status if normalized_row else None
                ),
                normalized_skill_id=(
                    normalized_row.skill_id if normalized_row else None
                ),
                normalized_source_name=(
                    normalized_row.source_name if normalized_row else None
                ),
                evidence_exists=evidence_row is not None,
                evidence_document_id=(
                    evidence_row.document_id if evidence_row else None
                ),
                evidence_alignment=(
                    evidence_row.alignment if evidence_row else None
                ),
                evidence_start=evidence_row.start if evidence_row else None,
                evidence_end=evidence_row.end if evidence_row else None,
                evidence_quote=evidence_row.quote if evidence_row else None,
                document_exists=document is not None,
                document_raw_text=document.raw_text if document else "",
                document_authority=(
                    document.fact_authority if document else None
                ),
                source_exists=source is not None,
                source_document_id=source.document_id if source else None,
                source_requirement_id=source.requirement_id if source else None,
                source_kind=source.kind if source else None,
                source_item_names=tuple(
                    str(item.get("name", "")) for item in source_items
                ),
                extraction_document_id=(
                    extraction.document_id if extraction else None
                ),
            )
        )
    return tuple(facts)


def _snapshot(
    db: Session,
    run: GraphBuildRun,
    relations: list[PositionSkillRelationDraft],
    *,
    include_explanation: bool = True,
    include_evidence_summary: bool = True,
) -> dict:
    position = db.scalar(
        select(StandardPosition).where(StandardPosition.position_id == run.position_id)
    )
    requirements = db.scalars(
        select(PositionRequirementAggregateDraft).where(
            PositionRequirementAggregateDraft.build_run_id == run.id,
            PositionRequirementAggregateDraft.status.notin_(
                EXCLUDED_OBJECT_STATUSES
            ),
        )
    ).all()
    tasks = db.scalars(
        select(PositionTaskAggregateDraft).where(
            PositionTaskAggregateDraft.build_run_id == run.id,
            PositionTaskAggregateDraft.status.notin_(EXCLUDED_OBJECT_STATUSES),
        )
    ).all()
    supports = (
        db.scalars(
            select(PositionSkillSupport).where(
                PositionSkillSupport.build_run_id == run.id
            )
        ).all()
        if include_evidence_summary
        else []
    )
    skills = []
    relations = [
        item for item in relations if item.status not in EXCLUDED_OBJECT_STATUSES
    ]
    for relation in sorted(relations, key=lambda item: item.skill_id):
        skill = db.scalar(select(Skill).where(Skill.skill_id == relation.skill_id))
        taxonomy_rows = db.execute(
            select(SkillClassification, SkillTaxonomyNode)
            .join(
                SkillTaxonomyNode,
                SkillTaxonomyNode.id == SkillClassification.taxonomy_node_id,
            )
            .where(SkillClassification.skill_id == relation.skill_id)
            .order_by(
                SkillClassification.facet,
                SkillClassification.is_primary.desc(),
                SkillTaxonomyNode.code,
            )
        ).all()
        if skill.taxonomy_version is not None and not taxonomy_rows:
            raise ValueError(
                f"skill {skill.skill_id} taxonomy projection is incomplete"
            )
        classifications = [
            {
                "facet": item.facet,
                "code": node.code,
                "name_zh": node.name_zh,
                "name_en": node.name_en,
                "is_primary": item.is_primary,
            }
            for item, node in taxonomy_rows
        ]
        primary_domain = next(
            (
                item
                for item in classifications
                if item["facet"] == "domain" and item["is_primary"]
            ),
            None,
        )
        category = (
            db.scalar(
                select(SkillCategory).where(
                    SkillCategory.code == skill.category_code
                )
            )
            if skill.category_code
            else None
        )
        skills.append(
            {
                "relation_id": relation.id,
                "skill_id": relation.skill_id,
                "canonical_name": skill.canonical_name,
                "category_code": (
                    primary_domain["code"] if primary_domain else skill.category_code
                ),
                "category_name": (
                    primary_domain["name_zh"]
                    if primary_domain
                    else (category.name if category else None)
                ),
                "subcategory_code": skill.subcategory_code,
                "classifications": classifications,
                "taxonomy_version": skill.taxonomy_version,
                "metrics": relation.metrics,
                "statistics": relation.statistics,
                "explanation": (
                    relation_explanation(db, relation)
                    if include_explanation
                    else None
                ),
                "revision": relation.revision,
                "auto_weight": relation.auto_weight,
                "manual_weight": relation.manual_weight,
                "final_weight": relation.final_weight,
                "weight": relation.final_weight,
                "auto_confidence": relation.auto_confidence,
                "manual_confidence": relation.manual_confidence,
                "final_confidence": relation.final_confidence,
                "confidence": relation.final_confidence,
                "auto_importance_level": relation.auto_importance_level,
                "manual_importance_level": relation.manual_importance_level,
                "final_importance_level": relation.final_importance_level,
                "importance_level": relation.final_importance_level,
                "primary_modality": max(
                    relation.metrics.get("modality_distribution", {"unknown": 1}),
                    key=relation.metrics.get("modality_distribution", {"unknown": 1}).get,
                ),
                "modality_distribution": relation.metrics.get("modality_distribution", {}),
                "trend_score": relation.trend_score,
            }
        )
    reqs = [{"aggregate_id": x.id, "kind": x.kind, **x.payload} for x in requirements]
    algorithm_metadata = {
        key: value
        for key, value in run.config_snapshot.items()
        if key not in {"base_version_id", "draft_source"}
    }
    return {
        "position_id": position.position_id,
        "base_version_id": run.base_version_id,
        "position": {
            "position_id": position.position_id,
            "name": position.name,
            "category_code": position.category_code,
        },
        "time_window": {
            "start": run.window_start.isoformat() if run.window_start else None,
            "end": run.window_end.isoformat() if run.window_end else None,
        },
        "sample_stats": build_summary_status(db, run),
        "skill_relations": skills,
        "requirement_profile": [
            x for x in reqs if x["kind"] not in ("company_fact", "employment_fact")
        ],
        "responsibilities": [{"aggregate_id": x.id, **x.payload} for x in tasks],
        "company_context": [x for x in reqs if x["kind"] == "company_fact"],
        "employment_context": [x for x in reqs if x["kind"] == "employment_fact"],
        "evidence_summary": [
            {
                "support_id": s.id,
                "evidence_id": s.evidence_id,
                "document_id": s.document_id,
                "requirement_id": s.requirement_id,
                "skill_id": s.skill_id,
            }
            for s in supports
        ],
        "algorithm_metadata": algorithm_metadata,
        "normalization_metadata": {"map_version": settings.normalization_map_version},
    }


def _load_run_objects(
    db: Session, run: GraphBuildRun
) -> tuple[
    StandardPosition | None,
    list[PositionSkillRelationDraft],
    list[PositionSkillSupport],
    tuple[SupportIntegrityFact, ...],
    list[ReviewTask],
]:
    position = db.scalar(
        select(StandardPosition).where(StandardPosition.position_id == run.position_id)
    )
    relations = db.scalars(
        select(PositionSkillRelationDraft).where(
            PositionSkillRelationDraft.build_run_id == run.id
        )
    ).all()
    supports = db.scalars(
        select(PositionSkillSupport).where(PositionSkillSupport.build_run_id == run.id)
    ).all()
    support_facts = _support_integrity_facts(db, supports)
    review_tasks = db.scalars(
        select(ReviewTask).where(ReviewTask.build_run_id == run.id)
    ).all()
    return position, list(relations), list(supports), support_facts, list(review_tasks)


def _build_gate_facts(
    db: Session,
    run: GraphBuildRun,
    position: StandardPosition | None,
    relations: list[PositionSkillRelationDraft],
    support_facts: tuple[SupportIntegrityFact, ...],
    review_tasks: list[ReviewTask],
) -> PublishGateFacts:
    sample_document_ids = select(GraphBuildSample.document_id).where(
        GraphBuildSample.build_run_id == run.id,
        GraphBuildSample.included.is_(True),
    )
    unresolved_count = (
        db.scalar(
            select(func.count(UnresolvedNormalizationItem.id)).where(
                UnresolvedNormalizationItem.document_id.in_(sample_document_ids),
                UnresolvedNormalizationItem.status == "open",
            )
        )
        or 0
    )
    non_exact_evidence = (
        db.scalar(
            select(func.count(ExtractionEvidence.id)).where(
                ExtractionEvidence.document_id.in_(sample_document_ids),
                ExtractionEvidence.alignment != "exact",
            )
        )
        or 0
    )
    requirement_aggregate_count = (
        db.scalar(
            select(func.count(PositionRequirementAggregateDraft.id)).where(
                PositionRequirementAggregateDraft.build_run_id == run.id,
                PositionRequirementAggregateDraft.status.notin_(
                    EXCLUDED_OBJECT_STATUSES
                ),
            )
        )
        or 0
    )
    task_aggregate_count = (
        db.scalar(
            select(func.count(PositionTaskAggregateDraft.id)).where(
                PositionTaskAggregateDraft.build_run_id == run.id,
                PositionTaskAggregateDraft.status.notin_(
                    EXCLUDED_OBJECT_STATUSES
                ),
            )
        )
        or 0
    )
    minimum = run.config_snapshot.get("minimum_valid_samples", 1)
    return PublishGateFacts(
        run.status,
        run.summary.get("included_samples", 0),
        minimum,
        position is not None and position.status == "active",
        support_facts,
        tuple(
            RelationGateFact(
                relation.id,
                relation.status,
                relation.final_confidence,
                float(relation.metrics.get("unknown_ratio", 0)),
                invalid_importance_level=relation.final_importance_level
                not in {"core", "important", "supplementary"},
                invalid_modality=(
                    float(relation.metrics.get("unknown_ratio", 0)) == 0
                    and max(
                        relation.metrics.get(
                            "modality_distribution", {"unknown": 1}
                        ),
                        key=relation.metrics.get(
                            "modality_distribution", {"unknown": 1}
                        ).get,
                    )
                    not in {"required", "preferred", "bonus"}
                )
            )
            for relation in relations
        ),
        tuple(
            ReviewTaskGateFact(task.id, task.object_type, task.status)
            for task in review_tasks
        ),
        unresolved_count,
        non_exact_evidence,
        requirement_aggregate_count,
        task_aggregate_count,
    )


def load_publish_gate_facts(db: Session, run: GraphBuildRun) -> PublishGateFacts:
    position, relations, _, support_facts, review_tasks = _load_run_objects(
        db, run
    )
    return _build_gate_facts(
        db, run, position, relations, support_facts, review_tasks
    )


def publish_gate_status(db: Session, run: GraphBuildRun) -> dict:
    result = publish_gate_result(evaluate_publish_gate(load_publish_gate_facts(db, run)))
    published_version = db.scalar(
        select(GraphVersion).where(GraphVersion.build_run_id == run.id)
    )
    if published_version is not None:
        result["already_published"] = True
        result["published_version_id"] = published_version.id
        result["allowed"] = True
        result["hard_gate_allowed"] = True
        result["errors"] = []
        return result
    position = db.scalar(
        select(StandardPosition).where(
            StandardPosition.position_id == run.position_id
        )
    )
    current_version_id = position.current_version_id if position else None
    if run.base_version_id != current_version_id:
        base_version_exists = (
            run.base_version_id is None
            or db.get(GraphVersion, run.base_version_id) is not None
        )
        result["errors"].append(
            {
                "rule": "graph_version_current",
                "message": (
                    "draft base graph version no longer exists"
                    if not base_version_exists
                    else "draft is based on a stale graph version"
                ),
            }
        )
        result["allowed"] = False
        result["hard_gate_allowed"] = False
    return result


def load_publish_version_facts(db: Session, run: GraphBuildRun) -> PublishVersionFacts:
    position, relations, _, support_facts, review_tasks = _load_run_objects(
        db, run
    )
    gate = _build_gate_facts(
        db, run, position, relations, support_facts, review_tasks
    )
    previous = db.scalar(
        select(GraphVersion)
        .where(GraphVersion.position_id == run.position_id)
        .order_by(GraphVersion.version_number.desc())
    )
    existing = db.scalar(select(GraphVersion).where(GraphVersion.build_run_id == run.id))
    versions = db.scalars(
        select(GraphVersion).where(GraphVersion.position_id == run.position_id)
    ).all()
    return PublishVersionFacts(
        run.id,
        run.position_id,
        run.base_version_id,
        position.current_version_id if position else None,
        previous.id if previous else None,
        previous.version_number if previous else None,
        ExistingGraphVersion(existing.id, existing.version_number) if existing else None,
        frozenset(item.version_number for item in versions),
        frozenset(item.version_name for item in versions),
        _snapshot(db, run, relations),
        run.config_snapshot.get("algorithm_version", settings.algorithm_version),
        GraphVersionDependencies(
            tuple(run.config_snapshot.get("published_fact_versions", ())),
            run.config_snapshot.get("skill_catalog_version", "absent"),
            run.config_snapshot.get("mapping_snapshot_version", "absent"),
            run.config_snapshot.get(
                "normalization_algorithm_version",
                settings.normalization_algorithm_version,
            ),
            run.config_snapshot.get("build_config_version", "legacy-unspecified"),
            {
                "start": run.window_start.isoformat() if run.window_start else None,
                "end": run.window_end.isoformat() if run.window_end else None,
            },
        ),
        gate,
    )
