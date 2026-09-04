from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.evidence_rag import get_evidence_rag_handlers
from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
from app.api.dependencies.positions import get_position_use_cases
from app.api.dependencies.integration_status import get_integration_status_query
from app.api.dependencies.cv_ingestion import get_cv_ingestion_use_cases
from app.api.dependencies.use_cases import (
    get_discovery_candidate_handlers,
    get_emerging_position_handlers,
    get_jd_use_cases,
    get_position_discovery_handlers,
)
from app.api.emerging_position_mapping import (
    asset_with_definition,
    emerging_changes_from_data,
    assessment_data,
    definition_version_data,
    emerging_record_data,
)
from app.api.discovery_mapping import (
    candidate_data,
    candidate_detail_data,
    recent_signal_data,
    trajectory_data,
)
from app.api.position_mapping import position_data
from app.api.knowledge_graph_mapping import mapping_data
from app.contexts.catalog import ManagePositions, PositionNotFound
from app.contexts.discovery import (
    Actor as DiscoveryActor,
    DiscoveryCandidateHandlers,
    PositionDiscoveryHandlers,
)
from app.contexts.emerging_positions import (
    DiscoveryEvidenceUnavailable,
    EmergingActor,
    EmergingClusterNotFound,
    EmergingPositionHandlers,
    EmergingPositionNotFound,
)
from app.contexts.knowledge_graph import (
    KnowledgeGraphBuildCommand,
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
    ManageKnowledgeGraphIntegration,
)
from app.contexts.jd_lifecycle import JDApplicationError, JDUseCases
from app.contexts.integration_status import QueryIntegrationStatus
from app.contexts.evidence_rag import ManageEvidenceRag
from app.contexts.cv_ingestion import (
    CVExtractionConflict,
    CVExtractionNotFound,
    CVIngestionUseCases,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.json_types import FrozenJsonObject, freeze_json_object, thaw_json
from app.domain.values import thaw as thaw_domain
from app.domain.permissions import require_permission
from app.domain.json_types import thaw_json_object
from app.schemas.task import PortalDemoTaskCollectionEnvelope
from app.schemas.emerging_position import EmergingPositionUpdate
from app.contexts.evidence_rag import enqueue_published_graph_auto_index


router = APIRouter(prefix="/portal", tags=["portal"])


@router.get("/admin/integration-status")
def get_admin_integration_status(
    jd_id: str | None = Query(default=None),
    cv_task_id: str | None = Query(default=None),
    trend_task_id: str | None = Query(default=None),
    actor: AccountActor = Depends(get_account_actor),
    query: QueryIntegrationStatus = Depends(get_integration_status_query),
):
    try:
        result = query.get(
            actor,
            jd_id=jd_id,
            cv_task_id=cv_task_id,
            trend_task_id=trend_task_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success_response(data=thaw_json(result))


@router.get(
    "/admin/demo-tasks",
    response_model=PortalDemoTaskCollectionEnvelope,
)
def list_admin_demo_tasks(
    task_type: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None, max_length=32),
    object_id: str | None = Query(default=None, max_length=255),
    actor: AccountActor = Depends(get_account_actor),
    query: QueryIntegrationStatus = Depends(get_integration_status_query),
):
    try:
        result = query.list_demo_tasks(
            actor,
            task_type=task_type,
            status=status,
            object_id=object_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success_response(data=thaw_json(result))


@router.post("/admin/integration-status/cv-extraction-tasks/{task_id}/retry")
def retry_admin_cv_extraction(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        task = use_cases.retry_for_operations(actor, task_id)
    except CVExtractionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CVExtractionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    values = asdict(task)
    if task.validation_report_payload is not None:
        values["validation_report_payload"] = thaw_json_object(
            task.validation_report_payload
        )
    return success_response(data=values)


class _PortalBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BuildGraphBody(_PortalBody):
    window_start: datetime | None = None
    window_end: datetime | None = None
    minimum_effective_weight: float = Field(default=0.05, ge=0, le=1)
    minimum_valid_samples: int = Field(default=1, ge=1)


class AutoReviewBody(_PortalBody):
    policy_version: str = Field(default="review-policy.v1", min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class PublishGraphBody(_PortalBody):
    reason: str | None = None
    version_name: str | None = Field(default=None, min_length=1, max_length=80)
    version_number: int | None = Field(default=None, ge=1)
    release_notes: str | None = None


class OpenDraftBody(_PortalBody):
    base_version_id: int | None = Field(default=None, ge=1)


class MappingUpdateBody(_PortalBody):
    knowledge_graph_id: str = Field(min_length=1, max_length=80)


class ModifyRelationBody(_PortalBody):
    build_run_id: int = Field(ge=1)
    position_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    weight: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    importance_level: str | None = None
    reason: str = Field(min_length=1)


class ReviewActionBody(_PortalBody):
    reason: str = Field(min_length=1)
    payload: dict[str, Any] | None = None


def _body(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode='json', exclude_unset=True)


def _result(value) -> dict:
    data = (
        thaw_json(value.result)
        if isinstance(value.result, FrozenJsonObject)
        else value.result
    )
    return success_response(
        data=data,
        details={"upstream_trace_id": value.upstream.trace_id},
    )


def _pagination_headers(value, response: Response) -> None:
    headers = thaw_json(value.upstream.response_headers) or {}
    for name in ("X-Total-Count", "X-Page", "X-Page-Size"):
        if name in headers:
            response.headers[name] = str(headers[name])


def _command(
    operation: KnowledgeGraphPortalOperation,
    *,
    position_id: str | None = None,
    resource_id: str | int | None = None,
    action: str | None = None,
    kind: str | None = None,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> KnowledgeGraphPortalCommand:
    return KnowledgeGraphPortalCommand(
        operation=operation,
        position_id=position_id,
        resource_id=str(resource_id) if resource_id is not None else None,
        action=action,
        kind=kind,
        payload=freeze_json_object(payload) if payload is not None else None,
        params=freeze_json_object(params) if params is not None else None,
    )


def _portal(
    actor: AccountActor,
    handlers: ManageKnowledgeGraphIntegration,
    operation: KnowledgeGraphPortalOperation,
    **values,
) -> dict:
    return _result(handlers.portal(actor, _command(operation, **values)))


@router.get("/positions")
def list_published_positions(
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.LIST_POSITIONS)


@router.get("/positions/{position_id}")
def get_published_position(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
    positions: ManagePositions = Depends(get_position_use_cases),
):
    try:
        record = position_data(positions.get(position_id))
    except PositionNotFound as exc:
        raise HTTPException(status_code=404, detail='Published position not found') from exc
    graph = handlers.portal(
        actor,
        _command(KnowledgeGraphPortalOperation.GRAPH, position_id=position_id),
    )
    return success_response(
        data={"position": record, "graph": thaw_json(graph.result)},
        details={"upstream_trace_id": graph.upstream.trace_id},
    )


@router.get("/positions/{position_id}/graph")
def get_published_graph(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor, handlers, KnowledgeGraphPortalOperation.GRAPH, position_id=position_id
    )


@router.get("/positions/{position_id}/requirement-inflation")
def get_requirement_inflation(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.REQUIREMENT_INFLATION,
        position_id=position_id,
        params={
            "contract_version": "position-profile.v3",
            "view": "published",
        },
    )


@router.get("/evidence/relations/{relation_id}")
def get_relation_evidence(
    relation_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.RELATION_EVIDENCE,
        resource_id=relation_id,
    )


@router.get("/evidence/{kind}/{aggregate_id}")
def get_aggregate_evidence(
    kind: str,
    aggregate_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    if kind not in {"requirements", "tasks", "company_facts", "employment_facts"}:
        raise HTTPException(status_code=422, detail="Unsupported evidence aggregate kind")
    result = handlers.portal(
        actor,
        _command(
            KnowledgeGraphPortalOperation.AGGREGATE_EVIDENCE,
            kind=kind,
            resource_id=aggregate_id,
        ),
    )
    rows = thaw_json(result.result) or []
    if isinstance(rows, list):
        document_ids = {
            str(
                row.get("source", {}).get("document_id")
                or (row.get("evidence") or {}).get("document_id")
                or ""
            )
            for row in rows
        } - {""}
        raw_texts: dict[str, str] = {}
        if document_ids:
            for jd_id in document_ids:
                try:
                    record = use_cases.get_jd(actor, jd_id)
                except JDApplicationError:
                    continue
                raw_texts[jd_id] = str(getattr(record, "raw_text", None) or "")
        for row in rows:
            source = row.setdefault("source", {})
            document_id = str(
                source.get("document_id")
                or (row.get("evidence") or {}).get("document_id")
                or ""
            )
            if document_id and not source.get("raw_text"):
                source["raw_text"] = raw_texts.get(document_id, "")
    return success_response(
        data=rows,
        details={"upstream_trace_id": result.upstream.trace_id},
    )


def _asset_views(actor, handlers, emerging_handlers):
    assets = thaw_domain(handlers.query.emerging_assets(DiscoveryActor(actor.account_id, actor.role)))
    records = {record.candidate.candidate_id: record for record in emerging_handlers.query.list(EmergingActor(actor.account_id, actor.role))}
    result = []
    for asset in assets:
        record = records.get(asset["governance_id"])
        if record is not None:
            if actor.role == "admin":
                definition = emerging_record_data(record)
            else:
                definition = thaw_domain(record.candidate.published_snapshot).get("definition", {})
            asset = asset_with_definition(asset, definition)
        result.append(asset)
    return result


@router.get("/emerging-assets")
def list_emerging_assets(
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
    emerging_handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    require_permission(actor.role, "emerging.read_published")
    return success_response(data=_asset_views(actor, handlers, emerging_handlers))


@router.get("/emerging-assets/{asset_id}")
def get_emerging_asset(
    asset_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
    emerging_handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    require_permission(actor.role, "emerging.read_published")
    assets = _asset_views(actor, handlers, emerging_handlers)
    item = next((item for item in assets if item["emerging_id"] == asset_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Emerging discovery asset not found")
    return success_response(data=item)


@router.put("/emerging-assets/{asset_id}")
def update_emerging_asset(
    asset_id: str,
    payload: EmergingPositionUpdate,
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
    emerging_handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    require_permission(actor.role, "emerging.candidate.manage")
    assets = thaw_domain(handlers.query.emerging_assets(DiscoveryActor(actor.account_id, actor.role)))
    asset = next((item for item in assets if item["emerging_id"] == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="Emerging discovery asset not found")
    emerging_actor = EmergingActor(actor.account_id, actor.role)
    # Materialize missing editing records only on an explicit write, never on GET.
    emerging_handlers.import_formal.execute(emerging_actor)
    record = emerging_handlers.update.execute(
        asset["governance_id"],
        emerging_changes_from_data(payload.model_dump(exclude_unset=True)),
        emerging_actor,
    )
    return success_response(data=asset_with_definition(asset, emerging_record_data(record)))


@router.get("/emerging-positions")
def list_published_emerging_positions(
    actor: AccountActor = Depends(get_account_actor),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    require_permission(actor.role, "emerging.read_published")
    records = handlers.query.list(EmergingActor(actor.account_id, actor.role))
    return success_response(
        data=[
            emerging_record_data(item)
            for item in records
            if item.candidate.status.value == "published"
        ]
    )


@router.get("/emerging-positions/{emerging_id}")
def get_published_emerging_position(
    emerging_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    require_permission(actor.role, "emerging.read_published")
    emerging_actor = EmergingActor(actor.account_id, actor.role)
    record = handlers.query.get(emerging_id, emerging_actor)
    if record.candidate.status.value != "published":
        raise HTTPException(status_code=404, detail="Published emerging position not found")
    assessment = handlers.assessment.execute(emerging_id, emerging_actor)
    assessment_payload = assessment_data(assessment)
    return success_response(
        data={
            **emerging_record_data(record),
            # The detail score and decision must describe the same effective
            # assessment (cluster assessment or lifecycle override).
            "germination_score": assessment_payload["germination_score"],
            "germination_assessment": assessment_payload,
        }
    )


@router.get("/emerging-position-signals")
def list_recent_position_signals(
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    require_permission(actor.role, "emerging.read_published")
    signals = handlers.query.recent_signals(
        DiscoveryActor(actor.account_id, actor.role)
    )
    observed = [item.observed_at for item in signals if item.observed_at is not None]
    return success_response(
        data={
            "signals": [recent_signal_data(item) for item in signals],
            "observed_from": min(observed).isoformat() if observed else None,
            "observed_to": max(observed).isoformat() if observed else None,
            "source_contract": "published-jd-fact.v2",
            "projection_version": "recent-position-signals.v1",
        }
    )


@router.get("/admin/discovery-runs")
def list_discovery_runs(
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    clusters = handlers.query.list(DiscoveryActor(actor.account_id, actor.role))
    grouped: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        run_id = cluster.discovery_run_id
        item = grouped.setdefault(
            run_id,
            {
                "run_id": run_id,
                "status": cluster.discovery_run_status,
                "algorithm_version": cluster.algorithm_version,
                "time_window_start": cluster.time_window_start,
                "time_window_end": cluster.time_window_end,
                "cluster_count": 0,
                "sample_count": 0,
                "request_id": cluster.discovery_assessment.get("request_id"),
                "input_fingerprint": cluster.discovery_assessment.get(
                    "input_fingerprint"
                ),
                "input_quality_report": thaw_domain(
                    cluster.discovery_assessment.get("input_quality_report", {})
                ),
                "run_context": thaw_domain(
                    cluster.discovery_assessment.get("run_context", {})
                ),
            },
        )
        item["cluster_count"] += 1
        item["sample_count"] += cluster.sample_count
    return success_response(data=list(grouped.values()))


@router.get("/admin/discovery-formal-experiment")
def get_discovery_formal_experiment(
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    report = handlers.query.formal_experiment(
        DiscoveryActor(actor.account_id, actor.role)
    )
    return success_response(data=thaw_domain(report))


@router.get("/admin/discovery-formal-experiment/clusters")
def list_discovery_formal_experiment_clusters(
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    clusters = handlers.query.formal_experiment_clusters(
        DiscoveryActor(actor.account_id, actor.role)
    )
    return success_response(data=thaw_domain(clusters))


@router.post("/admin/discovery-formal-experiment/replay")
def replay_discovery_formal_experiment(
    actor: AccountActor = Depends(get_account_actor),
    handlers: PositionDiscoveryHandlers = Depends(get_position_discovery_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    result = handlers.query.replay_formal_experiment(
        DiscoveryActor(actor.account_id, actor.role)
    )
    return success_response(data=thaw_domain(result))


@router.get("/admin/discovery-candidates")
def list_discovery_candidates(
    status: str | None = Query(default=None, max_length=64),
    window_id: str | None = Query(default=None, max_length=64),
    candidate_id: str | None = Query(default=None, max_length=64),
    actor: AccountActor = Depends(get_account_actor),
    handlers: DiscoveryCandidateHandlers = Depends(get_discovery_candidate_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    candidates = handlers.query.list(
        DiscoveryActor(actor.account_id, actor.role),
        status=status,
        candidate_id=candidate_id,
        window_id=window_id,
    )
    return success_response(
        data={
            "candidates": [candidate_data(item) for item in candidates],
            "filters": {
                "status": status,
                "candidate_id": candidate_id,
                "window_id": window_id,
            },
        }
    )


@router.get("/admin/discovery-candidates/{candidate_id}")
def get_discovery_candidate(
    candidate_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: DiscoveryCandidateHandlers = Depends(get_discovery_candidate_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    detail = handlers.query.get(
        candidate_id, DiscoveryActor(actor.account_id, actor.role)
    )
    return success_response(data=candidate_detail_data(detail))


@router.get("/admin/discovery-candidates/{candidate_id}/trajectory")
def get_discovery_candidate_trajectory(
    candidate_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: DiscoveryCandidateHandlers = Depends(get_discovery_candidate_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    trajectory = handlers.query.trajectory(
        candidate_id, DiscoveryActor(actor.account_id, actor.role)
    )
    return success_response(data=trajectory_data(trajectory))


@router.get("/admin/discovery-candidates/{candidate_id}/diffusion-graph")
def get_discovery_candidate_diffusion(
    candidate_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: DiscoveryCandidateHandlers = Depends(get_discovery_candidate_handlers),
):
    require_permission(actor.role, "emerging.discovery.manage")
    graph = handlers.query.diffusion(
        candidate_id, DiscoveryActor(actor.account_id, actor.role)
    )
    return success_response(data=thaw_domain(graph.graph))


@router.post("/admin/discovery-candidates/{candidate_id}/enter-governance")
def enter_discovery_candidate_governance(
    candidate_id: str,
    actor: AccountActor = Depends(get_account_actor),
    discovery_handlers: DiscoveryCandidateHandlers = Depends(get_discovery_candidate_handlers),
    emerging_handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    """Lifecycle gate: only stable_emerging_role candidates with a projected
    current cluster may enter the EmergingPosition governance chain."""
    require_permission(actor.role, "emerging.discovery.manage")
    require_permission(actor.role, "emerging.candidate.manage")
    detail = discovery_handlers.query.get(
        candidate_id, DiscoveryActor(actor.account_id, actor.role)
    )
    candidate = detail.candidate
    if candidate.status != "stable_emerging_role":
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Only stable_emerging_role candidates can enter governance; "
                    f"current status is {candidate.status}"
                ),
                "error_code": "candidate_lifecycle_gate_rejected",
                "candidate_status": candidate.status,
            },
        )
    if not candidate.current_cluster_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Candidate has no current cluster and cannot enter governance",
                "error_code": "candidate_lifecycle_cluster_missing",
            },
        )
    try:
        record = emerging_handlers.create.execute(
            candidate.current_cluster_id,
            EmergingActor(actor.account_id, actor.role),
            lifecycle_context={
                "candidate_id": candidate.candidate_id,
                "status": candidate.status,
                "emergence_score": candidate.emergence_score,
                "observed_window_ids": list(
                    candidate.identity_profile.get("observed_window_ids") or ()
                ),
                "support_count": candidate.support_count,
                "company_coverage": candidate.company_coverage,
            },
        )
    except EmergingClusterNotFound as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Current cluster is not projected into the main system",
                "error_code": "candidate_lifecycle_cluster_not_projected",
            },
        ) from exc
    except DiscoveryEvidenceUnavailable as exc:
        # 对齐旧 /emerging-positions/from-cluster 语义：定义不完整视为业务冲突，不是 500。
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "candidate_lifecycle_definition_incomplete",
            },
        ) from exc
    except EmergingPositionNotFound as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "EmergingPosition could not be created from the candidate",
                "error_code": "candidate_lifecycle_creation_failed",
            },
        ) from exc
    return success_response(data=emerging_record_data(record))


@router.get("/admin/emerging-positions/{emerging_id}/definition-versions")
def list_admin_definition_versions(
    emerging_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    require_permission(actor.role, "emerging.candidate.manage")
    values = handlers.versions.execute(
        emerging_id, EmergingActor(actor.account_id, actor.role)
    )
    return success_response(data=[definition_version_data(item) for item in values])


@router.get("/admin/catalog/positions")
def list_admin_positions(
    search: str | None = Query(default=None, max_length=120),
    domain: str | None = Query(default=None, max_length=120),
    sort: str = Query(default="name", pattern="^(name|domain|jd_count)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    actor: AccountActor = Depends(get_account_actor),
    positions: ManagePositions = Depends(get_position_use_cases),
):
    require_permission(actor.role, "kg.build.manage")
    result = positions.list_catalog(
        search=search or "",
        domain=domain or "",
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    total_pages = (result.total + result.page_size - 1) // result.page_size if result.total else 0
    return success_response(
        data={
            "items": [
                position_data(item, jd_count=result.jd_counts.get(item.position_id, 0))
                for item in result.items
            ],
            "pagination": {
                "page": result.page,
                "page_size": result.page_size,
                "total": result.total,
                "total_pages": total_pages,
            },
            "filters": {
                "domains": [
                    {"code": item.code, "name": item.name}
                    for item in result.domains
                ]
            },
            "sort": {"by": sort, "order": order},
        }
    )


@router.get("/admin/knowledge-graph/mappings")
def list_knowledge_graph_mappings(
    entity_type: str = Query(..., pattern="^(position|skill)$"),
    query: str | None = Query(default=None, max_length=120),
    status: str | None = Query(default=None, max_length=64),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(
        data=thaw_json(handlers.list_mappings(actor, entity_type, query, status))
    )


@router.get("/admin/knowledge-graph/mapping-candidates")
def list_knowledge_graph_mapping_candidates(
    entity_type: str = Query(..., pattern="^(position|skill)$"),
    query: str | None = Query(default=None, max_length=120),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(
        data=thaw_json(handlers.mapping_candidates(actor, entity_type, query))
    )


@router.put("/admin/knowledge-graph/mappings/{entity_type}/{main_system_id}")
def confirm_knowledge_graph_mapping(
    entity_type: str,
    main_system_id: str,
    payload: MappingUpdateBody,
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


@router.delete("/admin/knowledge-graph/mappings/{entity_type}/{main_system_id}")
def cancel_knowledge_graph_mapping(
    entity_type: str,
    main_system_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(
        data=mapping_data(handlers.cancel_mapping(actor, entity_type, main_system_id))
    )


@router.post("/admin/knowledge-graph/mappings/{entity_type}/{main_system_id}/retry")
def retry_knowledge_graph_mapping(
    entity_type: str,
    main_system_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return success_response(
        data=mapping_data(handlers.retry_mapping(actor, entity_type, main_system_id))
    )


@router.post("/admin/knowledge-graph/positions/{position_id}/build")
def build_position_graph(
    position_id: str,
    payload: BuildGraphBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    result = handlers.build(
        actor,
        position_id,
        KnowledgeGraphBuildCommand(
            payload.window_start,
            payload.window_end,
            payload.minimum_effective_weight,
            payload.minimum_valid_samples,
        ),
    )
    return success_response(
        data=thaw_json(result.build_run),
        details={"upstream_trace_id": result.upstream_trace_id},
    )


@router.get("/admin/knowledge-graph/positions/{position_id}/build-runs")
def list_build_runs(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.BUILD_RUNS,
        position_id=position_id,
    )


@router.get("/admin/knowledge-graph/build-runs/{run_id}")
def get_build_run(
    run_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.BUILD_RUN, resource_id=run_id)


@router.get("/admin/knowledge-graph/build-jobs/{job_id}")
def get_build_job(
    job_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor, handlers, KnowledgeGraphPortalOperation.BUILD_JOB, resource_id=job_id
    )


@router.post("/admin/knowledge-graph/build-jobs/{job_id}/retry")
def retry_build_job(
    job_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.RETRY_BUILD_JOB,
        resource_id=job_id,
    )


@router.get("/admin/knowledge-graph/build-runs/{run_id}/samples")
def get_build_samples(
    run_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.BUILD_SAMPLES, resource_id=run_id)


@router.get("/admin/knowledge-graph/build-runs/{run_id}/publish-gate")
def get_publish_gate(
    run_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.PUBLISH_GATE, resource_id=run_id)


@router.get("/admin/knowledge-graph/review-tasks")
def list_kg_review_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    status: str | None = None,
    task_kind: str | None = None,
    risk_level: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    params = {
        key: value
        for key, value in {
            "page": page,
            "page_size": page_size,
            "status": status,
            "task_kind": task_kind,
            "risk_level": risk_level,
        }.items()
        if value is not None
    }
    return _portal(
        actor, handlers, KnowledgeGraphPortalOperation.REVIEW_TASKS, params=params
    )


@router.post("/admin/knowledge-graph/review-tasks/{task_id}/{action}")
def act_kg_review_task(
    task_id: int,
    action: str,
    payload: ReviewActionBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    if action not in {"claim", "approve", "reject", "modify"}:
        raise HTTPException(status_code=422, detail="Unsupported review action")
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.REVIEW_ACTION,
        resource_id=task_id,
        action=action,
        payload=_body(payload),
    )


@router.post("/admin/knowledge-graph/build-runs/{run_id}/publish")
def publish_build(
    run_id: int,
    payload: PublishGraphBody,
    background_tasks: BackgroundTasks,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
    rag_handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    result = _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.PUBLISH,
        resource_id=run_id,
        payload=_body(payload),
    )
    published = result["data"]
    if not isinstance(published, dict) or not isinstance(published.get("version_id"), int):
        raise HTTPException(status_code=502, detail="图谱发布结果缺少图谱版本")
    background_tasks.add_task(
        enqueue_published_graph_auto_index,
        int(published["version_id"]),
        rag_handlers,
    )
    return result


@router.post("/admin/knowledge-graph/build-runs/{run_id}/auto-review")
def auto_review_build(
    run_id: int,
    payload: AutoReviewBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.AUTO_REVIEW,
        resource_id=run_id,
        payload=_body(payload),
    )


@router.post("/admin/knowledge-graph/positions/{position_id}/graph/drafts")
def open_graph_draft(
    position_id: str,
    payload: OpenDraftBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.OPEN_DRAFT,
        position_id=position_id,
        payload=_body(payload),
    )


@router.get("/admin/knowledge-graph/drafts/{run_id}/graph")
def get_draft_graph(
    run_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.DRAFT_GRAPH, resource_id=run_id)


@router.get("/admin/knowledge-graph/positions/{position_id}/relations")
def list_relations(
    position_id: str,
    version_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    skill_id: str | None = None,
    category_code: str | None = None,
    importance_level: str | None = None,
    modality: str | None = None,
    min_weight: float | None = Query(default=None, ge=0, le=1),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    params = {
        key: value
        for key, value in {
            "version_id": version_id,
            "page": page,
            "page_size": page_size,
            "skill_id": skill_id,
            "category_code": category_code,
            "importance_level": importance_level,
            "modality": modality,
            "min_weight": min_weight,
            "min_confidence": min_confidence,
        }.items()
        if value is not None
    }
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.RELATIONS,
        position_id=position_id,
        params=params,
    )


@router.get("/admin/knowledge-graph/relations/{relation_id}/explanation")
def get_relation_explanation(
    relation_id: int,
    version_id: int | None = Query(default=None, ge=1),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.RELATION_EXPLANATION,
        resource_id=relation_id,
        params={"version_id": version_id} if version_id is not None else None,
    )


@router.post("/admin/knowledge-graph/relations/{relation_id}/modify")
def modify_relation(
    relation_id: int,
    payload: ModifyRelationBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.MODIFY_RELATION,
        resource_id=relation_id,
        payload=_body(payload),
    )


@router.get("/admin/knowledge-graph/normalization/unresolved-items")
def list_unresolved(
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.UNRESOLVED)


@router.post("/admin/knowledge-graph/normalization/unresolved-items/{item_id}/{action}")
def resolve_unresolved(
    item_id: int,
    action: str,
    payload: ReviewActionBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    if action == 'create-skill':
        raise HTTPException(
            status_code=409,
            detail='Standard skills must be created by the main capability catalog',
        )
    if action not in {"resolve", "create-skill", "reject"}:
        raise HTTPException(status_code=422, detail="Unsupported normalization action")
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.RESOLVE_UNRESOLVED,
        resource_id=item_id,
        action=action,
        payload=_body(payload),
    )


@router.get("/admin/knowledge-graph/positions/{position_id}/versions")
def list_versions(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(actor, handlers, KnowledgeGraphPortalOperation.VERSIONS, position_id=position_id)


@router.get("/admin/knowledge-graph/positions/{position_id}/versions/{version_id:int}")
def get_version(
    position_id: str,
    version_id: int,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.VERSION,
        position_id=position_id,
        resource_id=version_id,
    )


@router.get("/admin/knowledge-graph/positions/{position_id}/versions/diff")
def diff_versions(
    position_id: str,
    from_version_id: int = Query(...),
    to_version_id: int = Query(...),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.VERSION_DIFF,
        position_id=position_id,
        params={"from_version_id": from_version_id, "to_version_id": to_version_id},
    )


@router.post("/admin/knowledge-graph/positions/{position_id}/versions/{version_id:int}/rollback")
def rollback_version(
    position_id: str,
    version_id: int,
    payload: ReviewActionBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.ROLLBACK,
        position_id=position_id,
        resource_id=version_id,
        payload=_body(payload),
    )


@router.get("/admin/knowledge-graph/positions/{position_id}/evolution-events")
def list_evolution_events(
    position_id: str,
    from_version_id: int | None = Query(default=None),
    to_version_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None, max_length=80),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    if from_version_id is not None and to_version_id is not None:
        params: dict[str, Any] = {
            "from_version_id": from_version_id,
            "to_version_id": to_version_id,
        }
        if event_type is not None:
            params["event_type"] = event_type
        return _portal(
            actor,
            handlers,
            KnowledgeGraphPortalOperation.EVOLUTION_EVENTS,
            position_id=position_id,
            params=params,
        )
    versions = thaw_json(handlers.versions(actor, position_id).result) or []
    ordered = sorted(
        (item for item in versions if isinstance(item, dict)),
        key=lambda item: (
            item.get("version_number")
            if isinstance(item.get("version_number"), int)
            else item.get("id") or 0
        ),
    )
    version_pairs: list[dict[str, int]] = []
    events: list[dict[str, Any]] = []
    for index in range(len(ordered) - 1):
        before = ordered[index]
        after = ordered[index + 1]
        before_id = before.get("id")
        after_id = after.get("id")
        if not isinstance(before_id, int) or not isinstance(after_id, int):
            continue
        version_pairs.append({"from_version_id": before_id, "to_version_id": after_id})
        pair_params: dict[str, Any] = {
            "from_version_id": before_id,
            "to_version_id": after_id,
        }
        if event_type is not None:
            pair_params["event_type"] = event_type
        pair_result = handlers.portal(
            actor,
            _command(
                KnowledgeGraphPortalOperation.EVOLUTION_EVENTS,
                position_id=position_id,
                params=pair_params,
            ),
        )
        pair_data = thaw_json(pair_result.result) or {}
        events.extend(pair_data.get("events") or [])
    return success_response(
        data={
            "position_id": position_id,
            "from_version_id": None,
            "to_version_id": None,
            "event_type": event_type,
            "versions": ordered,
            "version_pairs": version_pairs,
            "events": events,
            "count": len(events),
        }
    )


@router.get("/admin/knowledge-graph/positions/{position_id}/evolution-events/{event_id}")
def get_evolution_event(
    position_id: str,
    event_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor, handlers, KnowledgeGraphPortalOperation.EVOLUTION_EVENT,
        position_id=position_id, resource_id=event_id,
    )


@router.get("/admin/knowledge-graph/positions/{position_id}/capability-evolution")
def get_capability_evolution(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    return _portal(
        actor,
        handlers,
        KnowledgeGraphPortalOperation.CAPABILITY_EVOLUTION,
        position_id=position_id,
    )
