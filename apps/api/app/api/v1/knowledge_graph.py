from fastapi import APIRouter, Depends

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
from app.api.knowledge_graph_contracts import (
    KnowledgeGraphBuildRequest,
    KnowledgeGraphMappingUpdate,
)
from app.api.knowledge_graph_mapping import (
    build_data,
    mapping_data,
    status_data,
    sync_data,
    upstream_data,
)
from app.contexts.knowledge_graph import ManageKnowledgeGraphIntegration
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.contexts.knowledge_graph import KnowledgeGraphBuildCommand


router = APIRouter(
    prefix="/integrations/knowledge-graph", tags=["knowledge-graph-integration"]
)


@router.get("/status")
def integration_status(
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=status_data(handlers.status(actor)))


@router.put("/mappings/{entity_type}/{main_system_id}")
def update_mapping(
    entity_type: str,
    main_system_id: str,
    payload: KnowledgeGraphMappingUpdate,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(
        data=mapping_data(
            handlers.update_mapping(
                actor, entity_type, main_system_id, payload.knowledge_graph_id
            )
        )
    )


@router.post("/jds/{document_id}/sync")
def sync_jd(
    document_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=sync_data(handlers.sync_jd(actor, document_id)))


@router.get("/jds/{document_id}/status")
def jd_sync_status(
    document_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=mapping_data(handlers.jd_status(actor, document_id)))


@router.post("/positions/{position_id}/build")
def build_position_graph(
    position_id: str,
    payload: KnowledgeGraphBuildRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(
        data=build_data(
            handlers.build(
                actor,
                position_id,
                KnowledgeGraphBuildCommand(
                    payload.window_start,
                    payload.window_end,
                    payload.minimum_effective_weight,
                    payload.minimum_valid_samples,
                ),
            )
        )
    )


@router.get("/positions/{position_id}/build-runs")
def list_build_runs(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=upstream_data(handlers.build_runs(actor, position_id)))


@router.get("/build-runs/{build_run_id}")
def get_build_run(
    build_run_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=upstream_data(handlers.build_run(actor, build_run_id)))


@router.get("/positions/{position_id}/graph")
def get_published_graph(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=upstream_data(handlers.graph(actor, position_id)))


@router.get("/positions/{position_id}/versions")
def get_versions(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=upstream_data(handlers.versions(actor, position_id)))


@router.get("/relations/{relation_id}/evidence")
def get_relation_evidence(
    relation_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(data=upstream_data(handlers.relation_evidence(actor, relation_id)))
