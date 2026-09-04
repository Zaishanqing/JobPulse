from datetime import datetime, timezone
from typing import Literal, TypeAlias


TaskStatus: TypeAlias = Literal[
    "pending", "running", "succeeded", "failed", "cancelled"
]


INTERNAL_TASK_ROLES = frozenset({"admin", "developer", "reviewer"})
TERMINAL_TASK_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
TASK_TRANSITIONS = {
    "pending": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset({"pending"}),
    "cancelled": frozenset({"pending"}),
}


class TaskTransitionConflict(RuntimeError):
    pass


def require_transition(current: str, target: str) -> None:
    if target not in TASK_TRANSITIONS.get(current, frozenset()):
        raise TaskTransitionConflict(f"Invalid task transition: {current} -> {target}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
