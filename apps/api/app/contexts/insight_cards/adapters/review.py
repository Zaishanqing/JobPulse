from __future__ import annotations

from app.domain.json_types import MutableJsonObject
from app.contexts.insight_cards.contracts import HumanDecision


def human_decision_from_review_task(
    review: MutableJsonObject,
    *,
    original_authority_state: str | None = None,
    bound_object_type: str | None = None,
    bound_object_id: str | None = None,
    release_ref: str | None = None,
    graph_version_ref: str | None = None,
    algorithm_version: str | None = None,
    config_version: str | None = None,
) -> HumanDecision | None:
    """Map an existing Review task contract into a card human decision.

    Pending/claimed/modified tasks have no decision yet and return None;
    approved and rejected tasks become card decisions. The original authority
    state is passed by the caller from the module output that was reviewed.
    """

    status = str(review.get("status") or "")
    if status not in ("approved", "rejected"):
        return None
    task_id = review.get("task_id") or review.get("id")
    if not task_id:
        raise ValueError("review task requires task_id or id")
    return HumanDecision(
        decision_id=str(task_id),
        decision=status,
        decided_at=review.get("decided_at") or review.get("updated_at"),
        decided_by=str(
            review.get("assignee_id") or review.get("reviewer_id") or ""
        ),
        reason=review.get("reason") or review.get("review_comment"),
        original_authority_state=original_authority_state,
        bound_object_type=bound_object_type,
        bound_object_id=bound_object_id,
        release_ref=release_ref,
        graph_version_ref=graph_version_ref,
        algorithm_version=algorithm_version,
        config_version=config_version,
    )


__all__ = ["human_decision_from_review_task"]
