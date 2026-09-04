from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.feedback import get_feedback_use_cases
from app.contexts.governance_feedback import (
    FeedbackNotFound,
    FeedbackTargetNotFound,
    ManageFeedback,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.feedback import FeedbackConflict, FeedbackValidationError
from app.contexts.governance_feedback import FeedbackRecord
from app.domain.errors import PermissionDenied
from app.schemas.api_requests import FeedbackCreateRequest, FeedbackUpdateRequest


router = APIRouter(prefix="/feedback", tags=["feedback"])


def _data(item: FeedbackRecord) -> dict[str, object]:
    return {
        "feedback_id": item.feedback_id,
        "feedback_type": item.feedback_type,
        "user_id": item.created_by,
        "payload": dict(item.payload),
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "implementation_status": "database_persisted_review_queue",
    }


def _raise(exc: Exception) -> None:
    if isinstance(exc, (FeedbackNotFound, FeedbackTargetNotFound)):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, FeedbackConflict):
        code = 409
    else:
        code = 422
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _create(feedback_type: str, payload: dict, actor: AccountActor, use_cases: ManageFeedback):
    try:
        item = use_cases.create(actor, feedback_type, payload)
    except (
        FeedbackTargetNotFound,
        PermissionDenied,
        FeedbackConflict,
        FeedbackValidationError,
    ) as exc:
        _raise(exc)
    return success_response(data=_data(item))


def _add_create_route(path: str, feedback_type: str) -> None:
    def endpoint(
        payload: FeedbackCreateRequest = Body(default_factory=lambda: FeedbackCreateRequest({})),
        actor: AccountActor = Depends(get_account_actor),
        use_cases: ManageFeedback = Depends(get_feedback_use_cases),
    ):
        return _create(feedback_type, payload.root, actor, use_cases)

    endpoint.__name__ = f"create_{feedback_type}_feedback"
    router.add_api_route(f"/{path}", endpoint, methods=["POST"])


for _path, _type in (
    ("resume-parse", "resume_parse"),
    ("match-report", "match_report"),
    ("learning-path", "learning_path"),
    ("jd-parse", "jd_parse"),
    ("skill-weight", "skill_weight"),
    ("candidate-match", "candidate_match"),
    ("job-requirement-change", "job_requirement_change"),
):
    _add_create_route(_path, _type)


@router.get("")
def list_feedback(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    feedback_type: str | None = Query(default=None),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageFeedback = Depends(get_feedback_use_cases),
):
    try:
        items, total = use_cases.list_page(
            actor,
            page=page,
            page_size=page_size,
            status=status,
            feedback_type=feedback_type,
        )
    except FeedbackValidationError as exc:
        _raise(exc)
    response.headers["X-Total-Count"] = str(total)
    return success_response(data=[_data(item) for item in items])


@router.get("/{feedback_id}")
def get_feedback_detail(
    feedback_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageFeedback = Depends(get_feedback_use_cases),
):
    try:
        item = use_cases.get(actor, feedback_id)
    except (FeedbackNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_data(item))


@router.put("/{feedback_id}")
def edit_feedback(
    feedback_id: str,
    payload: FeedbackUpdateRequest = Body(default_factory=lambda: FeedbackUpdateRequest({})),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageFeedback = Depends(get_feedback_use_cases),
):
    try:
        item = use_cases.update(actor, feedback_id, payload.root)
    except (FeedbackNotFound, PermissionDenied, FeedbackConflict, FeedbackValidationError) as exc:
        _raise(exc)
    return success_response(data=_data(item))
