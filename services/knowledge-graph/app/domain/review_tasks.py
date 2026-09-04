"""Review-task state machine and persistence/effect plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.domain.decisions import DomainRejection
from app.domain.value_types import AuditSnapshot, ExtensionAttributes


ReviewAction = Literal[
    "claim", "modify", "approve", "reject", "auto_accept", "exclude"
]
OpenReviewTaskStatus = Literal["pending", "claimed", "modified"]
TerminalReviewTaskStatus = Literal[
    "approved", "rejected", "auto_accepted", "excluded"
]
ReviewTaskLifecycle = OpenReviewTaskStatus | TerminalReviewTaskStatus

# This is the single authoritative lifecycle policy. Adapters may persist a
# status supplied by a Plan, but they must not redefine which states are open.
OPEN_REVIEW_TASK_STATUSES: frozenset[OpenReviewTaskStatus] = frozenset(
    {"pending", "claimed", "modified"}
)
TERMINAL_REVIEW_TASK_STATUSES: frozenset[TerminalReviewTaskStatus] = frozenset(
    {"approved", "rejected", "auto_accepted", "excluded"}
)


def is_open_review_status(status: str) -> bool:
    return status in OPEN_REVIEW_TASK_STATUSES


def is_terminal_review_status(status: str) -> bool:
    return status in TERMINAL_REVIEW_TASK_STATUSES


REVIEW_POLICIES: Mapping[str, frozenset[str]] = {
    "review-policy.v1": frozenset(
        {
            "medium_or_low_confidence",
            "low_aggregate_confidence",
            "low_confidence_merge",
            "pre_publish_overall_review",
        }
    ),
    "review-policy.v2": frozenset(
        {
            "medium_or_low_confidence",
            "low_aggregate_confidence",
            "low_confidence_merge",
            "pre_publish_overall_review",
            "conflicting_requirements",
            "insufficient_evidence",
            "unknown_modality",
        }
    ),
}

AUTO_ACCEPTABLE_REVIEW_REASONS: frozenset[str] = REVIEW_POLICIES[
    "review-policy.v1"
]

# Reasons whose decision materially changes published content or indicates a
# hard integrity problem must stay in the human review queue.
HUMAN_REVIEW_REASONS: frozenset[str] = frozenset(
    {
        "unknown_modality",
        "conflicting_requirements",
        "insufficient_evidence",
        "manually_carried_override",
        "alignment_not_exact",
        "quote_coordinates_invalid",
        "evidence_conflict",
        "unresolved_or_ambiguous_classification",
    }
)


def review_task_reasons(payload: ExtensionAttributes) -> tuple[str, ...]:
    values = payload.get("reasons")
    if isinstance(values, list):
        reasons = [str(value) for value in values if isinstance(value, str)]
    else:
        primary = payload.get("reason")
        reasons = [str(primary)] if primary else []
    return tuple(dict.fromkeys(reasons))


def review_policy_auto_acceptable_reasons(
    policy_version: str,
) -> frozenset[str] | None:
    return REVIEW_POLICIES.get(policy_version)


def auto_review_allowed(
    payload: ExtensionAttributes,
    policy_version: str | None = None,
) -> bool:
    reasons = review_task_reasons(payload)
    allowed = (
        review_policy_auto_acceptable_reasons(policy_version)
        if policy_version is not None
        else AUTO_ACCEPTABLE_REVIEW_REASONS
    )
    if allowed is None:
        return False
    return bool(reasons) and all(reason in allowed for reason in reasons)


def requires_human_review(payload: ExtensionAttributes) -> bool:
    return not auto_review_allowed(payload)


@dataclass(frozen=True)
class ReviewTaskFacts:
    task_id: int
    object_type: str
    object_id: str
    build_run_id: int | None
    status: str
    assignee_id: int | None
    payload: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewTaskCommand:
    action: ReviewAction | str
    actor_id: int
    trace_id: str
    reason: str | None = None
    attributes: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewTaskTransition:
    expected_status: str
    target_status: str
    expected_assignee_id: int | None
    target_assignee_id: int | None


@dataclass(frozen=True)
class ReviewTaskEventPlan:
    task_id: int
    actor_id: int
    action: str
    before: AuditSnapshot
    after: AuditSnapshot
    reason: str | None
    trace_id: str


@dataclass(frozen=True)
class ReviewObjectEffect:
    object_type: str
    object_id: str
    build_run_id: int | None
    action: str
    target_status: str


@dataclass(frozen=True)
class ReviewTaskPlan:
    task_id: int
    transition: ReviewTaskTransition
    payload: ExtensionAttributes
    event: ReviewTaskEventPlan
    effect: ReviewObjectEffect | None
    concurrency_message: str


@dataclass(frozen=True)
class ReviewTaskDecision:
    accepted: bool
    plan: ReviewTaskPlan | None = None
    rejection: DomainRejection | None = None


@dataclass(frozen=True)
class ReviewTaskResult:
    task_id: int
    status: str


@dataclass(frozen=True)
class NewReviewTaskPlan:
    object_type: str
    object_id: str
    build_run_id: int | None
    status: str = "pending"
    assignee_id: int | None = None
    payload: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewTaskDedupKey:
    object_type: str
    object_id: str
    build_run_id: int | None


@dataclass(frozen=True)
class ReviewReasonSet:
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.values or any(not value for value in self.values):
            raise ValueError("review reasons must be non-empty")


@dataclass(frozen=True)
class ReviewTaskDedupFacts:
    key: ReviewTaskDedupKey
    matching_tasks: tuple[ReviewTaskFacts, ...]


@dataclass(frozen=True)
class ReviewTaskDedupCommand:
    key: ReviewTaskDedupKey
    reasons: ReviewReasonSet
    attributes: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewTaskMergePlan:
    task_id: int
    expected_status: str
    expected_assignee_id: int | None
    target_status: str
    target_assignee_id: int | None
    payload: ExtensionAttributes


@dataclass(frozen=True)
class ReviewTaskDedupDecision:
    action: Literal["reuse", "merge", "create"]
    existing: ReviewTaskResult | None = None
    merge_plan: ReviewTaskMergePlan | None = None
    new_task_plan: NewReviewTaskPlan | None = None


def decide_review_task_dedup(
    facts: ReviewTaskDedupFacts,
    command: ReviewTaskDedupCommand,
) -> ReviewTaskDedupDecision:
    """Choose reuse, merge, or create without persistence knowledge."""
    reasons = tuple(sorted(set(command.reasons.values)))
    primary_reason = command.reasons.values[0]
    open_tasks = tuple(
        sorted(
            (
                task
                for task in facts.matching_tasks
                if is_open_review_status(task.status)
            ),
            key=lambda task: task.task_id,
        )
    )
    if not open_tasks:
        payload: ExtensionAttributes = {
            **dict(command.attributes),
            "reason": primary_reason,
            "reasons": list(reasons),
        }
        return ReviewTaskDedupDecision(
            "create",
            new_task_plan=NewReviewTaskPlan(
                command.key.object_type,
                command.key.object_id,
                command.key.build_run_id,
                payload=payload,
            ),
        )

    existing = open_tasks[0]
    current_value = existing.payload.get("reasons", [])
    current_reasons = current_value if isinstance(current_value, list) else []
    merged_reasons = tuple(
        sorted(
            set(reasons)
            | {
                str(reason)
                for reason in current_reasons
                if isinstance(reason, str)
            }
        )
    )
    # Existing task metadata wins during reuse, matching the previous behavior
    # while preserving assignee, lifecycle state, and the original primary reason.
    merged_payload: ExtensionAttributes = {
        **dict(command.attributes),
        **dict(existing.payload),
        "reasons": list(merged_reasons),
    }
    if "reason" not in merged_payload:
        merged_payload = {**dict(merged_payload), "reason": merged_reasons[0]}
    result = ReviewTaskResult(existing.task_id, existing.status)
    if dict(merged_payload) == dict(existing.payload):
        return ReviewTaskDedupDecision("reuse", existing=result)
    return ReviewTaskDedupDecision(
        "merge",
        existing=result,
        merge_plan=ReviewTaskMergePlan(
            existing.task_id,
            existing.status,
            existing.assignee_id,
            existing.status,
            existing.assignee_id,
            merged_payload,
        ),
    )


def decide_review_task_transition(
    facts: ReviewTaskFacts, command: ReviewTaskCommand
) -> ReviewTaskDecision:
    if command.action == "claim":
        if facts.status != "pending":
            return ReviewTaskDecision(
                False,
                rejection=DomainRejection(
                    "conflict", "review task is not claimable"
                ),
            )
        target_status = "claimed"
        target_assignee = command.actor_id
        effect = None
    else:
        allowed = {
            "modify": frozenset({"claimed"}),
            "approve": frozenset({"claimed", "modified"}),
            "reject": frozenset({"claimed", "modified"}),
            "auto_accept": OPEN_REVIEW_TASK_STATUSES,
            "exclude": OPEN_REVIEW_TASK_STATUSES,
        }
        if command.action not in allowed or facts.status not in allowed[command.action]:
            return ReviewTaskDecision(
                False,
                rejection=DomainRejection(
                    "conflict", "illegal review task transition"
                ),
            )
        if (
            command.action not in {"auto_accept", "exclude"}
            and facts.assignee_id != command.actor_id
        ):
            return ReviewTaskDecision(
                False,
                rejection=DomainRejection(
                    "conflict", "review task belongs to another reviewer"
                ),
            )
        target_status = {
            "approve": "approved",
            "reject": "rejected",
            "modify": "modified",
            "auto_accept": "auto_accepted",
            "exclude": "excluded",
        }[command.action]
        target_assignee = facts.assignee_id
        effect = ReviewObjectEffect(
            facts.object_type,
            facts.object_id,
            facts.build_run_id,
            command.action,
            {"approve": "approved", "reject": "rejected", "modify": "draft"}[
                command.action
            ]
            if command.action in {"approve", "reject", "modify"}
            else {
                "auto_accept": "auto_accepted",
                "exclude": "excluded",
            }[command.action],
        )
    payload: ExtensionAttributes = {
        **dict(facts.payload),
        **dict(command.attributes),
    }
    before: AuditSnapshot = {
        "status": facts.status,
        "payload": dict(facts.payload),
        "assignee_id": facts.assignee_id,
    }
    after: AuditSnapshot = {
        "status": target_status,
        "payload": dict(payload),
        "assignee_id": target_assignee,
    }
    transition = ReviewTaskTransition(
        facts.status,
        target_status,
        facts.assignee_id,
        target_assignee,
    )
    return ReviewTaskDecision(
        True,
        ReviewTaskPlan(
            facts.task_id,
            transition,
            payload,
            ReviewTaskEventPlan(
                facts.task_id,
                command.actor_id,
                command.action,
                before,
                after,
                command.reason,
                command.trace_id,
            ),
            effect,
            (
                "review task is not claimable"
                if command.action == "claim"
                else "illegal review task transition"
            ),
        ),
    )
