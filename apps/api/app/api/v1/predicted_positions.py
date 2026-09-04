from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.trends import get_prediction_use_cases
from app.api.trend_delivery_mapping import collection_data, delivery_fields, trend_task_data
from app.contexts.tasks import TaskNotFound
from app.contexts.market_intelligence import ManagePredictedPositions, PredictedPositionNotFound
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.contexts.market_intelligence import PredictedPositionRecord
from app.schemas.predicted_position import (
    PredictionDefinitionUpdate,
    PredictionPublishRequest,
    PredictionRelationRequest,
    PredictionReviewSubmit,
    PredictionTaskRequest,
    PredictedPositionUpdate,
)
from app.schemas.trend_delivery import (
    TrendBatchQuery,
    TrendDeliveryCollectionEnvelope,
    TrendDeliveryEnvelope,
)
from app.domain.values import thaw
from app.domain.errors import PermissionDenied


router = APIRouter(prefix="/predicted-positions", tags=["predicted-positions"])


def _data(item: PredictedPositionRecord, delivery=None) -> dict:
    value = {
        "predicted_id": item.predicted_id, "position_name": item.position_name,
        "prediction_basis": list(item.prediction_basis),
        "related_source_ids": list(item.related_source_ids),
        "potential_responsibilities": list(item.potential_responsibilities),
        "potential_skills": list(item.potential_skills),
        "industry_scenarios": list(item.industry_scenarios),
        "confidence_score": item.confidence_score, "status": item.status,
        "provider_run_id": item.provider_run_id,
        "candidate_key": item.candidate_key,
        "industry_domain": item.industry_domain,
        "emergence_score": item.emergence_score,
        "score_components": dict(item.score_components or {}),
        "algorithm_version": item.algorithm_version,
        "formula_version": item.formula_version,
        "time_window": {
            "start": item.window_start.isoformat() if item.window_start else None,
            "end": item.window_end.isoformat() if item.window_end else None,
        },
        "source_coverage": item.source_coverage,
        "missing_sources": list(item.missing_sources),
        "quality_flags": list(item.quality_flags),
        "evidence_references": list(item.evidence_references),
        "published_definition_version_id": item.published_definition_version_id,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    value.update(delivery_fields(
        resource_type="predicted_position",
        resource_id=item.predicted_id,
        status=item.status,
        progress=1.0 if item.status == "published" else 0.75,
        source_coverage=item.source_coverage,
        missing_sources=item.missing_sources,
        quality_flags=item.quality_flags,
        evidence_references=item.evidence_references,
        review_status=str(delivery.get("review_status")) if delivery and delivery.get("review_status") else None,
        review_task_id=str(delivery.get("review_task_id")) if delivery and delivery.get("review_task_id") else None,
        publishable=bool(delivery.get("eligible")) if delivery else item.status == "published",
        publication_blockers=delivery.get("blockers", ()) if delivery else (() if item.status == "published" else ("GATE_NOT_EVALUATED",)),
    ))
    return value


def _raise(exc: Exception) -> None:
    code = 404 if isinstance(exc, (TaskNotFound, PredictedPositionNotFound, LookupError)) else (422 if isinstance(exc, ValueError) else 403)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _match_data(item) -> dict:
    return {
        "match_id": item.match_id,
        "predicted_position_id": item.predicted_position_id,
        "version": item.version,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "similarity_score": item.similarity_score,
        "matched_skills": list(item.matched_skills),
        "missing_skills": list(item.missing_skills),
        "overlap_evidence": thaw(item.overlap_evidence),
        "recommendation": item.recommendation,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _definition_data(item) -> dict:
    return {
        "definition_id": item.definition_id,
        "predicted_position_id": item.predicted_position_id,
        "version": item.version,
        "status": item.status,
        "definition": thaw(item.payload),
        "review_task_id": item.review_task_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _relation_data(item) -> dict:
    return {
        "relation_id": item.relation_id,
        "predicted_position_id": item.predicted_position_id,
        "version": item.version,
        "relation_identity_id": item.relation_identity_id,
        "supersedes_relation_id": item.supersedes_relation_id,
        "relation_type": item.relation_type,
        "target_id": item.target_id,
        "status": item.status,
        "reason": item.reason,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@router.post("/tasks", response_model=TrendDeliveryEnvelope)
def create_predicted_position_task(payload: PredictionTaskRequest | None = None, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        task = use_cases.run(
            actor,
            payload.source_ids if payload else [],
            window_start=payload.window_start if payload else None,
            window_end=payload.window_end if payload else None,
            data_sources=payload.data_sources if payload else None,
        )
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=trend_task_data(task, "prediction_run"))


@router.post("/tasks/batch-query", response_model=TrendDeliveryCollectionEnvelope)
def batch_query_prediction_tasks(payload: TrendBatchQuery, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    items = []
    missing = []
    for task_id in dict.fromkeys(payload.ids):
        try:
            items.append(trend_task_data(use_cases.task(actor, task_id), "prediction_run"))
        except TaskNotFound:
            missing.append(task_id)
        except PermissionDenied as exc:
            _raise(exc)
    return success_response(data=collection_data(
        items, page=1, page_size=max(len(items), 1), filters={"ids": payload.ids},
        sort_by="input_order", sort_order="asc", not_found_ids=missing,
    ))


@router.get("/tasks/{task_id}", response_model=TrendDeliveryEnvelope)
def get_predicted_position_task(task_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        task = use_cases.task(actor, task_id)
    except (PermissionDenied, TaskNotFound) as exc:
        _raise(exc)
    return success_response(data=trend_task_data(task, "prediction_run"))


@router.get("", response_model=TrendDeliveryCollectionEnvelope)
def list_predicted_positions_api(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    industry_domain: str | None = None,
    min_emergence_score: float | None = Query(default=None, ge=0, le=1),
    quality_flag: str | None = None,
    sort_by: Literal["created_at", "updated_at", "emergence_score", "source_coverage", "position_name"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases),
):
    try:
        items = use_cases.list(actor)
    except PermissionDenied as exc:
        _raise(exc)
    values = [
        _data(item, use_cases.delivery_status(actor, item.predicted_id))
        for item in items
    ]
    if status:
        values = [item for item in values if item["status"] == status]
    if industry_domain:
        values = [item for item in values if item["industry_domain"] == industry_domain]
    if min_emergence_score is not None:
        values = [item for item in values if (item["emergence_score"] or 0) >= min_emergence_score]
    if quality_flag:
        values = [item for item in values if quality_flag in item["quality_flags"]]
    present_values = [item for item in values if item.get(sort_by) is not None]
    missing_values = [item for item in values if item.get(sort_by) is None]
    present_values.sort(
        key=lambda item: item[sort_by],
        reverse=sort_order == "desc",
    )
    values = present_values + missing_values
    return success_response(data=collection_data(
        values, page=page, page_size=page_size,
        filters={
            "status": status, "industry_domain": industry_domain,
            "min_emergence_score": min_emergence_score, "quality_flag": quality_flag,
        },
        sort_by=sort_by, sort_order=sort_order,
    ))


@router.post("/batch-query", response_model=TrendDeliveryCollectionEnvelope)
def batch_query_predicted_positions(payload: TrendBatchQuery, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    items = []
    missing = []
    for predicted_id in dict.fromkeys(payload.ids):
        try:
            record = use_cases.get(actor, predicted_id)
            items.append(_data(record, use_cases.delivery_status(actor, predicted_id)))
        except PredictedPositionNotFound:
            missing.append(predicted_id)
        except PermissionDenied as exc:
            _raise(exc)
    return success_response(data=collection_data(
        items, page=1, page_size=max(len(items), 1), filters={"ids": payload.ids},
        sort_by="input_order", sort_order="asc", not_found_ids=missing,
    ))


@router.post("/{predicted_id}/matches/tasks")
def start_candidate_matching(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        items = use_cases.run_candidate_matching(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound, ValueError) as exc:
        _raise(exc)
    return success_response(data={"status": "completed", "results": [_match_data(item) for item in items]})


@router.get("/{predicted_id}/matches")
def get_candidate_matches(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        items = use_cases.matching_results(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=[_match_data(item) for item in items])


@router.post("/{predicted_id}/definition-drafts")
def generate_prediction_definition(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.generate_definition(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=_definition_data(item))


@router.get("/{predicted_id}/definition-drafts")
def list_prediction_definitions(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        items = use_cases.definitions(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=[_definition_data(item) for item in items])


@router.put("/{predicted_id}/definition-drafts/{definition_id}")
def edit_prediction_definition(predicted_id: str, definition_id: str, payload: PredictionDefinitionUpdate, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.edit_definition(actor, predicted_id, definition_id, payload.model_dump(exclude_unset=True))
    except (PermissionDenied, PredictedPositionNotFound, ValueError) as exc:
        _raise(exc)
    return success_response(data=_definition_data(item))


@router.post("/{predicted_id}/definition-drafts/{definition_id}/submit-review")
def submit_prediction_review(predicted_id: str, definition_id: str, payload: PredictionReviewSubmit, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.submit_definition_review(actor, predicted_id, definition_id, payload.reason)
    except (PermissionDenied, PredictedPositionNotFound, ValueError) as exc:
        _raise(exc)
    return success_response(data=_definition_data(item))


@router.get("/{predicted_id}/definition-drafts/{definition_id}/review")
def get_prediction_review(predicted_id: str, definition_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.definition_review(actor, predicted_id, definition_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=thaw(item) if item else None)


@router.post("/{predicted_id}/reject")
def reject_prediction(predicted_id: str, payload: PredictionPublishRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    if not payload.definition_id:
        raise HTTPException(status_code=422, detail="definition_id is required")
    try:
        item = use_cases.reject(actor, predicted_id, payload.definition_id)
    except (PermissionDenied, PredictedPositionNotFound, ValueError) as exc:
        _raise(exc)
    return success_response(data=_definition_data(item))


@router.post("/{predicted_id}/relations")
def create_prediction_relation(predicted_id: str, payload: PredictionRelationRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.create_relation(actor, predicted_id, payload.relation_type, payload.target_id, payload.reason)
    except (PermissionDenied, PredictedPositionNotFound, ValueError, LookupError) as exc:
        _raise(exc)
    return success_response(data=_relation_data(item))


@router.get("/{predicted_id}/relations")
def list_prediction_relations(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        items = use_cases.relations(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=[_relation_data(item) for item in items])


@router.get("/{predicted_id}/relations/history")
def list_prediction_relation_history(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        items = use_cases.relation_history(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=[_relation_data(item) for item in items])


@router.put("/{predicted_id}/relations/{relation_id}")
def update_prediction_relation(predicted_id: str, relation_id: str, payload: PredictionRelationRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.update_relation(actor, predicted_id, relation_id, payload.relation_type, payload.target_id, payload.reason)
    except (PermissionDenied, PredictedPositionNotFound, ValueError, LookupError) as exc:
        _raise(exc)
    return success_response(data=_relation_data(item))


@router.delete("/{predicted_id}/relations/{relation_id}")
def delete_prediction_relation(predicted_id: str, relation_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.delete_relation(actor, predicted_id, relation_id)
    except (PermissionDenied, PredictedPositionNotFound, ValueError) as exc:
        _raise(exc)
    return success_response(data=_relation_data(item))


@router.get("/{predicted_id}", response_model=TrendDeliveryEnvelope)
def get_predicted_position_api(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.get(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=_data(item, use_cases.delivery_status(actor, predicted_id)))


@router.put("/{predicted_id}", response_model=TrendDeliveryEnvelope)
def update_predicted_position_api(predicted_id: str, payload: PredictedPositionUpdate, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.update(actor, predicted_id, payload.model_dump(exclude_unset=True))
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data=_data(item, use_cases.delivery_status(actor, predicted_id)))


@router.get("/{predicted_id}/confidence-score")
def get_predicted_position_confidence(predicted_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.get(actor, predicted_id)
    except (PermissionDenied, PredictedPositionNotFound) as exc:
        _raise(exc)
    return success_response(data={"predicted_id": item.predicted_id, "confidence_score": item.confidence_score})


@router.post("/{predicted_id}/publish", response_model=TrendDeliveryEnvelope)
def publish_predicted_position_api(predicted_id: str, payload: PredictionPublishRequest | None = None, actor: AccountActor = Depends(get_account_actor), use_cases: ManagePredictedPositions = Depends(get_prediction_use_cases)):
    try:
        item = use_cases.publish(actor, predicted_id, payload.definition_id if payload else None)
    except (PermissionDenied, PredictedPositionNotFound, ValueError) as exc:
        _raise(exc)
    return success_response(data=_data(item, use_cases.delivery_status(actor, predicted_id)))
