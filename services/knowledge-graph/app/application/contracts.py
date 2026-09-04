from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

from app.domain.value_types import (
    AuditSnapshot,
    ExtensionAttributes,
)
from app.domain.graph_building import BuildSummary
from app.domain.dependency_analysis import DependencyPolicy
from app.domain.temporal_analysis import ComparabilityContext
from app.domain.traceability import MappingCandidate, MappingPriorityWeights
from app.domain.lineage import PublishedFactLineage
from app.domain.structured_facts import (
    ExtractionFacts,
    JDDocumentInput,
    NormalizationFacts,
    PublishedJDFact,
    PublishedFactImportResult,
)
from jobgraph_contracts.release_manifest import ReleaseManifestV1


class ValidationLineageInput(TypedDict):
    data_validation_task_id: str
    validation_report_id: str
    validated_bundle_snapshot_id: str | None
    validation_policy_version: str
    validation_conclusion: str
    bundle_lineage_version: str


class CatalogSnapshotRefInput(TypedDict):
    source: str
    catalog_version: str
    content_hash: str
    source_version: str
    effective_at: str
    status: str


@dataclass(frozen=True)
class ImportJDCommand:
    document: JDDocumentInput
    actor_id: int | None = None
    trace_id: str = ""
    integration_context: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class ImportJDResult:
    document_id: str


@dataclass(frozen=True)
class AutoReviewBuildResult:
    build_run_id: int
    policy_version: str
    allowed_reasons: tuple[str, ...]
    auto_accepted_count: int
    requires_human_count: int
    auto_accepted_task_ids: tuple[int, ...]
    requires_human_task_ids: tuple[int, ...]


@dataclass(frozen=True)
class ImportPublishedJDFactCommand:
    fact: PublishedJDFact
    lineage: PublishedFactLineage = field(default_factory=PublishedFactLineage)


@dataclass(frozen=True)
class DocumentWorkflowCommand:
    document_id: str


@dataclass(frozen=True)
class ExtractionResult:
    facts: ExtractionFacts


@dataclass(frozen=True)
class NormalizationResult:
    facts: NormalizationFacts


PublishedJDFactResult = PublishedFactImportResult


@dataclass(frozen=True)
class ImportReleaseCommand:
    manifest: ReleaseManifestV1
    manifest_hash: str
    facts: tuple[ImportPublishedJDFactCommand, ...]


@dataclass(frozen=True)
class ImportReleaseResult:
    release_id: str
    record_count: int
    idempotent: bool


@dataclass(frozen=True)
class QualityAssessmentResult:
    normalization_version: str
    duplicate_score: float
    copy_risk_score: float
    inflation_score: float
    effective_sample_weight: float


@dataclass(frozen=True)
class BuildGraphCommand:
    position_id: str
    window_start: datetime | None
    window_end: datetime | None
    minimum_effective_weight: float
    minimum_valid_samples: int
    authoritative_only: bool
    actor_id: int | None = None
    trace_id: str = ""
    integration_context: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class BuildGraphResult:
    build_run_id: int
    status: str
    summary: BuildSummary


@dataclass(frozen=True)
class PublishGraphCommand:
    run_id: int
    actor_id: int
    trace_id: str
    reason: str | None
    version_name: str | None
    version_number: int | None
    release_notes: str | None


@dataclass(frozen=True)
class RollbackGraphCommand:
    position_id: str
    version_id: int
    actor_id: int
    trace_id: str
    reason: str | None


@dataclass(frozen=True)
class GraphVersionResult:
    version_id: int
    version_number: int
    rollback_from_version_id: int | None = None


@dataclass(frozen=True)
class GraphDraftResult:
    draft_id: int
    build_run_id: int
    position_id: str
    base_version_id: int | None


@dataclass(frozen=True)
class CreateMappingCandidateCommand:
    candidate: MappingCandidate
    weights: MappingPriorityWeights
    actor_id: int
    trace_id: str


@dataclass(frozen=True)
class ReviewMappingCandidateCommand:
    candidate_id: str
    expected_revision: int
    decision: str
    reviewer_id: int
    reason: str
    policy_version: str
    decided_at: str
    effective_scope: str
    replacement_candidate_id: str | None
    trace_id: str


@dataclass(frozen=True)
class AnalyzeDependenciesCommand:
    build_run_id: int
    policy: DependencyPolicy
    actor_id: int
    trace_id: str


@dataclass(frozen=True)
class ReviewDependencyCandidateCommand:
    candidate_id: int
    decision: str
    reviewer_id: int
    reason: str
    policy_version: str
    decided_at: str
    trace_id: str


@dataclass(frozen=True)
class ReviewDependencyCandidateResult:
    candidate_id: int
    decision: str
    idempotent: bool


@dataclass(frozen=True)
class RebuildProjectionCommand:
    graph_version_id: int
    projection_version: str
    actor_id: int
    trace_id: str


@dataclass(frozen=True)
class CompareWatermarksCommand:
    left_build_run_id: int
    right_build_run_id: int
    context: ComparabilityContext
