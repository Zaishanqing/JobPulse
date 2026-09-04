from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from typing import Protocol
from app.integration_events import OutboxMessageDraft

from app.domain.positions import PositionSkill
from app.domain.json_types import FrozenJsonValue


PositionItems = tuple[PositionSkill, ...]


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    position_name: str
    source_emerging_position_id: str | None
    core_responsibilities: tuple[str, ...]
    required_skills: PositionItems
    bonus_skills: PositionItems
    industry_scenarios: tuple[str, ...]
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    graph_onboarding_status: str = "mapping_required"
    taxonomy_family_code: str | None = None
    taxonomy_family_name: str | None = None
    position_code: str | None = None
    skill_domain_codes: tuple[str, ...] = ()
    definition: str = ""
    aliases: tuple[str, ...] = ()
    include_when: tuple[str, ...] = ()
    exclude_when: tuple[str, ...] = ()
    confusable_with: tuple[FrozenJsonValue, ...] = ()
    taxonomy_version: str = "position-taxonomy.v3.0.0"
    lifecycle_status: str = "active"
    deprecated_at: datetime | None = None
    replaced_by: str | None = None
    sample_support_status: str = "none"


@dataclass(frozen=True)
class PositionDraft:
    position_name: str
    source_emerging_position_id: str | None
    core_responsibilities: tuple[str, ...]
    required_skills: PositionItems
    bonus_skills: PositionItems
    industry_scenarios: tuple[str, ...]
    status: str
    taxonomy_family_code: str | None = None
    taxonomy_family_name: str | None = None
    position_code: str | None = None
    skill_domain_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionChanges:
    changed_fields: frozenset[str]
    position_name: str | None = None
    core_responsibilities: tuple[str, ...] | None = None
    required_skills: PositionItems | None = None
    bonus_skills: PositionItems | None = None
    industry_scenarios: tuple[str, ...] | None = None
    status: str | None = None
    taxonomy_family_code: str | None = None
    taxonomy_family_name: str | None = None
    position_code: str | None = None
    skill_domain_codes: tuple[str, ...] | None = None


class PositionRepository(Protocol):
    def add(self, draft: PositionDraft) -> PositionRecord: ...
    def get(self, position_id: str) -> PositionRecord | None: ...
    def list_positions(self) -> list[PositionRecord]: ...
    def jd_counts_by_position(self) -> Mapping[str, int]: ...
    def update(self, position_id: str, changes: PositionChanges) -> PositionRecord: ...
    def delete(self, position_id: str) -> None: ...


class PositionUnitOfWork(Protocol):
    positions: PositionRepository
    def add_outbox(self, draft: OutboxMessageDraft) -> None: ...
    def __enter__(self) -> "PositionUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
