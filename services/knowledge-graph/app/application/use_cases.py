from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Callable, TypeVar

from jobgraph_contracts.catalog import StandardSkillSnapshotV1, StandardSkillSnapshotV2

from app.application.contracts import (
    AnalyzeDependenciesCommand,
    AutoReviewBuildResult,
    BuildGraphCommand,
    BuildGraphResult,
    CompareWatermarksCommand,
    CreateMappingCandidateCommand,
    DocumentWorkflowCommand,
    ExtractionResult,
    GraphDraftResult,
    GraphVersionResult,
    ImportJDCommand,
    ImportJDResult,
    ImportPublishedJDFactCommand,
    ImportReleaseCommand,
    ImportReleaseResult,
    NormalizationResult,
    PublishGraphCommand,
    QualityAssessmentResult,
    RebuildProjectionCommand,
    ReviewDependencyCandidateCommand,
    ReviewDependencyCandidateResult,
    ReviewMappingCandidateCommand,
    RollbackGraphCommand,
)
from app.application.errors import (
    ConcurrentFactWrite,
    ConflictError,
    DuplicateBuildRun,
    DuplicateFactVersion,
    NotFoundError,
    PublishGateError,
    StaleGraphDraftError,
    ValidationError,
)
from app.domain.auditing import AuditRecord
from app.domain.authoritative_writes import (
    AuthoritativeWriteCommand,
    decide_authoritative_write,
)
from app.domain.build_jobs import BuildJobRecord, BuildJobTransitionError
from app.domain.decisions import DomainRejection
from app.domain.dependency_analysis import analyze_skill_dependencies
from app.domain.graph_building import BuildWindow, build_graph_plan
from app.domain.graph_drafts import GraphDraftCommand, decide_graph_draft
from app.domain.innovation import MappingCandidateState
from app.domain.lineage import lineage_snapshot
from app.domain.policies import (
    EvidenceAligner,
    QualityScoringPolicy,
    duplicate_cluster_key,
    text_similarity,
)
from app.domain.projections import build_graph_projection
from app.domain.published_facts import decide_published_fact_import
from app.domain.publishing import GateViolation, evaluate_publish_gate
from app.domain.relation_editing import (
    RelationEditCommand,
    RelationEditResult,
    decide_relation_edit,
)
from app.domain.review_tasks import (
    NewReviewTaskPlan,
    OPEN_REVIEW_TASK_STATUSES,
    REVIEW_POLICIES,
    ReviewReasonSet,
    ReviewTaskCommand,
    ReviewTaskDedupCommand,
    ReviewTaskDedupKey,
    ReviewTaskEventPlan,
    ReviewTaskResult,
    auto_review_allowed,
    decide_review_task_dedup,
    decide_review_task_transition,
    review_policy_auto_acceptable_reasons,
)
from app.domain.skill_resolution import (
    SkillResolutionCommand,
    decide_skill_resolution,
)
from app.domain.structured_facts import (
    ExtractionFacts,
    JDDocumentInput,
    NormalizationFacts,
    PublishedFactImportResult,
    SavedExtractionFacts,
    SavedNormalizationFacts,
)
from app.domain.temporal_analysis import (
    compare_build_watermarks,
    create_build_input_watermark,
)
from app.domain.traceability import (
    MappingReviewDecision,
    RelationClaim,
    decide_relation_claim,
    observed_claim_id,
    rank_mapping_candidate,
    validate_mapping_review,
)
from app.domain.value_types import AuditSnapshot, SerializedPayload
from app.domain.versioning import (
    PublishVersionPlan,
    RollbackVersionPlan,
)
from app.domain.workflow_facts import QualityAssessmentPlan, default_job_title
from app.domain.write_models import (
    AlgorithmConfigResult,
    AlgorithmConfigUpdate,
    RelationModification,
    ReviewCompletion,
    ReviewTaskDraft,
    SkillResolutionRequest,
)
from app.ports.providers import SkillIdGenerator
from app.ports.repositories import NormalizationProvider
from app.ports.unit_of_work import UnitOfWork

UowFactory = Callable[[], UnitOfWork]
ResultT = TypeVar("ResultT")


@dataclass
class UseCase:
    uow_factory: UowFactory

    def _execute(self, operation: Callable[[UnitOfWork], ResultT]) -> ResultT:
        with self.uow_factory() as uow:
            result = operation(uow)
            uow.commit()
            return result

    @staticmethod
    def _audit(uow: UnitOfWork, *, actor_id: int | None, action: str,
               object_type: str, object_id: str, trace_id: str,
               context: AuditSnapshot | None = None,
               before: AuditSnapshot | None = None,
               after: AuditSnapshot | None = None,
               reason: str | None = None) -> None:
        if actor_id is None:
            return
        uow.audits.record(
            AuditRecord(
                actor_id, action, object_type, object_id, before,
                {
                    "integration_context": context or {},
                    **dict(after or {}),
                }, reason, trace_id,
            )
        )

    @staticmethod
    def _raise_rejection(rejection: DomainRejection | None) -> None:
        assert rejection is not None
        error_type = {
            "not_found": NotFoundError,
            "conflict": ConflictError,
            "validation": ValidationError,
        }[rejection.kind]
        raise error_type(rejection.message, error_code=rejection.error_code)

    @classmethod
    def _protect_authoritative(
        cls, uow: UnitOfWork, document_id: str, operation: str
    ) -> None:
        decision = decide_authoritative_write(
            uow.documents.load_authoritative_write_facts(document_id),
            AuthoritativeWriteCommand(operation),
        )
        if not decision.accepted:
            cls._raise_rejection(decision.rejection)


class ImportJDUseCase(UseCase):
    def execute(self, command: ImportJDCommand) -> ImportJDResult:
        def operation(uow):
            document_id = uow.documents.import_document(command.document)
            if command.integration_context:
                self._audit(uow, actor_id=command.actor_id, action="import_jd",
                            object_type="jd_document", object_id=document_id,
                            trace_id=command.trace_id,
                            context=dict(command.integration_context))
            return ImportJDResult(document_id)
        return self._execute(operation)


