from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AbstractSet, Literal


AvailableSignal = Literal[
    "blocking",
    "uncertainty",
    "impact",
    "reuse",
    "freshness",
]
DEFAULT_AVAILABLE_SIGNALS: frozenset[str] = frozenset(
    {"blocking", "uncertainty", "impact", "reuse", "freshness"}
)


@dataclass(frozen=True)
class ReviewRankInput:
    task_id: str
    status: str
    priority: str = "normal"
    blocking: bool | None = False
    uncertainty_count: int | None = 0
    impact_count: int | None = 0
    reuse_count: int | None = 0
    wait_days: float | None = 0.0
    estimated_review_cost: float | None = 1.0
    created_at: datetime | str | None = None
    available_signals: AbstractSet[str] = DEFAULT_AVAILABLE_SIGNALS
    subject_ref: str | None = None
    entity_ref: str | None = None
    candidate_ref: str | None = None
    object_ref: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    reuse_group_ref: str | None = None
    reuse_group_size: int | None = None
    propagation_count: int | None = None
    review_reason_code: str | None = None
    review_task_type: str | None = None


@dataclass(frozen=True)
class ReviewRankResult:
    task_id: str
    priority_score: float
    priority_reasons: tuple[str, ...]
    affected_subjects: tuple[str, ...]
    blocking_state: bool
    similar_task_count: int
    estimated_review_cost: float
    method_version: str = "review-value-rank.v1"
