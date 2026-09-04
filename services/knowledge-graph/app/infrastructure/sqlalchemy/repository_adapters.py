"""SQLAlchemy repository adapters for fact loading and persistence operations."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

from jobgraph_contracts.catalog import StandardSkillSnapshotV1, StandardSkillSnapshotV2
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.application.errors import (
    ConcurrentFactWrite,
    ConcurrentReviewTaskWrite,
    ConcurrentSkillResolution,
    DuplicateBuildRun,
    DuplicateFactVersion,
    NotFoundError,
    RelationEditConflictError,
    ValidationError,
)
from app.audit import AuditService
from app.config import settings
from app.domain.auditing import AuditRecord
from app.domain.authoritative_writes import AuthoritativeWriteFacts
from app.domain.graph_drafts import GraphDraftPlan, GraphDraftResult
from app.domain.lineage import PublishedFactLineage, lineage_lineage_version
from app.domain.published_facts import (
    PublishedFactIdentity,
    PublishedFactImportPlan,
    PublishedFactRecord,
    PublishedFactValidationFacts,
)
from app.domain.relation_editing import (
    RelationEditFacts,
    RelationEditPlan,
    RelationEditResult,
)
from app.domain.review_tasks import (
    NewReviewTaskPlan,
    ReviewTaskDedupFacts,
    ReviewTaskDedupKey,
    ReviewTaskEventPlan,
    ReviewTaskFacts,
    ReviewTaskMergePlan,
    ReviewTaskPlan,
    ReviewTaskResult,
)
from app.domain.skill_resolution import (
    NormalizedSkillTargetFact,
    SkillCatalogFact,
    SkillResolutionCommand,
    SkillResolutionFacts,
    SkillResolutionItemFact,
    SkillResolutionPlan,
    SkillResolutionResult,
)
from app.domain.structured_facts import (
    ExtractionFacts,
    JDDocumentInput,
    NormalizationFacts,
    PublishedJDFact,
    SavedExtractionFacts,
    SavedNormalizationFacts,
)
from app.domain.value_types import AuditSnapshot
from app.domain.versioning import (
    ExistingGraphVersion,
    GraphVersionDependencies,
    RollbackVersionFacts,
)
from app.domain.workflow_facts import (
    DocumentTextFacts,
    QualityAssessmentPlan,
    QualityFacts,
)
from app.domain.write_models import (
    AlgorithmConfigResult,
    AlgorithmConfigUpdate,
)
from app.infrastructure.sqlalchemy.fact_mappers import (
    latest_record,
    load_structured_extraction,
    persist_extracted,
    persist_normalized,
)
from app.infrastructure.sqlalchemy.graph_draft_mappers import load_graph_draft_facts
from app.infrastructure.sqlalchemy.graph_persistence import (
    load_publish_version_facts,
)
from app.infrastructure.sqlalchemy.structured_fact_mappers import (
    extraction_facts,
    extraction_schema,
    normalization_facts,
    normalization_schema,
    published_fact_payload,
)
from app.models import (
    AlgorithmConfig,
    DuplicateCluster,
    GraphBuildRun,
    GraphBuildSample,
    GraphVersion,
    JDDocument,
    JDExtractionRecord,
    JDNormalizedRecord,
    JDQualityAssessment,
    NormalizedRequirementRecord,
    NormalizedSkillRecord,
    PositionRequirementAggregateDraft,
    PositionSkillRelationDraft,
    PositionSkillSupport,
    PositionTaskAggregateDraft,
    PublishedFactImport,
    PublishedFactLineageRecord,
    ReviewTask,
    ReviewTaskEvent,
    Skill,
    SkillAlias,
    SkillClassification,
    SkillTaxonomyNode,
    StandardPosition,
    UnresolvedNormalizationItem,
)
from app.schemas.extraction import JDExtractionResult
from app.schemas.normalization import JDNormalizedResult


class SqlAlchemyDocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def import_document(self, input_document: JDDocumentInput) -> str:
        values = asdict(input_document)
        document_id = values.pop("document_id", None) or f"JD_{uuid.uuid4().hex[:10]}"
        document = JDDocument(document_id=document_id, **values)
        self.session.add(document)
        self.session.flush()
        return document.document_id

    def upsert_document(self, input_document: JDDocumentInput) -> str:
        values = asdict(input_document)
        document_id = values.pop("document_id", None) or f"JD_{uuid.uuid4().hex[:10]}"
        document = self.session.scalar(
            select(JDDocument).where(JDDocument.document_id == document_id)
        )
        if document is None:
            document = JDDocument(document_id=document_id, **values)
            self.session.add(document)
        else:
            for key, value in values.items():
                setattr(document, key, value)
        self.session.flush()
        return document.document_id

    def load_authoritative_write_facts(
        self, document_id: str
    ) -> AuthoritativeWriteFacts:
        document = self.session.scalar(
            select(JDDocument).where(JDDocument.document_id == document_id)
        )
        return AuthoritativeWriteFacts(
            document_id,
            document is not None,
            document.fact_authority if document is not None else None,
        )


class SqlAlchemyPublishedFactRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_validation_facts(
        self, fact: PublishedJDFact, lineage: PublishedFactLineage
    ) -> PublishedFactValidationFacts:
        identity = PublishedFactIdentity(fact.source_system, fact.source_fact_id)
        existing = self.session.scalar(
            select(PublishedFactImport).where(
                PublishedFactImport.source_system == identity.source_system,
                PublishedFactImport.source_fact_id == identity.source_fact_id,
                PublishedFactImport.source_fact_version == fact.source_fact_version,
            )
        )
        current = self.session.scalar(
            select(JDDocument).where(
                JDDocument.source_system == identity.source_system,
                JDDocument.source_fact_id == identity.source_fact_id,
                JDDocument.fact_authority == "authoritative",
            )
        )
        existing_lineage = (
            self.session.scalar(
                select(PublishedFactLineageRecord).where(
                    PublishedFactLineageRecord.published_fact_import_id
                    == existing.id
                )
            )
            if existing is not None
            else None
        )
        return PublishedFactValidationFacts(
            fact,
            (
                PublishedFactRecord(
                    existing.document_id,
                    identity,
                    existing.source_fact_version,
                    existing.source_version,
                    (
                        existing_lineage.lineage_lineage_version
                        if existing_lineage is not None
                        else None
                    ),
                )
                if existing is not None
                else None
            ),
            (
                PublishedFactRecord(
                    current.document_id,
                    identity,
                    current.source_fact_version,
                    current.source_version,
                )
                if current is not None and current.source_fact_version
                else None
            ),
            lineage,
        )

    def save_import_plan(self, plan: PublishedFactImportPlan) -> str:
        try:
            return self._save_import_plan(plan)
        except IntegrityError as exc:
            raise DuplicateFactVersion(
                "published JD fact version was imported concurrently"
            ) from exc
        except OperationalError as exc:
            raise ConcurrentFactWrite(
                "published JD fact projection is locked by a concurrent writer"
            ) from exc

    def _save_import_plan(self, plan: PublishedFactImportPlan) -> str:
        fact = plan.fact
        payload = published_fact_payload(fact)
        document_id = fact.source_jd_id
        extraction = JDExtractionResult.model_validate(payload["extraction_fact"])
        normalized = JDNormalizedResult.model_validate(payload["normalized_fact"])
        published_at = datetime.fromisoformat(
            fact.published_at.replace("Z", "+00:00")
        )
        document = self.session.scalar(
            select(JDDocument).where(JDDocument.document_id == document_id)
        )
        trace = payload.get("trace_metadata") or {}
        source_observed_at = trace.get("source_observed_at")
        if source_observed_at:
            observed_text = str(source_observed_at)
            if len(observed_text) == 10:
                observed_text = f"{observed_text}T00:00:00+00:00"
            document_published_at = datetime.fromisoformat(
                observed_text.replace("Z", "+00:00")
            )
        else:
            document_published_at = published_at
        values = {
            "raw_text": "",
            "source_type": "authoritative_import",
            "source_name": trace.get("source_name"),
            "enterprise_name": str(trace.get("enterprise_id") or "") or None,
            "published_at": document_published_at,
            "source_credibility": 1.0,
            "is_synthetic": False,
            "source_system": plan.identity.source_system,
            "fact_authority": "authoritative",
            "source_fact_id": plan.identity.source_fact_id,
            "source_fact_version": plan.version.raw,
            "source_schema_version": fact.schema_version,
            "source_version": plan.source_version,
        }
        if document is None:
            document = JDDocument(document_id=document_id, **values)
            self.session.add(document)
        else:
            for name, value in values.items():
                setattr(document, name, value)
        self.session.flush()

        extraction_record = JDExtractionRecord(
            document_id=document_id, payload=extraction.model_dump(mode="json"),
            status="authoritative_imported", confirmed=True,
        )
        self.session.add(extraction_record)
        self.session.flush()
        persist_extracted(self.session, extraction)
        persist_normalized(self.session, normalized)
        quality = self.session.scalar(select(JDQualityAssessment).where(
            JDQualityAssessment.document_id == document_id
        ))
        if quality is None:
            self.session.add(JDQualityAssessment(
                document_id=document_id, duplicate_score=0, copy_risk_score=0,
                inflation_score=0, effective_sample_weight=1, assessed=True,
            ))
        else:
            quality.effective_sample_weight = 1
            quality.assessed = True
        imported = PublishedFactImport(
            source_system=plan.identity.source_system,
            source_fact_id=plan.identity.source_fact_id,
            source_fact_version=plan.version.raw,
            source_schema_version=fact.schema_version,
            source_version=plan.source_version,
            document_id=document_id,
            published_at=published_at, payload=payload,
        )
        self.session.add(imported)
        self.session.flush()
        lineage_version = lineage_lineage_version(plan.lineage)
        if lineage_version is not None:
            validation = plan.lineage.validation
            catalog = plan.lineage.catalog
            self.session.add(PublishedFactLineageRecord(
                published_fact_import_id=imported.id,
                lineage_lineage_version=lineage_version,
                data_validation_task_id=(
                    validation.data_validation_task_id if validation else None
                ),
                validation_report_id=(
                    validation.validation_report_id if validation else None
                ),
                validated_bundle_snapshot_id=(
                    validation.validated_bundle_snapshot_id if validation else None
                ),
                validation_policy_version=(
                    validation.validation_policy_version if validation else None
                ),
                validation_conclusion=(
                    validation.validation_conclusion if validation else None
                ),
                bundle_lineage_version=(
                    validation.bundle_lineage_version if validation else None
                ),
                catalog_source=catalog.source if catalog else None,
                catalog_version=catalog.catalog_version if catalog else None,
                catalog_source_version=catalog.source_version if catalog else None,
                catalog_effective_at=catalog.effective_at if catalog else None,
                catalog_status=catalog.status if catalog else None,
            ))
            self.session.flush()
        return document_id


class SqlAlchemyExtractionRepository:
    def __init__(self, session: Session):
        self.session = session

    def document_facts(self, document_id: str) -> DocumentTextFacts:
        document = self.session.scalar(
            select(JDDocument).where(JDDocument.document_id == document_id)
        )
        if document is None:
            raise NotFoundError("JD not found")
        return DocumentTextFacts(
            document.document_id, document.raw_text, document.source_credibility
        )

    def save_generated(self, document_id: str, job_title: str) -> ExtractionFacts:
        title = job_title
        payload = JDExtractionResult(document_id=document_id, job_title={
            "text": title,
            "evidence": {"source_id": document_id, "quote": title, "alignment": "unresolved"},
        }).model_dump(mode="json")
        record = JDExtractionRecord(document_id=document_id, payload=payload)
        self.session.add(record); self.session.flush()
        persist_extracted(self.session, JDExtractionResult.model_validate(payload))
        return extraction_facts(JDExtractionResult.model_validate(payload))

    def save_imported(
        self, document_id: str, facts: ExtractionFacts
    ) -> SavedExtractionFacts:
        result = extraction_schema(facts)
        if result.document_id != document_id:
            raise ValidationError("document_id mismatch")
        if not self.session.scalar(
            select(JDDocument.id).where(JDDocument.document_id == document_id)
        ):
            raise NotFoundError("JD not found")
        record = JDExtractionRecord(
            document_id=document_id,
            payload=result.model_dump(mode="json"),
            status="imported",
        )
        self.session.add(record)
        self.session.flush()
        persist_extracted(self.session, result)
        return SavedExtractionFacts(record.id)

    def structured_facts(self, document_id: str) -> ExtractionFacts:
        self.document_facts(document_id)
        record = latest_record(self.session, JDExtractionRecord, document_id)
        if not record:
            raise NotFoundError("extraction not found")
        return extraction_facts(load_structured_extraction(self.session, document_id))

    def save_confirmed(
        self, document_id: str, facts: ExtractionFacts
    ) -> ExtractionFacts:
        record = latest_record(self.session, JDExtractionRecord, document_id)
        if not record:
            raise NotFoundError("extraction not found")
        record.status = "aligned"
        record.confirmed = True
        persist_extracted(self.session, extraction_schema(facts))
        return facts


class SqlAlchemyNormalizationRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_structured_facts(self, document_id: str) -> ExtractionFacts:
        if not latest_record(self.session, JDExtractionRecord, document_id):
            raise NotFoundError("extraction not found")
        return extraction_facts(load_structured_extraction(self.session, document_id))

    def save_result(
        self, document_id: str, facts: NormalizationFacts
    ) -> SavedNormalizationFacts:
        result = normalization_schema(facts)
        if result.document_id != document_id:
            raise ValidationError("document_id mismatch")
        if not latest_record(self.session, JDExtractionRecord, document_id):
            raise NotFoundError("extraction not found")
        saved = persist_normalized(self.session, result)
        return SavedNormalizationFacts(saved.id, normalization_facts(result))

    def load_skill_resolution_facts(
        self, item_id: int, command: SkillResolutionCommand
    ) -> SkillResolutionFacts:
        item = self.session.get(UnresolvedNormalizationItem, item_id)
        if not item:
            raise NotFoundError("unresolved item not found")
        normalized_record = latest_record(
            self.session, JDNormalizedRecord, item.document_id
        )
        if not normalized_record:
            raise NotFoundError("normalization not found")
        normalized_skill = None
        if item.item_type == "skill":
            record = self.session.scalar(
                select(NormalizedSkillRecord)
                .join(NormalizedRequirementRecord)
                .where(
                    NormalizedRequirementRecord.normalized_record_id
                    == normalized_record.id,
                    NormalizedSkillRecord.source_name == item.source_name,
                )
            )
            if record is not None:
                normalized_skill = NormalizedSkillTargetFact(record.id, record.source_name)
        requested_id = command.skill_id or command.generated_skill_id
        skill = (
            self.session.scalar(select(Skill).where(Skill.skill_id == command.skill_id))
            if command.skill_id
            else None
        )
        requested_exists = bool(
            requested_id
            and self.session.scalar(select(Skill.id).where(Skill.skill_id == requested_id))
        )
        return SkillResolutionFacts(
            SkillResolutionItemFact(
                item.id,
                item.document_id,
                item.item_type,
                item.source_name,
                item.status,
                item.resolution or {},
            ),
            normalized_skill,
            (
                SkillCatalogFact(
                    skill.skill_id,
                    skill.canonical_name,
                    skill.category_code,
                    skill.subcategory_code,
                )
                if skill is not None
                else None
            ),
            requested_exists,
        )

    def apply_skill_resolution_plan(
        self, plan: SkillResolutionPlan
    ) -> SkillResolutionResult:
        if plan.create_skill is not None:
            create = plan.create_skill
            self.session.add(
                Skill(
                    skill_id=create.skill_id,
                    canonical_name=create.canonical_name,
                    category_code=create.category_code,
                    subcategory_code=create.subcategory_code,
                    status="active",
                )
            )
            self.session.flush()
            self.session.add(SkillAlias(skill_id=create.skill_id, alias=create.alias))
        if plan.normalized_skill_id is not None and plan.resolved_skill is not None:
            skill = plan.resolved_skill
            result = self.session.execute(
                update(NormalizedSkillRecord)
                .where(NormalizedSkillRecord.id == plan.normalized_skill_id)
                .values(
                    skill_id=skill.skill_id,
                    canonical_name=skill.canonical_name,
                    category_code=skill.category_code,
                    subcategory_code=skill.subcategory_code,
                    resolution_status=plan.normalized_skill_status,
                )
            )
            if result.rowcount != 1:
                raise NotFoundError("normalized skill not found")
        updated = self.session.execute(
            update(UnresolvedNormalizationItem)
            .where(
                UnresolvedNormalizationItem.id == plan.item_id,
                UnresolvedNormalizationItem.status == plan.expected_item_status,
            )
            .values(
                status=plan.target_item_status,
                resolution=dict(plan.resolution),
                reviewer_id=plan.actor_id,
                reviewed_at=datetime.now(timezone.utc),
                review_reason=plan.reviewed_reason,
            )
        )
        if updated.rowcount != 1:
            raise ConcurrentSkillResolution("unresolved item was already processed")
        return SkillResolutionResult(
            plan.item_id,
            plan.target_item_status,
            plan.resolved_skill.skill_id if plan.resolved_skill else None,
            plan.resolved_skill.canonical_name if plan.resolved_skill else None,
        )


class SqlAlchemyGraphDraftRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _draft_record(run: GraphBuildRun) -> GraphDraftResult:
        return GraphDraftResult(run.id, run.position_id, run.base_version_id)

    def load_graph_draft_facts(self, position_id, base_version_id=None):
        return load_graph_draft_facts(self.session, position_id, base_version_id)

    def find_graph_draft(self, draft_key: str) -> GraphDraftResult | None:
        run = self.session.scalar(
            select(GraphBuildRun).where(GraphBuildRun.active_draft_key == draft_key)
        )
        return self._draft_record(run) if run is not None else None

    def save_graph_draft_plan(self, plan: GraphDraftPlan) -> GraphDraftResult:
        run = GraphBuildRun(
            position_id=plan.position_id,
            base_version_id=plan.base_version_id,
            active_draft_key=plan.draft_key,
            status=plan.status,
            window_start=plan.window_start,
            window_end=plan.window_end,
            config_snapshot=dict(plan.config_snapshot),
            summary=dict(plan.summary),
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
        except IntegrityError as exc:
            raise DuplicateBuildRun("graph draft was created concurrently") from exc
        for sample in plan.copy.samples:
            self.session.add(
                GraphBuildSample(
                    build_run_id=run.id,
                    document_id=sample.document_id,
                    included=sample.included,
                    exclusion_reasons=list(sample.exclusion_reasons),
                    effective_weight=sample.effective_weight,
                )
            )
        for support in plan.copy.supports:
            self.session.add(
                PositionSkillSupport(
                    build_run_id=run.id,
                    position_id=support.position_id,
                    skill_id=support.skill_id,
                    document_id=support.document_id,
                    requirement_id=support.requirement_id,
                    normalized_skill_id=support.normalized_skill_id,
                    evidence_id=support.evidence_id,
                    source_requirement_id=support.source_requirement_id,
                    extraction_record_id=support.extraction_record_id,
                    modality=support.modality,
                )
            )
        for item in plan.copy.relations:
            self.session.add(PositionSkillRelationDraft(
                build_run_id=run.id,
                position_id=plan.position_id,
                skill_id=item.skill_id,
                status=item.status,
                metrics=dict(item.metrics),
                statistics=dict(item.statistics),
                explanation=dict(item.explanation),
                auto_weight=item.auto_weight,
                manual_weight=item.manual_weight,
                final_weight=item.final_weight,
                auto_confidence=item.auto_confidence,
                manual_confidence=item.manual_confidence,
                final_confidence=item.final_confidence,
                auto_importance_level=item.auto_importance_level,
                manual_importance_level=item.manual_importance_level,
                final_importance_level=item.final_importance_level,
                trend_score=item.trend_score,
            ))
        for item in plan.copy.requirements:
            self.session.add(PositionRequirementAggregateDraft(
                build_run_id=run.id, kind=item.kind, payload=dict(item.payload)
            ))
        for item in plan.copy.tasks:
            self.session.add(PositionTaskAggregateDraft(
                build_run_id=run.id, payload=dict(item.payload)
            ))
        self.session.flush()
        return self._draft_record(run)

    def load_relation_edit_facts(self, relation_id: int) -> RelationEditFacts:
        relation = self.session.get(PositionSkillRelationDraft, relation_id)
        if relation is None:
            return RelationEditFacts(
                relation_id, False, None, None, False, None, False, 0, "", 0,
                None, 0, 0, None, 0, "", None, "",
            )
        run = self.session.get(GraphBuildRun, relation.build_run_id)
        published = self.session.scalar(
            select(GraphVersion.id).where(GraphVersion.build_run_id == relation.build_run_id)
        ) is not None
        return RelationEditFacts(
            relation.id, True, relation.build_run_id, relation.position_id,
            run is not None, run.position_id if run else None, published,
            relation.revision, relation.status, relation.auto_weight,
            relation.manual_weight, relation.final_weight,
            relation.auto_confidence, relation.manual_confidence,
            relation.final_confidence, relation.auto_importance_level,
            relation.manual_importance_level, relation.final_importance_level,
        )

    def apply_relation_edit_plan(self, plan: RelationEditPlan) -> RelationEditResult:
        result = self.session.execute(
            update(PositionSkillRelationDraft)
            .where(
                PositionSkillRelationDraft.id == plan.relation_id,
                PositionSkillRelationDraft.revision == plan.expected_revision,
            )
            .values(
                manual_weight=plan.manual_weight,
                final_weight=plan.final_weight,
                manual_confidence=plan.manual_confidence,
                final_confidence=plan.final_confidence,
                manual_importance_level=plan.manual_importance_level,
                final_importance_level=plan.final_importance_level,
                status=plan.target_status,
                revision=plan.next_revision,
            )
        )
        if result.rowcount != 1:
            self.session.expire_all()
            current_revision = self.session.scalar(select(
                PositionSkillRelationDraft.revision
            ).where(PositionSkillRelationDraft.id == plan.relation_id))
            raise RelationEditConflictError(current_revision=current_revision or 1)
        relation = self.session.get(PositionSkillRelationDraft, plan.relation_id)
        self.session.refresh(relation)
        return RelationEditResult(
            relation.id,
            relation.build_run_id,
            relation.build_run_id,
            relation.position_id,
            relation.auto_importance_level,
            relation.manual_importance_level,
            relation.final_importance_level,
            relation.final_weight,
            relation.final_confidence,
            relation.auto_weight,
            relation.manual_weight,
            relation.auto_confidence,
            relation.manual_confidence,
            relation.revision,
        )


class SqlAlchemyGraphVersionRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_publish_facts(self, run_id: int):
        run = self.session.get(GraphBuildRun, run_id)
        if not run:
            raise NotFoundError("build run not found")
        return load_publish_version_facts(self.session, run)

    def find_by_build_run(self, run_id: int):
        version = self.session.scalar(
            select(GraphVersion).where(GraphVersion.build_run_id == run_id)
        )
        return ExistingGraphVersion(version.id, version.version_number) if version else None

    def save_published(self, plan, actor_id):
        run = self.session.get(GraphBuildRun, plan.run_id)
        position = self.session.scalar(
            select(StandardPosition)
            .where(StandardPosition.position_id == plan.position_id)
            .with_for_update()
        )
        try:
            with self.session.begin_nested():
                version = GraphVersion(
                    position_id=plan.position_id, build_run_id=plan.run_id,
                    release_id=run.release_id,
                    version_number=plan.version_number,
                    version_name=plan.version_name, snapshot=dict(plan.snapshot),
                    source_version=plan.dependencies.build_config_version,
                    algorithm_version=plan.algorithm_version,
                    normalization_map_version=settings.normalization_map_version,
                    published_fact_versions=list(plan.dependencies.published_fact_versions),
                    skill_catalog_version=plan.dependencies.skill_catalog_version,
                    mapping_snapshot_version=plan.dependencies.mapping_snapshot_version,
                    normalization_algorithm_version=plan.dependencies.normalization_algorithm_version,
                    build_config_version=plan.dependencies.build_config_version,
                    source_time_window=dict(plan.dependencies.source_time_window),
                    published_by=actor_id,
                )
                self.session.add(version)
                self.session.flush()
        except IntegrityError as exc:
            raise DuplicateBuildRun("graph build was published concurrently") from exc
        position.current_version_id = version.id
        run.status = "published"
        run.active_draft_key = None
        self.session.flush()
        return ExistingGraphVersion(version.id, version.version_number)

    def load_rollback_facts(self, position_id, version_id):
        source = self.session.get(GraphVersion, version_id)
        if not source:
            raise NotFoundError("version not found")
        if source.position_id != position_id:
            raise ValidationError("version does not belong to position")
        position = self.session.scalar(
            select(StandardPosition).where(StandardPosition.position_id == position_id)
        )
        latest = self.session.scalar(
            select(GraphVersion).where(GraphVersion.position_id == position_id)
            .order_by(GraphVersion.version_number.desc())
        )
        return RollbackVersionFacts(
            source.id, source.position_id,
            position.current_version_id if position else None,
            latest.version_number, deepcopy(source.snapshot),
            source.algorithm_version, source.normalization_map_version,
            GraphVersionDependencies(
                tuple(source.published_fact_versions),
                source.skill_catalog_version,
                source.mapping_snapshot_version,
                source.normalization_algorithm_version,
                source.build_config_version,
                dict(source.source_time_window),
            ),
        )

    def save_rollback(self, plan, actor_id):
        position = self.session.scalar(
            select(StandardPosition)
            .where(StandardPosition.position_id == plan.position_id)
            .with_for_update()
        )
        source = self.session.get(GraphVersion, plan.source_version_id)
        if source is None:
            raise NotFoundError("version not found")
        source_run = self.session.get(GraphBuildRun, source.build_run_id)
        thresholds = (
            (source_run.config_snapshot or {}).get("position_profile_thresholds", {})
            if source_run is not None
            else {}
        )
        document_deduplication = (
            (source_run.config_snapshot or {}).get("document_deduplication", {})
            if source_run is not None
            else {}
        )
        run = GraphBuildRun(
            position_id=plan.position_id, base_version_id=plan.base_version_id,
            release_id=source.release_id,
            status="published",
            config_snapshot={"rollback_source_version_id": plan.source_version_id,
                             "algorithm_version": plan.algorithm_version,
                             "position_profile_thresholds": thresholds,
                             "document_deduplication": document_deduplication},
            summary={"rollback": True},
        )
        self.session.add(run)
        self.session.flush()
        if source_run is not None:
            for sample in self.session.scalars(
                select(GraphBuildSample).where(
                    GraphBuildSample.build_run_id == source_run.id
                )
            ).all():
                self.session.add(
                    GraphBuildSample(
                        build_run_id=run.id,
                        document_id=sample.document_id,
                        included=sample.included,
                        exclusion_reasons=list(sample.exclusion_reasons),
                        effective_weight=sample.effective_weight,
                    )
                )
            for support in self.session.scalars(
                select(PositionSkillSupport).where(
                    PositionSkillSupport.build_run_id == source_run.id
                )
            ).all():
                self.session.add(
                    PositionSkillSupport(
                        build_run_id=run.id,
                        position_id=support.position_id,
                        skill_id=support.skill_id,
                        document_id=support.document_id,
                        requirement_id=support.requirement_id,
                        normalized_skill_id=support.normalized_skill_id,
                        evidence_id=support.evidence_id,
                        source_requirement_id=support.source_requirement_id,
                        extraction_record_id=support.extraction_record_id,
                        modality=support.modality,
                    )
                )
        self.session.flush()
        version = GraphVersion(
            position_id=plan.position_id, build_run_id=run.id,
            release_id=source.release_id,
            version_number=plan.version_number, version_name=plan.version_name,
            snapshot=dict(plan.snapshot),
            source_version=source.source_version,
            algorithm_version=plan.algorithm_version,
            normalization_map_version=plan.normalization_map_version,
            published_fact_versions=list(plan.dependencies.published_fact_versions),
            skill_catalog_version=plan.dependencies.skill_catalog_version,
            mapping_snapshot_version=plan.dependencies.mapping_snapshot_version,
            normalization_algorithm_version=plan.dependencies.normalization_algorithm_version,
            build_config_version=plan.dependencies.build_config_version,
            source_time_window=dict(plan.dependencies.source_time_window),
            rollback_from_version_id=plan.source_version_id,
            published_by=actor_id,
        )
        self.session.add(version)
        self.session.flush()
        position.current_version_id = version.id
        self.session.flush()
        return ExistingGraphVersion(version.id, version.version_number)


class SqlAlchemyReviewTaskRepository:
    def __init__(self, session: Session):
        self.session = session

    def save_new_task(self, plan: NewReviewTaskPlan) -> ReviewTaskResult:
        task = ReviewTask(
            object_type=plan.object_type,
            object_id=plan.object_id,
            build_run_id=plan.build_run_id,
            status=plan.status,
            assignee_id=plan.assignee_id,
            payload=dict(plan.payload),
        )
        self.session.add(task)
        self.session.flush()
        return ReviewTaskResult(task.id, task.status)

    def save_new_tasks(
        self, plans: tuple[NewReviewTaskPlan, ...]
    ) -> tuple[ReviewTaskResult, ...]:
        if not plans:
            return ()
        rows = []
        for plan in plans:
            task = ReviewTask(
                object_type=plan.object_type,
                object_id=plan.object_id,
                build_run_id=plan.build_run_id,
                status=plan.status,
                assignee_id=plan.assignee_id,
                payload=dict(plan.payload),
            )
            self.session.add(task)
            rows.append(task)
        self.session.flush()
        return tuple(ReviewTaskResult(task.id, task.status) for task in rows)

    def load_review_task_dedup_facts(
        self, key: ReviewTaskDedupKey
    ) -> ReviewTaskDedupFacts:
        tasks = self.session.scalars(
            select(ReviewTask)
            .where(
                ReviewTask.object_type == key.object_type,
                ReviewTask.object_id == key.object_id,
                ReviewTask.build_run_id == key.build_run_id,
            )
            .order_by(ReviewTask.id)
        ).all()
        return ReviewTaskDedupFacts(
            key,
            tuple(
                ReviewTaskFacts(
                    task.id,
                    task.object_type,
                    task.object_id,
                    task.build_run_id,
                    task.status,
                    task.assignee_id,
                    dict(task.payload or {}),
                )
                for task in tasks
            ),
        )

    def load_review_task_dedup_facts_bulk(
        self, keys: tuple[ReviewTaskDedupKey, ...]
    ) -> Mapping[ReviewTaskDedupKey, ReviewTaskDedupFacts]:
        if not keys:
            return {}
        conditions = [
            and_(
                ReviewTask.object_type == key.object_type,
                ReviewTask.object_id == key.object_id,
                ReviewTask.build_run_id == key.build_run_id,
            )
            for key in keys
        ]
        rows = self.session.scalars(
            select(ReviewTask).where(or_(*conditions))
        ).all()
        grouped: dict[tuple[str, str, int | None], list[ReviewTask]] = {}
        for task in rows:
            grouped.setdefault(
                (task.object_type, task.object_id, task.build_run_id), []
            ).append(task)
        result = {}
        for key in keys:
            tasks = grouped.get(
                (key.object_type, key.object_id, key.build_run_id), []
            )
            result[key] = ReviewTaskDedupFacts(
                key,
                tuple(
                    ReviewTaskFacts(
                        task.id,
                        task.object_type,
                        task.object_id,
                        task.build_run_id,
                        task.status,
                        task.assignee_id,
                        dict(task.payload or {}),
                    )
                    for task in tasks
                ),
            )
        return result

    def load_review_tasks_by_build(
        self,
        build_run_id: int,
        statuses: tuple[str, ...] | None = None,
    ) -> tuple[ReviewTaskFacts, ...]:
        statement = (
            select(ReviewTask)
            .where(ReviewTask.build_run_id == build_run_id)
            .order_by(ReviewTask.id)
        )
        if statuses:
            statement = statement.where(ReviewTask.status.in_(statuses))
        return tuple(
            ReviewTaskFacts(
                task.id,
                task.object_type,
                task.object_id,
                task.build_run_id,
                task.status,
                task.assignee_id,
                dict(task.payload or {}),
            )
            for task in self.session.scalars(statement).all()
        )

    def apply_review_task_merge_plan(
        self, plan: ReviewTaskMergePlan
    ) -> ReviewTaskResult:
        assignee_condition = (
            ReviewTask.assignee_id.is_(None)
            if plan.expected_assignee_id is None
            else ReviewTask.assignee_id == plan.expected_assignee_id
        )
        result = self.session.execute(
            update(ReviewTask)
            .where(
                ReviewTask.id == plan.task_id,
                ReviewTask.status == plan.expected_status,
                assignee_condition,
            )
            .values(
                status=plan.target_status,
                assignee_id=plan.target_assignee_id,
                payload=dict(plan.payload),
            )
        )
        if result.rowcount != 1:
            raise ConcurrentReviewTaskWrite("review task changed concurrently")
        return ReviewTaskResult(plan.task_id, plan.target_status)

    def load_review_task_facts(self, task_id: int) -> ReviewTaskFacts:
        task = self.session.get(ReviewTask, task_id)
        if task is None:
            raise NotFoundError("review task not found")
        # Request-scoped adapters can share a Session in tests and embedded use.
        # Refresh so the state machine receives the committed CAS state, not an
        # identity-map snapshot left behind by a rejected concurrent command.
        self.session.refresh(task)
        return ReviewTaskFacts(
            task.id,
            task.object_type,
            task.object_id,
            task.build_run_id,
            task.status,
            task.assignee_id,
            dict(task.payload or {}),
        )

    def apply_review_task_plan(self, plan: ReviewTaskPlan) -> ReviewTaskResult:
        assignee_condition = (
            ReviewTask.assignee_id.is_(None)
            if plan.transition.expected_assignee_id is None
            else ReviewTask.assignee_id == plan.transition.expected_assignee_id
        )
        result = self.session.execute(
            update(ReviewTask)
            .where(
                ReviewTask.id == plan.task_id,
                ReviewTask.status == plan.transition.expected_status,
                assignee_condition,
            )
            .values(
                status=plan.transition.target_status,
                assignee_id=plan.transition.target_assignee_id,
                payload=dict(plan.payload),
            )
        )
        if result.rowcount != 1:
            raise ConcurrentReviewTaskWrite(plan.concurrency_message)
        return ReviewTaskResult(plan.task_id, plan.transition.target_status)

    def append_review_event(self, plan: ReviewTaskEventPlan) -> None:
        self.session.add(
            ReviewTaskEvent(
                task_id=plan.task_id,
                actor_id=plan.actor_id,
                action=plan.action,
                before=dict(plan.before),
                after=dict(plan.after),
                reason=plan.reason,
                trace_id=plan.trace_id,
            )
        )


class SqlAlchemyAuditRepository:
    def __init__(self, session: Session):
        self.session = session

    def record(self, record: AuditRecord) -> None:
        AuditService.record(
            self.session,
            actor_id=record.actor_id,
            action=record.action,
            object_type=record.object_type,
            object_id=record.object_id,
            before_snapshot=(
                dict(record.before_snapshot) if record.before_snapshot is not None else None
            ),
            after_snapshot=(
                dict(record.after_snapshot) if record.after_snapshot is not None else None
            ),
            reason=record.reason,
            trace_id=record.trace_id,
        )


class SqlAlchemyCatalogSnapshotRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_skill(
        self, snapshot: StandardSkillSnapshotV1 | StandardSkillSnapshotV2
    ) -> tuple[AuditSnapshot | None, AuditSnapshot]:
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_identity, 0))"
                ),
                {"lock_identity": f"kg-skill-catalog:{snapshot.skill_id}"},
            )
        skill = self.session.scalar(
            select(Skill).where(Skill.skill_id == snapshot.skill_id)
        )
        before = None
        if skill is None:
            skill = Skill(skill_id=snapshot.skill_id)
            self.session.add(skill)
        else:
            before = {
                'skill_id': skill.skill_id,
                'canonical_name': skill.canonical_name,
                'category_code': skill.category_code,
                'subcategory_code': skill.subcategory_code,
                'taxonomy_version': skill.taxonomy_version,
                'status': skill.status,
            }
        skill.canonical_name = snapshot.canonical_name
        if isinstance(snapshot, StandardSkillSnapshotV2):
            skill.category_code = None
            skill.subcategory_code = None
            skill.taxonomy_version = snapshot.taxonomy_version
            self.session.execute(
                delete(SkillClassification).where(
                    SkillClassification.skill_id == snapshot.skill_id
                )
            )
            for relation in snapshot.classifications:
                node = self.session.scalar(
                    select(SkillTaxonomyNode).where(
                        SkillTaxonomyNode.facet == relation.facet,
                        SkillTaxonomyNode.code == relation.code,
                    )
                )
                if node is None:
                    if relation.name_zh is None:
                        raise ValidationError(
                            "taxonomy node name_zh is required on first import"
                        )
                    node = SkillTaxonomyNode(
                        facet=relation.facet,
                        code=relation.code,
                        name_zh=relation.name_zh,
                        name_en=relation.name_en,
                    )
                    self.session.add(node)
                    self.session.flush()
                elif (
                    relation.name_zh is not None
                    and (
                        node.name_zh != relation.name_zh
                        or node.name_en != relation.name_en
                    )
                ):
                    raise ValidationError(
                        f"taxonomy node conflict: {relation.facet}:{relation.code}"
                    )
                self.session.add(
                    SkillClassification(
                        skill_id=snapshot.skill_id,
                        taxonomy_node_id=node.id,
                        facet=relation.facet,
                        is_primary=relation.is_primary,
                    )
                )
        else:
            skill.category_code = snapshot.category_code
            skill.subcategory_code = snapshot.subcategory_code
            skill.taxonomy_version = None
        skill.status = snapshot.status
        self.session.flush()
        self.session.execute(
            delete(SkillAlias).where(SkillAlias.skill_id == snapshot.skill_id)
        )
        for alias in sorted(set(snapshot.aliases)):
            self.session.add(SkillAlias(skill_id=snapshot.skill_id, alias=alias))
        after: AuditSnapshot = snapshot.model_dump(mode='json')
        return before, after


class SqlAlchemyQualityRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_facts(self, document_id: str) -> QualityFacts:
        document = self.session.scalar(select(JDDocument).where(JDDocument.document_id == document_id))
        if document is None:
            raise NotFoundError("JD not found")
        peers = self.session.scalars(
            select(JDDocument).where(JDDocument.document_id != document_id)
        ).all()
        clusters = self.session.scalars(select(DuplicateCluster)).all()
        cluster_ids_by_key = {
            cluster.cluster_key: set(cluster.document_ids)
            for cluster in clusters
        }
        existing_cluster_key = next(
            (
                key
                for key, document_ids in cluster_ids_by_key.items()
                if document_id in document_ids
            ),
            None,
        )
        peer_texts = tuple(peer.raw_text for peer in peers)
        peer_document_ids = tuple(peer.document_id for peer in peers)
        peer_cluster_keys = tuple(
            next(
                (
                    key
                    for key, document_ids in cluster_ids_by_key.items()
                    if peer.document_id in document_ids
                ),
                None,
            )
            for peer in peers
        )
        return QualityFacts(
            DocumentTextFacts(
                document.document_id, document.raw_text, document.source_credibility
            ),
            peer_texts,
            peer_document_ids,
            peer_cluster_keys,
            existing_cluster_key,
        )

    def save_assessment(self, plan: QualityAssessmentPlan) -> None:
        document_id = plan.document_id
        assessment = self.session.scalar(select(JDQualityAssessment).where(JDQualityAssessment.document_id == document_id))
        persisted = {
            "duplicate_score": plan.duplicate_score,
            "copy_risk_score": plan.copy_risk_score,
            "inflation_score": plan.inflation_score,
        }
        if assessment is None:
            assessment = JDQualityAssessment(document_id=document_id, **persisted, effective_sample_weight=plan.effective_sample_weight)
            self.session.add(assessment)
        else:
            for key, value in persisted.items(): setattr(assessment, key, value)
            assessment.effective_sample_weight = plan.effective_sample_weight
        clusters = self.session.scalars(select(DuplicateCluster)).all()
        if plan.duplicate_cluster_key:
            key = plan.duplicate_cluster_key
            current_cluster = next(
                (
                    cluster
                    for cluster in clusters
                    if document_id in set(cluster.document_ids)
                ),
                None,
            )
            peer_cluster = None
            if plan.duplicate_peer_document_id:
                peer_cluster = next(
                    (
                        cluster
                        for cluster in clusters
                        if cluster is not current_cluster
                        and plan.duplicate_peer_document_id in set(cluster.document_ids)
                    ),
                    None,
                )
            members = set(current_cluster.document_ids) if current_cluster else set()
            members.add(document_id)
            if plan.duplicate_peer_document_id:
                members.add(plan.duplicate_peer_document_id)
            if peer_cluster is not None:
                members.update(peer_cluster.document_ids)
            target = next(
                (cluster for cluster in clusters if cluster.cluster_key == key),
                None,
            )
            for cluster in clusters:
                if cluster is target:
                    continue
                remaining = [
                    item for item in cluster.document_ids if item not in members
                ]
                if len(remaining) != len(cluster.document_ids):
                    cluster.document_ids = remaining
            if target is None:
                self.session.add(
                    DuplicateCluster(
                        cluster_key=key,
                        document_ids=sorted(members),
                        score=plan.duplicate_score,
                    )
                )
            else:
                target.document_ids = list(
                    dict.fromkeys([*target.document_ids, *sorted(members)])
                )
                target.score = max(target.score, plan.duplicate_score)
        else:
            for cluster in clusters:
                remaining = [
                    item for item in cluster.document_ids if item != document_id
                ]
                if len(remaining) != len(cluster.document_ids):
                    cluster.document_ids = remaining
        for cluster in self.session.scalars(select(DuplicateCluster)).all():
            if len(cluster.document_ids) < 2:
                self.session.delete(cluster)


class SqlAlchemyAlgorithmConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_active(self) -> AlgorithmConfigResult | None:
        config = self.session.scalar(
            select(AlgorithmConfig).where(AlgorithmConfig.active.is_(True))
        )
        return AlgorithmConfigResult(config.id, config.version) if config else None

    def replace(self, update_value: AlgorithmConfigUpdate) -> AlgorithmConfigResult:
        before = self.session.scalar(select(AlgorithmConfig).where(AlgorithmConfig.active.is_(True)))
        if before: before.active = False
        values = {
            "version": update_value.version,
            "payload": dict(update_value.parameters),
            "active": update_value.active,
        }
        config = AlgorithmConfig(**values); self.session.add(config); self.session.flush()
        return AlgorithmConfigResult(config.id, config.version)