class UpsertJDUseCase(UseCase):
    def execute(self, document: JDDocumentInput, actor_id: int | None = None,
                trace_id: str = "", context: AuditSnapshot | None = None) -> ImportJDResult:
        def operation(uow):
            if document.document_id is not None:
                self._protect_authoritative(uow, document.document_id, "jd_upsert")
            document_id = uow.documents.upsert_document(document)
            if context:
                self._audit(uow, actor_id=actor_id, action="integration_upsert_jd",
                            object_type="jd_document", object_id=document_id,
                            trace_id=trace_id, context=context)
            return ImportJDResult(document_id)
        return self._execute(operation)


class ImportPublishedJDFactUseCase(UseCase):
    def execute(
        self, command: ImportPublishedJDFactCommand, actor_id: int | None = None,
        trace_id: str = "", context: AuditSnapshot | None = None,
    ) -> PublishedFactImportResult:
        def operation(uow):
            decision = decide_published_fact_import(
                uow.published_facts.load_validation_facts(
                    command.fact, command.lineage
                )
            )
            if not decision.accepted:
                self._raise_rejection(decision.rejection)
            self._audit(
                uow, actor_id=actor_id, action="import_published_jd_fact",
                object_type="published_jd_fact",
                object_id=command.fact.source_fact_id, trace_id=trace_id,
                context=context,
                after=(
                    {"lineage": lineage_snapshot(command.lineage)}
                    if command.lineage.present
                    else None
                ),
            )
            if decision.existing is not None:
                existing = decision.existing
                return PublishedFactImportResult(
                    command.fact.contract_version,
                    existing.document_id,
                    existing.identity.source_fact_id,
                    existing.version,
                    existing.source_version,
                    decision.idempotent,
                    decision.stale,
                )
            assert decision.plan is not None
            document_id = uow.published_facts.save_import_plan(decision.plan)
            return PublishedFactImportResult(
                command.fact.contract_version,
                document_id,
                command.fact.source_fact_id,
                command.fact.source_fact_version,
                command.fact.source_version,
                False,
                False,
            )
        try:
            return self._execute(operation)
        except (DuplicateFactVersion, ConcurrentFactWrite):
            return self._execute(operation)


class ImportReleaseUseCase(UseCase):
    def execute(
        self,
        command: ImportReleaseCommand,
        actor_id: int | None = None,
        trace_id: str = "",
    ) -> ImportReleaseResult:
        def operation(uow):
            existing_hash = uow.releases.find_manifest_hash(
                command.manifest.release_id
            )
            if existing_hash is not None:
                if existing_hash != command.manifest_hash:
                    raise ConflictError(
                        "release_id already exists with a different manifest",
                        error_code="RELEASE_MANIFEST_CONFLICT",
                    )
                return ImportReleaseResult(
                    command.manifest.release_id, len(command.facts), True
                )
            parent = command.manifest.parent_release_id
            if parent is not None and not uow.releases.parent_exists(parent):
                raise ConflictError(
                    "incremental release parent has not been imported",
                    error_code="RELEASE_PARENT_MISSING",
                )
            declared_count = sum(
                artifact.record_count for artifact in command.manifest.artifacts
            )
            if declared_count != len(command.facts):
                raise ValidationError(
                    "release manifest record_count does not match validated facts",
                    error_code="RELEASE_RECORD_COUNT_MISMATCH",
                )
            document_ids: list[str] = []
            facts = []
            for fact_command in command.facts:
                decision = decide_published_fact_import(
                    uow.published_facts.load_validation_facts(
                        fact_command.fact, fact_command.lineage
                    )
                )
                if not decision.accepted:
                    self._raise_rejection(decision.rejection)
                if decision.existing is not None:
                    document_ids.append(decision.existing.document_id)
                else:
                    assert decision.plan is not None
                    document_ids.append(
                        uow.published_facts.save_import_plan(decision.plan)
                    )
                facts.append(fact_command.fact)
            uow.releases.save_release(
                command.manifest,
                command.manifest_hash,
                tuple(facts),
                tuple(document_ids),
            )
            self._audit(
                uow,
                actor_id=actor_id,
                action="import_release",
                object_type="release",
                object_id=command.manifest.release_id,
                trace_id=trace_id,
                after={
                    "manifest_hash": command.manifest_hash,
                    "record_count": len(facts),
                },
            )
            return ImportReleaseResult(
                command.manifest.release_id, len(facts), False
            )

        return self._execute(operation)


class ImportExtractionResultUseCase(UseCase):
    def execute(self, document_id: str, facts: ExtractionFacts,
                actor_id: int | None = None, trace_id: str = "",
                context: AuditSnapshot | None = None) -> SavedExtractionFacts:
        def operation(uow):
            self._protect_authoritative(uow, document_id, "import_extraction")
            result = uow.extractions.save_imported(document_id, facts)
            if context:
                self._audit(uow, actor_id=actor_id, action="import_extraction_v2",
                            object_type="jd_extraction", object_id=document_id,
                            trace_id=trace_id, context=context)
            return result
        return self._execute(operation)


class ExtractJDUseCase(UseCase):
    def execute(self, command: DocumentWorkflowCommand) -> ExtractionResult:
        def operation(uow: UnitOfWork) -> ExtractionResult:
            self._protect_authoritative(
                uow, command.document_id, "default_extraction"
            )
            facts = uow.extractions.document_facts(command.document_id)
            title = default_job_title(facts.raw_text)
            return ExtractionResult(
                uow.extractions.save_generated(command.document_id, title)
            )
        return self._execute(operation)


class AssessJDQualityUseCase(UseCase):
    def execute(self, command: DocumentWorkflowCommand) -> QualityAssessmentResult:
        def operation(uow):
            facts = uow.quality.load_facts(command.document_id)
            policy = QualityScoringPolicy()
            scores = policy.score(facts.document.raw_text, facts.peer_texts)
            weight = policy.effective_weight(
                facts.document.source_credibility,
                scores.duplicate_score,
                scores.copy_risk_score,
                scores.inflation_score,
            )
            cluster_key = None
            duplicate_peer_document_id = None
            if scores.duplicate_score >= 0.8:
                best_index = max(
                    range(len(facts.peer_texts)),
                    key=lambda index: text_similarity(
                        facts.document.raw_text, facts.peer_texts[index]
                    ),
                    default=None,
                )
                if best_index is not None:
                    duplicate_peer_document_id = facts.peer_document_ids[best_index]
                    cluster_key = (
                        facts.existing_cluster_key
                        or facts.peer_cluster_keys[best_index]
                        or duplicate_cluster_key(
                            command.document_id, duplicate_peer_document_id
                        )
                    )
            plan = QualityAssessmentPlan(
                command.document_id,
                scores.normalization_version,
                scores.duplicate_score,
                scores.copy_risk_score,
                scores.inflation_score,
                weight,
                cluster_key,
                duplicate_peer_document_id,
            )
            uow.quality.save_assessment(plan)
            return QualityAssessmentResult(
                normalization_version=scores.normalization_version,
                duplicate_score=scores.duplicate_score,
                copy_risk_score=scores.copy_risk_score,
                inflation_score=scores.inflation_score,
                effective_sample_weight=weight,
            )
        return self._execute(operation)


