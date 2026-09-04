from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.input_limits import MAX_BATCH_SIZE


ReviewPriority = Literal["low", "normal", "high", "urgent"]


class ReviewTaskCreate(BaseModel):
    object_type: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=64)
    priority: ReviewPriority = "normal"
    reason: str | None = None


class ReviewTaskDecision(BaseModel):
    review_comment: str | None = None


class ReviewTaskRejection(BaseModel):
    review_comment: str = Field(min_length=1, max_length=2000)


class ReviewTaskModify(BaseModel):
    review_comment: str | None = None
    modified_payload: dict[str, Any] = Field(default_factory=dict)


class ReviewTaskBatch(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
    action: Literal["claim", "approve", "reject"]
    reason: str = Field(min_length=1, max_length=2000)
