from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import ContextManager, Generic, Protocol, TypeVar

from app.domain.accounts import AccountActor
from app.domain.json_types import FrozenJsonObject, FrozenJsonValue


T = TypeVar("T")


@dataclass(frozen=True)
class KnowledgeGraphStatus:
    status: str
    enabled: bool
    service: FrozenJsonValue = None
    upstream_trace_id: str | None = None


@dataclass(frozen=True)
class KnowledgeGraphMapping:
    sync_status: str
    entity_type: str | None = None
    main_system_id: str | None = None
    knowledge_graph_id: str | None = None
    sync_version: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_trace_id: str | None = None
    synced_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class KnowledgeGraphSyncResult:
    document_id: str
    knowledge_graph_id: str | None
    sync_version: str
    sync_status: str
    idempotent: bool
    upstream_trace_id: str | None


@dataclass(frozen=True)
class KnowledgeGraphBuildResult:
    position_id: str
    knowledge_graph_position_id: str
    build_run: FrozenJsonValue
    upstream_trace_id: str | None


@dataclass(frozen=True)
class KnowledgeGraphBuildCommand:
    window_start: str | None
    window_end: str | None
    minimum_effective_weight: float
    minimum_valid_samples: int


@dataclass(frozen=True)
class KnowledgeGraphUpstream:
    code: int
    message: str
    details: FrozenJsonValue
    trace_id: str | None
    response_headers: FrozenJsonValue = None


@dataclass(frozen=True)
class KnowledgeGraphUpstreamResult(Generic[T]):
    result: T
    upstream: KnowledgeGraphUpstream


class KnowledgeGraphPortalOperation(str, Enum):
    LIST_POSITIONS = "list_positions"
    POSITION = "position"
    GRAPH = "graph"
    REQUIREMENT_INFLATION = "requirement_inflation"
    RELATIONS = "relations"
    RELATION_EXPLANATION = "relation_explanation"
    OPEN_DRAFT = "open_draft"
    DRAFT_GRAPH = "draft_graph"
    MODIFY_RELATION = "modify_relation"
    BUILD_RUNS = "build_runs"
    BUILD_RUN = "build_run"
    BUILD_JOB = "build_job"
    RETRY_BUILD_JOB = "retry_build_job"
    BUILD_SAMPLES = "build_samples"
    PUBLISH_GATE = "publish_gate"
    PUBLISH = "publish"
    AUTO_REVIEW = "auto_review"
    UNRESOLVED = "unresolved"
    RESOLVE_UNRESOLVED = "resolve_unresolved"
    REVIEW_TASKS = "review_tasks"
    REVIEW_TASK = "review_task"
    REVIEW_ACTION = "review_action"
    REVIEW_BATCH = "review_batch"
    VERSIONS = "versions"
    VERSION = "version"
    VERSION_DIFF = "version_diff"
    ROLLBACK = "rollback"
    AGGREGATE_EVIDENCE = "aggregate_evidence"
    RELATION_EVIDENCE = "relation_evidence"
    EVOLUTION_EVENTS = "evolution_events"
    EVOLUTION_EVENT = "evolution_event"
    CAPABILITY_EVOLUTION = "capability_evolution"


@dataclass(frozen=True)
class KnowledgeGraphPortalCommand:
    operation: KnowledgeGraphPortalOperation
    position_id: str | None = None
    resource_id: str | None = None
    secondary_id: str | None = None
    action: str | None = None
    kind: str | None = None
    payload: FrozenJsonObject | None = None
    params: FrozenJsonObject | None = None


class KnowledgeGraphIntegrationPort(Protocol):
    """Narrow boundary used by the main backend to coordinate the KG context."""

    def status(self) -> KnowledgeGraphStatus: ...
    def set_mapping(self, entity_type: str, main_system_id: str, knowledge_graph_id: str) -> KnowledgeGraphMapping: ...
    def list_mappings(self, entity_type: str, query: str | None, status: str | None) -> FrozenJsonValue: ...
    def mapping_candidates(self, entity_type: str, query: str | None) -> FrozenJsonValue: ...
    def cancel_mapping(self, entity_type: str, main_system_id: str) -> KnowledgeGraphMapping: ...
    def retry_mapping(self, entity_type: str, main_system_id: str) -> KnowledgeGraphMapping: ...
    def sync_jd(self, document_id: str, actor: AccountActor) -> KnowledgeGraphSyncResult: ...
    def mapping_status(self, document_id: str) -> KnowledgeGraphMapping: ...
    def build(self, position_id: str, command: KnowledgeGraphBuildCommand, actor: AccountActor) -> KnowledgeGraphBuildResult: ...
    def build_runs(self, position_id: str) -> KnowledgeGraphUpstreamResult: ...
    def build_run(self, run_id: str) -> KnowledgeGraphUpstreamResult: ...
    def graph(self, position_id: str) -> KnowledgeGraphUpstreamResult: ...
    def versions(self, position_id: str) -> KnowledgeGraphUpstreamResult: ...
    def relation_evidence(self, relation_id: str) -> KnowledgeGraphUpstreamResult: ...
    def portal(self, command: KnowledgeGraphPortalCommand, actor: AccountActor) -> KnowledgeGraphUpstreamResult: ...


class KnowledgeGraphIntegrationFactory(Protocol):
    def __call__(self) -> ContextManager[KnowledgeGraphIntegrationPort]: ...
    def sync_jd(self, document_id: str, actor: AccountActor) -> KnowledgeGraphSyncResult: ...
