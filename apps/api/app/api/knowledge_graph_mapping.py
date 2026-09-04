from app.contexts.knowledge_graph import (
    KnowledgeGraphBuildResult,
    KnowledgeGraphMapping,
    KnowledgeGraphStatus,
    KnowledgeGraphSyncResult,
    KnowledgeGraphUpstreamResult,
)
from app.domain.json_types import thaw_json


def status_data(value: KnowledgeGraphStatus) -> dict[str, object]:
    result: dict[str, object] = {"status": value.status, "enabled": value.enabled}
    if value.enabled:
        result.update(
            service=thaw_json(value.service), upstream_trace_id=value.upstream_trace_id
        )
    return result


def mapping_data(value: KnowledgeGraphMapping) -> dict[str, object]:
    if value.entity_type is None:
        return {"sync_status": value.sync_status}
    return {
        "entity_type": value.entity_type,
        "main_system_id": value.main_system_id,
        "knowledge_graph_id": value.knowledge_graph_id,
        "sync_version": value.sync_version,
        "sync_status": value.sync_status,
        "last_error_code": value.last_error_code,
        "last_error_message": value.last_error_message,
        "last_trace_id": value.last_trace_id,
        "synced_at": value.synced_at,
        "updated_at": value.updated_at,
    }


def sync_data(value: KnowledgeGraphSyncResult) -> dict[str, object]:
    return {
        "document_id": value.document_id,
        "knowledge_graph_id": value.knowledge_graph_id,
        "sync_version": value.sync_version,
        "sync_status": value.sync_status,
        "idempotent": value.idempotent,
        "upstream_trace_id": value.upstream_trace_id,
    }


def build_data(value: KnowledgeGraphBuildResult) -> dict[str, object]:
    return {
        "position_id": value.position_id,
        "knowledge_graph_position_id": value.knowledge_graph_position_id,
        "build_run": thaw_json(value.build_run),
        "upstream_trace_id": value.upstream_trace_id,
    }


def upstream_data(value: KnowledgeGraphUpstreamResult) -> dict[str, object]:
    return {
        "result": thaw_json(value.result),
        "upstream": {
            "code": value.upstream.code,
            "message": value.upstream.message,
            "details": thaw_json(value.upstream.details),
            "trace_id": value.upstream.trace_id,
        },
    }
