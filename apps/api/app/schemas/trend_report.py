from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TrendAnalysisTaskRequest(BaseModel):
    time_window_start: date | None = None
    time_window_end: date | None = None


class TrendReportUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    current_graph: dict[str, Any] | None = None
    skill_weight_distribution: dict[str, Any] | None = None
    new_skills: list[dict[str, Any]] | None = None
    rising_skills: list[dict[str, Any]] | None = None
    declining_skills: list[dict[str, Any]] | None = None
    replaced_skills: list[dict[str, Any]] | None = None
    skill_combo_shifts: list[dict[str, Any]] | None = None
    risks: list[dict[str, Any]] | None = None
    summary: str | None = None
