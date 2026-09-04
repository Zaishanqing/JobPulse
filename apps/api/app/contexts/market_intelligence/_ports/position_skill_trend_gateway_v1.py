from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.json_types import FrozenJsonObject


@dataclass(frozen=True)
class CreatePositionSkillTrendV1:
    request_id: str
    idempotency_key: str
    position_id: str
    position_name: str
    graph_version_id: str
    standard_skills: tuple[FrozenJsonObject, ...]
    window_start: datetime
    window_end: datetime
    skill_catalog_version: str
    algorithm_version: str
    formula_version: str
    config_version: str
    data_sources: tuple[str, ...] = ("academic", "policy", "funding", "github")


@dataclass(frozen=True)
class PositionSkillTrendRunV1:
    run_id: str
    status: str
    error_message: str | None = None


@dataclass(frozen=True)
class PositionSkillTrendResultV1:
    payload: FrozenJsonObject


class PositionSkillTrendGatewayV1(Protocol):
    provider_name: str

    def create_position_skill_trend(self, request: CreatePositionSkillTrendV1) -> PositionSkillTrendRunV1: ...
    def get_position_skill_trend_run(self, run_id: str) -> PositionSkillTrendRunV1: ...
    def get_position_skill_trend_result(self, run_id: str) -> PositionSkillTrendResultV1: ...
