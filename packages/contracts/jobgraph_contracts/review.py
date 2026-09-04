from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceContextV1(ReviewContractModel):
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    original_values: dict[str, Any] = Field(default_factory=dict)
    current_values: dict[str, Any] = Field(default_factory=dict)
    modified_values: dict[str, Any] = Field(default_factory=dict)
    impacted_relations: list[dict[str, Any]] = Field(default_factory=list)
    review_flags: list[Any] = Field(default_factory=list)
    impact_scope: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class ReviewTaskV1(ReviewContractModel):
    model_config = ConfigDict(extra="allow")

    contract_version: Literal["review-task.v1"] = "review-task.v1"
    task_id: str
    source_system: Literal["knowledge-graph", "main-system"]
    task_kind: str
    object_type: str
    object_id: str
    build_run_id: int | None = None
    status: Literal["pending", "claimed", "modified", "approved", "rejected"]
    allowed_actions: list[Literal["claim", "release", "modify", "approve", "reject"]]
    risk_level: Literal["low", "medium", "high"]
    assignee_id: str | int | None = None
    evidence_context: EvidenceContextV1
    created_at: str | None = None


class ReviewTaskPageV1(ReviewContractModel):
    contract_version: Literal["review-page.v1"] = "review-page.v1"
    items: list[ReviewTaskV1]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class ReviewBatchOperationV1(ReviewContractModel):
    contract_version: Literal["review-batch.v1"] = "review-batch.v1"
    task_ids: list[int] = Field(min_length=1, max_length=100)
    action: Literal["claim", "approve", "reject"]
    reason: str = Field(min_length=1)


class ReviewBatchResultV1(ReviewContractModel):
    contract_version: Literal["review-batch-result.v1"] = "review-batch-result.v1"
    action: Literal["claim", "approve", "reject"]
    task_ids: list[int]
    statuses: dict[str, str]
