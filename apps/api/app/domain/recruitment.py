from __future__ import annotations


JOB_STATUSES = frozenset({"draft", "published", "paused", "cancelled"})
ALLOWED_TRANSITIONS = {
    "draft": frozenset({"published", "cancelled"}),
    "published": frozenset({"paused", "cancelled"}),
    "paused": frozenset({"published", "cancelled"}),
    "cancelled": frozenset(),
}


class RecruitmentRuleViolation(ValueError):
    pass


def require_job_manager_role(role: str) -> None:
    if role != "enterprise_user":
        raise RecruitmentRuleViolation("Only enterprise users can manage enterprise jobs")


def require_job_status(status: str) -> None:
    if status not in JOB_STATUSES:
        raise RecruitmentRuleViolation("Invalid enterprise job status")


def require_job_status_transition(current: str, target: str) -> None:
    require_job_status(current)
    require_job_status(target)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise RecruitmentRuleViolation(
            f"Invalid enterprise job status transition: {current} -> {target}"
        )
