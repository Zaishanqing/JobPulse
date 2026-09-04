from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.emerging_position import EmergingCandidate, GerminationAssessment
from app.domain.values import FrozenDict


@dataclass(frozen=True)
class EmergingActor:
    actor_id: str
    role: str


@dataclass(frozen=True)
class ReleaseGateConfig:
    minimum_stability_score: float
    emerging_threshold: float


@dataclass(frozen=True)
class ClusterRecord:
    cluster_id: str
    cluster_name: str
    core_skills: tuple[FrozenDict[str, object], ...]
    representative_jd_ids: tuple[str, ...]
    stability_score: float
    discovery_run_id: str | None
    discovery_run_status: str | None
    assessment: GerminationAssessment
    generated_definition: FrozenDict[str, object]


@dataclass(frozen=True)
class EmergingRecord:
    candidate: EmergingCandidate
    created_at: datetime | None
    updated_at: datetime | None
    standard_position: StandardPositionRecord | None = None


@dataclass(frozen=True)
class StandardPositionRecord:
    standard_position_id: str
    position_name: str
    source_emerging_position_id: str | None
    status: str
    required_skills: tuple[FrozenDict[str, object], ...]
    created_at: datetime | None
    graph_onboarding_status: str = "mapping_required"


@dataclass(frozen=True)
class DefinitionVersionRecord:
    version_id: str
    emerging_id: str
    snapshot: FrozenDict[str, object]
    selected: bool
    created_by: str
    created_at: datetime | None


__all__ = [
    "ClusterRecord",
    "DefinitionVersionRecord",
    "EmergingActor",
    "EmergingRecord",
    "ReleaseGateConfig",
    "StandardPositionRecord",
]
