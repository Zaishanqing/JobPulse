"""Persistence-facing facts for TraceSkill innovation application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.dependency_analysis import DependencyCandidate, RequirementContext
from app.domain.projections import GraphProjection
from app.domain.temporal_analysis import BuildInputWatermark, WatermarkSourceFact
from app.domain.traceability import (
    ClaimEvidenceRef,
    MappingCandidate,
    RelationClaim,
)
from app.domain.value_types import SerializedPayload


@dataclass(frozen=True)
class BuildWatermarkFacts:
    build_run_id: int
    source_facts: tuple[WatermarkSourceFact, ...]
    observation_window_start: str
    observation_window_end: str
    catalog_snapshot_id: str
    catalog_source_version: str
    validation_state: Literal["present", "absent"]
    validation_policy_version: str | None
    mapping_policy_version: str
    aggregation_algorithm_version: str
    normalized_config: SerializedPayload
    input_coverage: float


@dataclass(frozen=True)
class ClaimSourceFact:
    support_id: int
    build_run_id: int
    position_id: str
    skill_id: str
    source_kind: Literal["published_fact", "legacy_local"]
    source_fact_id: str
    source_fact_version: str
    requirement_id: str
    evidence: ClaimEvidenceRef
    validation_lineage_lineage_version: str | None
    observed_at: str


@dataclass(frozen=True)
class MappingCandidateState:
    candidate: MappingCandidate
    priority: float
    status: str
    revision: int


@dataclass(frozen=True)
class SavedDependencyAnalysis:
    analysis_run_id: int
    build_run_id: int
    candidate_count: int
    rejected_count: int


@dataclass(frozen=True)
class DependencyProjectionFacts:
    candidates: tuple[DependencyCandidate, ...]


@dataclass(frozen=True)
class ProjectionFacts:
    graph_version_id: int
    source_version: str
    watermark: BuildInputWatermark
    claims: tuple[RelationClaim, ...]
    mapping_candidates: tuple[MappingCandidate, ...]
    dependency_candidates: tuple[DependencyCandidate, ...]


@dataclass(frozen=True)
class SavedProjection:
    manifest_id: int
    projection: GraphProjection


@dataclass(frozen=True)
class DependencyContextFacts:
    build_run_id: int
    contexts: tuple[RequirementContext, ...]
