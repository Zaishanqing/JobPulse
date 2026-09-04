from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ReviewDecisionProjection:
    decision_id: str
    decision: str
    decided_at: datetime | None = None
    decided_by: str | None = None
    reason: str | None = None


class ReviewChainPort(Protocol):
    def create_scenario_review(
        self,
        object_type: str,
        object_id: str,
        priority: str,
        reason: str,
    ) -> str: ...

    def get_terminal_decision(
        self,
        object_type: str,
        object_id: str,
    ) -> ReviewDecisionProjection | None: ...


__all__ = [
    "ReviewChainPort",
    "ReviewDecisionProjection",
]
