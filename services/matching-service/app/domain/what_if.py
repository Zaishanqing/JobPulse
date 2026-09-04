"""Deterministic v1 counterfactual profile transformations and contracts."""

from __future__ import annotations

import re
from datetime import timedelta
from math import ceil
from typing import Literal

from pydantic import Field, model_validator

from app.domain.evaluation import MatchEvaluation
from app.domain.profiles import (
    CapabilityEvidenceLink,
    CapabilityProfile,
    CredentialFeature,
    CVMatchProfile,
    CVSkill,
    EducationFeature,
    Evidence,
    ExperienceFeature,
    ImmutableDTO,
    LanguageFeature,
    MatchFeature,
    PositionMatchProfile,
)

WhatIfActionType = Literal[
    "add_skill",
    "add_project_experience",
    "strengthen_evidence",
    "strengthen_ownership",
    "satisfy_hard_condition",
    "controlled_skill_transfer",
]
OwnershipLevel = Literal[
    "unknown", "declared", "used", "participated", "implemented", "owned", "designed", "led"
]
MilestoneStatus = Literal[
    "planned", "in_progress", "completed", "demonstrated", "verified"
]


class CostBand(ImmutableDTO):
    """Honest cost estimate: a range plus confidence, never a false-precise hour."""

    min_hours: float = Field(ge=0)
    expected_hours: float = Field(ge=0)
    max_hours: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> CostBand:
        if not (self.min_hours <= self.expected_hours <= self.max_hours):
            raise ValueError("cost band must satisfy min <= expected <= max")
        return self


class WhatIfAction(ImmutableDTO):
    action_id: str = Field(min_length=1)
    action_type: WhatIfActionType
    skill_id: str | None = None
    canonical_name: str | None = None
    # Catalog display title (e.g. “PyTorch 模型训练与调优实践”). Kept separate
    # from canonical_name so the skill's canonical name stays intact.
    learning_title: str | None = None
    target_level: str | None = None
    ownership: OwnershipLevel | None = None
    target_requirement_ids: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    business_scenarios: tuple[str, ...] = ()
    source_skill_id: str | None = Field(default=None, min_length=1)
    path_refs: tuple[str, ...] = ()
    graph_version: str | None = Field(default=None, min_length=1)
    confidence_basis: str | None = None
    source_confidence: float | None = Field(default=None, ge=0, le=1)
    path_quality: float | None = Field(default=None, ge=0, le=1)
    edge_confidences: tuple[float, ...] = ()
    validated_path_refs: tuple[str, ...] = ()
    target_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_algorithm_version: str | None = None
    transfer_hop_count: int | None = Field(default=None, ge=1, le=2)
    transfer_outcome_status: Literal["eligible", "partial"] | None = None
    transfer_relation_types: tuple[
        Literal["equivalent", "parent_child", "transferable"], ...
    ] = ()
    estimated_hours: float = Field(default=0.0, ge=0)
    stage: Literal[
        "foundation",
        "proficiency",
        "evidence",
        "project",
        "ownership",
        "context",
        "hard_gate",
        "transfer",
    ] | None = None
    requires_action_ids: tuple[str, ...] = ()
    supersedes_action_ids: tuple[str, ...] = ()
    cost_model: Literal[
        "heuristic_level_distance.v1",
        "heuristic_hard_gate.v1",
        "heuristic_transfer_path.v1",
        "cost-band.v1",
    ] = "heuristic_level_distance.v1"
    estimated_score_delta: float | None = None
    estimated_utility: float | None = None
    score_effect_reason: str | None = None
    milestone_status: MilestoneStatus | None = None
    deliverable: str | None = None
    acceptance_criteria: tuple[str, ...] = ()
    score_credit_allowed: bool | None = None
    suitable_for_learning: bool = True
    cost_band: CostBand | None = None

    @model_validator(mode="after")
    def validate_target(self) -> WhatIfAction:
        if self.action_type in {
            "add_skill",
            "strengthen_evidence",
            "strengthen_ownership",
            "controlled_skill_transfer",
        } and not self.skill_id:
            raise ValueError(f"{self.action_type} requires skill_id")
        if self.action_type == "strengthen_ownership" and self.ownership is None:
            raise ValueError("strengthen_ownership requires ownership")
        if self.action_type == "add_project_experience" and not (
            self.skill_id or self.responsibilities or self.business_scenarios
        ):
            raise ValueError("add_project_experience requires a skill or project context")
        if self.action_type == "satisfy_hard_condition" and not self.target_requirement_ids:
            raise ValueError("satisfy_hard_condition requires a target requirement")
        if self.action_type == "controlled_skill_transfer":
            missing = []
            if not self.source_skill_id:
                missing.append("source_skill_id")
            if not self.path_refs:
                missing.append("path_refs")
            if not self.graph_version:
                missing.append("graph_version")
            if missing:
                raise ValueError(
                    "controlled_skill_transfer requires " + ", ".join(missing)
                )
        return self


