"""Narrow persistence ports used by knowledge-graph application use cases."""

from collections.abc import Mapping
from typing import Protocol
from jobgraph_contracts.release_manifest import ReleaseManifestV1

from jobgraph_contracts.catalog import StandardSkillSnapshotV1, StandardSkillSnapshotV2

from app.domain.auditing import AuditRecord
from app.domain.build_jobs import BuildJobRecord
from app.domain.value_types import AuditSnapshot, SerializedPayload
from app.domain.authoritative_writes import AuthoritativeWriteFacts
from app.domain.graph_building import GraphBuildFacts, GraphBuildPlan, PersistedBuildRun
from app.domain.graph_drafts import GraphDraftFacts, GraphDraftPlan, GraphDraftResult
from app.domain.published_facts import (
    PublishedFactImportPlan,
    PublishedFactValidationFacts,
)
from app.domain.lineage import PublishedFactLineage
from app.domain.relation_editing import (
    RelationEditFacts,
    RelationEditPlan,
    RelationEditResult,
)
from app.domain.review_tasks import (
    NewReviewTaskPlan,
    ReviewObjectEffect,
    ReviewTaskDedupFacts,
    ReviewTaskDedupKey,
    ReviewTaskEventPlan,
    ReviewTaskFacts,
    ReviewTaskMergePlan,
    ReviewTaskPlan,
    ReviewTaskResult,
)
from app.domain.skill_resolution import (
    SkillResolutionCommand,
    SkillResolutionFacts,
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
from app.domain.versioning import (
    ExistingGraphVersion,
    PublishVersionFacts,
    PublishVersionPlan,
    RollbackVersionFacts,
    RollbackVersionPlan,
)
from app.domain.workflow_facts import DocumentTextFacts, QualityAssessmentPlan, QualityFacts
from app.domain.write_models import AlgorithmConfigResult, AlgorithmConfigUpdate
from app.domain.dependency_analysis import DependencyAnalysis, DependencyPolicy
from app.domain.innovation import (
    BuildWatermarkFacts,
    ClaimSourceFact,
    DependencyContextFacts,
    MappingCandidateState,
    ProjectionFacts,
    SavedDependencyAnalysis,
    SavedProjection,
)
from app.domain.projections import GraphProjection
from app.domain.temporal_analysis import BuildInputWatermark, WatermarkSourceFact
from app.domain.traceability import MappingReviewDecision, RelationClaim


class ExtractionRepository(Protocol):
    def document_facts(self, document_id: str) -> DocumentTextFacts: ...
    def save_generated(self, document_id: str, job_title: str) -> ExtractionFacts: ...
    def save_imported(
        self, document_id: str, facts: ExtractionFacts
    ) -> SavedExtractionFacts: ...
    def structured_facts(self, document_id: str) -> ExtractionFacts: ...
    def save_confirmed(
        self, document_id: str, facts: ExtractionFacts
    ) -> ExtractionFacts: ...


class CatalogSnapshotRepository(Protocol):
    def upsert_skill(
        self, snapshot: StandardSkillSnapshotV1 | StandardSkillSnapshotV2
    ) -> tuple[AuditSnapshot | None, AuditSnapshot]: ...


class NormalizationRepository(Protocol):
    def load_structured_facts(self, document_id: str) -> ExtractionFacts: ...
    def save_result(
        self, document_id: str, facts: NormalizationFacts
    ) -> SavedNormalizationFacts: ...
    def load_skill_resolution_facts(
        self, item_id: int, command: SkillResolutionCommand
    ) -> SkillResolutionFacts: ...
    def apply_skill_resolution_plan(
        self, plan: SkillResolutionPlan
    ) -> SkillResolutionResult: ...


class QualityRepository(Protocol):
    def load_facts(self, document_id: str) -> QualityFacts: ...
    def save_assessment(self, plan: QualityAssessmentPlan) -> None: ...


class NormalizationProvider(Protocol):
    def produce(self, facts: ExtractionFacts) -> NormalizationFacts: ...


class GraphVersionRepository(Protocol):
    def load_publish_facts(self, run_id: int) -> PublishVersionFacts: ...
    def find_by_build_run(self, run_id: int) -> ExistingGraphVersion | None: ...
    def save_published(
        self, plan: PublishVersionPlan, actor_id: int
    ) -> ExistingGraphVersion: ...
    def load_rollback_facts(
        self, position_id: str, version_id: int
    ) -> RollbackVersionFacts: ...
    def save_rollback(
        self, plan: RollbackVersionPlan, actor_id: int
    ) -> ExistingGraphVersion: ...


class GraphBuildRepository(Protocol):
    def load_facts(
        self, position_id: str, *, authoritative_only: bool
    ) -> GraphBuildFacts: ...
    def save_plan(self, plan: GraphBuildPlan) -> PersistedBuildRun: ...


class BuildJobRepository(Protocol):
    def enqueue(
        self,
        job_key: str,
        position_id: str,
        command: SerializedPayload,
        max_attempts: int,
    ) -> BuildJobRecord: ...
    def get(self, job_id: int) -> BuildJobRecord | None: ...
    def claim(self, worker_id: str, job_id: int | None = None) -> BuildJobRecord | None: ...
    def succeed(self, job_id: int, build_run_id: int) -> BuildJobRecord: ...
    def fail(self, job_id: int, error_code: str, error_message: str) -> BuildJobRecord: ...
    def retry(self, job_id: int) -> BuildJobRecord: ...


class DocumentRepository(Protocol):
    def import_document(self, document: JDDocumentInput) -> str: ...
    def upsert_document(self, document: JDDocumentInput) -> str: ...
    def load_authoritative_write_facts(
        self, document_id: str
    ) -> AuthoritativeWriteFacts: ...


class PublishedFactRepository(Protocol):
    def load_validation_facts(
        self, fact: PublishedJDFact, lineage: PublishedFactLineage
    ) -> PublishedFactValidationFacts: ...
    def save_import_plan(self, plan: PublishedFactImportPlan) -> str: ...


class ReleaseRepository(Protocol):
    def find_manifest_hash(self, release_id: str) -> str | None: ...
    def parent_exists(self, release_id: str) -> bool: ...
    def save_release(
        self,
        manifest: ReleaseManifestV1,
        manifest_hash: str,
        facts: tuple[PublishedJDFact, ...],
        document_ids: tuple[str, ...],
    ) -> None: ...


class GraphDraftRepository(Protocol):
    def load_graph_draft_facts(
        self, position_id: str, base_version_id: int | None
    ) -> GraphDraftFacts: ...
    def find_graph_draft(self, draft_key: str) -> GraphDraftResult | None: ...
    def save_graph_draft_plan(self, plan: GraphDraftPlan) -> GraphDraftResult: ...
    def load_relation_edit_facts(self, relation_id: int) -> RelationEditFacts: ...
    def apply_relation_edit_plan(self, plan: RelationEditPlan) -> RelationEditResult: ...


class ReviewTaskRepository(Protocol):
    def save_new_task(self, plan: NewReviewTaskPlan) -> ReviewTaskResult: ...
    def save_new_tasks(
        self, plans: tuple[NewReviewTaskPlan, ...]
    ) -> tuple[ReviewTaskResult, ...]: ...
    def load_review_task_dedup_facts(
        self, key: ReviewTaskDedupKey
    ) -> ReviewTaskDedupFacts: ...
    def load_review_task_dedup_facts_bulk(
        self, keys: tuple[ReviewTaskDedupKey, ...]
    ) -> Mapping[ReviewTaskDedupKey, ReviewTaskDedupFacts]: ...
    def load_review_tasks_by_build(
        self,
        build_run_id: int,
        statuses: tuple[str, ...] | None = None,
    ) -> tuple[ReviewTaskFacts, ...]: ...
    def apply_review_task_merge_plan(
        self, plan: ReviewTaskMergePlan
    ) -> ReviewTaskResult: ...
    def load_review_task_facts(self, task_id: int) -> ReviewTaskFacts: ...
    def apply_review_task_plan(self, plan: ReviewTaskPlan) -> ReviewTaskResult: ...
    def append_review_event(self, plan: ReviewTaskEventPlan) -> None: ...


class ReviewObjectEffectPort(Protocol):
    def apply(self, effect: ReviewObjectEffect) -> None: ...


class AuditRepository(Protocol):
    def record(self, record: AuditRecord) -> None: ...


class AlgorithmConfigRepository(Protocol):
    def find_active(self) -> AlgorithmConfigResult | None: ...
    def replace(self, update: AlgorithmConfigUpdate) -> AlgorithmConfigResult: ...


class InnovationRepository(Protocol):
    def load_build_watermark_facts(self, build_run_id: int) -> BuildWatermarkFacts: ...
    def save_build_watermark(
        self, build_run_id: int, watermark: BuildInputWatermark
    ) -> int: ...
    def load_build_watermark(self, build_run_id: int) -> BuildInputWatermark: ...
    def latest_build_watermark(self, position_id: str) -> BuildInputWatermark | None: ...
    def current_build_source_facts(
        self, position_id: str, authoritative_only: bool
    ) -> tuple[WatermarkSourceFact, ...]: ...
    def current_algorithm_version(self) -> str: ...
    def current_catalog_source_version(self) -> str: ...
    def load_claim_sources(self, build_run_id: int) -> tuple[ClaimSourceFact, ...]: ...
    def save_relation_claims(self, claims: tuple[RelationClaim, ...]) -> int: ...
    def copy_rollback_lineage(
        self, source_version_id: int, target_version_id: int
    ) -> int: ...
    def save_mapping_candidate(
        self, state: MappingCandidateState
    ) -> MappingCandidateState: ...
    def load_mapping_candidate(self, candidate_id: str) -> MappingCandidateState: ...
    def save_mapping_review(
        self, decision: MappingReviewDecision, expected_revision: int
    ) -> MappingCandidateState: ...
    def apply_mapping_review_effect(self, candidate_id: str) -> int: ...
    def mapping_change_impact(self, candidate_id: str) -> AuditSnapshot: ...
    def save_dependency_event(
        self, *, event_key: str, entity_type: str, entity_id: str,
        change_kind: str, before: AuditSnapshot, after: AuditSnapshot,
        impact: AuditSnapshot, actor_id: int | None, trace_id: str,
    ) -> int: ...
    def save_dependency_reference(
        self, *, consumer_system: str, reference_type: str,
        reference_id: str, graph_version_id: int, metadata: AuditSnapshot,
    ) -> int: ...
    def load_dependency_contexts(self, build_run_id: int) -> DependencyContextFacts: ...
    def save_dependency_analysis(
        self,
        build_run_id: int,
        policy: DependencyPolicy,
        analysis: DependencyAnalysis,
    ) -> SavedDependencyAnalysis: ...
    def review_dependency_candidate(
        self,
        candidate_id: int,
        decision: str,
        reviewer_id: int,
        reason: str,
        policy_version: str,
        decided_at: str,
    ) -> bool: ...
    def freeze_reviewed_dependencies(
        self, build_run_id: int, graph_version_id: int
    ) -> int: ...
    def load_projection_facts(self, graph_version_id: int) -> ProjectionFacts: ...
    def save_projection(self, projection: GraphProjection) -> SavedProjection: ...
