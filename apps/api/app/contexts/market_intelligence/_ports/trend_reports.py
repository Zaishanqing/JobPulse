from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.domain.trend_analysis import (
    SkillComboShift,
    SkillReplacement,
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendRisk,
    TrendSkill,
)
from app.contexts.tasks import TaskRecord
from app.domain.json_types import FrozenJsonObject


@dataclass(frozen=True)
class TrendGraphVersion:
    version_id: str
    graph: TrendGraphSnapshot


@dataclass(frozen=True)
class PositionSkillTrendInput:
    graph: TrendGraphSnapshot
    standard_skills: tuple[FrozenJsonObject, ...]
    skill_catalog_version: str


@dataclass(frozen=True)
class TrendReportRecord:
    report_id: str
    position_id: str
    graph_version_id: str | None
    time_window_start: date | None
    time_window_end: date | None
    current_graph: TrendGraphSnapshot
    skill_weight_distribution: SkillWeightDistribution
    new_skills: tuple[TrendSkill, ...]
    rising_skills: tuple[TrendSkill, ...]
    declining_skills: tuple[TrendSkill, ...]
    replaced_skills: tuple[SkillReplacement, ...]
    skill_combo_shifts: tuple[SkillComboShift, ...]
    risks: tuple[TrendRisk, ...]
    summary: str | None
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    provider_run_id: str | None = None
    algorithm_version: str | None = None
    formula_version: str | None = None
    skill_catalog_version: str | None = None
    source_coverage: float | None = None
    missing_sources: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    unresolved_terms: tuple[FrozenJsonObject, ...] = ()
    skill_trend_details: tuple[FrozenJsonObject, ...] = ()
    algorithm_result: FrozenJsonObject | None = None
    reviewed_result: FrozenJsonObject | None = None
    review_adjustments: tuple[FrozenJsonObject, ...] = ()


@dataclass(frozen=True)
class TrendReportDraft:
    position_id: str
    graph_version_id: str
    time_window_start: date | None
    time_window_end: date | None
    current_graph: TrendGraphSnapshot
    skill_weight_distribution: SkillWeightDistribution
    new_skills: tuple[TrendSkill, ...]
    rising_skills: tuple[TrendSkill, ...]
    declining_skills: tuple[TrendSkill, ...]
    replaced_skills: tuple[SkillReplacement, ...]
    skill_combo_shifts: tuple[SkillComboShift, ...]
    risks: tuple[TrendRisk, ...]
    summary: str
    provider_run_id: str
    algorithm_version: str
    formula_version: str
    skill_catalog_version: str
    source_coverage: float
    missing_sources: tuple[str, ...]
    quality_flags: tuple[str, ...]
    evidence_references: tuple[str, ...]
    unresolved_terms: tuple[FrozenJsonObject, ...]
    skill_trend_details: tuple[FrozenJsonObject, ...]


@dataclass(frozen=True)
class TrendReportChanges:
    changed_fields: frozenset[str]
    current_graph: TrendGraphSnapshot | None = None
    skill_weight_distribution: SkillWeightDistribution | None = None
    new_skills: tuple[TrendSkill, ...] | None = None
    rising_skills: tuple[TrendSkill, ...] | None = None
    declining_skills: tuple[TrendSkill, ...] | None = None
    replaced_skills: tuple[SkillReplacement, ...] | None = None
    skill_combo_shifts: tuple[SkillComboShift, ...] | None = None
    risks: tuple[TrendRisk, ...] | None = None
    summary: str | None = None
    status: str | None = None


class TrendReportRepository(Protocol):
    def add(self, draft: TrendReportDraft) -> TrendReportRecord: ...
    def get(self, report_id: str) -> TrendReportRecord | None: ...
    def list_by_position(self, position_id: str) -> list[TrendReportRecord]: ...
    def update(self, report_id: str, changes: TrendReportChanges) -> TrendReportRecord: ...
    def add_review_adjustment(self, report_id: str, actor_id: str, reason: str, changes: TrendReportChanges) -> TrendReportRecord: ...
    def get_by_provider(self, provider_run_id: str, position_id: str, graph_version_id: str) -> TrendReportRecord | None: ...


class TrendAnalysisUnitOfWork(Protocol):
    reports: TrendReportRepository
    def get_position_graph(self, position_id: str) -> TrendGraphSnapshot | None: ...
    def get_graph_version(self, position_id: str, version_id: str) -> TrendGraphVersion | None: ...
    def position_skill_input(self, graph: TrendGraphSnapshot) -> PositionSkillTrendInput: ...
    def flush_report(self, draft: TrendReportDraft) -> TrendReportRecord: ...
    def add_task(self, task: TaskRecord) -> None: ...
    def get_task(self, task_id: str) -> TaskRecord | None: ...
    def active_task_ids(self, limit: int = 50) -> tuple[str, ...]: ...
    def save_task(self, task: TaskRecord) -> None: ...
    def add_unresolved_terms(self, provider_run_id: str, terms: tuple[FrozenJsonObject, ...]) -> None: ...
    def report_publication_facts(self, report_id: str) -> FrozenJsonObject: ...
    def __enter__(self) -> "TrendAnalysisUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
