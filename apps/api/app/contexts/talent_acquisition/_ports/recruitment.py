from __future__ import annotations
from app.domain.json_types import FrozenJsonObject

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from app.integration_events import OutboxMessageDraft


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    enterprise_id: str
    title: str
    standard_position_id: str | None
    jd_text: str | None
    requirement_graph: FrozenJsonObject | None
    headcount: int
    location: str | None
    employment_type: str | None
    salary_min: int | None
    salary_max: int | None
    salary_unit: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class PublishedJobRecord:
    job_id: str
    enterprise_name: str
    title: str
    jd_text: str | None
    headcount: int
    location: str | None
    employment_type: str | None
    salary_min: int | None
    salary_max: int | None
    salary_unit: str
    status: str


@dataclass(frozen=True)
class SkillWeightRecord:
    weight_id: str
    job_id: str
    skill_id: str
    weight: float
    is_required: bool
    is_bonus: bool


@dataclass(frozen=True)
class SkillWeightInput:
    skill_id: str
    weight: float
    is_required: bool
    is_bonus: bool


class JobRepository(Protocol):
    def enterprise_owner(self, enterprise_id: str) -> str | None: ...
    def get(self, job_id: str) -> JobRecord | None: ...
    def list_all(self) -> list[JobRecord]: ...
    def list_for_owner(self, owner_id: str) -> list[JobRecord]: ...
    def list_published(self) -> list[PublishedJobRecord]: ...
    def get_published(self, job_id: str) -> PublishedJobRecord | None: ...
    def add(self, values: FrozenJsonObject) -> JobRecord: ...
    def update(self, job_id: str, changes: FrozenJsonObject) -> JobRecord: ...
    def delete(self, job_id: str) -> None: ...
    def list_weights(self, job_id: str) -> list[SkillWeightRecord]: ...
    def replace_weights(
        self, job_id: str, weights: list[SkillWeightInput]
    ) -> list[SkillWeightRecord]: ...
    def clear_weights(self, job_id: str) -> int: ...


class RecruitmentUnitOfWork(Protocol):
    jobs: JobRepository
    def add_outbox(self, draft: OutboxMessageDraft) -> None: ...

    def __enter__(self) -> "RecruitmentUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
