"""SQLAlchemy mapping for graph-build facts and plans."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.errors import NotFoundError
from app.application.mappers import GraphSnapshotCompatibilityMapper
from app.config import settings
from app.domain.graph_building import (
    BuildDocumentFacts,
    BuildIssueFact,
    EvidenceFact,
    GraphBuildFacts,
    GraphBuildPlan,
    ManualRelationOverride,
    PersistedBuildObject,
    PersistedBuildRun,
    PreviousRelationWeight,
    RelationFormula,
    SkillOccurrenceFact,
    TextAggregateFact,
)
from app.domain.policies import merged_relation_config, normalize_key
from app.domain.profile_thresholds import (
    DEFAULT_POSITION_PROFILE_THRESHOLDS,
    PositionProfileThresholdConfig,
    build_config_version,
)
from app.infrastructure.sqlalchemy.fact_mappers import latest_record, load_structured_extraction
from app.models import (
    AlgorithmConfig,
    DuplicateCluster,
    EffectiveMappingRecord,
    ExtractedCandidateRequirement,
    ExtractionEvidence,
    GraphBuildRun,
    GraphBuildSample,
    GraphVersion,
    JDDocument,
    JDExtractionRecord,
    JDNormalizedRecord,
    JDQualityAssessment,
    NormalizedJobClassification,
    NormalizedRequirementRecord,
    NormalizedSkillRecord,
    PositionRequirementAggregateDraft,
    PositionSkillRelationDraft,
    PositionSkillSupport,
    PositionTaskAggregateDraft,
    PublishedFactImport,
    PublishedFactReleaseLink,
    Skill,
    StandardPosition,
)

RELATION_STATISTIC_FIELDS = (
    "supporting_jd_count",
    "deduplicated_jd_count",
    "enterprise_count",
    "source_count",
    "evidence_count",
    "first_seen_at",
    "last_seen_at",
    "raw_frequency",
    "quality_adjusted_frequency",
)


class SqlAlchemyGraphBuildRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_facts(self, position_id: str, *, authoritative_only: bool) -> GraphBuildFacts:
        position = self.session.scalar(
            select(StandardPosition).where(StandardPosition.position_id == position_id)
        )
        if position is None:
            raise NotFoundError("position not found")
        stored = self.session.scalar(
            select(AlgorithmConfig).where(AlgorithmConfig.active.is_(True))
            .order_by(AlgorithmConfig.id.desc())
        )
        config = merged_relation_config(stored.payload if stored else None)
        previous = self.session.get(GraphVersion, position.current_version_id) if position.current_version_id else None
        previous_snapshot = GraphSnapshotCompatibilityMapper.to_current(previous.snapshot) if previous else {}
        previous_relations = tuple(
            PreviousRelationWeight(str(item["skill_id"]), float(item.get("weight", 0)))
            for item in previous_snapshot.get("skill_relations", [])
        )
        previous_samples = int(previous_snapshot.get("sample_stats", {}).get("included_samples", 0))
        overrides = self.session.scalars(
            select(PositionSkillRelationDraft)
            .where(
                PositionSkillRelationDraft.position_id == position_id,
                PositionSkillRelationDraft.manual_importance_level.is_not(None),
            )
            .order_by(PositionSkillRelationDraft.id.desc())
        ).all()
        unique_overrides = {}
        for item in overrides:
            unique_overrides.setdefault(item.skill_id, ManualRelationOverride(
                item.skill_id, item.manual_weight, item.manual_confidence,
                item.manual_importance_level,
            ))
        query = select(JDDocument).where(
            JDDocument.fact_authority == ("authoritative" if authoritative_only else "legacy_local")
        )
        if authoritative_only:
            query = query.where(JDDocument.source_system == "main-system")
        cluster_keys = {
            document_id: cluster.cluster_key
            for cluster in self.session.scalars(select(DuplicateCluster)).all()
            for document_id in cluster.document_ids
        }
        documents = tuple(
            self._document_facts(item, cluster_keys.get(item.document_id))
            for item in self.session.scalars(query).all()
        )
        document_deduplication = {
            item.document_id: cluster_keys.get(
                item.document_id, f"document:{item.document_id}"
            )
            for item in documents
        }
        weight = config["weight_coefficients"]
        modality = config["modality_coefficients"]
        confidence = config["confidence_coefficients"]
        normalization = config["normalization"]
        profile_thresholds = PositionProfileThresholdConfig.from_serialized(
            config.get("position_profile_thresholds")
            or DEFAULT_POSITION_PROFILE_THRESHOLDS.serialized()
        )
        return GraphBuildFacts(
            position_id, position.current_version_id,
            stored.version if stored else settings.algorithm_version,
            RelationFormula(
                float(weight["weighted_frequency"]),
                float(weight.get("support_ratio", 0)),
                float(weight["modality_strength"]),
                float(weight["source_diversity"]),
                float(weight["enterprise_coverage"]),
                float(weight["freshness_score"]),
                float(weight["trusted_evidence_ratio"]),
                float(modality["required_ratio"]),
                float(modality["preferred_ratio"]),
                float(modality["bonus_ratio"]),
                float(modality["unknown_ratio"]),
                float(confidence["weighted_frequency"]),
                float(confidence["support_sufficiency"]),
                float(confidence["trusted_evidence_ratio"]),
                float(confidence["source_diversity"]),
                float(normalization["source_diversity_cap"]),
                float(normalization["enterprise_coverage_cap"]),
                float(normalization["support_document_cap"]),
                float(config["freshness_decay_days"]),
                float(config["trusted_source_threshold"]),
            ),
            profile_thresholds,
            document_deduplication,
            previous_samples, previous_relations, documents,
            tuple(unique_overrides.values()),
        )

    def _document_facts(
        self, document: JDDocument, duplicate_cluster_key: str | None
    ) -> BuildDocumentFacts:
        extraction = latest_record(self.session, JDExtractionRecord, document.document_id)
        normalized = latest_record(self.session, JDNormalizedRecord, document.document_id)
        quality = latest_record(self.session, JDQualityAssessment, document.document_id)
        evidence_rows = self.session.scalars(
            select(ExtractionEvidence).where(ExtractionEvidence.document_id == document.document_id)
        ).all()
        evidence = tuple(EvidenceFact(
            item.id, item.owner_type, item.owner_ref, item.quote, item.alignment,
            item.start, item.end,
        ) for item in evidence_rows)
        evidence_by_ref = {item.owner_ref: item for item in evidence_rows}
        issues: list[BuildIssueFact] = []
        seen: dict[tuple[str, str], set[str]] = defaultdict(set)
        for item in evidence_rows:
            seen[(item.owner_type, item.owner_ref)].add(item.quote)
            if item.alignment not in ("exact", "normalized_exact"):
                issues.append(BuildIssueFact("evidence_alignment", str(item.id), "alignment_not_exact", document.document_id))
            if document.fact_authority != "authoritative" and (
                item.start is None or item.end is None
                or document.raw_text[item.start:item.end] != item.quote
            ):
                issues.append(BuildIssueFact("evidence_alignment", str(item.id), "quote_coordinates_invalid", document.document_id))
        for item in evidence_rows:
            if len(seen[(item.owner_type, item.owner_ref)]) > 1:
                issues.append(BuildIssueFact("evidence_alignment", str(item.id), "evidence_conflict", document.document_id))

        normalized_position_id = None
        mapping_rows = self.session.scalars(
            select(EffectiveMappingRecord).where(
                EffectiveMappingRecord.source_fact_id
                == (document.source_fact_id or document.document_id)
            )
        ).all()
        superseded_mapping_ids = {
            row.supersedes_effective_mapping_id
            for row in mapping_rows
            if row.supersedes_effective_mapping_id is not None
        }
        effective_mappings = {
            (row.requirement_id, normalize_key(row.source_expression)): row
            for row in mapping_rows
            if row.id not in superseded_mapping_ids
        }
        occurrences: list[SkillOccurrenceFact] = []
        aggregates: list[TextAggregateFact] = []
        if normalized is not None:
            classification = self.session.scalar(select(NormalizedJobClassification).where(
                NormalizedJobClassification.normalized_record_id == normalized.id
            ))
            classification_payload = normalized.payload.get("job_classification", {})
            normalized_position_code = classification_payload.get("position_code")
            if (
                not normalized_position_code
                or classification_payload.get("classification_status")
                not in {"resolved", "manually_confirmed"}
            ):
                issues.append(
                    BuildIssueFact(
                        "job_classification",
                        str(classification.id) if classification else document.document_id,
                        "unresolved_or_ambiguous_classification",
                        document.document_id,
                    )
                )
            else:
                normalized_position_id = str(normalized_position_code)
        if extraction is not None:
            payload = load_structured_extraction(self.session, document.document_id)
            for task in payload.responsibilities:
                if document.fact_authority == "authoritative" or task.evidence.is_exact_for(document.raw_text):
                    row = evidence_by_ref.get(task.requirement_id)
                    aggregates.append(TextAggregateFact("responsibility", task.text, row.id if row else None))
            for requirement in payload.requirements:
                row = evidence_by_ref.get(requirement.requirement_id)
                if requirement.kind != "skill" and (
                    document.fact_authority == "authoritative" or requirement.evidence.is_exact_for(document.raw_text)
                ):
                    aggregates.append(TextAggregateFact(requirement.kind, requirement.text, row.id if row else None))
            for fact in payload.company_facts:
                if document.fact_authority == "authoritative" or fact.evidence.is_exact_for(document.raw_text):
                    row = evidence_by_ref.get(fact.fact_id)
                    aggregates.append(TextAggregateFact("company_fact", fact.text, row.id if row else None))
            for fact in payload.employment_facts:
                if document.fact_authority == "authoritative" or fact.evidence.is_exact_for(document.raw_text):
                    row = evidence_by_ref.get(fact.fact_id)
                    aggregates.append(TextAggregateFact("employment_fact", fact.text, row.id if row else None))
            if normalized is not None:
                requirement_map = {item.requirement_id: item for item in payload.requirements if item.kind == "skill"}
                normalized_requirements = self.session.scalars(select(NormalizedRequirementRecord).where(
                    NormalizedRequirementRecord.normalized_record_id == normalized.id,
                    NormalizedRequirementRecord.kind == "skill",
                )).all()
                for normalized_requirement in normalized_requirements:
                    source_requirement = self.session.scalar(select(ExtractedCandidateRequirement).where(
                        ExtractedCandidateRequirement.document_id == document.document_id,
                        ExtractedCandidateRequirement.requirement_id == normalized_requirement.requirement_id,
                    ))
                    extracted = requirement_map.get(normalized_requirement.requirement_id)
                    evidence_row = evidence_by_ref.get(normalized_requirement.requirement_id)
                    for skill_row in self.session.scalars(select(NormalizedSkillRecord).where(
                        NormalizedSkillRecord.normalized_requirement_id == normalized_requirement.id
                    )).all():
                        effective_mapping = effective_mappings.get(
                            (
                                normalized_requirement.requirement_id,
                                normalize_key(skill_row.source_name),
                            )
                        )
                        resolved_skill_id = (
                            effective_mapping.skill_id
                            if effective_mapping is not None
                            else skill_row.skill_id
                        )
                        skill = self.session.scalar(select(Skill).where(Skill.skill_id == resolved_skill_id)) if resolved_skill_id else None
                        if not (extracted and source_requirement and evidence_row and skill and skill.status == "active"):
                            continue
                        source_names = {normalize_key(str(item.get("name", ""))) for item in source_requirement.payload.get("items", [])}
                        allowed_alignment = evidence_row.alignment in (("exact", "normalized_exact") if document.fact_authority == "authoritative" else ("exact",))
                        if (
                            effective_mapping is None
                            and skill_row.resolution_status not in ("resolved", "manually_confirmed")
                            or normalize_key(skill_row.source_name) not in source_names
                            or not allowed_alignment
                        ):
                            continue
                        occurrences.append(SkillOccurrenceFact(
                            skill.skill_id, skill_row.id, extracted.requirement_id,
                            source_requirement.id, evidence_row.id, extraction.id,
                            extracted.modality,
                        ))
        return BuildDocumentFacts(
            document.document_id, document.raw_text,
            document.fact_authority == "authoritative",
            document.source_name or document.source_type,
            document.enterprise_name or "", document.published_at,
            document.published_at or document.created_at,
            duplicate_cluster_key,
            document.source_credibility,
            bool(extraction and extraction.confirmed), normalized is not None,
            bool(quality and quality.assessed), normalized_position_id,
            quality.effective_sample_weight if quality else 0,
            evidence, tuple(occurrences), tuple(aggregates), tuple(issues),
        )

    def save_plan(self, plan: GraphBuildPlan) -> PersistedBuildRun:
        persisted_objects: list[PersistedBuildObject] = []
        included_document_ids = {
            sample.document_id for sample in plan.samples if sample.included
        }
        included_documents = self.session.scalars(
            select(JDDocument).where(JDDocument.document_id.in_(included_document_ids))
        ).all() if included_document_ids else []
        source_fact_ids = {
            document.source_fact_id or document.document_id
            for document in included_documents
        }
        mapping_rows = self.session.scalars(
            select(EffectiveMappingRecord).where(
                EffectiveMappingRecord.source_fact_id.in_(source_fact_ids)
            )
        ).all() if source_fact_ids else []
        superseded_mapping_ids = {
            row.supersedes_effective_mapping_id
            for row in mapping_rows
            if row.supersedes_effective_mapping_id is not None
        }
        mapping_policy_versions = sorted({
            row.policy_version
            for row in mapping_rows
            if row.id not in superseded_mapping_ids
        })
        mapping_policy_version = mapping_policy_versions[-1] if mapping_policy_versions else settings.normalization_map_version
        mapping_snapshot_version = mapping_policy_version
        published_fact_versions = sorted(
            f"{document.source_system}:{document.source_fact_id}@{document.source_fact_version}"
            for document in included_documents
            if document.fact_authority == "authoritative"
            and document.source_fact_id
            and document.source_fact_version
        )
        skill_ids = sorted({support.skill_id for support in plan.supports})
        taxonomy_versions = sorted(
            {
                version
                for version in self.session.scalars(
                    select(Skill.taxonomy_version).where(Skill.skill_id.in_(skill_ids))
                ).all()
                if version
            }
        ) if skill_ids else []
        skill_catalog_version = (
            taxonomy_versions[0]
            if len(taxonomy_versions) == 1
            else taxonomy_versions[-1]
            if taxonomy_versions
            else "absent"
        )
        release_id = None
        if included_document_ids:
            release_rows = self.session.execute(
                    select(
                        PublishedFactImport.document_id,
                        PublishedFactReleaseLink.release_id,
                    )
                    .join(
                        PublishedFactImport,
                        PublishedFactImport.id
                        == PublishedFactReleaseLink.published_fact_import_id,
                    )
                    .join(
                        JDDocument,
                        JDDocument.document_id == PublishedFactImport.document_id,
                    )
                    .where(
                        PublishedFactImport.document_id.in_(included_document_ids),
                        PublishedFactImport.source_system == JDDocument.source_system,
                        PublishedFactImport.source_fact_id == JDDocument.source_fact_id,
                        PublishedFactImport.source_fact_version == JDDocument.source_fact_version,
                    )
                    .distinct()
                ).all()
            linked_documents = {row.document_id for row in release_rows}
            release_ids = {row.release_id for row in release_rows}
            if linked_documents == included_document_ids and len(release_ids) == 1:
                release_id = next(iter(release_ids))
        document_deduplication = {
            document_id: plan.document_deduplication.get(
                document_id, f"document:{document_id}"
            )
            for document_id in sorted(included_document_ids)
        }
        build_config = {
            "algorithm_version": plan.algorithm_version,
            "normalization_map_version": settings.normalization_map_version,
            "normalization_algorithm_version": settings.normalization_algorithm_version,
            "mapping_policy_version": mapping_policy_version,
            "mapping_snapshot_version": mapping_snapshot_version,
            "published_fact_versions": published_fact_versions,
            "skill_catalog_version": skill_catalog_version,
            "min_weight": plan.window.minimum_weight,
            "minimum_valid_samples": plan.window.minimum_samples,
            "fact_source_mode": "authoritative_main_system" if plan.window.authoritative_only else "legacy_local",
        }
        build_config["position_profile_thresholds"] = (
            plan.position_profile_thresholds.serialized()
        )
        build_config["document_deduplication"] = document_deduplication
        config_version = build_config_version(
            plan.algorithm_version,
            plan.window.minimum_weight,
            plan.window.minimum_samples,
            plan.position_profile_thresholds,
        )
        build_config["build_config_version"] = config_version
        run = GraphBuildRun(
            position_id=plan.position_id, base_version_id=plan.base_version_id,
            release_id=release_id,
            status="running", window_start=plan.window.start, window_end=plan.window.end,
            config_snapshot=build_config,
        )
        self.session.add(run)
        self.session.flush()
        self.session.add_all([
            GraphBuildSample(
                build_run_id=run.id, document_id=sample.document_id,
                included=sample.included,
                exclusion_reasons=list(sample.exclusion_reasons),
                effective_weight=sample.effective_weight,
            )
            for sample in plan.samples
        ])
        self.session.add_all([
            PositionSkillSupport(
                build_run_id=run.id, position_id=support.position_id,
                skill_id=support.skill_id, document_id=support.document_id,
                requirement_id=support.requirement_id,
                normalized_skill_id=support.normalized_skill_id,
                evidence_id=support.evidence_id,
                source_requirement_id=support.source_requirement_id,
                extraction_record_id=support.extraction_record_id,
                modality=support.modality,
            )
            for support in plan.supports
        ])
        relation_rows = []
        for relation in plan.relations:
            metrics = dict(relation.metrics.serialized())
            statistics = {
                field: metrics[field] for field in RELATION_STATISTIC_FIELDS
            }
            row = PositionSkillRelationDraft(
                build_run_id=run.id, position_id=plan.position_id,
                skill_id=relation.skill_id, status=relation.status,
                metrics=metrics,
                statistics=statistics,
                explanation={
                    "source_summary": statistics,
                    "weight_basis": {
                        "auto": relation.auto_weight,
                        "manual": relation.manual_weight,
                        "final": relation.final_weight,
                        "weighted_frequency": metrics["weighted_frequency"],
                        "support_ratio": metrics["support_ratio"],
                        "modality_strength": metrics["modality_strength"],
                    },
                    "confidence_basis": {
                        "auto": relation.auto_confidence,
                        "manual": relation.manual_confidence,
                        "final": relation.final_confidence,
                        "trusted_evidence_ratio": metrics["trusted_evidence_ratio"],
                        "source_diversity": metrics["source_diversity"],
                    },
                    "quality_impact": {
                        "raw_frequency": statistics["raw_frequency"],
                        "adjusted_frequency": statistics[
                            "quality_adjusted_frequency"
                        ],
                        "frequency_delta": round(
                            statistics["quality_adjusted_frequency"]
                            - statistics["raw_frequency"],
                            4,
                        ),
                        "freshness_score": metrics["freshness_score"],
                        "trusted_evidence_ratio": metrics[
                            "trusted_evidence_ratio"
                        ],
                    },
                    "review_reasons": list(relation.review_reasons),
                },
                auto_weight=relation.auto_weight, manual_weight=relation.manual_weight,
                final_weight=relation.final_weight,
                auto_confidence=relation.auto_confidence,
                manual_confidence=relation.manual_confidence,
                final_confidence=relation.final_confidence,
                auto_importance_level=relation.auto_importance_level,
                manual_importance_level=relation.manual_importance_level,
                final_importance_level=relation.final_importance_level,
                trend_score=relation.trend_score,
            )
            relation_rows.append(row)
        self.session.add_all(relation_rows)
        aggregate_rows = []
        for aggregate in plan.aggregates:
            payload = {
                "text": aggregate.text,
                "support_document_count": len(aggregate.document_ids),
                "document_ids": list(aggregate.document_ids),
                "evidence_ids": list(aggregate.evidence_ids),
            }
            if aggregate.kind == "responsibility":
                row = PositionTaskAggregateDraft(build_run_id=run.id, payload=payload)
                object_type = "position_task"
            else:
                row = PositionRequirementAggregateDraft(build_run_id=run.id, kind=aggregate.kind, payload=payload)
                object_type = "position_requirement"
            aggregate_rows.append((row, aggregate, object_type))
        self.session.add_all([row for row, _, _ in aggregate_rows])
        self.session.flush()
        for relation, row in zip(plan.relations, relation_rows):
            persisted_objects.append(
                PersistedBuildObject(
                    relation.review_reference,
                    "position_skill_relation",
                    str(row.id),
                )
            )
        for row, aggregate, object_type in aggregate_rows:
            persisted_objects.append(
                PersistedBuildObject(
                    aggregate.review_reference,
                    object_type,
                    str(row.id),
                )
            )
        persisted_objects.append(
            PersistedBuildObject("build", "graph_version", f"build:{run.id}")
        )
        run.summary = dict(plan.summary.serialized())
        run.status = "succeeded"
        self.session.flush()
        return PersistedBuildRun(
            run.id, run.status,
            plan.summary,
            tuple(persisted_objects),
        )
