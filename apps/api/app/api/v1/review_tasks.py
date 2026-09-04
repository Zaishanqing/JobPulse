from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.governance import get_governance_handlers
from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
from app.contexts.governance_feedback import (
    GovernanceHandlers,
    ReviewConflict,
    ReviewNotFound,
    ReviewValidationError,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json_object
from app.contexts.governance_feedback import ReviewEventRecord, ReviewRecord
from app.schemas.review import (
    ReviewTaskCreate,
    ReviewTaskBatch,
    ReviewTaskDecision,
    ReviewTaskModify,
    ReviewTaskRejection,
)
from app.domain.errors import PermissionDenied
from app.contexts.knowledge_graph import (
    KnowledgeGraphIntegrationDisabled,
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
    ManageKnowledgeGraphIntegration,
)
from app.contexts.review_value_ranking import (
    ReviewRankInput,
    rank_review_queue_v4,
    rank_review_task,
    rank_review_task_v4,
    review_wait_days,
)
from app.contexts.review_value_ranking.contracts import ReviewRankResult


router = APIRouter(prefix="/review-tasks", tags=["review-tasks"])


def _data(task: ReviewRecord) -> dict:
    return {
        "task_id": task.task_id,
        "object_type": task.object_type,
        "object_id": task.object_id,
        "priority": task.priority,
        "reason": task.reason,
        "status": task.status,
        "reviewer_id": task.reviewer_id,
        "reviewer_name": task.reviewer_name,
        "object_name": task.object_name,
        "review_stage": task.review_stage,
        "review_comment": task.review_comment,
        "modified_payload": task.modified_payload,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _event_data(event: ReviewEventRecord) -> dict:
    return {
        "event_id": event.event_id,
        "task_id": event.task_id,
        "actor_user_id": event.actor_user_id,
        "action": event.action,
        "before_status": event.before_status,
        "after_status": event.after_status,
        "status": event.after_status,
        "reviewer_id": event.actor_user_id,
        "review_comment": event.comment,
        "modified_payload": event.payload_snapshot,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _allowed_local(status: str) -> list[str]:
    return {
        "pending": ["claim"],
        "claimed": ["release", "approve", "reject", "modify"],
        "modified": ["approve", "reject"],
    }.get(status, [])


def _local_queue_data(task: ReviewRecord, context: dict) -> dict:
    raw_evidence = context.get("evidence") or context.get("evidence_context") or []
    raw_review_flags = context.get("review_flags") or []
    evidence = list(raw_evidence) if isinstance(raw_evidence, (list, tuple)) else []
    review_flags = (
        list(raw_review_flags) if isinstance(raw_review_flags, (list, tuple)) else []
    )
    return {
        **_data(task),
        "contract_version": "review-task.v1",
        "source_system": "main-system",
        "task_kind": task.object_type,
        "allowed_actions": _allowed_local(task.status),
        "risk_level": context.get("risk_level", "medium"),
        "evidence_count": len(evidence),
        "review_flag_count": len(review_flags),
        "evidence_context": {
            "evidence": evidence,
            "original_values": context.get("original_values", {}),
            "current_values": context.get("current_values", {}),
            "modified_values": context.get("modified_values", {}),
            "impacted_relations": context.get("impacted_relations", []),
            "review_flags": review_flags,
            "impact_scope": context.get("impact_scope", {}),
            "history": context.get("history", []),
        },
    }


def _kg_tasks(
    actor: AccountActor,
    handlers: ManageKnowledgeGraphIntegration,
    *, page: int, page_size: int, status: str | None,
    task_kind: str | None, risk_level: str | None,
) -> tuple[list[dict], int]:
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
    try:
        upstream_result = handlers.portal(
            actor,
            KnowledgeGraphPortalCommand(
                KnowledgeGraphPortalOperation.REVIEW_TASKS,
                params=freeze_json_object(params, field="review_queue.params"),
            ),
        )
    except KnowledgeGraphIntegrationDisabled:
        return [], 0
    result = upstream_result.result
    upstream = upstream_result.upstream
    total_header = (
        dict(upstream.response_headers or {}).get("X-Total-Count")
        if upstream is not None
        else None
    )
    raw_items = list(result or [])
    try:
        kg_total = int(total_header) if total_header is not None else len(raw_items)
    except (TypeError, ValueError):
        kg_total = len(raw_items)
    return [
        {**dict(item), "task_id": f"kg:{item['task_id']}"}
        for item in raw_items
    ], kg_total


def _kg_all_tasks(
    actor: AccountActor,
    handlers: ManageKnowledgeGraphIntegration,
    *,
    status: str | None,
    task_kind: str | None,
    risk_level: str | None,
) -> tuple[list[dict], int]:
    """Fetch the complete KG review pool for a strict global page."""
    page = 1
    page_size = 100
    items: list[dict] = []
    total = 0
    while True:
        page_items, page_total = _kg_tasks(
            actor,
            handlers,
            page=page,
            page_size=page_size,
            status=status,
            task_kind=task_kind,
            risk_level=risk_level,
        )
        items.extend(page_items)
        total = page_total
        if not page_items or (total > 0 and len(items) >= total):
            break
        page += 1
    return items, total


def _local_all_tasks(
    actor: AccountActor,
    handlers: GovernanceHandlers,
    *,
    status: str | None,
    task_kind: str | None,
    risk_level: str | None,
) -> tuple[list[dict], int]:
    local_page, total = handlers.reviews.list_page(
        actor,
        page=1,
        page_size=1_000_000,
        status=status,
        task_kind=task_kind,
        risk_level=risk_level,
    )
    return [_local_queue_data(task, dict(context)) for task, context in local_page], total


def _ranked_items(items: list[dict]) -> list[dict]:
    ranked = [
        {**item, "value_ranking": asdict_rank(_rank_from_item(item))}
        for item in items
    ]
    ranked.sort(
        key=lambda item: (
            -item["value_ranking"]["priority_score"],
            -item["value_ranking"]["estimated_review_cost"],
            item["task_id"],
        )
    )
    return ranked


def _ranked_items_v4(items: list[dict]) -> list[dict]:
    inputs = [_rank_input_from_item(item) for item in items]
    ordered = rank_review_queue_v4(inputs)
    input_by_task = {item.task_id: item for item in inputs}
    item_by_task = {item["task_id"]: item for item in items}
    ranked = []
    for input_ in ordered:
        item = item_by_task[input_.task_id]
        result = rank_review_task_v4(input_)
        ranked.append({**item, "value_ranking": asdict_rank(result)})
    return ranked


def _task_order_key(item: dict) -> tuple:
    task_id = item["task_id"]
    if task_id.startswith("kg:"):
        try:
            return (0, int(task_id.removeprefix("kg:")))
        except ValueError:
            return (0, 0)
    return (1, task_id)


def _created_sort_key(item: dict) -> tuple:
    created_at = item.get("created_at")
    if created_at is None:
        return (1, 0.0, _task_order_key(item))
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return (1, 0.0, _task_order_key(item))
    return (0, -parsed.timestamp(), _task_order_key(item))


def _slice_page(items: list[dict], page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    return items[start : start + page_size]


def _kg_task(
    task_id: str,
    actor: AccountActor,
    handlers: ManageKnowledgeGraphIntegration,
) -> dict | None:
    try:
        upstream_result = handlers.portal(
            actor,
            KnowledgeGraphPortalCommand(
                KnowledgeGraphPortalOperation.REVIEW_TASK,
                resource_id=task_id.removeprefix("kg:"),
            ),
        )
    except KnowledgeGraphIntegrationDisabled:
        return None
    result = upstream_result.result
    if result is None:
        return None
    return {**dict(result), "task_id": task_id}


def _rank_from_item(item: dict, *, now=None) -> ReviewRankResult:
    return rank_review_task(_rank_input_from_item(item, now=now))


def _rank_input_from_item(
    item: dict, *, now=None
) -> ReviewRankInput:
    context = item.get("evidence_context") or {}
    review_flags = context.get("review_flags") or item.get("review_flags") or []
    uncertainty_flags = [
        flag
        for flag in review_flags
        if any(
            token in str(flag).lower()
            for token in ("unresolved", "conflict", "missing", "ambiguous", "non_exact")
        )
    ]
    blocking = (
        bool(context.get("blocking_issues"))
        or any(str(flag).find("blocking") >= 0 for flag in review_flags)
        or item.get("risk_level") == "high"
    )
    impact = len(context.get("impacted_relations") or item.get("impacted_relations") or [])
    if not impact:
        impact_scope = context.get("impact_scope") or item.get("impact_scope") or {}
        if isinstance(impact_scope, dict):
            impact = sum(
                1 for value in impact_scope.values() if value is not None
            )
    created_at = item.get("created_at")
    wait_days = review_wait_days(created_at, now=now) if created_at else 0.0
    return ReviewRankInput(
        task_id=item["task_id"],
        status=item.get("status", "pending"),
        priority=item.get("priority", "normal"),
        blocking=blocking,
        uncertainty_count=len(uncertainty_flags),
        impact_count=impact,
        reuse_count=item.get("similar_task_count") or 0,
        wait_days=wait_days,
        estimated_review_cost=item.get("estimated_review_cost", 1.0),
        created_at=created_at,
        object_type=item.get("object_type") or item.get("task_kind"),
        object_ref=item.get("object_id") or item.get("object_ref"),
        subject_ref=(
            item.get("subject_ref")
            or context.get("subject_ref")
        ),
        candidate_ref=(
            item.get("candidate_ref")
            or context.get("candidate_ref")
        ),
        reuse_group_ref=(
            item.get("reuse_group_ref")
            or (
                f"{item.get('object_type') or item.get('task_kind')}:"
                f"{context.get('entity_ref') or item.get('entity_ref')}"
                if (context.get("entity_ref") or item.get("entity_ref"))
                else (
                    f"{item.get('object_type') or item.get('task_kind')}:"
                    f"{item.get('object_id')}"
                )
            )
        ),
        reuse_group_size=item.get("reuse_group_size"),
        propagation_count=(
            item.get("propagation_count")
            or item.get("reuse_count")
            or item.get("reuse_group_size")
        ),
    )


def _raise(exc: Exception) -> None:
    status_code = (
        404
        if isinstance(exc, ReviewNotFound)
        else 409
        if isinstance(exc, ReviewConflict)
        else 422
        if isinstance(exc, ReviewValidationError)
        else 403
    )
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("")
def create_review_task_api(payload: ReviewTaskCreate, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        task = handlers.reviews.create(actor, **payload.model_dump())
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=_data(task))


@router.get("")
def list_review_tasks_api(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    status: str | None = None,
    task_kind: str | None = None,
    risk_level: str | None = None,
    source_system: str | None = None,
    sort: str | None = Query(default=None, pattern="^(created|value|value_v4)$"),
    actor: AccountActor = Depends(get_account_actor),
    handlers: GovernanceHandlers = Depends(get_governance_handlers),
    kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    if source_system == "main-system":
        try:
            if sort in {"value", "value_v4"}:
                local, local_total = _local_all_tasks(
                    actor,
                    handlers,
                    status=status,
                    task_kind=task_kind,
                    risk_level=risk_level,
                )
                ranked = (
                    _ranked_items_v4(local)
                    if sort == "value_v4"
                    else _ranked_items(local)
                )
                local = _slice_page(ranked, page, page_size)
            else:
                local_page, local_total = handlers.reviews.list_page(
                    actor,
                    page=page,
                    page_size=page_size,
                    status=status,
                    task_kind=task_kind,
                    risk_level=risk_level,
                )
                local = [
                    _local_queue_data(task, dict(context))
                    for task, context in local_page
                ]
        except PermissionDenied as exc:
            _raise(exc)
        response.headers["X-Total-Count"] = str(local_total)
        response.headers["X-Page"] = str(page)
        response.headers["X-Page-Size"] = str(page_size)
        return success_response(data=local)
    if source_system == "knowledge-graph":
        if sort in {"value", "value_v4"}:
            kg_items, kg_total = _kg_all_tasks(
                actor,
                kg_handlers,
                status=status,
                task_kind=task_kind,
                risk_level=risk_level,
            )
            ranked = (
                _ranked_items_v4(kg_items)
                if sort == "value_v4"
                else _ranked_items(kg_items)
            )
            kg_items = _slice_page(ranked, page, page_size)
        else:
            kg_items, kg_total = _kg_tasks(
                actor,
                kg_handlers,
                page=page,
                page_size=page_size,
                status=status,
                task_kind=task_kind,
                risk_level=risk_level,
            )
        response.headers["X-Total-Count"] = str(kg_total)
        response.headers["X-Page"] = str(page)
        response.headers["X-Page-Size"] = str(page_size)
        return success_response(data=kg_items)
    try:
        local, local_total = _local_all_tasks(
            actor,
            handlers,
            status=status,
            task_kind=task_kind,
            risk_level=risk_level,
        )
    except PermissionDenied as exc:
        _raise(exc)
    kg_items, kg_total = _kg_all_tasks(
        actor,
        kg_handlers,
        status=status,
        task_kind=task_kind,
        risk_level=risk_level,
    )
    combined = [*local, *kg_items]
    if sort == "value_v4":
        combined = _ranked_items_v4(combined)
    elif sort == "value":
        combined = _ranked_items(combined)
    else:
        combined = sorted(combined, key=_created_sort_key)
    combined = _slice_page(combined, page, page_size)
    response.headers["X-Total-Count"] = str(local_total + kg_total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Page-Size"] = str(page_size)
    return success_response(data=combined)


def asdict_rank(result: ReviewRankResult) -> dict:
    return {
        "priority_score": result.priority_score,
        "priority_reasons": list(result.priority_reasons),
        "affected_subjects": list(result.affected_subjects),
        "blocking_state": result.blocking_state,
        "similar_task_count": result.similar_task_count,
        "estimated_review_cost": result.estimated_review_cost,
        "method_version": result.method_version,
    }


@router.post("/batch")
def batch_review_tasks_api(
    payload: ReviewTaskBatch,
    actor: AccountActor = Depends(get_account_actor),
    handlers: GovernanceHandlers = Depends(get_governance_handlers),
    kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers),
):
    local_ids = [value for value in payload.task_ids if not value.startswith("kg:")]
    kg_ids = [int(value.removeprefix("kg:")) for value in payload.task_ids if value.startswith("kg:")]
    if local_ids and kg_ids:
        raise HTTPException(
            status_code=422,
            detail="Batch review cannot mix main-system and knowledge-graph tasks",
        )
    statuses = {}
    try:
        local_tasks = handlers.reviews.batch_transition(
            actor, local_ids, payload.action, payload.reason
        ) if local_ids else []
    except (
        ReviewConflict,
        ReviewNotFound,
        ReviewValidationError,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    for task in local_tasks:
        task_id = task.task_id
        statuses[task_id] = task.status
    if kg_ids:
        result = kg_handlers.portal(
            actor,
            KnowledgeGraphPortalCommand(
                KnowledgeGraphPortalOperation.REVIEW_BATCH,
                payload=freeze_json_object(
                    {
                        "contract_version": "review-batch.v1",
                        "task_ids": kg_ids,
                        "action": payload.action,
                        "reason": payload.reason,
                    },
                    field="review_batch.payload",
                ),
            ),
        ).result
        statuses.update(
            {f"kg:{key}": value for key, value in dict(result["statuses"]).items()}
        )
    return success_response(
        data={
            "contract_version": "review-batch-result.v1",
            "action": payload.action,
            "task_ids": payload.task_ids,
            "statuses": statuses,
        }
    )


@router.get("/unresolved-skills")
def list_unresolved_skills_api(actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        items = handlers.reviews.unresolved_skills(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=items)


@router.get("/summary")
def review_tasks_summary_api(
    actor: AccountActor = Depends(get_account_actor),
    handlers: GovernanceHandlers = Depends(get_governance_handlers),
):
    try:
        counts = handlers.reviews.counts_by_status(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=counts)


@router.get("/{task_id}/context")
def get_review_task_context_api(task_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    if task_id.startswith("kg:"):
        item = _kg_task(task_id, actor, kg_handlers)
        if item is None:
            raise HTTPException(status_code=404, detail="Review task not found")
        return success_response(data=item["evidence_context"])
    try:
        context = handlers.reviews.context(actor, task_id)
    except (ReviewNotFound, PermissionDenied, LookupError) as exc:
        _raise(exc)
    return success_response(data=context)


@router.get("/{task_id}")
def get_review_task_api(task_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    if task_id.startswith("kg:"):
        item = _kg_task(task_id, actor, kg_handlers)
        if item is None:
            raise HTTPException(status_code=404, detail="Review task not found")
        return success_response(data=item)
    try:
        task = handlers.reviews.get(actor, task_id)
    except (
        ReviewConflict,
        ReviewNotFound,
        ReviewValidationError,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    return success_response(data=_data(task))


def _transition(task_id: str, action: str, actor: AccountActor, handlers: GovernanceHandlers, kg_handlers: ManageKnowledgeGraphIntegration, *, comment: str | None = None, modified_payload: dict | None = None):
    if task_id.startswith("kg:"):
        result = kg_handlers.portal(
            actor,
            KnowledgeGraphPortalCommand(
                KnowledgeGraphPortalOperation.REVIEW_ACTION,
                resource_id=task_id.removeprefix("kg:"),
                action=action,
                payload=freeze_json_object(
                    {
                        "reason": comment or f"Unified review queue {action}",
                        "payload": modified_payload,
                    },
                    field="review_action.payload",
                ),
            ),
        ).result
        return success_response(data={**dict(result), "task_id": task_id})
    try:
        task = handlers.reviews.transition(actor, task_id, action, comment, modified_payload)
    except (
        ReviewConflict,
        ReviewNotFound,
        ReviewValidationError,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    return success_response(data=_data(task))


@router.post("/{task_id}/claim")
def claim_review_task_api(task_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    return _transition(task_id, "claim", actor, handlers, kg_handlers)


@router.post("/{task_id}/release")
def release_review_task_api(task_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    if task_id.startswith("kg:"):
        raise HTTPException(status_code=422, detail="KG review tasks do not support release")
    return _transition(task_id, "release", actor, handlers, kg_handlers)


@router.post("/{task_id}/approve")
def approve_review_task_api(task_id: str, payload: ReviewTaskDecision | None = None, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    return _transition(task_id, "approve", actor, handlers, kg_handlers, comment=payload.review_comment if payload else None)


@router.post("/{task_id}/reject")
def reject_review_task_api(task_id: str, payload: ReviewTaskRejection, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    return _transition(task_id, "reject", actor, handlers, kg_handlers, comment=payload.review_comment)


@router.put("/{task_id}/modify")
def modify_review_task_api(task_id: str, payload: ReviewTaskModify, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    return _transition(task_id, "modify", actor, handlers, kg_handlers, comment=payload.review_comment, modified_payload=payload.modified_payload)


@router.get("/{task_id}/history")
def get_review_task_history_api(task_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers), kg_handlers: ManageKnowledgeGraphIntegration = Depends(get_knowledge_graph_handlers)):
    if task_id.startswith("kg:"):
        item = _kg_task(task_id, actor, kg_handlers)
        if item is None:
            raise HTTPException(status_code=404, detail="Review task not found")
        return success_response(data=item["evidence_context"]["history"])
    try:
        events = handlers.reviews.history(actor, task_id)
    except (ReviewNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=[_event_data(event) for event in events])
