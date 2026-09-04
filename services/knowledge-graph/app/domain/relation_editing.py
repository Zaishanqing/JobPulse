"""Relation edit validation and optimistic-concurrency plan generation."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.decisions import DomainRejection
from app.domain.value_types import AuditSnapshot


@dataclass(frozen=True)
class RelationEditFacts:
    relation_id: int
    relation_exists: bool
    relation_build_run_id: int | None
    relation_position_id: str | None
    run_exists: bool
    run_position_id: str | None
    published: bool
    revision: int
    status: str
    auto_weight: float
    manual_weight: float | None
    final_weight: float
    auto_confidence: float
    manual_confidence: float | None
    final_confidence: float
    auto_importance_level: str
    manual_importance_level: str | None
    final_importance_level: str


@dataclass(frozen=True)
class RelationEditCommand:
    relation_id: int
    build_run_id: int
    position_id: str
    expected_revision: int
    reason: str
    changed_fields: frozenset[str]
    weight: float | None = None
    confidence: float | None = None
    importance_level: str | None = None


@dataclass(frozen=True)
class RelationEditPlan:
    relation_id: int
    build_run_id: int
    position_id: str
    expected_revision: int
    next_revision: int
    manual_weight: float | None
    final_weight: float
    manual_confidence: float | None
    final_confidence: float
    manual_importance_level: str | None
    final_importance_level: str
    target_status: str
    ensure_review_task: bool
    before: AuditSnapshot
    after: AuditSnapshot
    reason: str


@dataclass(frozen=True)
class RelationEditDecision:
    accepted: bool
    plan: RelationEditPlan | None = None
    rejection: DomainRejection | None = None


@dataclass(frozen=True)
class RelationEditResult:
    relation_id: int
    draft_id: int
    build_run_id: int
    position_id: str
    auto_importance_level: str
    manual_importance_level: str | None
    importance_level: str
    weight: float
    confidence: float
    auto_weight: float
    manual_weight: float | None
    auto_confidence: float
    manual_confidence: float | None
    revision: int


def decide_relation_edit(
    facts: RelationEditFacts, command: RelationEditCommand
) -> RelationEditDecision:
    if not command.reason.strip():
        return RelationEditDecision(
            False, rejection=DomainRejection("validation", "edit reason is required")
        )
    if command.weight is not None and not 0 <= command.weight <= 1:
        return RelationEditDecision(
            False, rejection=DomainRejection("validation", "weight must be between 0 and 1")
        )
    if command.confidence is not None and not 0 <= command.confidence <= 1:
        return RelationEditDecision(
            False,
            rejection=DomainRejection("validation", "confidence must be between 0 and 1"),
        )
    if command.importance_level not in {None, "core", "important", "supplementary"}:
        return RelationEditDecision(
            False,
            rejection=DomainRejection("validation", "invalid importance level"),
        )
    if not facts.relation_exists:
        return RelationEditDecision(
            False, rejection=DomainRejection("not_found", "relation not found")
        )
    if facts.relation_build_run_id != command.build_run_id:
        return RelationEditDecision(
            False,
            rejection=DomainRejection(
                "conflict", "relation does not belong to the requested draft"
            ),
        )
    if facts.relation_position_id != command.position_id:
        return RelationEditDecision(
            False,
            rejection=DomainRejection(
                "conflict", "relation does not belong to the requested position"
            ),
        )
    if not facts.run_exists:
        return RelationEditDecision(
            False, rejection=DomainRejection("not_found", "draft not found")
        )
    if facts.run_position_id != command.position_id:
        return RelationEditDecision(
            False,
            rejection=DomainRejection(
                "conflict", "build run does not belong to the requested position"
            ),
        )
    if facts.published:
        return RelationEditDecision(
            False,
            rejection=DomainRejection(
                "conflict", "published graph versions are immutable; create a draft"
            ),
        )
    manual_weight = (
        command.weight if "weight" in command.changed_fields else facts.manual_weight
    )
    manual_confidence = (
        command.confidence
        if "confidence" in command.changed_fields
        else facts.manual_confidence
    )
    manual_level = (
        command.importance_level
        if "importance_level" in command.changed_fields
        else facts.manual_importance_level
    )
    final_weight = manual_weight if manual_weight is not None else facts.auto_weight
    final_confidence = (
        manual_confidence
        if manual_confidence is not None
        else facts.auto_confidence
    )
    final_level = manual_level or facts.auto_importance_level
    before: AuditSnapshot = {
        "final_weight": facts.final_weight,
        "final_confidence": facts.final_confidence,
        "final_importance_level": facts.final_importance_level,
        "status": facts.status,
        "revision": facts.revision,
    }
    after: AuditSnapshot = {
        "final_weight": final_weight,
        "final_confidence": final_confidence,
        "final_importance_level": final_level,
        "status": "draft",
        "revision": facts.revision + 1,
    }
    return RelationEditDecision(
        True,
        RelationEditPlan(
            command.relation_id,
            command.build_run_id,
            command.position_id,
            command.expected_revision,
            facts.revision + 1,
            manual_weight,
            final_weight,
            manual_confidence,
            final_confidence,
            manual_level,
            final_level,
            "draft",
            True,
            before,
            after,
            command.reason,
        ),
    )
