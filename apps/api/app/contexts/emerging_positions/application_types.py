from __future__ import annotations

from dataclasses import dataclass

from app.contexts.emerging_positions.domain import (
    DefinitionVersionRecord,
    EmergingRecord,
)
from app.domain.emerging_position import GerminationAssessment
from app.domain.values import FrozenDict


@dataclass(frozen=True)
class EmergingChanges:
    changed_fields: frozenset[str]
    position_name: str | None = None
    core_responsibilities: tuple[str, ...] | None = None
    required_skills: tuple[FrozenDict[str, object], ...] | None = None
    bonus_skills: tuple[FrozenDict[str, object], ...] | None = None
    industry_scenarios: tuple[str, ...] | None = None
    status: str | None = None
    field_evidence: FrozenDict[str, object] | None = None


@dataclass(frozen=True)
class ReviewEmergingDefinitionCommand:
    conclusion: str
    reason: str
    position_name: str | None = None
    core_responsibilities: tuple[str, ...] | None = None
    required_skills: tuple[FrozenDict[str, object], ...] | None = None
    field_evidence: FrozenDict[str, object] | None = None


@dataclass(frozen=True)
class GerminationAssessmentRecord:
    emerging_id: str
    assessment: GerminationAssessment
    discovery_run_id: str | None
    qualification_basis: str = "cluster_assessment"


@dataclass(frozen=True)
class GeneratedDefinitionRecord:
    record: EmergingRecord
    definition_version_id: str
    generation_mode: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class DefinitionSelectionRecord:
    definition: EmergingRecord
    version: DefinitionVersionRecord


__all__ = [
    "DefinitionSelectionRecord",
    "EmergingChanges",
    "GeneratedDefinitionRecord",
    "GerminationAssessmentRecord",
    "ReviewEmergingDefinitionCommand",
]