class ConfirmExtractionUseCase(UseCase):
    def execute(self, document_id: str, actor_id: int | None = None,
                trace_id: str = "", context: AuditSnapshot | None = None) -> ExtractionFacts:
        def operation(uow):
            self._protect_authoritative(uow, document_id, "confirm_extraction")
            facts = uow.extractions.document_facts(document_id)
            current = uow.extractions.structured_facts(document_id)
            aligned = EvidenceAligner().align_facts(facts.raw_text, current)
            result = uow.extractions.save_confirmed(document_id, aligned)
            if context:
                self._audit(uow, actor_id=actor_id, action="confirm_extraction_evidence",
                            object_type="jd_extraction", object_id=document_id,
                            trace_id=trace_id, context=context)
            return result
        return self._execute(operation)


@dataclass
class NormalizeJDUseCase(UseCase):
    normalization_provider: NormalizationProvider

    def execute(self, command: DocumentWorkflowCommand) -> NormalizationResult:
        def operation(uow: UnitOfWork) -> NormalizationResult:
            self._protect_authoritative(uow, command.document_id, "normalization")
            extraction = uow.normalizations.load_structured_facts(command.document_id)
            normalized = self.normalization_provider.produce(extraction)
            saved = uow.normalizations.save_result(command.document_id, normalized)
            return NormalizationResult(saved.facts)
        return self._execute(operation)


class ImportNormalizedResultUseCase(UseCase):
    def execute(self, document_id: str, facts: NormalizationFacts,
                actor_id: int | None = None, trace_id: str = "",
                context: AuditSnapshot | None = None) -> SavedNormalizationFacts:
        def operation(uow):
            self._protect_authoritative(uow, document_id, "import_normalization")
            result = uow.normalizations.save_result(document_id, facts)
            if context:
                self._audit(uow, actor_id=actor_id, action="import_normalization_v2",
                            object_type="jd_normalization", object_id=document_id,
                            trace_id=trace_id, context=context)
            return result
        return self._execute(operation)


@dataclass
class ResolveUnresolvedSkillUseCase(UseCase):
    skill_id_generator: SkillIdGenerator

    def execute(
        self, item_id: int, action: str, request: SkillResolutionRequest, actor_id: int,
        reason: str | None, trace_id: str,
    ):
        def operation(uow: UnitOfWork):
            command = SkillResolutionCommand(
                action,
                actor_id,
                reason,
                trace_id,
                request.skill_id,
                None,
                request.canonical_name,
                request.category_code,
                request.subcategory_code,
                request.alias,
                request.extensions,
            )
            facts = uow.normalizations.load_skill_resolution_facts(item_id, command)
            self._protect_authoritative(uow, facts.item.document_id, "skill_resolution")
            if action == "create_skill" and request.skill_id is None:
                command = replace(
                    command,
                    generated_skill_id=self.skill_id_generator.new_skill_id(),
                )
                facts = uow.normalizations.load_skill_resolution_facts(
                    item_id, command
                )
            decision = decide_skill_resolution(facts, command)
            if not decision.accepted:
                self._raise_rejection(decision.rejection)
            assert decision.plan is not None
            plan = decision.plan
            result = uow.normalizations.apply_skill_resolution_plan(plan)
            before = {
                "status": facts.item.status,
                "resolution": dict(facts.item.resolution),
            }
            after = {
                "status": plan.target_item_status,
                "resolution": dict(plan.resolution),
            }
            task = uow.review_tasks.save_new_task(
                NewReviewTaskPlan(
                    "normalization_item",
                    str(plan.item_id),
                    None,
                    plan.review_status,
                    actor_id,
                    {"resolution": dict(plan.resolution)},
                )
            )
            uow.review_tasks.append_review_event(
                ReviewTaskEventPlan(
                    task.task_id,
                    actor_id,
                    plan.event_action,
                    before,
                    after,
                    reason,
                    trace_id,
                )
            )
            uow.audits.record(
                AuditRecord(
                    actor_id,
                    f"unresolved_{action}",
                    "normalization_item",
                    str(item_id),
                    before,
                    after,
                    reason,
                    trace_id,
                )
            )
            return result

        return self._execute(operation)


def _persist_build_watermark(uow: UnitOfWork, build_run_id: int) -> None:
    facts = uow.innovation.load_build_watermark_facts(build_run_id)
    watermark = create_build_input_watermark(
        source_facts=facts.source_facts,
        observation_window_start=facts.observation_window_start,
        observation_window_end=facts.observation_window_end,
        catalog_snapshot_id=facts.catalog_snapshot_id,
        catalog_source_version=facts.catalog_source_version,
        validation_state=facts.validation_state,
        validation_policy_version=facts.validation_policy_version,
        mapping_policy_version=facts.mapping_policy_version,
        aggregation_algorithm_version=facts.aggregation_algorithm_version,
        normalized_config=facts.normalized_config,
        input_coverage=facts.input_coverage,
    )
    uow.innovation.save_build_watermark(build_run_id, watermark)


