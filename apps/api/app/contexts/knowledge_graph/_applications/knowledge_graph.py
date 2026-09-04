from __future__ import annotations

from dataclasses import dataclass

from app.domain.accounts import AccountActor
from app.contexts.knowledge_graph._ports.knowledge_graph import (
    KnowledgeGraphBuildResult,
    KnowledgeGraphBuildCommand,
    KnowledgeGraphIntegrationFactory,
    KnowledgeGraphMapping,
    KnowledgeGraphStatus,
    KnowledgeGraphSyncResult,
    KnowledgeGraphUpstreamResult,
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
)
from app.domain.permissions import require_permission
from app.domain.errors import PermissionDenied  # noqa: F401 - compatibility export


class KnowledgeGraphIntegrationDisabled(RuntimeError):
    pass


class KnowledgeGraphIntegrationNotFound(LookupError):
    pass


class KnowledgeGraphIntegrationConflict(RuntimeError):
    pass


class KnowledgeGraphIntegrationRuleViolation(ValueError):
    pass


@dataclass(frozen=True)
class ManageKnowledgeGraphIntegration:
    """Application facade; API authorization and concrete adapters stay outside it."""

    adapter_factory: KnowledgeGraphIntegrationFactory

    def status(self, actor: AccountActor) -> KnowledgeGraphStatus:
        with self.adapter_factory() as adapter:
            return adapter.status()

    def update_mapping(self, actor: AccountActor, entity_type: str, main_system_id: str, knowledge_graph_id: str) -> KnowledgeGraphMapping:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.set_mapping(entity_type, main_system_id, knowledge_graph_id)

    def list_mappings(
        self, actor: AccountActor, entity_type: str, query: str | None, status: str | None
    ):
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.list_mappings(entity_type, query, status)

    def mapping_candidates(
        self, actor: AccountActor, entity_type: str, query: str | None
    ):
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.mapping_candidates(entity_type, query)

    def cancel_mapping(
        self, actor: AccountActor, entity_type: str, main_system_id: str
    ) -> KnowledgeGraphMapping:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.cancel_mapping(entity_type, main_system_id)

    def retry_mapping(
        self, actor: AccountActor, entity_type: str, main_system_id: str
    ) -> KnowledgeGraphMapping:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.retry_mapping(entity_type, main_system_id)

    def sync_jd(self, actor: AccountActor, document_id: str) -> KnowledgeGraphSyncResult:
        require_permission(actor.role, 'kg.build.manage')
        return self.adapter_factory.sync_jd(document_id, actor)

    def jd_status(self, actor: AccountActor, document_id: str) -> KnowledgeGraphMapping:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.mapping_status(document_id)

    def build(self, actor: AccountActor, position_id: str, command: KnowledgeGraphBuildCommand) -> KnowledgeGraphBuildResult:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.build(position_id, command, actor)

    def build_runs(self, actor: AccountActor, position_id: str) -> KnowledgeGraphUpstreamResult:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.build_runs(position_id)

    def build_run(self, actor: AccountActor, run_id: str) -> KnowledgeGraphUpstreamResult:
        require_permission(actor.role, 'kg.build.manage')
        with self.adapter_factory() as adapter:
            return adapter.build_run(run_id)

    def graph(self, actor: AccountActor, position_id: str) -> KnowledgeGraphUpstreamResult:
        with self.adapter_factory() as adapter:
            return adapter.graph(position_id)

    def versions(self, actor: AccountActor, position_id: str) -> KnowledgeGraphUpstreamResult:
        with self.adapter_factory() as adapter:
            return adapter.versions(position_id)

    def relation_evidence(self, actor: AccountActor, relation_id: str) -> KnowledgeGraphUpstreamResult:
        require_permission(actor.role, 'evidence.read_public')
        with self.adapter_factory() as adapter:
            return adapter.relation_evidence(relation_id)

    def portal(
        self, actor: AccountActor, command: KnowledgeGraphPortalCommand
    ) -> KnowledgeGraphUpstreamResult:
        permission_by_operation = {
            KnowledgeGraphPortalOperation.LIST_POSITIONS: 'catalog.read_published',
            KnowledgeGraphPortalOperation.POSITION: 'catalog.read_published',
            KnowledgeGraphPortalOperation.GRAPH: 'catalog.read_published',
            KnowledgeGraphPortalOperation.REQUIREMENT_INFLATION: 'catalog.read_published',
            KnowledgeGraphPortalOperation.RELATION_EXPLANATION: 'catalog.read_published',
            KnowledgeGraphPortalOperation.RELATION_EVIDENCE: 'evidence.read_public',
            KnowledgeGraphPortalOperation.AGGREGATE_EVIDENCE: 'evidence.read_public',
            KnowledgeGraphPortalOperation.OPEN_DRAFT: 'kg.build.manage',
            KnowledgeGraphPortalOperation.DRAFT_GRAPH: 'kg.build.manage',
            KnowledgeGraphPortalOperation.MODIFY_RELATION: 'kg.build.manage',
            KnowledgeGraphPortalOperation.BUILD_RUNS: 'kg.build.manage',
            KnowledgeGraphPortalOperation.BUILD_RUN: 'kg.build.manage',
            KnowledgeGraphPortalOperation.BUILD_JOB: 'kg.build.manage',
            KnowledgeGraphPortalOperation.RETRY_BUILD_JOB: 'kg.build.manage',
            KnowledgeGraphPortalOperation.BUILD_SAMPLES: 'kg.build.manage',
            KnowledgeGraphPortalOperation.PUBLISH_GATE: 'kg.build.manage',
            KnowledgeGraphPortalOperation.PUBLISH: 'kg.build.manage',
            KnowledgeGraphPortalOperation.AUTO_REVIEW: 'kg.review.manage',
            KnowledgeGraphPortalOperation.UNRESOLVED: 'kg.normalization.manage',
            KnowledgeGraphPortalOperation.RESOLVE_UNRESOLVED: 'kg.normalization.manage',
            KnowledgeGraphPortalOperation.REVIEW_TASKS: 'kg.review.manage',
            KnowledgeGraphPortalOperation.REVIEW_TASK: 'kg.review.manage',
            KnowledgeGraphPortalOperation.REVIEW_ACTION: 'kg.review.manage',
            KnowledgeGraphPortalOperation.REVIEW_BATCH: 'kg.review.manage',
            KnowledgeGraphPortalOperation.VERSIONS: 'kg.version.manage',
            KnowledgeGraphPortalOperation.VERSION: 'kg.version.manage',
            KnowledgeGraphPortalOperation.VERSION_DIFF: 'kg.version.manage',
            KnowledgeGraphPortalOperation.ROLLBACK: 'kg.version.manage',
            KnowledgeGraphPortalOperation.EVOLUTION_EVENTS: 'catalog.read_published',
            KnowledgeGraphPortalOperation.EVOLUTION_EVENT: 'catalog.read_published',
            KnowledgeGraphPortalOperation.CAPABILITY_EVOLUTION: 'catalog.read_published',
        }
        require_permission(actor.role, permission_by_operation[command.operation])
        with self.adapter_factory() as adapter:
            return adapter.portal(command, actor)
