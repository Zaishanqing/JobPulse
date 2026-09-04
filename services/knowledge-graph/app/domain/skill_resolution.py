"""State machine for resolving normalized skill candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domain.decisions import DomainRejection
from app.domain.value_types import ExtensionAttributes


SkillResolutionAction = Literal["resolve", "create_skill", "reject"]


@dataclass(frozen=True)
class SkillResolutionItemFact:
    item_id: int
    document_id: str
    item_type: str
    source_name: str
    status: str
    resolution: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class SkillCatalogFact:
    skill_id: str
    canonical_name: str
    category_code: str
    subcategory_code: str | None


@dataclass(frozen=True)
class NormalizedSkillTargetFact:
    normalized_skill_id: int
    source_name: str


@dataclass(frozen=True)
class SkillResolutionFacts:
    item: SkillResolutionItemFact
    normalized_skill: NormalizedSkillTargetFact | None
    target_skill: SkillCatalogFact | None
    requested_skill_id_exists: bool


@dataclass(frozen=True)
class SkillResolutionCommand:
    action: SkillResolutionAction | str
    actor_id: int
    reason: str | None
    trace_id: str
    skill_id: str | None = None
    generated_skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None
    alias: str | None = None
    extensions: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class SkillCreatePlan:
    skill_id: str
    canonical_name: str
    category_code: str
    subcategory_code: str | None
    alias: str


@dataclass(frozen=True)
class SkillResolutionPlan:
    item_id: int
    document_id: str
    expected_item_status: str
    target_item_status: str
    actor_id: int
    reviewed_reason: str | None
    resolved_skill: SkillCatalogFact | None
    normalized_skill_id: int | None
    normalized_skill_status: str | None
    resolution: ExtensionAttributes
    create_skill: SkillCreatePlan | None
    review_status: str
    event_action: str
    trace_id: str


@dataclass(frozen=True)
class SkillResolutionDecision:
    accepted: bool
    plan: SkillResolutionPlan | None = None
    rejection: DomainRejection | None = None


@dataclass(frozen=True)
class SkillResolutionResult:
    item_id: int
    status: str
    skill_id: str | None = None
    canonical_name: str | None = None


def decide_skill_resolution(
    facts: SkillResolutionFacts,
    command: SkillResolutionCommand,
) -> SkillResolutionDecision:
    item = facts.item
    if item.status != "open":
        return SkillResolutionDecision(
            False,
            rejection=DomainRejection(
                "conflict", "unresolved item was already processed"
            ),
        )
    if command.action not in {"resolve", "create_skill", "reject"}:
        return SkillResolutionDecision(
            False,
            rejection=DomainRejection(
                "validation", "unsupported unresolved item action"
            ),
        )
    if command.action == "reject":
        return SkillResolutionDecision(
            True,
            SkillResolutionPlan(
                item.item_id,
                item.document_id,
                item.status,
                "rejected",
                command.actor_id,
                command.reason,
                None,
                None,
                None,
                command.extensions,
                None,
                "rejected",
                "reject",
                command.trace_id,
            ),
        )
    if item.item_type != "skill" or facts.normalized_skill is None:
        return SkillResolutionDecision(
            False,
            rejection=DomainRejection(
                "not_found", "normalized skill not found"
            ),
        )
    if command.action == "resolve":
        if facts.target_skill is None:
            return SkillResolutionDecision(
                False,
                rejection=DomainRejection(
                    "validation", "target skill does not exist"
                ),
            )
        target = facts.target_skill
        create = None
        status = "resolved_existing_skill"
    else:
        if not command.category_code:
            return SkillResolutionDecision(
                False,
                rejection=DomainRejection(
                    "validation", "category_code is required"
                ),
            )
        skill_id = command.skill_id or command.generated_skill_id
        if not skill_id:
            return SkillResolutionDecision(
                False,
                rejection=DomainRejection(
                    "validation", "generated skill_id is required"
                ),
            )
        if facts.requested_skill_id_exists:
            return SkillResolutionDecision(
                False,
                rejection=DomainRejection("conflict", "skill_id already exists"),
            )
        target = SkillCatalogFact(
            skill_id,
            command.canonical_name or item.source_name,
            command.category_code,
            command.subcategory_code,
        )
        create = SkillCreatePlan(
            target.skill_id,
            target.canonical_name,
            target.category_code,
            target.subcategory_code,
            command.alias or item.source_name,
        )
        status = "created_new_skill"
    resolution: ExtensionAttributes = {
        "skill_id": target.skill_id,
        "canonical_name": target.canonical_name,
    }
    return SkillResolutionDecision(
        True,
        SkillResolutionPlan(
            item.item_id,
            item.document_id,
            item.status,
            status,
            command.actor_id,
            command.reason,
            target,
            facts.normalized_skill.normalized_skill_id,
            "manually_confirmed",
            resolution,
            create,
            "approved",
            command.action,
            command.trace_id,
        ),
    )

def skill_resolution_result(plan: SkillResolutionPlan) -> SkillResolutionResult:
    return SkillResolutionResult(
        plan.item_id,
        plan.target_item_status,
        plan.resolved_skill.skill_id if plan.resolved_skill else None,
        plan.resolved_skill.canonical_name if plan.resolved_skill else None,
    )