class BuildGraphUseCase(UseCase):
    def execute(self, command: BuildGraphCommand) -> BuildGraphResult:
        def operation(uow):
            facts = uow.graph_builds.load_facts(
                command.position_id,
                authoritative_only=command.authoritative_only,
            )
            plan = build_graph_plan(
                facts,
                BuildWindow(
                    command.window_start, command.window_end,
                    command.minimum_effective_weight,
                    command.minimum_valid_samples,
                    command.authoritative_only,
                ),
            )
            run = uow.graph_builds.save_plan(plan)
            _persist_build_watermark(uow, run.build_run_id)
            persisted_objects = {
                item.reference: item for item in run.objects
            }
            dedup_commands = []
            for intent in plan.review_intents:
                object_id = intent.object_id
                if object_id is None:
                    object_id = persisted_objects[
                        intent.object_reference
                    ].object_id
                dedup_commands.append(
                    ReviewTaskDedupCommand(
                        ReviewTaskDedupKey(
                            intent.object_type,
                            object_id,
                            run.build_run_id,
                        ),
                        intent.reasons,
                    )
                )
            _apply_review_task_dedup_batch(uow, tuple(dedup_commands))
            if command.integration_context:
                self._audit(uow, actor_id=command.actor_id, action="build_graph",
                            object_type="graph_build_run", object_id=str(run.build_run_id),
                            trace_id=command.trace_id,
                            context=dict(command.integration_context))
            return BuildGraphResult(run.build_run_id, run.status, run.summary)
        return self._execute(operation)


class BuildJobUseCase(UseCase):
    @staticmethod
    def _payload(command: BuildGraphCommand) -> dict:
        return {
            "position_id": command.position_id,
            "window_start": command.window_start.isoformat() if command.window_start else None,
            "window_end": command.window_end.isoformat() if command.window_end else None,
            "minimum_effective_weight": command.minimum_effective_weight,
            "minimum_valid_samples": command.minimum_valid_samples,
            "authoritative_only": command.authoritative_only,
            "actor_id": command.actor_id,
            "trace_id": command.trace_id,
            "integration_context": dict(command.integration_context),
        }

    @staticmethod
    def command(record: BuildJobRecord) -> BuildGraphCommand:
        payload = record.command
        return BuildGraphCommand(
            str(payload["position_id"]),
            datetime.fromisoformat(payload["window_start"])
            if payload.get("window_start")
            else None,
            datetime.fromisoformat(payload["window_end"])
            if payload.get("window_end")
            else None,
            float(payload["minimum_effective_weight"]),
            int(payload["minimum_valid_samples"]),
            bool(payload["authoritative_only"]),
            payload.get("actor_id"),
            str(payload.get("trace_id") or ""),
            dict(payload.get("integration_context") or {}),
        )

    def enqueue(self, command: BuildGraphCommand, max_attempts: int = 3) -> BuildJobRecord:
        def operation(uow):
            latest = uow.innovation.latest_build_watermark(command.position_id)
            current_facts = uow.innovation.current_build_source_facts(
                command.position_id, command.authoritative_only
            )
            current_algorithm = uow.innovation.current_algorithm_version()
            if (
                latest is not None
                and current_facts == latest.source_facts
                and current_algorithm == latest.aggregation_algorithm_version
                and uow.innovation.current_catalog_source_version() == latest.catalog_source_version
            ):
                raise BuildJobTransitionError(
                    "当前输入数据与最近一次成功构建一致，未发现数据变化，未创建新任务"
                )
            return uow.build_jobs.enqueue(
                uuid.uuid4().hex, command.position_id, self._payload(command), max_attempts
            )

        return self._execute(operation)

    def get(self, job_id: int) -> BuildJobRecord | None:
        return self._execute(lambda uow: uow.build_jobs.get(job_id))

    def claim(self, worker_id: str, job_id: int | None = None) -> BuildJobRecord | None:
        return self._execute(lambda uow: uow.build_jobs.claim(worker_id, job_id))

    def succeed(self, job_id: int, build_run_id: int) -> BuildJobRecord:
        return self._execute(lambda uow: uow.build_jobs.succeed(job_id, build_run_id))

    def fail(self, job_id: int, error: Exception) -> BuildJobRecord:
        return self._execute(
            lambda uow: uow.build_jobs.fail(job_id, type(error).__name__, str(error))
        )

    def retry(self, job_id: int) -> BuildJobRecord:
        return self._execute(lambda uow: uow.build_jobs.retry(job_id))


def _apply_review_task_dedup(
    uow: UnitOfWork,
    command: ReviewTaskDedupCommand,
) -> ReviewTaskResult:
    decision = decide_review_task_dedup(
        uow.review_tasks.load_review_task_dedup_facts(command.key),
        command,
    )
    if decision.action == "reuse":
        assert decision.existing is not None
        return decision.existing
    if decision.action == "merge":
        assert decision.merge_plan is not None
        return uow.review_tasks.apply_review_task_merge_plan(
            decision.merge_plan
        )
    assert decision.new_task_plan is not None
    return uow.review_tasks.save_new_task(decision.new_task_plan)


def _apply_review_task_dedup_batch(
    uow: UnitOfWork,
    commands: tuple[ReviewTaskDedupCommand, ...],
) -> None:
    if not commands:
        return
    facts_by_key = uow.review_tasks.load_review_task_dedup_facts_bulk(
        tuple(command.key for command in commands)
    )
    new_plans: list[NewReviewTaskPlan] = []
    for command in commands:
        decision = decide_review_task_dedup(
            facts_by_key[command.key],
            command,
        )
        if decision.action == "merge" and decision.merge_plan is not None:
            uow.review_tasks.apply_review_task_merge_plan(decision.merge_plan)
        elif decision.action == "create" and decision.new_task_plan is not None:
            new_plans.append(decision.new_task_plan)
    if new_plans:
        uow.review_tasks.save_new_tasks(tuple(new_plans))


