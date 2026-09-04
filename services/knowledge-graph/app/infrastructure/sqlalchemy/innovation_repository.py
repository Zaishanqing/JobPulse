"""SQLAlchemy persistence adapter for TraceSkill innovation planes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.application.errors import (
    ConcurrentInnovationWrite,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.domain.dependency_analysis import (
    DependencyAnalysis,
    DependencyCandidate,
    DependencyPolicy,
    RequirementContext,
)
from app.domain.innovation import (
    BuildWatermarkFacts,
    ClaimSourceFact,
    DependencyContextFacts,
    MappingCandidateState,
    ProjectionFacts,
    SavedDependencyAnalysis,
    SavedProjection,
)
from app.domain.projections import (
    GraphProjection,
    ProjectionEdge,
    ProjectionManifest,
    ProjectionNode,
)
from app.domain.temporal_analysis import BuildInputWatermark, WatermarkSourceFact
from app.domain.traceability import (
    ClaimEvidenceRef,
    MappingCandidate,
    MappingAffectedContext,
    MappingCandidateSignals,
    MappingReviewDecision,
    RelationClaim,
    claim_lineage_version,
)
from app.models import (
    AlgorithmConfig,
    BuildInputWatermarkRecord,
    DependencyAnalysisRunRecord,
    DependencyCandidateRecord,
    DependencyReviewDecisionRecord,
    DependencyEvent,
    DownstreamDependencyReference,
    EffectiveMappingRecord,
    ExtractionEvidence,
    ExtractedCandidateRequirement,
    GraphBuildRun,
    GraphBuildSample,
    GraphVersion,
    GraphVersionDependencyRecord,
    JDDocument,
    JDNormalizedRecord,
    MappingCandidateRecord,
    MappingReviewDecisionRecord,
    NormalizedJobClassification,
    PositionSkillSupport,
    ProjectionManifestRecord,
    PublishedFactImport,
    PublishedFactLineageRecord,
    RelationClaimRecord,
    Skill,
    UnresolvedNormalizationItem,
)


PROJECTION_MAPPING_STATUSES = ("pending", "accepted")


def _watermark(row: BuildInputWatermarkRecord) -> BuildInputWatermark:
    return BuildInputWatermark(
        source_facts=tuple(
            WatermarkSourceFact(
                item["source_kind"],
                item["source_fact_id"],
                item["source_fact_version"],
                item["source_version"],
            )
            for item in row.source_facts
        ),
        observation_window_start=row.observation_window_start,
        observation_window_end=row.observation_window_end,
        catalog_snapshot_id=row.catalog_snapshot_id,
        catalog_source_version=row.catalog_source_version,
        validation_state=row.validation_state,
        validation_policy_version=row.validation_policy_version,
        mapping_policy_version=row.mapping_policy_version,
        aggregation_algorithm_version=row.aggregation_algorithm_version,
        normalized_config=dict(row.normalized_config),
        config_version=row.config_version,
        input_coverage=row.input_coverage,
        lineage_version=row.lineage_version,
    )


def _claim(row: RelationClaimRecord) -> RelationClaim:
    return RelationClaim(
        claim_id=row.claim_id,
        support_id=row.support_id,
        subject_id=row.subject_id,
        predicate=row.predicate,
        object_id=row.object_id,
        claim_kind=row.claim_kind,
        source_kind=row.source_kind,
        source_fact_id=row.source_fact_id,
        source_fact_version=row.source_fact_version,
        requirement_id=row.requirement_id,
        evidence=tuple(ClaimEvidenceRef(**item) for item in row.evidence_refs),
        validation_lineage_lineage_version=row.validation_lineage_lineage_version,
        catalog_snapshot_lineage_version=row.catalog_snapshot_lineage_version,
        mapping_policy_version=row.mapping_policy_version,
        observed_at=row.observed_at,
        graph_version_id=row.graph_version_id,
    )


def _mapping_candidate(row: MappingCandidateRecord) -> MappingCandidate:
    return MappingCandidate(
        candidate_id=row.candidate_id,
        source_expression=row.source_expression,
        proposed_skill_id=row.proposed_skill_id,
        signals=MappingCandidateSignals(**row.signals),
        model_version=row.model_version,
        index_version=row.index_version,
        mapping_policy_version=row.mapping_policy_version,
        affected_contexts=tuple(
            MappingAffectedContext(**item) for item in row.affected_contexts
        ),
    )


def _dependency_candidate(row: DependencyCandidateRecord) -> DependencyCandidate:
    return DependencyCandidate(
        prerequisite_skill_id=row.prerequisite_skill_id,
        advanced_skill_id=row.advanced_skill_id,
        evidence_ids=tuple(row.evidence_ids),
        claim_kind=row.claim_kind,
        **row.metrics,
    )


class SqlAlchemyInnovationRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_build_watermark_facts(self, build_run_id: int) -> BuildWatermarkFacts:
        run = self.session.get(GraphBuildRun, build_run_id)
        if run is None:
            raise NotFoundError("build run not found")
        required_config = {"algorithm_version", "normalization_map_version"}
        missing_config = sorted(required_config - set(run.config_snapshot))
        if missing_config:
            raise ValidationError(
                "build config snapshot is incomplete",
                error_code="BUILD_WATERMARK_CONFIG_INCOMPLETE",
                details={"missing_fields": missing_config},
            )
        required_summary = {"included_samples", "excluded_samples"}
        missing_summary = sorted(required_summary - set(run.summary))
        if missing_summary:
            raise ValidationError(
                "build summary is incomplete",
                error_code="BUILD_WATERMARK_SUMMARY_INCOMPLETE",
                details={"missing_fields": missing_summary},
            )
        documents = self.session.scalars(
            select(JDDocument)
            .join(GraphBuildSample, GraphBuildSample.document_id == JDDocument.document_id)
            .where(
                GraphBuildSample.build_run_id == build_run_id,
                GraphBuildSample.included.is_(True),
            )
            .order_by(JDDocument.document_id)
        ).all()
        source_facts: list[WatermarkSourceFact] = []
        validation_versions: set[str] = set()
        validation_complete = bool(documents)
        for document in documents:
            if document.fact_authority == "authoritative":
                if not document.source_fact_id or not document.source_fact_version:
                    raise ValueError("authoritative document lacks source fact identity")
                if not document.source_version:
                    raise ValueError("authoritative document lacks a source version")
                source_facts.append(
                    WatermarkSourceFact(
                        "published_fact",
                        document.source_fact_id,
                        document.source_fact_version,
                        document.source_version,
                    )
                )
                imported = self.session.scalar(
                    select(PublishedFactImport).where(
                        PublishedFactImport.source_fact_id == document.source_fact_id,
                        PublishedFactImport.source_fact_version == document.source_fact_version,
                    )
                )
                lineage = (
                    self.session.scalar(
                        select(PublishedFactLineageRecord).where(
                            PublishedFactLineageRecord.published_fact_import_id == imported.id
                        )
                    )
                    if imported is not None
                    else None
                )
                if lineage is None or not lineage.validation_policy_version:
                    validation_complete = False
                else:
                    validation_versions.add(lineage.validation_policy_version)
            else:
                source_facts.append(
                    WatermarkSourceFact(
                        "legacy_local",
                        document.document_id,
                        document.created_at.isoformat(),
                        document.document_id,
                    )
                )
                validation_complete = False
        catalog_source_version = settings.normalization_map_version
        validation_policy = None
        if validation_complete:
            validation_policy = (
                next(iter(validation_versions))
                if len(validation_versions) == 1
                else f"policy-set:{','.join(sorted(validation_versions))}"
            )
        total = int(run.summary["included_samples"]) + int(
            run.summary["excluded_samples"]
        )
        coverage = len(documents) / total if total else 1.0
        return BuildWatermarkFacts(
            build_run_id=run.id,
            source_facts=tuple(source_facts),
            observation_window_start=(
                run.window_start.isoformat() if run.window_start else "unbounded"
            ),
            observation_window_end=(
                run.window_end.isoformat() if run.window_end else "unbounded"
            ),
            catalog_snapshot_id=f"kg-consumed-catalog:{catalog_source_version}",
            catalog_source_version=catalog_source_version,
            validation_state="present" if validation_complete else "absent",
            validation_policy_version=validation_policy,
            mapping_policy_version=str(run.config_snapshot["mapping_policy_version"]),
            aggregation_algorithm_version=str(run.config_snapshot["algorithm_version"]),
            normalized_config=dict(run.config_snapshot),
            input_coverage=round(coverage, 8),
        )

    def save_build_watermark(self, build_run_id: int, watermark: BuildInputWatermark) -> int:
        existing = self.session.scalar(
            select(BuildInputWatermarkRecord).where(
                BuildInputWatermarkRecord.build_run_id == build_run_id
            )
        )
        if existing is not None:
            if existing.lineage_version != watermark.lineage_version:
                raise ConcurrentInnovationWrite("build watermark conflict")
            return existing.id
        row = BuildInputWatermarkRecord(
            build_run_id=build_run_id,
            lineage_version=watermark.lineage_version,
            source_facts=[asdict(item) for item in watermark.source_facts],
            observation_window_start=watermark.observation_window_start,
            observation_window_end=watermark.observation_window_end,
            catalog_snapshot_id=watermark.catalog_snapshot_id,
            catalog_source_version=watermark.catalog_source_version,
            validation_state=watermark.validation_state,
            validation_policy_version=watermark.validation_policy_version,
            mapping_policy_version=watermark.mapping_policy_version,
            aggregation_algorithm_version=watermark.aggregation_algorithm_version,
            normalized_config=dict(watermark.normalized_config),
            config_version=watermark.config_version,
            input_coverage=watermark.input_coverage,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def load_build_watermark(self, build_run_id: int) -> BuildInputWatermark:
        row = self.session.scalar(
            select(BuildInputWatermarkRecord).where(
                BuildInputWatermarkRecord.build_run_id == build_run_id
            )
        )
        if row is None:
            raise NotFoundError("build watermark not found")
        return _watermark(row)

    def latest_build_watermark(self, position_id: str) -> BuildInputWatermark | None:
        row = self.session.scalar(
            select(BuildInputWatermarkRecord)
            .join(
                GraphBuildRun,
                GraphBuildRun.id == BuildInputWatermarkRecord.build_run_id,
            )
            .where(
                GraphBuildRun.position_id == position_id,
                GraphBuildRun.status == "succeeded",
            )
            .order_by(GraphBuildRun.id.desc())
            .limit(1)
        )
        return _watermark(row) if row is not None else None

    def current_build_source_facts(
        self, position_id: str, authoritative_only: bool
    ) -> tuple[WatermarkSourceFact, ...]:
        latest_normalized = (
            select(
                JDNormalizedRecord.document_id,
                func.max(JDNormalizedRecord.id).label("latest_id"),
            )
            .group_by(JDNormalizedRecord.document_id)
            .subquery()
        )
        classified_documents = (
            select(JDDocument)
            .join(
                JDNormalizedRecord,
                JDNormalizedRecord.document_id == JDDocument.document_id,
            )
            .join(
                latest_normalized,
                latest_normalized.c.latest_id == JDNormalizedRecord.id,
            )
            .join(
                NormalizedJobClassification,
                NormalizedJobClassification.normalized_record_id
                == JDNormalizedRecord.id,
            )
            .where(NormalizedJobClassification.position_id == position_id)
        )
        facts: list[WatermarkSourceFact] = []
        if authoritative_only:
            rows = self.session.execute(
                select(
                    PublishedFactImport.source_fact_id,
                    PublishedFactImport.source_fact_version,
                    PublishedFactImport.source_version,
                )
                .join(
                    JDDocument,
                    JDDocument.document_id == PublishedFactImport.document_id,
                )
                .where(
                    JDDocument.document_id.in_(
                        select(JDDocument.document_id)
                        .where(
                            classified_documents.c.fact_authority == "authoritative",
                            classified_documents.c.source_system == "main-system",
                        )
                    )
                )
                .order_by(
                    PublishedFactImport.source_fact_id,
                    PublishedFactImport.source_fact_version,
                )
            ).all()
            facts.extend(
                WatermarkSourceFact(
                    "published_fact",
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                )
                for row in rows
                if row[0] and row[1] and row[2]
            )
        else:
            legacy_documents = self.session.scalars(
                classified_documents.where(
                    JDDocument.fact_authority == "legacy_local"
                ).order_by(JDDocument.document_id)
            ).all()
            facts.extend(
                WatermarkSourceFact(
                    "legacy_local",
                    document.document_id,
                    document.created_at.isoformat()
                    if document.created_at
                    else document.document_id,
                    document.document_id,
                )
                for document in legacy_documents
            )
        return tuple(
            sorted(
                facts,
                key=lambda item: (
                    item.source_kind,
                    item.source_fact_id,
                    item.source_fact_version,
                ),
            )
        )

    def current_algorithm_version(self) -> str:
        row = self.session.scalar(
            select(AlgorithmConfig)
            .where(AlgorithmConfig.active.is_(True))
            .order_by(AlgorithmConfig.id.desc())
        )
        return row.version if row is not None else settings.algorithm_version

    def current_catalog_source_version(self) -> str:
        return settings.normalization_map_version

    def load_claim_sources(self, build_run_id: int) -> tuple[ClaimSourceFact, ...]:
        rows = self.session.scalars(
            select(PositionSkillSupport)
            .where(PositionSkillSupport.build_run_id == build_run_id)
            .order_by(PositionSkillSupport.id)
        ).all()
        result: list[ClaimSourceFact] = []
        for support in rows:
            document = self.session.scalar(
                select(JDDocument).where(JDDocument.document_id == support.document_id)
            )
            evidence = self.session.get(ExtractionEvidence, support.evidence_id)
            if document is None or evidence is None:
                raise ValueError("claim support lineage is incomplete")
            if evidence.alignment != "exact" or evidence.start is None or evidence.end is None:
                raise ValueError("claim support evidence is not exact")
            lineage_lineage_version = None
            if document.fact_authority == "authoritative":
                source_kind = "published_fact"
                if not document.source_fact_id or not document.source_fact_version:
                    raise ValueError("authoritative claim lacks source fact identity")
                source_fact_id = document.source_fact_id
                source_fact_version = document.source_fact_version
                imported = self.session.scalar(
                    select(PublishedFactImport).where(
                        PublishedFactImport.source_fact_id == source_fact_id,
                        PublishedFactImport.source_fact_version == source_fact_version,
                    )
                )
                lineage = (
                    self.session.scalar(
                        select(PublishedFactLineageRecord).where(
                            PublishedFactLineageRecord.published_fact_import_id == imported.id
                        )
                    )
                    if imported is not None
                    else None
                )
                lineage_lineage_version = (
                    lineage.lineage_lineage_version
                    if lineage is not None and lineage.validation_policy_version
                    else None
                )
            else:
                source_kind = "legacy_local"
                source_fact_id = document.document_id
                source_fact_version = document.created_at.isoformat()
            result.append(
                ClaimSourceFact(
                    support_id=support.id,
                    build_run_id=build_run_id,
                    position_id=support.position_id,
                    skill_id=support.skill_id,
                    source_kind=source_kind,
                    source_fact_id=source_fact_id,
                    source_fact_version=source_fact_version,
                    requirement_id=support.requirement_id,
                    evidence=ClaimEvidenceRef(
                        evidence.id,
                        evidence.owner_ref,
                        evidence.quote,
                        evidence.start,
                        evidence.end,
                        True,
                    ),
                    validation_lineage_lineage_version=lineage_lineage_version,
                    observed_at=(document.published_at or document.created_at).isoformat(),
                )
            )
        return tuple(result)

    def save_relation_claims(self, claims: tuple[RelationClaim, ...]) -> int:
        saved = 0
        for claim in claims:
            existing = self.session.scalar(
                select(RelationClaimRecord).where(
                    RelationClaimRecord.claim_id == claim.claim_id
                )
            )
            lineage_version = claim_lineage_version(claim)
            if existing is not None:
                if existing.lineage_version != lineage_version:
                    raise ConcurrentInnovationWrite("relation claim conflict")
                continue
            graph_version = self.session.get(GraphVersion, claim.graph_version_id)
            if graph_version is None:
                raise NotFoundError("claim graph version not found")
            row = RelationClaimRecord(
                claim_id=claim.claim_id,
                graph_version_id=claim.graph_version_id,
                build_run_id=graph_version.build_run_id,
                support_id=claim.support_id,
                subject_id=claim.subject_id,
                predicate=claim.predicate,
                object_id=claim.object_id,
                claim_kind=claim.claim_kind,
                source_kind=claim.source_kind,
                source_fact_id=claim.source_fact_id,
                source_fact_version=claim.source_fact_version,
                requirement_id=claim.requirement_id,
                evidence_refs=[asdict(item) for item in claim.evidence],
                validation_lineage_lineage_version=claim.validation_lineage_lineage_version,
                catalog_snapshot_lineage_version=claim.catalog_snapshot_lineage_version,
                mapping_policy_version=claim.mapping_policy_version,
                observed_at=claim.observed_at,
                lineage_version=lineage_version,
            )
            self.session.add(row)
            saved += 1
        self.session.flush()
        return saved

    def copy_rollback_lineage(
        self, source_version_id: int, target_version_id: int
    ) -> int:
        source = self.session.get(GraphVersion, source_version_id)
        target = self.session.get(GraphVersion, target_version_id)
        if source is None or target is None:
            raise NotFoundError("rollback graph version not found")
        source_watermark = self.load_build_watermark(source.build_run_id)
        self.save_build_watermark(target.build_run_id, source_watermark)
        source_claims = tuple(
            _claim(row)
            for row in self.session.scalars(
                select(RelationClaimRecord).where(
                    RelationClaimRecord.graph_version_id == source_version_id
                )
            ).all()
        )
        copied = tuple(
            RelationClaim(
                claim_id=f"rollback:{target_version_id}:{claim.support_id}",
                support_id=claim.support_id,
                subject_id=claim.subject_id,
                predicate=claim.predicate,
                object_id=claim.object_id,
                claim_kind=claim.claim_kind,
                source_kind=claim.source_kind,
                source_fact_id=claim.source_fact_id,
                source_fact_version=claim.source_fact_version,
                requirement_id=claim.requirement_id,
                evidence=claim.evidence,
                validation_lineage_lineage_version=claim.validation_lineage_lineage_version,
                catalog_snapshot_lineage_version=claim.catalog_snapshot_lineage_version,
                mapping_policy_version=claim.mapping_policy_version,
                observed_at=claim.observed_at,
                graph_version_id=target_version_id,
            )
            for claim in source_claims
        )
        return self.save_relation_claims(copied)

    def save_mapping_candidate(self, state: MappingCandidateState) -> MappingCandidateState:
        candidate = state.candidate
        if self.session.scalar(select(Skill).where(Skill.skill_id == candidate.proposed_skill_id)) is None:
            raise NotFoundError("proposed standard skill does not exist")
        for context in candidate.affected_contexts:
            document = self.session.scalar(
                select(JDDocument).where(
                    or_(
                        JDDocument.document_id == context.source_fact_id,
                        JDDocument.source_fact_id == context.source_fact_id,
                    )
                )
            )
            if document is None:
                raise NotFoundError("mapping candidate source fact not found")
            requirement = self.session.scalar(
                select(ExtractedCandidateRequirement).where(
                    ExtractedCandidateRequirement.document_id == document.document_id,
                    ExtractedCandidateRequirement.requirement_id
                    == context.requirement_id,
                )
            )
            if requirement is None:
                raise NotFoundError("mapping candidate requirement context not found")
        existing = self.session.scalar(
            select(MappingCandidateRecord).where(
                MappingCandidateRecord.candidate_id == candidate.candidate_id
            )
        )
        if existing is not None:
            persisted = _mapping_candidate(existing)
            if persisted != candidate or existing.priority != state.priority:
                raise ConcurrentInnovationWrite("mapping candidate conflict")
            return MappingCandidateState(
                persisted, existing.priority, existing.status, existing.revision
            )
        row = MappingCandidateRecord(
            candidate_id=candidate.candidate_id,
            source_expression=candidate.source_expression,
            proposed_skill_id=candidate.proposed_skill_id,
            signals=asdict(candidate.signals),
            priority=state.priority,
            model_version=candidate.model_version,
            index_version=candidate.index_version,
            mapping_policy_version=candidate.mapping_policy_version,
            affected_contexts=[asdict(item) for item in candidate.affected_contexts],
            status=state.status,
            revision=state.revision,
        )
        self.session.add(row)
        self.session.flush()
        return MappingCandidateState(candidate, row.priority, row.status, row.revision)

    def load_mapping_candidate(self, candidate_id: str) -> MappingCandidateState:
        row = self.session.scalar(
            select(MappingCandidateRecord).where(
                MappingCandidateRecord.candidate_id == candidate_id
            )
        )
        if row is None:
            raise NotFoundError("mapping candidate not found")
        return MappingCandidateState(
            _mapping_candidate(row), row.priority, row.status, row.revision
        )

    def save_mapping_review(
        self, decision: MappingReviewDecision, expected_revision: int
    ) -> MappingCandidateState:
        if decision.replacement_candidate_id is not None:
            if decision.replacement_candidate_id == decision.candidate_id:
                raise ValidationError(
                    "mapping candidate cannot replace itself",
                    error_code="INVALID_MAPPING_SUPERSESSION",
                )
            replacement = self.session.scalar(
                select(MappingCandidateRecord).where(
                    MappingCandidateRecord.candidate_id
                    == decision.replacement_candidate_id
                )
            )
            if replacement is None:
                raise NotFoundError("replacement mapping candidate not found")
        target_status = {
            "accept": "accepted",
            "reject": "rejected",
            "no_match": "no_match",
            "supersede": "superseded",
        }[decision.decision]
        allowed_status = "accepted" if decision.decision == "supersede" else "pending"
        result = self.session.execute(
            update(MappingCandidateRecord)
            .where(
                MappingCandidateRecord.candidate_id == decision.candidate_id,
                MappingCandidateRecord.status == allowed_status,
                MappingCandidateRecord.revision == expected_revision,
            )
            .values(status=target_status, revision=expected_revision + 1)
        )
        if result.rowcount != 1:
            raise ConcurrentInnovationWrite("mapping candidate changed concurrently")
        self.session.add(
            MappingReviewDecisionRecord(
                candidate_id=decision.candidate_id,
                candidate_revision=expected_revision,
                decision=decision.decision,
                reviewer_id=decision.reviewer_id,
                reason=decision.reason,
                policy_version=decision.policy_version,
                decided_at=decision.decided_at,
                effective_scope=decision.effective_scope,
                replacement_candidate_id=decision.replacement_candidate_id,
            )
        )
        self.session.flush()
        return self.load_mapping_candidate(decision.candidate_id)

    def apply_mapping_review_effect(self, candidate_id: str) -> int:
        candidate = self.session.scalar(
            select(MappingCandidateRecord).where(
                MappingCandidateRecord.candidate_id == candidate_id,
                MappingCandidateRecord.status == "accepted",
            )
        )
        if candidate is None:
            raise ValidationError("only an accepted mapping candidate can become effective")
        decision = self.session.scalar(
            select(MappingReviewDecisionRecord)
            .where(
                MappingReviewDecisionRecord.candidate_id == candidate_id,
                MappingReviewDecisionRecord.decision == "accept",
            )
            .order_by(MappingReviewDecisionRecord.id.desc())
        )
        if decision is None:
            raise ValueError("accepted mapping candidate has no review decision")
        supersession = self.session.scalar(
            select(MappingReviewDecisionRecord)
            .where(
                MappingReviewDecisionRecord.replacement_candidate_id == candidate_id,
                MappingReviewDecisionRecord.decision == "supersede",
            )
            .order_by(MappingReviewDecisionRecord.id.desc())
        )
        predecessor = None
        if supersession is not None:
            predecessor = self.session.scalar(
                select(MappingCandidateRecord).where(
                    MappingCandidateRecord.candidate_id == supersession.candidate_id
                )
            )
            if predecessor is None:
                raise ValueError("mapping supersession predecessor is missing")
            if predecessor.affected_contexts != candidate.affected_contexts:
                raise ValidationError(
                    "replacement mapping candidate must preserve affected contexts",
                    error_code="MAPPING_SUPERSESSION_SCOPE_MISMATCH",
                )
        saved = 0
        for context in candidate.affected_contexts:
            rows = self.session.scalars(
                select(EffectiveMappingRecord).where(
                    EffectiveMappingRecord.source_fact_id == context["source_fact_id"],
                    EffectiveMappingRecord.requirement_id == context["requirement_id"],
                )
            ).all()
            superseded_ids = {
                row.supersedes_effective_mapping_id
                for row in rows
                if row.supersedes_effective_mapping_id is not None
            }
            active = [row for row in rows if row.id not in superseded_ids]
            supersedes_id = None
            if predecessor is not None:
                matches = [
                    row for row in active
                    if row.source_expression == predecessor.source_expression
                ]
                if len(matches) != 1:
                    raise ConflictError(
                        "mapping supersession predecessor is not uniquely effective",
                        error_code="MAPPING_SUPERSESSION_PREDECESSOR_INVALID",
                    )
                supersedes_id = matches[0].id
            elif active:
                raise ConflictError(
                    "affected context already has an effective mapping",
                    error_code="EFFECTIVE_MAPPING_EXISTS",
                )
            self.session.add(
                EffectiveMappingRecord(
                    review_decision_id=decision.id,
                    supersedes_effective_mapping_id=supersedes_id,
                    source_fact_id=context["source_fact_id"],
                    requirement_id=context["requirement_id"],
                    source_expression=candidate.source_expression,
                    skill_id=candidate.proposed_skill_id,
                    policy_version=decision.policy_version,
                )
            )
            document = self.session.scalar(
                select(JDDocument).where(
                    or_(
                        JDDocument.document_id == context["source_fact_id"],
                        JDDocument.source_fact_id == context["source_fact_id"],
                    )
                )
            )
            if document is None:
                raise NotFoundError("effective mapping source fact not found")
            unresolved = self.session.scalars(
                select(UnresolvedNormalizationItem).where(
                    UnresolvedNormalizationItem.document_id == document.document_id,
                    UnresolvedNormalizationItem.status == "open",
                    UnresolvedNormalizationItem.source_name == candidate.source_expression,
                )
            ).all()
            for item in unresolved:
                item.status = "resolved"
                item.resolution = {
                    "skill_id": candidate.proposed_skill_id,
                    "policy_version": decision.policy_version,
                    "mapping_review_decision_id": decision.id,
                }
                item.reviewer_id = decision.reviewer_id
                item.reviewed_at = datetime.fromisoformat(
                    decision.decided_at.replace("Z", "+00:00")
                )
                item.review_reason = decision.reason
            saved += 1
        self.session.flush()
        return saved

    def mapping_change_impact(self, candidate_id: str) -> dict:
        candidate = self.session.scalar(
            select(MappingCandidateRecord).where(
                MappingCandidateRecord.candidate_id == candidate_id
            )
        )
        if candidate is None:
            raise NotFoundError("mapping candidate not found")
        contexts = [dict(item) for item in candidate.affected_contexts]
        source_fact_ids = {str(item["source_fact_id"]) for item in contexts}
        documents = self.session.scalars(
            select(JDDocument).where(
                or_(
                    JDDocument.document_id.in_(source_fact_ids),
                    JDDocument.source_fact_id.in_(source_fact_ids),
                )
            )
        ).all()
        document_ids = {row.document_id for row in documents}
        context_predicates = [
            and_(
                PositionSkillSupport.document_id == document.document_id,
                PositionSkillSupport.requirement_id == context["requirement_id"],
            )
            for context in contexts
            for document in documents
            if context["source_fact_id"] in {
                document.document_id,
                document.source_fact_id,
            }
        ]
        supports = self.session.scalars(
            select(PositionSkillSupport).where(or_(*context_predicates))
        ).all() if context_predicates else []
        build_run_ids = sorted({row.build_run_id for row in supports})
        builds = self.session.scalars(
            select(GraphBuildRun).where(GraphBuildRun.id.in_(build_run_ids))
        ).all() if build_run_ids else []
        versions = self.session.scalars(
            select(GraphVersion).where(GraphVersion.build_run_id.in_(build_run_ids))
        ).all() if build_run_ids else []
        version_ids = [row.id for row in versions]
        references = self.session.scalars(
            select(DownstreamDependencyReference).where(
                DownstreamDependencyReference.graph_version_id.in_(version_ids)
            )
        ).all() if version_ids else []
        facts = self.session.scalars(
            select(PublishedFactImport).where(
                PublishedFactImport.document_id.in_(document_ids)
            )
        ).all() if document_ids else []
        return {
            "contract_version": "change-impact.v1",
            "entity_type": "skill_mapping",
            "entity_id": candidate_id,
            "affected_contexts": contexts,
            "jd_facts": [
                {
                    "source_system": row.source_system,
                    "source_fact_id": row.source_fact_id,
                    "source_fact_version": row.source_fact_version,
                    "document_id": row.document_id,
                }
                for row in facts
            ],
            "graph_versions": [
                {
                    "graph_version_id": row.id,
                    "position_id": row.position_id,
                    "version_name": row.version_name,
                    "build_run_id": row.build_run_id,
                }
                for row in versions
            ],
            "build_runs": [
                {
                    "build_run_id": row.id,
                    "position_id": row.position_id,
                    "status": row.status,
                    "rebuild_required": True,
                }
                for row in builds
            ],
            "downstream_references": [
                {
                    "consumer_system": row.consumer_system,
                    "reference_type": row.reference_type,
                    "reference_id": row.reference_id,
                    "graph_version_id": row.graph_version_id,
                    "metadata": dict(row.metadata_payload),
                }
                for row in references
            ],
            "suggested_actions": [
                *(
                    ["rebuild_affected_positions"]
                    if builds
                    else ["rebuild_not_required_for_existing_versions"]
                ),
                *(["notify_downstream_consumers"] if references else []),
                "publish_new_graph_versions_after_review",
            ],
        }

    def save_dependency_event(
        self, *, event_key: str, entity_type: str, entity_id: str,
        change_kind: str, before: dict, after: dict, impact: dict,
        actor_id: int | None, trace_id: str,
    ) -> int:
        existing = self.session.scalar(
            select(DependencyEvent).where(DependencyEvent.event_key == event_key)
        )
        if existing is not None:
            return existing.id
        row = DependencyEvent(
            event_key=event_key,
            entity_type=entity_type,
            entity_id=entity_id,
            change_kind=change_kind,
            before_snapshot=before,
            after_snapshot=after,
            impact_snapshot=impact,
            actor_id=actor_id,
            trace_id=trace_id,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def save_dependency_reference(
        self, *, consumer_system: str, reference_type: str,
        reference_id: str, graph_version_id: int, metadata: dict,
    ) -> int:
        if consumer_system not in {"matching", "trend", "discovery"}:
            raise ValidationError("dependency consumer system is invalid")
        if self.session.get(GraphVersion, graph_version_id) is None:
            raise NotFoundError("graph version not found")
        existing = self.session.scalar(
            select(DownstreamDependencyReference).where(
                DownstreamDependencyReference.consumer_system == consumer_system,
                DownstreamDependencyReference.reference_type == reference_type,
                DownstreamDependencyReference.reference_id == reference_id,
                DownstreamDependencyReference.graph_version_id == graph_version_id,
            )
        )
        if existing is not None:
            return existing.id
        row = DownstreamDependencyReference(
            consumer_system=consumer_system,
            reference_type=reference_type,
            reference_id=reference_id,
            graph_version_id=graph_version_id,
            metadata_payload=metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def load_dependency_contexts(self, build_run_id: int) -> DependencyContextFacts:
        if self.session.get(GraphBuildRun, build_run_id) is None:
            raise NotFoundError("build run not found")
        supports = self.session.scalars(
            select(PositionSkillSupport)
            .where(PositionSkillSupport.build_run_id == build_run_id)
            .order_by(PositionSkillSupport.document_id, PositionSkillSupport.requirement_id)
        ).all()
        grouped: dict[tuple[str, str], list[PositionSkillSupport]] = defaultdict(list)
        for support in supports:
            grouped[(support.document_id, support.requirement_id)].append(support)
        contexts: list[RequirementContext] = []
        for (document_id, requirement_id), items in grouped.items():
            skill_ids = frozenset(item.skill_id for item in items)
            if len(skill_ids) < 2:
                continue
            document = self.session.scalar(
                select(JDDocument).where(JDDocument.document_id == document_id)
            )
            if document is None:
                raise ValueError("dependency context document is missing")
            observed = document.published_at or document.created_at
            template_hash = document.document_id
            contexts.append(
                RequirementContext(
                    context_id=f"{build_run_id}:{document_id}:{requirement_id}",
                    document_id=document_id,
                    requirement_id=requirement_id,
                    skill_ids=skill_ids,
                    source_name=document.source_name or document.source_type,
                    enterprise_id=document.enterprise_name or f"not-provided:{document_id}",
                    industry="not_provided",
                    region="not_provided",
                    time_slice=observed.strftime("%Y-%m"),
                    template_family_id=template_hash,
                    evidence_ids=tuple(sorted({item.evidence_id for item in items})),
                )
            )
        return DependencyContextFacts(build_run_id, tuple(contexts))

    def save_dependency_analysis(
        self,
        build_run_id: int,
        policy: DependencyPolicy,
        analysis: DependencyAnalysis,
    ) -> SavedDependencyAnalysis:
        policy_payload = asdict(policy)
        policy_version = "dependency-policy.v1"
        existing = self.session.scalar(
            select(DependencyAnalysisRunRecord).where(
                DependencyAnalysisRunRecord.build_run_id == build_run_id,
                DependencyAnalysisRunRecord.policy_hash == policy_version,
            )
        )
        if existing is not None:
            return SavedDependencyAnalysis(
                existing.id,
                build_run_id,
                int(existing.summary["candidate_count"]),
                int(existing.summary["rejected_count"]),
            )
        row = DependencyAnalysisRunRecord(
            build_run_id=build_run_id,
            policy_hash=policy_version,
            policy=policy_payload,
            status="completed",
            summary={
                "included_context_count": len(analysis.included_contexts),
                "excluded_context_count": len(analysis.excluded_contexts),
                "candidate_count": len(analysis.candidates),
                "rejected_count": len(analysis.rejected),
            },
        )
        self.session.add(row)
        self.session.flush()
        for candidate in analysis.candidates:
            metrics = asdict(candidate)
            metrics.pop("prerequisite_skill_id")
            metrics.pop("advanced_skill_id")
            metrics.pop("evidence_ids")
            metrics.pop("claim_kind")
            self.session.add(
                DependencyCandidateRecord(
                    analysis_run_id=row.id,
                    prerequisite_skill_id=candidate.prerequisite_skill_id,
                    advanced_skill_id=candidate.advanced_skill_id,
                    metrics=metrics,
                    evidence_ids=list(candidate.evidence_ids),
                    claim_kind=candidate.claim_kind,
                )
            )
        self.session.flush()
        return SavedDependencyAnalysis(
            row.id, build_run_id, len(analysis.candidates), len(analysis.rejected)
        )

    def review_dependency_candidate(
        self,
        candidate_id: int,
        decision: str,
        reviewer_id: int,
        reason: str,
        policy_version: str,
        decided_at: str,
    ) -> bool:
        if decision not in {"accept", "reject"}:
            raise ValidationError("dependency review decision is invalid")
        if not reason.strip() or not policy_version.strip():
            raise ValidationError("dependency review reason and policy are required")
        if self.session.get(DependencyCandidateRecord, candidate_id) is None:
            raise NotFoundError("dependency candidate not found")
        existing = self.session.scalar(
            select(DependencyReviewDecisionRecord).where(
                DependencyReviewDecisionRecord.dependency_candidate_id == candidate_id
            )
        )
        if existing is not None:
            if (
                existing.decision != decision
                or existing.reviewer_id != reviewer_id
                or existing.reason != reason
                or existing.policy_version != policy_version
            ):
                raise ConflictError(
                    "dependency candidate was already reviewed",
                    error_code="DEPENDENCY_REVIEW_CONFLICT",
                )
            return True
        self.session.add(
            DependencyReviewDecisionRecord(
                dependency_candidate_id=candidate_id,
                decision=decision,
                reviewer_id=reviewer_id,
                reason=reason,
                policy_version=policy_version,
                decided_at=decided_at,
            )
        )
        self.session.flush()
        return False

    def freeze_reviewed_dependencies(
        self, build_run_id: int, graph_version_id: int
    ) -> int:
        analysis = self.session.scalar(
            select(DependencyAnalysisRunRecord)
            .where(DependencyAnalysisRunRecord.build_run_id == build_run_id)
            .order_by(DependencyAnalysisRunRecord.id.desc())
        )
        if analysis is None:
            return 0
        candidates = self.session.scalars(
            select(DependencyCandidateRecord).where(
                DependencyCandidateRecord.analysis_run_id == analysis.id
            )
        ).all()
        decisions = {
            row.dependency_candidate_id: row
            for row in self.session.scalars(
                select(DependencyReviewDecisionRecord).where(
                    DependencyReviewDecisionRecord.dependency_candidate_id.in_(
                        [candidate.id for candidate in candidates]
                    )
                )
            ).all()
        } if candidates else {}
        if len(decisions) != len(candidates):
            raise ConflictError(
                "all dependency candidates must be reviewed before graph publication",
                error_code="DEPENDENCY_REVIEW_INCOMPLETE",
            )
        saved = 0
        for candidate in candidates:
            decision = decisions[candidate.id]
            if decision.decision != "accept":
                continue
            self.session.add(
                GraphVersionDependencyRecord(
                    graph_version_id=graph_version_id,
                    dependency_candidate_id=candidate.id,
                    review_decision_id=decision.id,
                    prerequisite_skill_id=candidate.prerequisite_skill_id,
                    advanced_skill_id=candidate.advanced_skill_id,
                    metrics=dict(candidate.metrics),
                    evidence_ids=list(candidate.evidence_ids),
                    claim_kind="reviewed",
                    policy_version=decision.policy_version,
                )
            )
            saved += 1
        self.session.flush()
        return saved

    def load_projection_facts(self, graph_version_id: int) -> ProjectionFacts:
        version = self.session.get(GraphVersion, graph_version_id)
        if version is None:
            raise NotFoundError("graph version not found")
        watermark = self.load_build_watermark(version.build_run_id)
        claims = tuple(
            _claim(row)
            for row in self.session.scalars(
                select(RelationClaimRecord)
                .where(RelationClaimRecord.graph_version_id == graph_version_id)
                .order_by(RelationClaimRecord.claim_id)
            ).all()
        )
        claim_contexts = {
            (claim.source_fact_id, claim.requirement_id) for claim in claims
        }
        mapping_candidates = tuple(
            candidate
            for candidate in (
                _mapping_candidate(row)
                for row in self.session.scalars(
                select(MappingCandidateRecord)
                .where(MappingCandidateRecord.status.in_(PROJECTION_MAPPING_STATUSES))
                .order_by(MappingCandidateRecord.candidate_id)
                ).all()
            )
            if claim_contexts.intersection(
                (context.source_fact_id, context.requirement_id)
                for context in candidate.affected_contexts
            )
        )
        dependencies = tuple(
            DependencyCandidate(
                prerequisite_skill_id=row.prerequisite_skill_id,
                advanced_skill_id=row.advanced_skill_id,
                evidence_ids=tuple(row.evidence_ids),
                claim_kind="reviewed",
                **dict(row.metrics),
            )
            for row in self.session.scalars(
                select(GraphVersionDependencyRecord)
                .where(GraphVersionDependencyRecord.graph_version_id == graph_version_id)
                .order_by(
                    GraphVersionDependencyRecord.prerequisite_skill_id,
                    GraphVersionDependencyRecord.advanced_skill_id,
                )
            ).all()
        )
        return ProjectionFacts(
            graph_version_id,
            version.source_version,
            watermark,
            claims,
            mapping_candidates,
            dependencies,
        )

    def save_projection(self, projection: GraphProjection) -> SavedProjection:
        manifest = projection.manifest
        existing = self.session.scalar(
            select(ProjectionManifestRecord).where(
                ProjectionManifestRecord.graph_version_id == manifest.graph_version_id,
                ProjectionManifestRecord.projection_version == manifest.projection_version,
            )
        )
        if existing is not None:
            if existing.source_version != manifest.source_version:
                raise ConcurrentInnovationWrite("projection manifest conflict")
            return SavedProjection(existing.id, projection)
        payload = {
            "nodes": [asdict(item) for item in projection.nodes],
            "edges": [asdict(item) for item in projection.edges],
        }
        row = ProjectionManifestRecord(
            graph_version_id=manifest.graph_version_id,
            projection_version=manifest.projection_version,
            watermark_lineage_version=manifest.watermark_lineage_version,
            node_count=manifest.node_count,
            edge_count=manifest.edge_count,
            source_version=manifest.source_version,
            payload=payload,
        )
        self.session.add(row)
        self.session.flush()
        return SavedProjection(row.id, projection)