class WhatIfActionSetError(ValueError):
    """A stable validation failure for a counterfactual action graph."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ActionSetResolution:
    """Result of resolving a counterfactual action graph.

    ``active_actions`` are the only actions allowed to reach mutation and are
    returned in the deterministic dependency execution order.
    """

    __slots__ = ("_active_actions", "_superseded_action_ids")

    def __init__(
        self,
        *,
        active_actions: tuple[WhatIfAction, ...],
        superseded_action_ids: tuple[str, ...],
    ) -> None:
        self._active_actions = active_actions
        self._superseded_action_ids = superseded_action_ids

    @property
    def active_actions(self) -> tuple[WhatIfAction, ...]:
        return self._active_actions

    @property
    def superseded_action_ids(self) -> tuple[str, ...]:
        return self._superseded_action_ids

    @property
    def execution_order(self) -> tuple[str, ...]:
        return tuple(item.action_id for item in self._active_actions)


class ActionSetValidator:
    """Validate references and resolve supersession into one deterministic order.

    Action application is order-sensitive when several actions update the same
    capability. Centralizing this validation prevents the public What-if API
    and route planner from assigning different meanings to the same action set.
    """

    @staticmethod
    def validate(
        actions: tuple[WhatIfAction, ...],
        *,
        known_requirement_ids: frozenset[str] | None = None,
        known_hard_condition_ids: frozenset[str] | None = None,
    ) -> tuple[WhatIfAction, ...]:
        action_ids = tuple(item.action_id for item in actions)
        if len(action_ids) != len(set(action_ids)):
            raise WhatIfActionSetError(
                "WHAT_IF_ACTION_ID_DUPLICATE", "action_id values must be unique"
            )
        by_id = {item.action_id: item for item in actions}
        followers: dict[str, set[str]] = {action_id: set() for action_id in action_ids}
        indegree: dict[str, int] = {action_id: 0 for action_id in action_ids}
        for action in actions:
            dependencies = set(action.requires_action_ids)
            superseded = set(action.supersedes_action_ids)
            if len(dependencies) != len(action.requires_action_ids):
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_DEPENDENCY_DUPLICATE",
                    f"action {action.action_id} repeats a dependency",
                )
            if len(superseded) != len(action.supersedes_action_ids):
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_SUPERSEDE_DUPLICATE",
                    f"action {action.action_id} repeats a superseded action",
                )
            if action.action_id in dependencies:
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_DEPENDENCY_SELF",
                    f"action {action.action_id} cannot depend on itself",
                )
            if action.action_id in superseded:
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_SUPERSEDE_SELF",
                    f"action {action.action_id} cannot supersede itself",
                )
            missing_dependencies = sorted(dependencies - by_id.keys())
            if missing_dependencies:
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_DEPENDENCY_UNKNOWN",
                    "unknown dependency action_id: " + ", ".join(missing_dependencies),
                )
            missing_superseded = sorted(superseded - by_id.keys())
            if missing_superseded:
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_SUPERSEDE_UNKNOWN",
                    "unknown superseded action_id: " + ", ".join(missing_superseded),
                )
            if known_requirement_ids is not None:
                missing_targets = sorted(
                    set(action.target_requirement_ids) - known_requirement_ids
                )
                if missing_targets:
                    raise WhatIfActionSetError(
                        "WHAT_IF_ACTION_TARGET_UNKNOWN",
                        "unknown target requirement_id: " + ", ".join(missing_targets),
                    )
            if (
                action.action_type == "satisfy_hard_condition"
                and known_hard_condition_ids is not None
                and len(
                    set(action.target_requirement_ids).intersection(
                        known_hard_condition_ids
                    )
                )
                != 1
            ):
                raise WhatIfActionSetError(
                    "WHAT_IF_HARD_CONDITION_TARGET_INVALID",
                    "satisfy_hard_condition must target exactly one hard condition",
                )
            indegree[action.action_id] = len(dependencies)
            for dependency_id in dependencies:
                followers[dependency_id].add(action.action_id)

        ready = sorted(
            action_id for action_id, degree in indegree.items() if degree == 0
        )
        ordered: list[WhatIfAction] = []
        while ready:
            action_id = ready.pop(0)
            ordered.append(by_id[action_id])
            for follower_id in sorted(followers[action_id]):
                indegree[follower_id] -= 1
                if indegree[follower_id] == 0:
                    ready.append(follower_id)
                    ready.sort()
        if len(ordered) != len(actions):
            raise WhatIfActionSetError(
                "WHAT_IF_ACTION_DEPENDENCY_CYCLE",
                "action dependencies must form an acyclic graph",
            )
        return tuple(ordered)

    @staticmethod
    def resolve(actions: tuple[WhatIfAction, ...]) -> ActionSetResolution:
        """Resolve supersede edges into the actions that will actually apply.

        An action is inactive when an active action transitively supersedes it
        (roots of the supersede graph stay active). Superseded actions never
        reach mutation. The active actions are ordered by their dependency
        graph so ``apply_actions`` can consume them directly.
        """
        ordered = ActionSetValidator.validate(actions)
        action_ids = tuple(item.action_id for item in ordered)

        # A single action may not both require and supersede the same action:
        # the superseded action is never applied, so the requirement would
        # silently become unsatisfiable.
        for action in ordered:
            overlapping = sorted(
                set(action.requires_action_ids).intersection(
                    action.supersedes_action_ids
                )
            )
            if overlapping:
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_SUPERSEDE_CONFLICT",
                    f"action {action.action_id} cannot require and supersede the "
                    f"same action: {', '.join(overlapping)}",
                )

        # Supersession forms a directed graph (superseder -> superseded).
        # Active actions are the roots (nothing supersedes them); every other
        # node is transitively superseded by some root and therefore inactive.
        superseded_by_count: dict[str, int] = {
            action_id: 0 for action_id in action_ids
        }
        supersede_followers: dict[str, set[str]] = {
            action_id: set() for action_id in action_ids
        }
        for action in ordered:
            for target_id in action.supersedes_action_ids:
                superseded_by_count[target_id] += 1
                supersede_followers[action.action_id].add(target_id)

        active_ids = {
            action_id
            for action_id, count in superseded_by_count.items()
            if count == 0
        }
        superseded_ids = tuple(
            action_id for action_id in action_ids if action_id not in active_ids
        )

        # Cycle detection over the supersede graph (Kahn, deterministic).
        indegree = dict(superseded_by_count)
        ready = sorted(
            action_id for action_id, degree in indegree.items() if degree == 0
        )
        visited = 0
        while ready:
            action_id = ready.pop(0)
            visited += 1
            for follower_id in sorted(supersede_followers[action_id]):
                indegree[follower_id] -= 1
                if indegree[follower_id] == 0:
                    ready.append(follower_id)
                    ready.sort()
        if visited != len(action_ids):
            raise WhatIfActionSetError(
                "WHAT_IF_ACTION_SUPERSEDE_CYCLE",
                "action supersede relations must form an acyclic graph",
            )

        # An active action may not depend on an inactive (superseded) action.
        for action in ordered:
            if action.action_id not in active_ids:
                continue
            missing_active = sorted(set(action.requires_action_ids) - active_ids)
            if missing_active:
                raise WhatIfActionSetError(
                    "WHAT_IF_ACTION_DEPENDENCY_SUPERSEDED",
                    "active action " + action.action_id
                    + " requires superseded action(s): "
                    + ", ".join(missing_active),
                )

        # The supersede relation is fully consumed above, so the resolved
        # active actions no longer carry it: referencing a superseded action
        # that is not part of the active set would otherwise look like a
        # dangling reference at the mutation boundary.
        active_ordered = tuple(
            item.model_copy(update={"supersedes_action_ids": ()})
            for item in ordered
            if item.action_id in active_ids
        )
        return ActionSetResolution(
            active_actions=active_ordered,
            superseded_action_ids=superseded_ids,
        )


class DimensionDelta(ImmutableDTO):
    dimension: str = Field(min_length=1)
    baseline_score: float | None = Field(default=None, ge=0, le=100)
    scenario_score: float | None = Field(default=None, ge=0, le=100)
    delta: float | None = None


class WhatIfResult(ImmutableDTO):
    # Outcome boundary: every result here is a *modeled counterfactual re-score*
    # produced by running the formal scorer on a hypothetical profile. It is NOT
    # an observed real-world outcome.
    outcome_semantics: Literal["modeled_counterfactual"] = "modeled_counterfactual"
    observed_outcome: Literal[False] = False

    generation_status: Literal["completed", "rejected"]
    scenario_id: str = Field(min_length=1)
    baseline_evaluation: MatchEvaluation | None = None
    scenario_evaluation: MatchEvaluation | None = None
    # When actions are planned (not yet demonstrated/verified), the formal
    # projected re-score comes from a separate projected evaluation. Route
    # radar comparisons must use this evaluation's dimension scores, otherwise
    # the modeled overall gain has no per-dimension counterpart.
    projected_evaluation: MatchEvaluation | None = None
    actions: tuple[WhatIfAction, ...] = ()
    baseline_score: float | None = Field(default=None, ge=0, le=100)
    # Primary modeled-contract fields (the canonical names for these values).
    modeled_final_score: float | None = Field(default=None, ge=0, le=100)
    modeled_score_delta: float | None = None
    modeled_confidence_delta: float | None = None
    # Deprecated aliases kept for compatibility with existing callers/artifacts.
    # Prefer the modeled_* fields; scenario_score/score_delta/confidence_delta
    # mirror the same values and do NOT represent observed real-world learning.
    scenario_score: float | None = Field(default=None, ge=0, le=100)
    score_delta: float | None = None
    baseline_confidence: float | None = Field(default=None, ge=0, le=1)
    scenario_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_delta: float | None = None
    baseline_recommendation: str | None = None
    scenario_recommendation: str | None = None
    baseline_hard_gate_status: str | None = None
    scenario_hard_gate_status: str | None = None
    dimension_deltas: tuple[DimensionDelta, ...] = ()
    denominator_changed: bool = False
    score_effect_status: Literal["modeled", "not_modeled_in_v1"] = "modeled"
    baseline_evaluation_id: str | None = None
    scoring_algorithm_version: str | None = None
    scoring_config_version: str | None = None
    position_graph_version: str | None = None
    target_type: Literal["standard_position", "enterprise_job"] | None = None
    use_enterprise_weights: bool | None = None
    hypothetical: Literal[True] = True
    algorithm_version: Literal["counterfactual-profile.v2"] = "counterfactual-profile.v2"
    error_code: str | None = None
    error_message: str | None = None
    # Projected-if-completed outcome: a hypothetical re-score under the
    # assumption that every planned action was completed and verified.  It is
    # never a current-verified result and never mutates the source CV.
    projected_if_completed: bool = False
    projected_actions: tuple[WhatIfAction, ...] = ()
    projected_score: float | None = Field(default=None, ge=0, le=100)
    projected_score_delta: float | None = None
    projected_confidence: float | None = Field(default=None, ge=0, le=1)
    projected_recommendation: str | None = None
    projected_hard_gate_status: str | None = None
    current_verified_outcome: str | None = None
    projected_if_completed_outcome: str | None = None


_LEVELS = ("unknown", "basic", "working", "proficient", "advanced", "expert")
_VERIFICATION_ORDER = {
    "not_observed": 0,
    "experience_only": 1,
    "partially_supported": 2,
    "supported": 3,
}


def _level(value: str | None, fallback: str = "working") -> str:
    return value if value in _LEVELS else fallback


def _confidence_band(value: float) -> Literal["none", "low", "medium", "high"]:
    if value <= 0:
        return "none"
    if value < 0.5:
        return "low"
    if value < 0.8:
        return "medium"
    return "high"


def _skill_identity(
    action: WhatIfAction, position: PositionMatchProfile
) -> tuple[str | None, str | None, str]:
    requirement = next(
        (
            item
            for item in position.required_skills + position.preferred_skills
            if item.skill_id == action.skill_id
        ),
        None,
    )
    skill_id = action.skill_id or (requirement.skill_id if requirement else None)
    name = action.canonical_name or (requirement.canonical_name if requirement else None)
    target = _level(action.target_level or (requirement.required_level if requirement else None))
    return skill_id, name, target


def _hypothetical_evidence(action: WhatIfAction, text: str) -> Evidence:
    return Evidence(
        source_id=f"what-if:{action.action_id}",
        quote=f"hypothetical:{text}",
        alignment="unresolved",
    )


def _upsert_capability(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    action: WhatIfAction,
    *,
    project_id: str | None = None,
    transfer_confidence: float | None = None,
) -> CVMatchProfile:
    skill_id, name, target = _skill_identity(action, position)
    if skill_id is None:
        return cv
    existing = next((item for item in cv.capability_profiles if item.skill_id == skill_id), None)
    confidence = (
        transfer_confidence
        if action.action_type == "controlled_skill_transfer"
        and transfer_confidence is not None
        else 0.85
        if action.action_type == "add_project_experience"
        else 0.70
    )
    if existing is not None:
        target = max(
            (existing.demonstrated_level, target),
            key=_LEVELS.index,
        )
        if action.action_type != "controlled_skill_transfer":
            confidence = max(existing.support_confidence, confidence)
    if action.action_type == "strengthen_evidence":
        confidence = max(confidence, 0.90)
    evidence_text = name or skill_id
    if action.action_type == "controlled_skill_transfer":
        evidence_text = (
            f"controlled-skill-transfer:{action.source_skill_id}->{skill_id}"
            f":graph:{action.graph_version or 'unspecified'}"
            f":paths:{','.join(action.validated_path_refs or action.path_refs)}"
        )
    evidence = _hypothetical_evidence(action, evidence_text)
    link_id = f"what-if-link:{action.action_id}:{skill_id}"
    experience_id = project_id or f"what-if-experience:{action.action_id}"
    link = CapabilityEvidenceLink(
        link_id=link_id,
        document_id=cv.cv_id,
        aggregation_key=f"skill:{skill_id}",
        skill_id=skill_id,
        canonical_name=name or skill_id,
        experience_skill_feature_id=f"what-if-feature:{action.action_id}:{skill_id}",
        experience_feature_id=experience_id,
        support_signals=(
            "hypothetical",
            f"ownership:{action.ownership or ('implemented' if project_id else 'used')}",
            *(
                (
                    "controlled_skill_transfer",
                    f"transfer_hops:{action.transfer_hop_count}",
                    f"transfer_outcome:{action.transfer_outcome_status}",
                    "transfer_relations:" + ",".join(action.transfer_relation_types),
                )
                if action.action_type == "controlled_skill_transfer"
                else ()
            ),
        ),
        support_score=3 if project_id else 2,
        demonstrated_level=target,
        support_confidence=confidence,
        confidence_band=_confidence_band(confidence),
        evidence_refs=(evidence,),
        taxonomy_version=cv.taxonomy_version,
        derivation_version="counterfactual-profile.v2",
    )
    proposed_verification = (
        "supported"
        if project_id or action.action_type == "strengthen_evidence"
        else "partially_supported"
    )
    verification_status = max(
        (
            existing.verification_status if existing else "not_observed",
            proposed_verification,
        ),
        key=lambda value: _VERIFICATION_ORDER.get(value, -1),
    )
    profile = CapabilityProfile(
        profile_id=(
            existing.profile_id
            if existing
            else f"what-if-capability:{action.action_id}:{skill_id}"
        ),
        document_id=cv.cv_id,
        aggregation_key=(existing.aggregation_key if existing else f"skill:{skill_id}"),
        skill_id=skill_id,
        canonical_name=name or (existing.canonical_name if existing else skill_id),
        declared_feature_ids=(existing.declared_feature_ids if existing else ()),
        experience_skill_feature_ids=tuple(
            dict.fromkeys(
                (
                    *((existing.experience_skill_feature_ids) if existing else ()),
                    link.experience_skill_feature_id,
                )
            )
        ),
        evidence_link_ids=tuple(
            dict.fromkeys((*((existing.evidence_link_ids) if existing else ()), link_id))
        ),
        declared_level=(existing.declared_level if existing else None),
        demonstrated_level=target,
        demonstrated_level_label=target,
        verification_status=verification_status,
        support_confidence=confidence,
        confidence_band=_confidence_band(confidence),
        independent_experience_count=(
            existing.independent_experience_count if existing else 0
        )
        + (1 if project_id else 0),
        aggregate_support_score=(
            existing.aggregate_support_score if existing else 0
        )
        + link.support_score,
        evidence_bonus=max(existing.evidence_bonus if existing else 0.0, 0.1),
        resolution_status="resolved",
    )
    capabilities = tuple(
        item for item in cv.capability_profiles if item.skill_id != skill_id
    ) + (profile,)
    skills = tuple(item for item in cv.skills if item.skill_id != skill_id) + (
        CVSkill(
            aggregation_key=f"skill:{skill_id}",
            skill_id=skill_id,
            canonical_name=name or skill_id,
            normalization_confidence=1.0,
            resolution_source="what_if_action",
            declared_level=(existing.declared_level if existing else None),
            demonstrated_level=target,
            verification_status=profile.verification_status,
            resolution_status="resolved",
            evidence_refs=(evidence,),
        ),
    )
    return cv.model_copy(
        update={
            "skills": skills,
            "capability_profiles": capabilities,
            "capability_evidence_links": cv.capability_evidence_links + (link,),
            "evidence_refs": cv.evidence_refs + (evidence,),
        }
    )


def _first_allowed_value(value: str) -> str:
    return next(
        (item.strip() for item in re.split(r"\s*[|,]\s*", value) if item.strip()),
        value,
    )


def _required_experience_days(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(years?|months?)\s*", value, re.I)
    if match is None:
        return None
    amount = float(match.group(1))
    months = amount * 12 if match.group(2).casefold() in {"year", "years"} else amount
    return ceil(months * 30.4375) + 1


def _satisfy_hard_condition(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    action: WhatIfAction,
) -> CVMatchProfile:
    targets = set(action.target_requirement_ids)
    condition = next(
        (item for item in position.hard_conditions if item.condition_id in targets),
        None,
    )
    if condition is None:
        return cv
    value = _first_allowed_value(condition.value)
    evidence = _hypothetical_evidence(action, f"{condition.condition_type}:{value}")
    item_id = f"what-if-hard:{action.action_id}"
    update: dict[str, object]
    if condition.condition_type == "education":
        update = {
            "education": cv.education
            + (
                EducationFeature(
                    education_id=item_id,
                    degree_level=value,
                    resolution_status="resolved",
                    evidence_refs=(evidence,),
                ),
            )
        }
    elif condition.condition_type == "experience":
        days = _required_experience_days(condition.value)
        if days is None or condition.operator != "at_least":
            return cv
        update = {
            "work_experiences": cv.work_experiences
            + (
                ExperienceFeature(
                    experience_id=item_id,
                    kind="work",
                    role="hypothetical",
                    responsibilities=(f"satisfy {condition.condition_id}",),
                    start_date=cv.as_of_date - timedelta(days=days),
                    end_date=cv.as_of_date,
                    evidence_refs=(evidence,),
                ),
            )
        }
    elif condition.condition_type == "certificate":
        update = {
            "certificates": cv.certificates
            + (
                CredentialFeature(
                    credential_id=item_id,
                    name=value,
                    resolution_status="resolved",
                    evidence_refs=(evidence,),
                ),
            )
        }
    elif condition.condition_type == "language":
        code, separator, proficiency = value.partition(":")
        update = {
            "languages": cv.languages
            + (
                LanguageFeature(
                    language_code=code,
                    proficiency=proficiency if separator else None,
                    resolution_status="resolved",
                    evidence_refs=(evidence,),
                ),
            )
        }
    else:
        feature = MatchFeature(
            feature_id=item_id,
            document_id=cv.cv_id,
            side="cv",
            feature_type=condition.condition_type,
            source_object_id=item_id,
            source_scope="what_if_hard_condition",
            canonical_id=value,
            canonical_name=value,
            raw_text=f"hypothetical:{value}",
            vector_text=None,
            structured_values={"hypothetical": True},
            resolution_status="resolved",
            evidence_refs=(evidence,),
            taxonomy_version=cv.taxonomy_version,
            derivation_version="counterfactual-hard-gate.v1",
        )
        update = {"match_features": cv.match_features + (feature,)}
    return cv.model_copy(
        update={**update, "evidence_refs": cv.evidence_refs + (evidence,)}
    )


def apply_actions(
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    actions: tuple[WhatIfAction, ...],
    *,
    scenario_id: str,
) -> CVMatchProfile:
    """Apply actions to an in-memory profile; the source profile remains immutable."""
    result = cv
    # Resolve supersession again at the mutation boundary so internal callers
    # cannot bypass the same action semantics enforced by the public service.
    # Superseded actions never reach mutation.
    resolution = ActionSetValidator.resolve(actions)
    for action in resolution.active_actions:
        if action.action_type == "satisfy_hard_condition":
            result = _satisfy_hard_condition(result, position, action)
            continue
        if action.action_type == "controlled_skill_transfer":
            if action.score_credit_allowed is False:
                # Parent-child / learning-only transfer paths never create
                # scoring evidence.  They stay as prospective learning actions.
                continue
            if (
                not action.validated_path_refs
                or not action.edge_confidences
                or action.source_confidence is None
                or action.path_quality is None
                or action.target_confidence is None
                or action.confidence_basis is None
            ):
                raise WhatIfActionSetError(
                    "WHAT_IF_TRANSFER_VALIDATION_REQUIRED",
                    f"action {action.action_id} must be validated before execution",
                )
            result = _upsert_capability(
                result,
                position,
                action,
                transfer_confidence=action.target_confidence,
            )
            continue
        if (
            action.action_type in {"add_skill", "add_project_experience"}
            and action.milestone_status not in {"demonstrated", "verified"}
        ):
            # planned/in_progress/completed-but-unverified actions never
            # mutate the formal CV profile or create scoring evidence.
            continue
        project_id = None
        if action.action_type == "add_project_experience":
            project_id = f"what-if-project:{action.action_id}"
            evidence = _hypothetical_evidence(
                action, " | ".join(action.responsibilities) or project_id
            )
            project = ExperienceFeature(
                experience_id=project_id,
                kind="project",
                role=action.ownership or "implemented",
                responsibilities=action.responsibilities,
                business_scenarios=action.business_scenarios,
                tool_skill_ids=((action.skill_id,) if action.skill_id else ()),
                evidence_refs=(evidence,),
            )
            result = result.model_copy(
                update={
                    "projects": result.projects + (project,),
                    "evidence_refs": result.evidence_refs + (evidence,),
                }
            )
        result = _upsert_capability(result, position, action, project_id=project_id)
    return result.model_copy(
        update={
            "profile_version": f"{cv.profile_version}|scenario:{scenario_id}",
            "source_version": f"{cv.source_version}|scenario:{scenario_id}",
        }
    )


_OUTCOME_RECOMMENDATION_RANK = {
    "not_recommended": 0,
    "insufficient_information": 0,
    "weak_match": 1,
    "potential_match": 2,
    "strong_match": 3,
}
_OUTCOME_HARD_RANK = {
    "failed": 0,
    "uncertain": 1,
    "passed": 2,
    "not_applicable": 2,
}


def classify_what_if_outcome(
    result: WhatIfResult,
    minimal_status: str,
    *,
    meaningful_score_delta: float = 5.0,
) -> tuple[str, tuple[str, ...]]:
    """Classify What-if outcome without treating any positive delta as success.

    ``partial_effective`` requires at least one of:
    * recommendation level improved;
    * a key requirement gap actually resolved;
    * information/evidence sufficiency materially improved;
    * score improvement at or above the meaningful delta.
    A Hard Gate regression always blocks partial_effective.
    """

    if minimal_status == "already_satisfied":
        return "already_satisfied", ("ALREADY_SATISFIED",)
    if result.generation_status != "completed":
        return "scenario_error", ((result.error_code or "SCENARIO_ERROR"),)
    baseline_gate = result.baseline_hard_gate_status or ""
    scenario_gate = result.scenario_hard_gate_status or ""
    gate_regressed = (
        _OUTCOME_HARD_RANK.get(scenario_gate, 0)
        < _OUTCOME_HARD_RANK.get(baseline_gate, 0)
    )
    if gate_regressed:
        return "no_effect", ("HARD_GATE_REGRESSION",)
    if scenario_gate == "failed":
        return "hard_blocked", ("HARD_GATE_FAILED",)
    if minimal_status == "reached":
        return "reached", ("TARGET_REACHED",)
    if result.scenario_recommendation == "insufficient_information":
        return "insufficient_information", ("INSUFFICIENT_INFORMATION",)

    reasons: list[str] = []
    before_rec = result.baseline_recommendation or ""
    after_rec = result.scenario_recommendation or ""
    if (
        _OUTCOME_RECOMMENDATION_RANK.get(after_rec, 0)
        > _OUTCOME_RECOMMENDATION_RANK.get(before_rec, 0)
    ):
        reasons.append("RECOMMENDATION_IMPROVED")
    delta = result.score_delta if result.score_delta is not None else 0.0
    if delta >= meaningful_score_delta:
        reasons.append("MEANINGFUL_SCORE_DELTA")

    baseline = result.baseline_evaluation
    scenario = result.scenario_evaluation
    if baseline is not None and scenario is not None:
        before_by_id = {
            item.requirement_id: item for item in baseline.skill_results
        }
        after_by_id = {
            item.requirement_id: item for item in scenario.skill_results
        }
        key_resolved = any(
            after_item.match_status == "matched"
            and after_item.evidence_sufficient
            and (before := before_by_id.get(requirement_id)) is not None
            and before.match_status
            in {"missing", "weak", "declared_only", "unknown", "unresolved"}
            for requirement_id, after_item in after_by_id.items()
        )
        if key_resolved:
            reasons.append("KEY_REQUIREMENT_GAP_RESOLVED")
        if (
            baseline.information_sufficient is False
            and scenario.information_sufficient is True
        ):
            reasons.append("INFORMATION_SUFFICIENCY_IMPROVED")

    if reasons:
        return "partial_effective", tuple(dict.fromkeys(reasons))
    return "no_effect", ("NO_MEANINGFUL_EFFECT",)


def as_projected_actions(
    actions: tuple[WhatIfAction, ...],
) -> tuple[WhatIfAction, ...]:
    """Completed/verified copies of actions for the projected-if-completed lens.

    The originals stay planned; the copies are only used in an in-memory
    hypothetical scenario and never persist as CV Evidence.
    """

    output: list[WhatIfAction] = []
    for action in actions:
        updates: dict[str, object] = {}
        if action.action_type in {"add_skill", "add_project_experience"}:
            updates["milestone_status"] = "verified"
        if action.action_type == "controlled_skill_transfer":
            updates["score_credit_allowed"] = True
        if updates:
            output.append(action.model_copy(update=updates))
        else:
            output.append(action)
    return tuple(output)


def classify_projected_what_if_outcome(
    result: WhatIfResult,
    minimal_status: str,
    *,
    meaningful_score_delta: float = 5.0,
) -> tuple[str, tuple[str, ...]]:
    """Classify the projected-if-completed lens of a What-if result."""

    if result.projected_score is None:
        return "no_effect", ("PROJECTED_SCORE_UNAVAILABLE",)
    projected = result.model_copy(
        update={
            "generation_status": result.generation_status,
            "baseline_score": result.baseline_score,
            "scenario_score": result.projected_score,
            "score_delta": result.projected_score_delta,
            "baseline_recommendation": result.baseline_recommendation,
            "scenario_recommendation": result.projected_recommendation,
            "baseline_hard_gate_status": result.baseline_hard_gate_status,
            "scenario_hard_gate_status": result.projected_hard_gate_status,
        }
    )
    return classify_what_if_outcome(
        projected,
        minimal_status,
        meaningful_score_delta=meaningful_score_delta,
    )