class ModifyRelationUseCase(UseCase):
    @staticmethod
    def apply_in_uow(
        use_case: UseCase,
        uow: UnitOfWork,
        relation_id: int,
        modification: RelationModification,
        actor_id: int,
        trace_id: str,
        *,
        ensure_review_task: bool,
        source: str,
        review_task_id: int | None = None,
    ):
        command = RelationEditCommand(
            relation_id,
            modification.build_run_id,
            modification.position_id,
            modification.expected_revision,
            modification.reason,
            modification.changed_fields,
            modification.weight,
            modification.confidence,
            modification.importance_level,
        )
        decision = decide_relation_edit(
            uow.graph_drafts.load_relation_edit_facts(relation_id), command
        )
        if not decision.accepted:
            use_case._raise_rejection(decision.rejection)
        assert decision.plan is not None
        plan = decision.plan
        result = uow.graph_drafts.apply_relation_edit_plan(plan)
        if ensure_review_task and plan.ensure_review_task:
            _apply_review_task_dedup(
                uow,
                ReviewTaskDedupCommand(
                    ReviewTaskDedupKey(
                        "position_skill_relation",
                        str(plan.relation_id),
                        plan.build_run_id,
                    ),
                    ReviewReasonSet(("manually_modified_relation",)),
                    {"actor_id": actor_id},
                ),
            )
        uow.audits.record(AuditRecord(
            actor_id,
            "modify_relation",
            "relation",
            str(plan.relation_id),
            plan.before,
            {
                **dict(plan.after),
                "modification_source": source,
                "review_task_id": review_task_id,
            },
            plan.reason,
            trace_id,
        ))
        return result, plan

    def execute(
        self, relation_id: int, modification: RelationModification,
        actor_id: int, trace_id: str
    ) -> RelationEditResult:
        def operation(uow: UnitOfWork):
            result, _ = self.apply_in_uow(
                self, uow, relation_id, modification, actor_id, trace_id,
                ensure_review_task=True, source="relation_edit",
            )
            return result

        return self._execute(operation)


class OpenGraphDraftUseCase(UseCase):
    def execute(
        self, position_id: str, base_version_id: int | None = None
    ) -> GraphDraftResult:
        def operation(uow: UnitOfWork) -> GraphDraftResult:
            command = GraphDraftCommand(position_id, base_version_id)
            decision = decide_graph_draft(
                uow.graph_drafts.load_graph_draft_facts(position_id, base_version_id),
                command,
            )
            if not decision.accepted:
                self._raise_rejection(decision.rejection)
            assert decision.plan is not None
            run = uow.graph_drafts.find_graph_draft(decision.plan.draft_key)
            if run is None:
                run = uow.graph_drafts.save_graph_draft_plan(decision.plan)
                _persist_build_watermark(uow, run.build_run_id)
            return GraphDraftResult(
                run.build_run_id,
                run.build_run_id,
                run.position_id,
                run.base_version_id,
            )
        try:
            return self._execute(operation)
        except DuplicateBuildRun:
            key = f"{position_id}:{base_version_id}"
            if base_version_id is None:
                return self._execute(operation)
            existing = self._execute(
                lambda uow: uow.graph_drafts.find_graph_draft(key)
            )
            if existing is None:
                raise
            return GraphDraftResult(
                existing.build_run_id,
                existing.build_run_id,
                existing.position_id,
                existing.base_version_id,
            )


def _transition_review_task(
    use_case: UseCase, task_id: int, command: ReviewTaskCommand
):
    def operation(uow: UnitOfWork):
        decision = decide_review_task_transition(
            uow.review_tasks.load_review_task_facts(task_id), command
        )
        if not decision.accepted:
            use_case._raise_rejection(decision.rejection)
        assert decision.plan is not None
        plan = decision.plan
        result = uow.review_tasks.apply_review_task_plan(plan)
        if plan.effect is not None:
            uow.review_effects.apply(plan.effect)
        uow.review_tasks.append_review_event(plan.event)
        uow.audits.record(
            AuditRecord(
                command.actor_id,
                f"review_{command.action}",
                "review_task",
                str(task_id),
                plan.event.before,
                plan.event.after,
                command.reason,
                command.trace_id,
            )
        )
        return result

    return use_case._execute(operation)


class ClaimReviewTaskUseCase(UseCase):
    def execute(
        self, task_id: int, actor_id: int, trace_id: str,
        reason: str | None = None,
    ):
        return _transition_review_task(
            self, task_id, ReviewTaskCommand(
                "claim", actor_id, trace_id, reason
            )
        )


class BatchReviewTasksUseCase(UseCase):
    def execute(
        self, task_ids: tuple[int, ...], action: str,
        actor_id: int, trace_id: str, reason: str,
    ) -> Mapping[int, str]:
        if action not in {"claim", "approve", "reject"}:
            raise ValidationError("batch review action is invalid")
        if len(set(task_ids)) != len(task_ids):
            raise ValidationError("batch review task_ids must be unique")

        def operation(uow: UnitOfWork) -> Mapping[int, str]:
            statuses = {}
            for task_id in task_ids:
                command = ReviewTaskCommand(action, actor_id, trace_id, reason)
                decision = decide_review_task_transition(
                    uow.review_tasks.load_review_task_facts(task_id), command
                )
                if not decision.accepted:
                    self._raise_rejection(decision.rejection)
                assert decision.plan is not None
                plan = decision.plan
                result = uow.review_tasks.apply_review_task_plan(plan)
                if plan.effect is not None:
                    uow.review_effects.apply(plan.effect)
                uow.review_tasks.append_review_event(plan.event)
                uow.audits.record(
                    AuditRecord(
                        actor_id,
                        f"review_{action}",
                        "review_task",
                        str(task_id),
                        plan.event.before,
                        plan.event.after,
                        reason,
                        trace_id,
                    )
                )
                statuses[task_id] = result.status
            return statuses

        return self._execute(operation)


class AutoReviewBuildUseCase(UseCase):
    """Apply one versioned policy decision to every auto-acceptable task."""

    def execute(
        self,
        build_run_id: int,
        policy_version: str,
        actor_id: int,
        trace_id: str,
        reason: str,
    ) -> AutoReviewBuildResult:
        policy_version = policy_version.strip()
        if not policy_version:
            raise ValidationError("auto review policy_version is required")
        if policy_version not in REVIEW_POLICIES:
            raise ValidationError(
                f"unknown auto review policy version: {policy_version}"
            )
        allowed_reasons = review_policy_auto_acceptable_reasons(
            policy_version
        )
        assert allowed_reasons is not None

        def operation(uow: UnitOfWork) -> AutoReviewBuildResult:
            tasks = uow.review_tasks.load_review_tasks_by_build(
                build_run_id,
                statuses=tuple(OPEN_REVIEW_TASK_STATUSES),
            )
            auto_accepted: list[int] = []
            requires_human: list[int] = []
            for task in tasks:
                if not auto_review_allowed(task.payload, policy_version):
                    requires_human.append(task.task_id)
                    continue
                command = ReviewTaskCommand(
                    "auto_accept",
                    actor_id,
                    trace_id,
                    reason,
                    {
                        "policy_version": policy_version,
                        "decision": "auto_accepted",
                    },
                )
                decision = decide_review_task_transition(task, command)
                if not decision.accepted:
                    requires_human.append(task.task_id)
                    continue
                assert decision.plan is not None
                plan = decision.plan
                result = uow.review_tasks.apply_review_task_plan(plan)
                if plan.effect is not None:
                    uow.review_effects.apply(plan.effect)
                uow.review_tasks.append_review_event(plan.event)
                uow.audits.record(
                    AuditRecord(
                        actor_id,
                        "review_auto_accept",
                        "review_task",
                        str(task.task_id),
                        plan.event.before,
                        plan.event.after,
                        reason,
                        trace_id,
                    )
                )
                auto_accepted.append(result.task_id)
            return AutoReviewBuildResult(
                build_run_id=build_run_id,
                policy_version=policy_version,
                allowed_reasons=tuple(sorted(allowed_reasons)),
                auto_accepted_count=len(auto_accepted),
                requires_human_count=len(requires_human),
                auto_accepted_task_ids=tuple(auto_accepted),
                requires_human_task_ids=tuple(requires_human),
            )

        return self._execute(operation)


