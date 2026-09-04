from dataclasses import dataclass


POSITION_ADMIN_ROLES = frozenset({"admin", "developer", "reviewer"})


class PositionRuleViolation(ValueError):
    pass


class PositionCatalogConflict(PositionRuleViolation):
    pass


def require_position_admin(role: str) -> None:
    if role not in POSITION_ADMIN_ROLES:
        raise PositionRuleViolation("No permission to manage standard positions")


@dataclass(frozen=True)
class PositionSkill:
    skill_id: str
    skill_name: str
    category: str
    weight: float
    confidence: float
    importance_level: str
    trend_score: float
    evidence_count: int
    created_at: str | None = None
