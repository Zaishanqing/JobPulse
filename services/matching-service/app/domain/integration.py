"""Immutable contracts for upstream mapping diagnostics and end-to-end runs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import GapAnalysis
from app.domain.profiles import CVMatchProfile, ImmutableDTO, PositionMatchProfile


class ContractIssue(ImmutableDTO):
    side: Literal["cv", "position", "transport", "graph"]
    code: str = Field(min_length=1)
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Literal["error", "warning"]
    source_version: str | None = None


class SourceVersionRecord(ImmutableDTO):
    source_system: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    taxonomy_version: str | None = None
    derivation_version: str | None = None
    snapshot_id: str | None = None
    graph_version: str | None = None
    source_version: str | None = None
    created_at: datetime | None = None


class ContractIntegrationResult(ImmutableDTO):
    integration_status: Literal["completed", "rejected"]
    cv_profile: CVMatchProfile | None = None
    position_profile: PositionMatchProfile | None = None
    evaluation: MatchEvaluation | None = None
    gap_analysis: GapAnalysis | None = None
    contract_issues: tuple[ContractIssue, ...] = ()
    source_versions: tuple[SourceVersionRecord, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