class CreateReviewTaskUseCase(UseCase):
    def execute(
        self, draft: ReviewTaskDraft, actor_id: int, trace_id: str
    ):
        def operation(uow: UnitOfWork):
            result = uow.review_tasks.save_new_task(
                NewReviewTaskPlan(
                    draft.object_type,
                    draft.object_id,
                    draft.build_run_id,
                    payload=draft.attributes,
                )
            )
            uow.audits.record(
                AuditRecord(
                    actor_id,
                    "create_review_task",
                    "review_task",
                    str(result.task_id),
                    None,
                    {
                        "status": result.status,
                        "object_type": draft.object_type,
                        "object_id": draft.object_id,
                    },
                    None,
                    trace_id,
                )
            )
            return result

        return self._execute(operation)


class CompleteReviewTaskUseCase(UseCase):
    def execute(
        self, task_id: int, completion: ReviewCompletion,
        actor_id: int, trace_id: str,
    ):
        if completion.action == "modify":
            def operation(uow: UnitOfWork):
                facts = uow.review_tasks.load_review_task_facts(task_id)
                attributes = dict(completion.attributes)
                if facts.object_type == "position_skill_relation":
                    try:
                        relation_id = int(facts.object_id)
                        relation_facts = uow.graph_drafts.load_relation_edit_facts(
                            relation_id
                        )
                        expected_revision = int(attributes["expected_revision"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValidationError(
                            "relation review modify requires expected_revision"
                        ) from exc
                    changed_fields = frozenset(
                        field for field in (
                            "weight", "confidence", "importance_level"
                        ) if field in attributes
                    )
                    if not changed_fields:
                        raise ValidationError(
                            "relation review modify requires at least one changed value"
                        )
                    _, edit_plan = ModifyRelationUseCase.apply_in_uow(
                        self,
                        uow,
                        relation_id,
                        RelationModification(
                            relation_facts.relation_build_run_id or 0,
                            relation_facts.relation_position_id or "",
                            expected_revision,
                            completion.reason or "",
                            attributes.get("weight"),
                            attributes.get("confidence"),
                            attributes.get("importance_level"),
                            changed_fields,
                        ),
                        actor_id,
                        trace_id,
                        ensure_review_task=False,
                        source="review_task",
                        review_task_id=task_id,
                    )
                    attributes.update({
                        "changed_content": dict(edit_plan.after),
                        "current_content": dict(edit_plan.after),
                        "modification_source": "review_task",
                    })
                command = ReviewTaskCommand(
                    completion.action, actor_id, trace_id, completion.reason, attributes
                )
                decision = decide_review_task_transition(facts, command)
                if not decision.accepted:
                    self._raise_rejection(decision.rejection)
                assert decision.plan is not None
                plan = decision.plan
                result = uow.review_tasks.apply_review_task_plan(plan)
                if plan.effect is not None:
                    uow.review_effects.apply(plan.effect)
                uow.review_tasks.append_review_event(plan.event)
                uow.audits.record(AuditRecord(
                    actor_id, "review_modify", "review_task", str(task_id),
                    plan.event.before, plan.event.after, completion.reason, trace_id,
                ))
                return result

            return self._execute(operation)
        return _transition_review_task(
            self,
            task_id,
            ReviewTaskCommand(
                completion.action,
                actor_id,
                trace_id,
                completion.reason,
                completion.attributes,
            ),
        )


class PublishGraphVersionUseCase(UseCase):
    def execute(self, command: PublishGraphCommand) -> GraphVersionResult:
        def operation(uow: UnitOfWork) -> GraphVersionResult:
            facts = uow.graph_versions.load_publish_facts(command.run_id)
            if facts.existing:
                return GraphVersionResult(
                    facts.existing.version_id, facts.existing.version_number
                )
            if facts.base_version_id != facts.current_version_id:
                raise StaleGraphDraftError(
                    base_version_id=facts.base_version_id,
                    current_version_id=facts.current_version_id,
                )
            gate = evaluate_publish_gate(facts.gate)
            errors = list(gate.errors)
            number = command.version_number or (
                (facts.previous_version_number or 0) + 1
            )
            name = command.version_name or f"v{number}"
            if number in facts.used_numbers or name in facts.used_names:
                # The fact snapshot is assembled by multiple reads. A concurrent
                # publisher can commit after `existing` was read but before the
                # used version sets were read. Recheck the build before treating
                # that mixed snapshot as a genuine version conflict.
                concurrent_existing = uow.graph_versions.find_by_build_run(
                    command.run_id
                )
                if concurrent_existing is not None:
                    return GraphVersionResult(
                        concurrent_existing.version_id,
                        concurrent_existing.version_number,
                    )
            if number < 1:
                errors.append(
                    GateViolation("version_number", "version number must be positive")
                )
            if number in facts.used_numbers:
                errors.append(
                    GateViolation("version_unique", "version number already exists")
                )
            if name in facts.used_names:
                errors.append(
                    GateViolation("version_name_unique", "version name already exists")
                )
            if errors:
                raise PublishGateError(tuple(errors))
            snapshot = dict(facts.snapshot)
            snapshot["release_notes"] = command.release_notes
            watermark = uow.innovation.load_build_watermark(command.run_id)
            if not snapshot.get("position"):
                errors.append(GateViolation("snapshot_complete", "snapshot is incomplete"))
            if errors:
                raise PublishGateError(tuple(errors))
            saved = uow.graph_versions.save_published(
                PublishVersionPlan(
                    command.run_id, facts.position_id, number, name, snapshot,
                    facts.algorithm_version, facts.dependencies,
                    facts.previous_version_id,
                ),
                command.actor_id,
            )
            saved_dependency_count = uow.innovation.freeze_reviewed_dependencies(
                command.run_id, saved.version_id
            )
            claims = []
            for source in uow.innovation.load_claim_sources(command.run_id):
                claim = RelationClaim(
                    claim_id=observed_claim_id(saved.version_id, source.support_id),
                    support_id=source.support_id,
                    subject_id=source.position_id,
                    predicate="REQUIRES_SKILL",
                    object_id=source.skill_id,
                    claim_kind="observed",
                    source_kind=source.source_kind,
                    source_fact_id=source.source_fact_id,
                    source_fact_version=source.source_fact_version,
                    requirement_id=source.requirement_id,
                    evidence=(source.evidence,),
                    validation_lineage_lineage_version=(
                        source.validation_lineage_lineage_version
                    ),
                    catalog_snapshot_lineage_version=watermark.catalog_source_version,
                    mapping_policy_version=watermark.mapping_policy_version,
                    observed_at=source.observed_at,
                    graph_version_id=saved.version_id,
                )
                claim_decision = decide_relation_claim(claim)
                if not claim_decision.accepted:
                    self._raise_rejection(claim_decision.rejection)
                claims.append(claim)
            saved_claim_count = uow.innovation.save_relation_claims(tuple(claims))
            uow.audits.record(
                AuditRecord(
                    command.actor_id,
                    "publish_graph",
                    "graph_version",
                    str(saved.version_id),
                    {"current_version_id": facts.previous_version_id},
                    {
                        "version_number": number,
                        "source_version": watermark.catalog_source_version,
                        "claim_count": saved_claim_count,
                        "reviewed_dependency_count": saved_dependency_count,
                        "watermark_lineage_version": watermark.lineage_version,
                    },
                    command.reason,
                    command.trace_id,
                )
            )
            return GraphVersionResult(saved.version_id, saved.version_number)
        try:
            return self._execute(operation)
        except DuplicateBuildRun:
            existing = self._execute(
                lambda uow: uow.graph_versions.find_by_build_run(command.run_id)
            )
            if existing is None:
                raise
            return GraphVersionResult(existing.version_id, existing.version_number)


class RollbackGraphVersionUseCase(UseCase):
    def execute(self, command: RollbackGraphCommand) -> GraphVersionResult:
        def operation(uow: UnitOfWork) -> GraphVersionResult:
            facts = uow.graph_versions.load_rollback_facts(
                command.position_id, command.version_id
            )
            number = facts.latest_version_number + 1
            plan = RollbackVersionPlan(
                facts.source_version_id, facts.position_id,
                facts.current_version_id, number, f"v{number}", facts.snapshot,
                facts.algorithm_version,
                facts.normalization_map_version, facts.dependencies,
            )
            saved = uow.graph_versions.save_rollback(
                plan, command.actor_id
            )
            copied_claim_count = uow.innovation.copy_rollback_lineage(
                command.version_id, saved.version_id
            )
            uow.audits.record(
                AuditRecord(
                    command.actor_id,
                    "rollback_graph",
                    "graph_version",
                    str(saved.version_id),
                    {"current_version_id": plan.base_version_id},
                    {
                        "version_number": plan.version_number,
                        "rollback_from_version_id": plan.source_version_id,
                        "claim_count": copied_claim_count,
                    },
                    command.reason,
                    command.trace_id,
                )
            )
            return GraphVersionResult(
                saved.version_id, saved.version_number, command.version_id
            )
        return self._execute(operation)


class CreateMappingCandidateUseCase(UseCase):
    def execute(self, command: CreateMappingCandidateCommand) -> MappingCandidateState:
        try:
            ranked = rank_mapping_candidate(command.candidate, command.weights)
        except ValueError as exc:
            raise ValidationError(
                str(exc), error_code="INVALID_MAPPING_CANDIDATE"
            ) from exc

        def operation(uow: UnitOfWork) -> MappingCandidateState:
            result = uow.innovation.save_mapping_candidate(
                MappingCandidateState(ranked.candidate, ranked.priority, "pending", 1)
            )
            uow.audits.record(
                AuditRecord(
                    command.actor_id,
                    "create_mapping_candidate",
                    "mapping_candidate",
                    command.candidate.candidate_id,
                    None,
                    {"priority": ranked.priority, "status": result.status},
                    None,
                    command.trace_id,
                )
            )
            return result

        return self._execute(operation)


class ReviewMappingCandidateUseCase(UseCase):
    def execute(self, command: ReviewMappingCandidateCommand) -> MappingCandidateState:
        if command.effective_scope != "affected_contexts":
            raise ValidationError(
                "mapping review effective_scope must be affected_contexts",
                error_code="INVALID_MAPPING_EFFECTIVE_SCOPE",
            )
        decision = MappingReviewDecision(
            candidate_id=command.candidate_id,
            decision=command.decision,
            reviewer_id=command.reviewer_id,
            reason=command.reason,
            policy_version=command.policy_version,
            decided_at=command.decided_at,
            effective_scope=command.effective_scope,
            replacement_candidate_id=command.replacement_candidate_id,
        )
        try:
            validate_mapping_review(decision)
        except ValueError as exc:
            raise ValidationError(
                str(exc), error_code="INVALID_MAPPING_REVIEW"
            ) from exc

        def operation(uow: UnitOfWork) -> MappingCandidateState:
            before = uow.innovation.load_mapping_candidate(command.candidate_id)
            impact = uow.innovation.mapping_change_impact(command.candidate_id)
            result = uow.innovation.save_mapping_review(
                decision, command.expected_revision
            )
            effective_record_count = (
                uow.innovation.apply_mapping_review_effect(command.candidate_id)
                if command.decision == "accept"
                else 0
            )
            event_material = (
                f"{command.candidate_id}:{command.expected_revision}:"
                f"{command.decision}:{command.policy_version}"
            )
            uow.innovation.save_dependency_event(
                event_key=event_material,
                entity_type="skill_mapping",
                entity_id=command.candidate_id,
                change_kind=f"mapping_{command.decision}",
                before=asdict(before),
                after=asdict(result),
                impact=impact,
                actor_id=command.reviewer_id,
                trace_id=command.trace_id,
            )
            uow.audits.record(
                AuditRecord(
                    command.reviewer_id,
                    "review_mapping_candidate",
                    "mapping_candidate",
                    command.candidate_id,
                    {"status": before.status, "revision": before.revision},
                    {
                        "status": result.status,
                        "revision": result.revision,
                        "effective_record_count": effective_record_count,
                    },
                    command.reason,
                    command.trace_id,
                )
            )
            return result

        return self._execute(operation)


class DependencyReferenceUseCase(UseCase):
    def execute(
        self, *, consumer_system: str, reference_type: str,
        reference_id: str, graph_version_id: int, metadata: SerializedPayload,
    ) -> int:
        return self._execute(
            lambda uow: uow.innovation.save_dependency_reference(
                consumer_system=consumer_system,
                reference_type=reference_type,
                reference_id=reference_id,
                graph_version_id=graph_version_id,
                metadata=metadata,
            )
        )


class AnalyzeDependenciesUseCase(UseCase):
    def execute(self, command: AnalyzeDependenciesCommand):
        def operation(uow: UnitOfWork):
            facts = uow.innovation.load_dependency_contexts(command.build_run_id)
            if not facts.contexts:
                raise ConflictError(
                    "dependency analysis requires multi-skill requirement contexts",
                    error_code="DEPENDENCY_CONTEXT_REQUIRED",
                )
            analysis = analyze_skill_dependencies(facts.contexts, command.policy)
            result = uow.innovation.save_dependency_analysis(
                command.build_run_id, command.policy, analysis
            )
            uow.audits.record(
                AuditRecord(
                    command.actor_id,
                    "analyze_skill_dependencies",
                    "dependency_analysis_run",
                    str(result.analysis_run_id),
                    None,
                    {
                        "build_run_id": command.build_run_id,
                        "candidate_count": result.candidate_count,
                        "rejected_count": result.rejected_count,
                    },
                    None,
                    command.trace_id,
                )
            )
            return result

        return self._execute(operation)


class ReviewDependencyCandidateUseCase(UseCase):
    def execute(
        self, command: ReviewDependencyCandidateCommand
    ) -> ReviewDependencyCandidateResult:
        def operation(uow: UnitOfWork) -> ReviewDependencyCandidateResult:
            idempotent = uow.innovation.review_dependency_candidate(
                command.candidate_id,
                command.decision,
                command.reviewer_id,
                command.reason,
                command.policy_version,
                command.decided_at,
            )
            uow.audits.record(
                AuditRecord(
                    command.reviewer_id,
                    "review_dependency_candidate",
                    "dependency_candidate",
                    str(command.candidate_id),
                    None,
                    {
                        "decision": command.decision,
                        "policy_version": command.policy_version,
                        "idempotent": idempotent,
                    },
                    command.reason,
                    command.trace_id,
                )
            )
            return ReviewDependencyCandidateResult(
                command.candidate_id, command.decision, idempotent
            )

        return self._execute(operation)


class RebuildProjectionUseCase(UseCase):
    def execute(self, command: RebuildProjectionCommand):
        def operation(uow: UnitOfWork):
            facts = uow.innovation.load_projection_facts(command.graph_version_id)
            projection = build_graph_projection(
                projection_version=command.projection_version,
                graph_version_id=facts.graph_version_id,
                source_version=facts.source_version,
                watermark_lineage_version=facts.watermark.lineage_version,
                claims=facts.claims,
                mapping_candidates=facts.mapping_candidates,
                dependency_candidates=facts.dependency_candidates,
            )
            result = uow.innovation.save_projection(projection)
            uow.audits.record(
                AuditRecord(
                    command.actor_id,
                    "rebuild_graph_projection",
                    "projection_manifest",
                    str(result.manifest_id),
                    None,
                    {
                        "graph_version_id": command.graph_version_id,
                        "source_version": projection.manifest.source_version,
                    },
                    None,
                    command.trace_id,
                )
            )
            return result

        return self._execute(operation)


class CompareBuildWatermarksUseCase(UseCase):
    def execute(self, command: CompareWatermarksCommand):
        def operation(uow: UnitOfWork):
            left = uow.innovation.load_build_watermark(command.left_build_run_id)
            right = uow.innovation.load_build_watermark(command.right_build_run_id)
            return compare_build_watermarks(left, right, command.context)

        return self._execute(operation)


@dataclass
class ImportCapabilitySkillSnapshotUseCase(UseCase):
    def execute(
        self,
        snapshot: StandardSkillSnapshotV1 | StandardSkillSnapshotV2,
        actor_id: int,
        trace_id: str,
        context: AuditSnapshot,
    ) -> AuditSnapshot:
        def operation(uow: UnitOfWork) -> AuditSnapshot:
            before, after = uow.catalog_snapshots.upsert_skill(snapshot)
            uow.audits.record(
                AuditRecord(
                    actor_id,
                    'import_capability_skill_snapshot',
                    'standard_skill_snapshot',
                    snapshot.skill_id,
                    before,
                    {**after, 'integration_context': dict(context)},
                    'authoritative capability catalog snapshot',
                    trace_id,
                )
            )
            return after
        return self._execute(operation)


class UpdateAlgorithmConfigUseCase(UseCase):
    def execute(
        self, update: AlgorithmConfigUpdate, actor_id: int, trace_id: str
    ) -> AlgorithmConfigResult:
        def operation(uow: UnitOfWork) -> AlgorithmConfigResult:
            before = uow.algorithm_configs.find_active()
            result = uow.algorithm_configs.replace(update)
            uow.audits.record(
                AuditRecord(
                    actor_id,
                    "modify_algorithm_config",
                    "algorithm_config",
                    str(result.config_id),
                    {"version": before.version} if before else None,
                    {
                        "version": update.version,
                        "payload": dict(update.parameters),
                        "active": update.active,
                    },
                    None,
                    trace_id,
                )
            )
            return result

        return self._execute(operation)
