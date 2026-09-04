from __future__ import annotations
from app.domain.json_types import FrozenJsonObject

from dataclasses import dataclass
from datetime import date, datetime
from collections.abc import Mapping
from typing import Protocol


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_type: str
    source_name: str | None
    title: str
    url: str | None
    raw_text: str | None
    publish_date: date | None
    credibility_score: float
    related_object_type: str | None
    related_object_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    source_platform: str | None = None
    enterprise_id: str | None = None
    template_cluster_id: str | None = None
    source_version: str | None = None
    source_fact_id: str | None = None
    source_jd_id: str | None = None
    # TEMP-LAG lineage: the SourceJDVersion primary key, carried independently
    # from ``source_version`` (which in the governance/release world is the
    # SOURCE FACT version, not the crawler SourceJDVersion.source_version).
    source_jd_version_id: str | None = None


@dataclass(frozen=True)
class EvidenceDraft:
    source_type: str
    source_name: str | None
    title: str
    url: str | None
    raw_text: str | None
    publish_date: date | None
    credibility_score: float
    related_object_type: str | None
    related_object_id: str | None
    source_platform: str | None = None
    enterprise_id: str | None = None
    template_cluster_id: str | None = None
    source_version: str | None = None
    source_fact_id: str | None = None
    source_jd_id: str | None = None
    source_jd_version_id: str | None = None


@dataclass(frozen=True)
class ReviewRecord:
    task_id: str
    object_type: str
    object_id: str
    priority: str
    reason: str | None
    status: str
    reviewer_id: str | None
    review_comment: str | None
    modified_payload: dict | None
    created_at: datetime | None
    updated_at: datetime | None
    reviewer_name: str | None = None
    object_name: str | None = None
    review_stage: str | None = None


@dataclass(frozen=True)
class ReviewEventRecord:
    event_id: str
    task_id: str
    actor_user_id: str
    action: str
    before_status: str | None
    after_status: str
    comment: str | None
    payload_snapshot: dict | None
    created_at: datetime | None


@dataclass(frozen=True)
class RagGenerationRecord:
    generation_id: str
    prompt: str
    text: str
    evidence_ids: tuple[str, ...]
    citations: tuple[dict, ...]
    need_review: bool
    status: str
    created_by: str
    confirmed_by: str | None
    created_at: datetime | None
    updated_at: datetime | None


class EvidenceRepository(Protocol):
    def add(self, draft: EvidenceDraft) -> EvidenceRecord: ...
    def list(self) -> list[EvidenceRecord]: ...
    def get(self, evidence_id: str) -> EvidenceRecord | None: ...
    def update(self, evidence_id: str, changes: FrozenJsonObject) -> EvidenceRecord: ...
    def delete(self, evidence_id: str) -> None: ...
    def related(self, object_type: str, object_id: str) -> list[EvidenceRecord]: ...


class ReviewRepository(Protocol):
    def add(self, object_type: str, object_id: str, priority: str, reason: str | None) -> ReviewRecord: ...
    def list(self) -> list[ReviewRecord]: ...
    def list_page(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        task_kind: str | None = None,
    ) -> tuple[list[ReviewRecord], int]: ...
    def counts_by_status(self) -> Mapping[str, int]: ...
    def get(self, task_id: str) -> ReviewRecord | None: ...
    def context(self, task_id: str) -> FrozenJsonObject: ...
    def validate_approve_active(
        self,
        parse_result_id: str,
        *,
        task_id: str,
        actor_id: str,
        actor_role: str,
    ) -> None: ...
    def unresolved_skills(self) -> list[FrozenJsonObject]: ...
    def transition(
        self,
        task_id: str,
        *,
        actor_id: str,
        action: str,
        status: str,
        comment: str | None = None,
        modified_payload: dict | None = None,
    ) -> ReviewRecord: ...
    def history(self, task_id: str) -> list[ReviewEventRecord]: ...
    def approve_active(
        self,
        parse_result_id: str,
        *,
        task_id: str | None,
        actor_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> None: ...
    def set_jd_parse_review_status(
        self,
        parse_result_id: str,
        *,
        workflow_status: str,
        need_review: bool,
    ) -> None: ...


class RagGenerationRepository(Protocol):
    def add(self, *, prompt: str, text: str, evidence_ids: list[str], citations: list[FrozenJsonObject], need_review: bool, created_by: str) -> RagGenerationRecord: ...
    def get(self, generation_id: str) -> RagGenerationRecord | None: ...
    def update_text(self, generation_id: str, text: str) -> RagGenerationRecord: ...
    def confirm(self, generation_id: str, actor_id: str) -> RagGenerationRecord: ...
    def list_need_review(self) -> list[RagGenerationRecord]: ...


class EvidenceRetrieverPort(Protocol):
    def retrieve(self, query: str, documents: tuple[FrozenJsonObject, ...], top_k: int) -> tuple[FrozenJsonObject, ...]: ...
    def metadata(self) -> tuple[str, str]: ...


class GovernanceUnitOfWork(Protocol):
    evidence: EvidenceRepository
    reviews: ReviewRepository
    rag: RagGenerationRepository

    def __enter__(self) -> "GovernanceUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
