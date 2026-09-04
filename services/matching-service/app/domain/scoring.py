"""Pure, configurable and explainable multi-dimensional scoring."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.evaluation import (
    DimensionScore,
    DimensionStatus,
    FinalMatchResult,
    MatchEvaluation,
    RequirementCapShare,
    ScoreContribution,
    ScoreDimension,
    ScoreInsight,
    TwoLevelNormalization,
)
from app.domain.profiles import CVMatchProfile, Evidence, PositionMatchProfile
from app.domain.requirement_graph import (
    SPECIALTY_ROUTE_GROUP_PREFIX,
    SPECIALTY_ROUTE_ROOT_PREFIX,
)


@dataclass(frozen=True)
class RequirementContribution:
    """One requirement-level term of the formal score."""

    requirement_id: str
    dimension: ScoreDimension
    canonical_feature: str
    canonical_feature_id: str
    status: str
    match_type: str | None
    reason_code: str
    match_value: float | None
    weight: float
    weighted_points: float
    confidence: float
    position_evidence: tuple[Evidence, ...]
    candidate_evidence: tuple[Evidence, ...]
    relation_evidence: tuple[Evidence, ...] = ()
    required_level: str | None = None
    current_level: str | None = None


@dataclass(frozen=True)
class ContributionLedger:
    """Deterministic requirement-level breakdown of the formal overall score."""

    algorithm_version: str
    scoring_config_version: str
    overall_score: float | None
    requirement_contributions: tuple[RequirementContribution, ...]
    two_level_normalization: TwoLevelNormalization | None = None

    def weighted_points_sum(self) -> float:
        return round(
            sum(
                item.weighted_points
                for item in self.requirement_contributions
            ),
            6,
        )


def build_contribution_ledger(
    evaluation: MatchEvaluation,
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: ScoringConfig | None = None,
) -> ContributionLedger:
    """Expose the formal score as a requirement-level contribution ledger.

    The ledger uses exactly the same item scores, dimension normalization and
    effective weights as ``score_match_evaluation``, so the sum of the ledger
    weighted points equals the formal overall score.
    """

    scoring = config or ScoringConfig()
    final = score_match_evaluation(evaluation, cv, position, scoring)
    contributions = _requirement_contributions(
        evaluation, final.score_contributions
    )
    return ContributionLedger(
        algorithm_version=final.algorithm_version,
        scoring_config_version=final.scoring_config_version,
        overall_score=final.overall_score,
        requirement_contributions=tuple(contributions),
        two_level_normalization=final.two_level_normalization,
    )


def _requirement_contributions(
    evaluation: MatchEvaluation,
    score_contributions: Iterable[ScoreContribution],
) -> list[RequirementContribution]:
    canonical_by_result_id = _canonical_by_result_id(evaluation)
    level_by_result_id = {
        item.requirement_id: (
            item.required_level,
            item.candidate_declared_level
            or (
                item.candidate_demonstrated_level
                if item.candidate_demonstrated_level is not None
                else None
            ),
        )
        for item in evaluation.skill_results
    }
    output: list[RequirementContribution] = []
    for contribution in score_contributions:
        feature_id, feature_name = canonical_by_result_id.get(
            contribution.result_id, (contribution.result_id, contribution.result_id)
        )
        required_level, current_level = level_by_result_id.get(
            contribution.result_id, (None, None)
        )
        output.append(
            RequirementContribution(
                requirement_id=contribution.result_id,
                dimension=contribution.dimension,
                canonical_feature=feature_name,
                canonical_feature_id=feature_id,
                status=contribution.status,
                match_type=contribution.match_type,
                reason_code=contribution.reason_code,
                match_value=contribution.score_value,
                weight=contribution.effective_weight,
                weighted_points=contribution.weighted_points,
                confidence=contribution.confidence,
                position_evidence=contribution.position_evidence,
                candidate_evidence=contribution.candidate_evidence,
                relation_evidence=contribution.relation_evidence,
                required_level=required_level,
                current_level=current_level,
            )
        )
    return output


def _canonical_by_result_id(
    evaluation: MatchEvaluation,
) -> dict[str, tuple[str, str]]:
    canonical: dict[str, tuple[str, str]] = {}
    for item in evaluation.skill_results:
        canonical[item.requirement_id] = (
            item.skill_id or item.requirement_id,
            item.skill_name or item.requirement_id,
        )
    for item in evaluation.hard_constraint_results:
        canonical[item.requirement_id] = (
            f"{item.constraint_type}:{item.required_value or ''}",
            item.required_value or item.constraint_type,
        )
    for item in evaluation.responsibility_results:
        canonical[item.requirement_id] = (
            item.requirement_id,
            item.position_requirement,
        )
    for item in evaluation.project_results:
        canonical[item.requirement_id] = (
            item.requirement_id,
            " ".join(item.position_requirement),
        )
    for item in evaluation.scenario_results:
        canonical[item.requirement_id] = (
            item.requirement_id,
            item.position_requirement,
        )
    for item in evaluation.requirement_group_results:
        canonical[item.group_id] = (item.group_id, item.group_type)
    return canonical


@dataclass(frozen=True)
class ScoringWeights:
    required_skills: float = 0.35
    responsibilities: float = 0.20
    # The `projects` weight nominates the *Applied Experience* (综合实践证据)
    # dimension: whether the candidate actually used the abilities required by
    # the position in project / internship / work contexts.  It is deliberately
    # NOT a fixed "project-experience requirement" - the wire/DB key stays
    # `projects` for backward compatibility with frozen artifacts and the BFF.
    projects: float = 0.15
    capability_level: float = 0.10
    hard_conditions: float = 0.10
    business_scenarios: float = 0.05
    bonus_transferable: float = 0.05
    requirement_groups: float = 0.10

    def __post_init__(self) -> None:
        values = tuple(self.as_dict().values())
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("scoring weights must be between 0 and 1")
        # The seven v1 dimensions retain a unit budget. Requirement Graph is an
        # optional v2 dimension and joins normalization only when a graph exists.
        if abs(sum(values[:-1]) - 1.0) > 1e-9:
            raise ValueError("base scoring weights must sum to 1.0")

    def as_dict(self) -> dict[ScoreDimension, float]:
        return {
            "required_skills": self.required_skills,
            "responsibilities": self.responsibilities,
            "projects": self.projects,
            "capability_level": self.capability_level,
            "hard_conditions": self.hard_conditions,
            "business_scenarios": self.business_scenarios,
            "bonus_transferable": self.bonus_transferable,
            "requirement_groups": self.requirement_groups,
        }


@dataclass(frozen=True)
class ScoringConfig:
    algorithm_version: str = "explainable-scoring.v4"
    scoring_config_version: str = "scoring-config.v3"
    weights: ScoringWeights = ScoringWeights()
    strong_match_threshold: float = 80.0
    potential_match_threshold: float = 60.0
    weak_match_threshold: float = 40.0
    minimum_recommendation_confidence: float = 0.50
    coverage_recommendation_mapping: bool = False
    strong_coverage: float = 0.55
    potential_coverage: float = 0.60
    weak_coverage: float = 0.35
    coverage_minimum_confidence: float = 0.20
    # When an evaluation is marked material-information-uncertain, a strong
    # recommendation should still require at least one fully verified direct
    # responsibility evidence path.  This is a structural evidence floor, not
    # a score threshold.
    strong_evidence_floor: bool = True
    material_uncertainty_confidence_factor: float = 0.80
    hard_partial_score: float = 0.50
    skill_weak_score: float = 0.50
    skill_declared_only_score: float = 0.20
    equivalent_skill_score: float = 0.90
    parent_child_score: float = 0.55
    related_skill_score: float = 0.35
    transferable_skill_score: float = 0.60
    prerequisite_score: float = 0.0
    deterministic_context_partial_score: float = 0.50
    semantic_context_partial_score: float = 0.40
    strength_threshold: float = 0.75
    gap_threshold: float = 0.25
    two_level_requirement_normalization: bool = False
    max_requirement_share: float = 0.40
    capability_levels: tuple[str, ...] = (
        "unknown",
        "basic",
        "working",
        "proficient",
        "advanced",
        "expert",
    )

    def __post_init__(self) -> None:
        unit_values = (
            self.minimum_recommendation_confidence,
            self.material_uncertainty_confidence_factor,
            self.hard_partial_score,
            self.skill_weak_score,
            self.skill_declared_only_score,
            self.equivalent_skill_score,
            self.parent_child_score,
            self.related_skill_score,
            self.transferable_skill_score,
            self.prerequisite_score,
            self.deterministic_context_partial_score,
            self.semantic_context_partial_score,
            self.strength_threshold,
            self.gap_threshold,
        )
        if any(value < 0 or value > 1 for value in unit_values):
            raise ValueError("scoring factors must be between 0 and 1")
        if not (
            0
            <= self.weak_match_threshold
            <= self.potential_match_threshold
            <= self.strong_match_threshold
            <= 100
        ):
            raise ValueError("recommendation thresholds must be ordered within 0..100")
        if len(set(self.capability_levels)) != len(self.capability_levels):
            raise ValueError("capability level order must not contain duplicates")
        if not isinstance(self.two_level_requirement_normalization, bool):
            raise ValueError("two_level_requirement_normalization must be boolean")
        if (
            isinstance(self.max_requirement_share, bool)
            or not isinstance(self.max_requirement_share, int | float)
            or not 0 < self.max_requirement_share <= 1
        ):
            raise ValueError("max_requirement_share must be between 0 and 1")
        coverage_values = (
            self.coverage_minimum_confidence,
            self.weak_coverage,
            self.potential_coverage,
            self.strong_coverage,
        )
        if any(value < 0 or value > 1 for value in coverage_values):
            raise ValueError("coverage recommendation thresholds must be within 0..1")


@dataclass(frozen=True)
class _ScoreItem:
    dimension: ScoreDimension
    result_id: str
    status: str
    match_type: str | None
    reason_code: str
    score: float | None
    confidence: float
    position_evidence: tuple[Evidence, ...]
    candidate_evidence: tuple[Evidence, ...]
    relation_evidence: tuple[Evidence, ...] = ()


_DIMENSION_ORDER: tuple[ScoreDimension, ...] = (
    "required_skills",
    "responsibilities",
    "projects",
    "capability_level",
    "hard_conditions",
    "business_scenarios",
    "bonus_transferable",
    "requirement_groups",
)
_UNCERTAIN = frozenset({"unknown", "unresolved"})


def _skill_score(item, config: ScoringConfig) -> float | None:
    if item.match_status in _UNCERTAIN:
        return None
    if item.match_status == "missing":
        return 0.0
    if item.match_status == "weak":
        return config.skill_weak_score
    if item.match_status == "declared_only":
        return config.skill_declared_only_score
    if item.match_status == "matched" and item.match_type == "semantic_text":
        # Semantic text evidence proves presence/partial support only; it must
        # never be scored as full exact proficiency.
        return config.skill_weak_score
    if (
        item.match_status == "matched"
        and item.match_type == "exact"
        and item.proficiency_satisfied is False
    ):
        return config.skill_weak_score
    relation_scores = {
        "equivalent": config.equivalent_skill_score,
        "parent_child": config.parent_child_score,
        "related": 0.0,
        "transferable": config.transferable_skill_score,
        "prerequisite": config.prerequisite_score,
    }
    if item.match_type == "exact":
        return 1.0
    if item.match_type in relation_scores:
        score = relation_scores[item.match_type]
        if item.match_type == "transferable" and item.relation_confidence is not None:
            score *= item.relation_confidence
        return score
    return None


_OWNERSHIP_RANK = {
    "unknown": 0,
    "declared": 0,
    "used": 1,
    "participated": 1,
    "implemented": 2,
    "owned": 3,
    "designed": 4,
    "led": 5,
}


def _ownership_factor(item) -> float:
    target = _OWNERSHIP_RANK.get((item.required_ownership or "").casefold())
    if target is None or target == 0:
        return 1.0
    current = _OWNERSHIP_RANK.get((item.candidate_ownership or "unknown").casefold(), 0)
    if current >= target:
        return 1.0
    return (current + 1) / (target + 1)


def _capability_score(item, config: ScoringConfig) -> float | None:
    if item.match_status in _UNCERTAIN:
        return None
    if item.match_type == "semantic_text":
        return config.skill_weak_score * _ownership_factor(item)
    if item.match_status == "matched":
        base = 1.0 if item.match_type == "exact" else _skill_score(item, config)
        if (
            item.match_type == "exact"
            and item.proficiency_satisfied is False
            and item.required_level in config.capability_levels
            and item.candidate_demonstrated_level in config.capability_levels
        ):
            required = config.capability_levels.index(item.required_level)
            candidate = config.capability_levels.index(item.candidate_demonstrated_level)
            base = candidate / required if required > 0 else config.skill_weak_score
        return base * _ownership_factor(item) if base is not None else None
    if item.match_status == "weak":
        if (
            item.required_level in config.capability_levels
            and item.candidate_demonstrated_level in config.capability_levels
        ):
            required = config.capability_levels.index(item.required_level)
            candidate = config.capability_levels.index(item.candidate_demonstrated_level)
            base = candidate / required if required > 0 else config.skill_weak_score
            return base * _ownership_factor(item)
        return config.skill_weak_score * _ownership_factor(item)
    if item.match_status == "declared_only":
        return config.skill_declared_only_score * _ownership_factor(item)
    if item.match_status == "partial":
        base = _skill_score(item, config)
        return base * _ownership_factor(item) if base is not None else None
    return 0.0


def _context_score(item, config: ScoringConfig) -> float | None:
    if item.match_type == "semantic_candidate":
        return None
    if item.match_status in _UNCERTAIN:
        return None
    if getattr(item, "status_detail", None) in {"uncertain", "insufficient_evidence"}:
        return None
    if item.match_status == "matched":
        return 1.0
    if item.match_status == "partial":
        return (
            config.semantic_context_partial_score
            if item.match_type == "semantic"
            else config.deterministic_context_partial_score
        )
    return 0.0


def _responsibility_score(item, config: ScoringConfig) -> float | None:
    """Score the final ResponsibilityDecisionPolicy state when available.

    The frozen Cross-Encoder only verifies a binary semantic question. The
    policy then produces a product-facing final state in ``status_detail``
    (``matched`` / ``partial`` / ``insufficient_evidence`` /
    ``not_observed``). That final state, not the legacy CE ``match_status``,
    is authoritative for the responsibility dimension.

    Rule-mode results have no ``status_detail`` and continue to use the
    existing deterministic context scoring. Raw CE logits and retrieval
    similarities are deliberately never converted into an overall score.
    """

    detail = getattr(item, "status_detail", None)
    if detail is None:
        return _context_score(item, config)
    if detail in {"uncertain", "insufficient_evidence"}:
        return None
    if detail == "matched":
        return 1.0
    if detail == "partial":
        # Reuse the existing validated deterministic partial factor. Do not
        # invent a new numeric constant in this compatibility fix.
        return config.deterministic_context_partial_score
    if detail == "not_observed":
        return 0.0
    return _context_score(item, config)


def _responsibility_final_status(item) -> str:
    return getattr(item, "status_detail", None) or item.match_status


def _project_score(item, config: ScoringConfig) -> float | None:
    """Score measured Applied Experience (综合实践证据) overlap.

    The underlying result list keeps the wire name ``projects`` but expresses
    whether the candidate demonstrably applied the required abilities in
    project / internship / work contexts, rather than a fixed project-experience
    requirement of the position.
    """
    if item.match_status == "partial" and item.match_type != "semantic":
        return item.confidence
    return _context_score(item, config)


def _graph_coverage(evaluation: MatchEvaluation) -> dict[ScoreDimension, set[str]]:
    """Map root-graph leaves to the flat dimensions they replace."""
    coverage: dict[ScoreDimension, set[str]] = {}
    for group in evaluation.requirement_group_results:
        if not group.is_root:
            continue
        for dimension in group.covered_dimensions:
            coverage.setdefault(dimension, set()).update(group.covered_result_ids)
    return coverage


def _specialty_route_ids(
    evaluation: MatchEvaluation,
) -> tuple[set[str], set[str], str | None]:
    roots = tuple(
        item
        for item in evaluation.requirement_group_results
        if item.is_root and item.group_id.startswith(SPECIALTY_ROUTE_ROOT_PREFIX)
    )
    if len(roots) != 1:
        return set(), set(), None
    route_ids: set[str] = set()
    for item in evaluation.requirement_group_results:
        if not item.is_root and item.group_id.startswith(SPECIALTY_ROUTE_GROUP_PREFIX):
            route_ids.update(item.covered_result_ids)
    return set(roots[0].covered_result_ids), route_ids, roots[0].group_id


def _items(evaluation: MatchEvaluation, config: ScoringConfig) -> tuple[_ScoreItem, ...]:
    output: list[_ScoreItem] = []
    graph_coverage = _graph_coverage(evaluation)
    active_route_ids, all_route_ids, route_root_id = _specialty_route_ids(evaluation)
    specialty_route = route_root_id is not None
    for item in evaluation.hard_constraint_results:
        if item.status == "not_required":
            continue
        if item.requirement_id in graph_coverage.get("hard_conditions", set()):
            continue
        score = None if item.status in _UNCERTAIN else {
            "pass": 1.0,
            "partial": config.hard_partial_score,
            "fail": 0.0,
        }[item.status]
        output.append(
            _ScoreItem(
                "hard_conditions",
                item.requirement_id,
                item.status,
                "deterministic",
                item.reason_code,
                score,
                item.confidence,
                item.position_evidence,
                item.candidate_evidence,
            )
        )
    for item in evaluation.skill_results:
        dimension: ScoreDimension = (
            "required_skills"
            if item.importance_level == "required"
            else "bonus_transferable"
        )
        if specialty_route and item.requirement_id in all_route_ids and (
            item.requirement_id not in active_route_ids
        ):
            continue
        if item.requirement_id not in graph_coverage.get(dimension, set()):
            output.append(
                _ScoreItem(
                    dimension,
                    item.requirement_id,
                    item.match_status,
                    item.match_type,
                    item.reason_code,
                    _skill_score(item, config),
                    item.confidence,
                    item.position_evidence,
                    item.candidate_evidence,
                    item.relation_evidence,
                )
            )
        if item.importance_level == "required" and not (
            specialty_route and item.requirement_id in active_route_ids
        ):
            ownership_below = _ownership_factor(item) < 1.0
            output.append(
                _ScoreItem(
                    "capability_level",
                    item.requirement_id,
                    item.match_status,
                    item.match_type,
                    "SKILL_OWNERSHIP_BELOW_REQUIRED" if ownership_below else item.reason_code,
                    _capability_score(item, config),
                    item.confidence,
                    item.position_evidence,
                    item.candidate_evidence,
                    item.relation_evidence,
                )
            )
    if specialty_route and active_route_ids and route_root_id is not None:
        active_skill_results = tuple(
            item
            for item in evaluation.skill_results
            if item.importance_level == "required"
            and item.requirement_id in active_route_ids
        )
        capability_scores = tuple(
            _capability_score(item, config) for item in active_skill_results
        )
        scored_capabilities = tuple(
            score for score in capability_scores if score is not None
        )
        capability_score = (
            sum(scored_capabilities) / len(scored_capabilities)
            if scored_capabilities
            else None
        )
        capability_status = (
            "unknown"
            if capability_score is None
            else "matched"
            if capability_score >= 1.0
            else "partial"
            if capability_score > 0.0
            else "missing"
        )
        output.append(
            _ScoreItem(
                "capability_level",
                f"{route_root_id}:capability_level",
                capability_status,
                "deterministic",
                "SPECIALTY_ROUTE_CAPABILITY_AGGREGATE",
                capability_score,
                (
                    sum(item.confidence for item in active_skill_results)
                    / len(active_skill_results)
                    if active_skill_results
                    else 0.0
                ),
                _dedupe_evidence(
                    *(item.position_evidence for item in active_skill_results)
                ),
                _dedupe_evidence(
                    *(item.candidate_evidence for item in active_skill_results)
                ),
            )
        )
    for dimension, results in (
        ("responsibilities", evaluation.responsibility_results),
        ("projects", evaluation.project_results),
    ):
        for item in results:
            if item.requirement_id in graph_coverage.get(dimension, set()):
                continue
            output.append(
                _ScoreItem(
                    dimension,
                    item.requirement_id,
                    _responsibility_final_status(item),
                    item.match_type,
                    item.reason_code,
                    (
                        _project_score(item, config)
                        if dimension == "projects"
                        else _responsibility_score(item, config)
                    ),
                    item.confidence,
                    item.position_evidence,
                    item.candidate_evidence,
                )
            )
    for item in evaluation.scenario_results:
        if item.scenario_type != "business_scenario":
            continue
        if item.requirement_id in graph_coverage.get("business_scenarios", set()):
            continue
        output.append(
            _ScoreItem(
                "business_scenarios",
                item.requirement_id,
                item.match_status,
                item.match_type,
                item.reason_code,
                _context_score(item, config),
                item.confidence,
                item.position_evidence,
                item.candidate_evidence,
            )
        )
    for item in evaluation.requirement_group_results:
        if not item.is_root:
            continue
        group_dimension: ScoreDimension = (
            item.covered_dimensions[0]
            if len(item.covered_dimensions) == 1
            else "requirement_groups"
        )
        output.append(
            _ScoreItem(
                group_dimension,
                item.group_id,
                item.status,
                "deterministic",
                item.reason_code,
                item.score,
                item.confidence,
                item.position_evidence,
                (),
            )
        )
    return tuple(output)


def _dedupe_evidence(*groups: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    unique = {
        (item.source_id, item.start, item.end, item.quote): item
        for group in groups
        for item in group
    }
    return tuple(unique[key] for key in sorted(unique, key=str))


_TWO_LEVEL_NORMALIZATION_VERSION = "requirement-normalization.two-level.v2"


def _two_level_item_weights(
    items: Iterable[_ScoreItem],
    effective_weights: dict[str, float],
    target_scored_mass: float,
    max_requirement_share: float,
) -> tuple[list[float], TwoLevelNormalization]:
    """Cap each requirement's total share without a second global re-scale.

    Level one is the existing dimension normalization (``effective_weights``).
    Level two caps the total weight emitted by one requirement and re-distributes
    the capped surplus only to requirements that still have headroom.

    By construction a requirement is *never* amplified again to refill total
    mass: the final per-requirement weight stays at or below
    ``max_requirement_share * target_scored_mass``.  Mass that cannot be
    allocated because the cap ceiling binds is returned as explicit residual
    mass (``allocated_mass + residual_mass == target_scored_mass``) instead of
    being silently re-inflated past the cap.
    """

    item_list = list(items)
    scored_indices = [index for index, item in enumerate(item_list) if item.score is not None]
    weights = [0.0] * len(item_list)
    normalization = TwoLevelNormalization(
        version=_TWO_LEVEL_NORMALIZATION_VERSION,
        active=True,
        max_requirement_share=max_requirement_share,
        target_scored_mass=round(target_scored_mass, 6),
        allocated_mass=0.0,
        residual_mass=round(target_scored_mass, 6),
        cap_satisfied=True,
        capped_requirement_count=0,
    )
    if target_scored_mass <= 0 or not scored_indices:
        return weights, normalization
    for index in scored_indices:
        item = item_list[index]
        scored_count = sum(
            other.score is not None
            for other in item_list
            if other.dimension == item.dimension
        )
        weights[index] = (
            effective_weights[item.dimension] / scored_count
            if scored_count
            else 0.0
        )
    cap = max_requirement_share * target_scored_mass
    requirement_total: dict[str, float] = {}
    for index in scored_indices:
        requirement_total[item_list[index].result_id] = (
            requirement_total.get(item_list[index].result_id, 0.0)
            + weights[index]
        )

    # Level two: cap each requirement, then re-distribute the cut surplus to
    # requirements that still have headroom.  Surplus that cannot be placed
    # (because every remaining requirement already sits at the cap) stays as
    # explicit residual mass - it is never re-amplified onto a capped item.
    requirement_total = {
        requirement_id: min(total, cap)
        for requirement_id, total in requirement_total.items()
    }
    for _round in range(len(requirement_total) + 1):
        allocated = sum(requirement_total.values())
        surplus = target_scored_mass - allocated
        if surplus <= 1e-9:
            break
        headroom = {
            requirement_id: cap - total
            for requirement_id, total in requirement_total.items()
            if total < cap
        }
        headroom_total = sum(headroom.values())
        if headroom_total <= 1e-9:
            break
        for requirement_id, room in headroom.items():
            requirement_total[requirement_id] += surplus * room / headroom_total
        requirement_total = {
            requirement_id: min(total, cap)
            for requirement_id, total in requirement_total.items()
        }

    # Scale per-requirement item weights to their capped totals.  The original
    # per-requirement mass must be computed from the *unscaled* weights first so
    # that a requirement spanning multiple dimensions (e.g. a required skill
    # that also emits a capability_level item) is scaled exactly once.
    original_by_requirement: dict[str, float] = {}
    for index in scored_indices:
        result_id = item_list[index].result_id
        original_by_requirement[result_id] = (
            original_by_requirement.get(result_id, 0.0) + weights[index]
        )
    for index in scored_indices:
        item = item_list[index]
        requirement_weight = requirement_total.get(item.result_id, 0.0)
        original_total = original_by_requirement[item.result_id]
        if original_total > 1e-9:
            weights[index] *= requirement_weight / original_total

    allocated = sum(requirement_total.values())
    residual = target_scored_mass - allocated
    shares = tuple(
        RequirementCapShare(
            requirement_id=requirement_id,
            dimension=next(
                item_list[index].dimension
                for index in scored_indices
                if item_list[index].result_id == requirement_id
            ),
            allocated_weight=round(total, 6),
            capped=total >= cap - 1e-9,
        )
        for requirement_id, total in requirement_total.items()
    )
    normalization = TwoLevelNormalization(
        version=_TWO_LEVEL_NORMALIZATION_VERSION,
        active=True,
        max_requirement_share=max_requirement_share,
        target_scored_mass=round(target_scored_mass, 6),
        allocated_mass=round(allocated, 6),
        residual_mass=round(residual, 6),
        cap_satisfied=all(total <= cap + 1e-9 for total in requirement_total.values()),
        capped_requirement_count=sum(total >= cap - 1e-9 for total in requirement_total.values()),
        requirement_shares=shares,
    )
    return weights, normalization


def _insight(item: _ScoreItem, kind: str) -> ScoreInsight:
    return ScoreInsight(
        dimension=item.dimension,
        result_id=item.result_id,
        reason_code=item.reason_code,
        message=f"{kind}:{item.dimension}:{item.status}:{item.reason_code}",
        evidence=_dedupe_evidence(
            item.position_evidence, item.candidate_evidence, item.relation_evidence
        ),
    )


def _strong_evidence_sufficient(
    evaluation: MatchEvaluation,
    config: ScoringConfig,
) -> bool:
    """Apply the structural strong-evidence floor.

    Material information uncertainty is already a reason to be cautious about
    strong claims.  A strong recommendation is additionally blocked unless at
    least one responsibility result is fully verified as ``matched``.  This
    prevents skills-only strong conclusions when direct responsibility evidence
    is missing or only partially supported.
    """
    if not config.strong_evidence_floor:
        return True
    if evaluation.information_sufficiency_level != "material":
        return True
    return any(
        item.match_status == "matched"
        and getattr(item, "status_detail", None) == "matched"
        for item in evaluation.responsibility_results
    )


def _recommendation(
    score: float | None,
    confidence: float,
    hard_gate_status: str,
    config: ScoringConfig,
    *,
    strong_conclusion_allowed: bool,
    strong_evidence_sufficient: bool,
    coverage: float | None = None,
    requirement_coverage: float | None = None,
    responsibility_coverage: float | None = None,
) -> str:
    if hard_gate_status == "failed":
        return "not_recommended"
    if config.coverage_recommendation_mapping and coverage is not None:
        if confidence < config.coverage_minimum_confidence:
            return "insufficient_information"
        if (
            coverage >= config.strong_coverage
            and (requirement_coverage is None or requirement_coverage >= 0.80)
            and hard_gate_status in {"passed", "not_applicable"}
            and strong_evidence_sufficient
        ):
            return "strong_match"
        if (
            coverage >= config.potential_coverage
            and (requirement_coverage is None or requirement_coverage >= 0.60)
        ):
            return "potential_match"
        if (
            coverage >= config.weak_coverage
            and (requirement_coverage is None or requirement_coverage >= 0.40)
        ):
            return "weak_match"
        return "not_recommended"
    if score is None or confidence < config.minimum_recommendation_confidence:
        return "insufficient_information"
    if (
        score >= config.strong_match_threshold
        and strong_conclusion_allowed
        and strong_evidence_sufficient
        and hard_gate_status in {"passed", "not_applicable"}
    ):
        return "strong_match"
    if score >= config.potential_match_threshold:
        return "potential_match"
    if score >= config.weak_match_threshold:
        return "weak_match"
    return "not_recommended"


def expected_dimensions(position: PositionMatchProfile) -> frozenset[ScoreDimension]:
    """Dimensions the position independently declares, regardless of evaluation.

    Derived from the formal PositionMatchProfile (and its optional Requirement
    Graph) so that a dimension whose result list is entirely missing can be
    told apart from a dimension the position never had.

    The ``projects`` dimension is treated as the *Applied Experience*
    (综合实践证据) evidence channel.  It is declared whenever the position asks
    for required skills or core responsibilities, because the scorer evaluates
    whether the candidate *actually used* those abilities in project / internship
    / work contexts.  This is NOT a claim that the JD declares an explicit
    ``project_experience_required`` requirement; the wire key is intentionally
    kept as ``projects`` for compatibility with frozen artifacts and the BFF.
    """

    expected: set[ScoreDimension] = set()
    if position.required_skills:
        expected.add("required_skills")
        expected.add("capability_level")
    if position.preferred_skills:
        expected.add("bonus_transferable")
    if position.core_responsibilities:
        expected.add("responsibilities")
    if position.required_skills or position.core_responsibilities:
        expected.add("projects")
    if position.hard_conditions:
        expected.add("hard_conditions")
    if position.business_scenarios.values:
        expected.add("business_scenarios")
    if position.requirement_graph is not None and position.requirement_graph.groups:
        expected.add("requirement_groups")
    return frozenset(expected)


def produced_dimensions(evaluation: MatchEvaluation) -> frozenset[ScoreDimension]:
    """Dimensions that actually produced at least one result item."""

    produced: set[ScoreDimension] = set()
    if any(item.status != "not_required" for item in evaluation.hard_constraint_results):
        produced.add("hard_conditions")
    for item in evaluation.skill_results:
        produced.add(
            "required_skills" if item.importance_level == "required" else "bonus_transferable"
        )
        if item.importance_level == "required":
            produced.add("capability_level")
    if evaluation.responsibility_results:
        produced.add("responsibilities")
    if evaluation.project_results:
        produced.add("projects")
    for item in evaluation.scenario_results:
        if item.scenario_type == "business_scenario":
            produced.add("business_scenarios")
    for item in evaluation.requirement_group_results:
        if item.is_root:
            produced.add("requirement_groups")
    return frozenset(produced)


def score_match_evaluation(
    evaluation: MatchEvaluation,
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    config: ScoringConfig | None = None,
) -> FinalMatchResult:
    """Fuse existing results without mutating or suppressing their analysis."""
    scoring = config or ScoringConfig()
    weights = scoring.weights.as_dict()
    items = _items(evaluation, scoring)
    expected = expected_dimensions(position)
    produced = produced_dimensions(evaluation)
    missing = expected - produced
    grouped = {
        dimension: tuple(item for item in items if item.dimension == dimension)
        for dimension in _DIMENSION_ORDER
    }
    raw_scores: dict[ScoreDimension, float | None] = {}
    confidences: dict[ScoreDimension, float] = {}
    for dimension, dimension_items in grouped.items():
        scored = tuple(item for item in dimension_items if item.score is not None)
        raw_scores[dimension] = (
            sum(item.score for item in scored if item.score is not None) / len(scored)
            if scored
            else None
        )
        confidences[dimension] = (
            sum(item.confidence for item in dimension_items if item.score is not None)
            / len(dimension_items)
            if dimension_items
            else 1.0
        )
    available_weight = sum(
        weights[dimension]
        for dimension in _DIMENSION_ORDER
        if raw_scores[dimension] is not None
    )
    reserved_weight = sum(
        weights[dimension] for dimension in _DIMENSION_ORDER if dimension in missing
    )
    weight_denominator = available_weight + reserved_weight
    effective_weights = {
        dimension: (
            weights[dimension] / weight_denominator
            if (raw_scores[dimension] is not None or dimension in missing)
            and weight_denominator
            else 0.0
        )
        for dimension in _DIMENSION_ORDER
    }
    overall = (
        100
        * sum(
            raw_scores[dimension] * effective_weights[dimension]
            for dimension in _DIMENSION_ORDER
            if raw_scores[dimension] is not None
        )
        if available_weight
        else None
    )
    applicable_weight = sum(
        weights[dimension] for dimension in _DIMENSION_ORDER if grouped[dimension]
    )
    match_confidence = (
        sum(
            weights[dimension] * confidences[dimension]
            for dimension in _DIMENSION_ORDER
            if grouped[dimension]
        )
        / applicable_weight
        if applicable_weight
        else 0.0
    )
    def _dimension_status(dimension: ScoreDimension) -> DimensionStatus:
        if dimension in missing:
            return "missing_evaluation"
        if dimension not in expected and dimension not in produced:
            return "not_applicable"
        if raw_scores[dimension] is None:
            return "uncertain"
        return "scored"

    dimension_scores = tuple(
        DimensionScore(
            dimension=dimension,
            score=(
                round(raw_scores[dimension] * 100, 4)
                if raw_scores[dimension] is not None
                else None
            ),
            confidence=(
                0.0
                if dimension in missing
                else round(confidences[dimension], 6)
            ),
            configured_weight=weights[dimension],
            effective_weight=round(effective_weights[dimension], 6),
            applicable_count=len(grouped[dimension]),
            scored_count=sum(item.score is not None for item in grouped[dimension]),
            uncertain_count=sum(item.score is None for item in grouped[dimension]),
            dimension_status=_dimension_status(dimension),
        )
        for dimension in _DIMENSION_ORDER
    )
    contributions = []
    for item in items:
        scored_count = sum(other.score is not None for other in grouped[item.dimension])
        item_weight = (
            effective_weights[item.dimension] / scored_count
            if item.score is not None and scored_count
            else 0.0
        )
        contributions.append(
            ScoreContribution(
                dimension=item.dimension,
                result_id=item.result_id,
                status=item.status,
                match_type=item.match_type,
                reason_code=item.reason_code,
                score_value=item.score,
                effective_weight=round(item_weight, 6),
                weighted_points=round((item.score or 0.0) * item_weight * 100, 6),
                confidence=item.confidence,
                position_evidence=item.position_evidence,
                candidate_evidence=item.candidate_evidence,
                relation_evidence=item.relation_evidence,
            )
        )
    two_level_normalization: TwoLevelNormalization | None = None
    if scoring.two_level_requirement_normalization:
        scored_weight_mass = sum(
            effective_weights[dimension]
            for dimension in _DIMENSION_ORDER
            if raw_scores[dimension] is not None
        )
        item_weights, two_level_normalization = _two_level_item_weights(
            items,
            effective_weights,
            scored_weight_mass,
            scoring.max_requirement_share,
        )
        contributions = []
        for item, item_weight in zip(items, item_weights, strict=True):
            contributions.append(
                ScoreContribution(
                    dimension=item.dimension,
                    result_id=item.result_id,
                    status=item.status,
                    match_type=item.match_type,
                    reason_code=item.reason_code,
                    score_value=item.score,
                    effective_weight=round(item_weight, 6),
                    weighted_points=round((item.score or 0.0) * item_weight * 100, 6),
                    confidence=item.confidence,
                    position_evidence=item.position_evidence,
                    candidate_evidence=item.candidate_evidence,
                    relation_evidence=item.relation_evidence,
                )
            )
        overall = (
            sum(
                (item.score or 0.0) * item_weight
                for item, item_weight in zip(items, item_weights, strict=True)
                if item.score is not None
            )
            * 100
            if scored_weight_mass > 0
            else None
        )
    # Requirement Graph groups replace covered leaves only in the soft-score
    # denominator. Every atomic hard condition must still participate in the
    # Hard Gate, even when the graph also references that condition.
    hard_statuses = {item.status for item in evaluation.hard_constraint_results}
    hard_gate_status = (
        "failed"
        if "fail" in hard_statuses
        else "uncertain"
        if hard_statuses.intersection(_UNCERTAIN | {"partial"})
        else "passed"
        if "pass" in hard_statuses
        else "not_applicable"
    )
    strengths = tuple(
        _insight(item, "strength")
        for item in items
        if item.score is not None and item.score >= scoring.strength_threshold
    )
    gaps = tuple(
        _insight(item, "gap")
        for item in items
        if item.score is not None and item.score <= scoring.gap_threshold
    )
    uncertain = tuple(_insight(item, "uncertain") for item in items if item.score is None)
    overall_score = round(overall, 4) if overall is not None else None
    confidence = round(match_confidence, 6)
    if evaluation.information_sufficiency_level == "material":
        confidence = round(
            confidence * scoring.material_uncertainty_confidence_factor,
            6,
        )
        uncertain = uncertain + (
            ScoreInsight(
                dimension="hard_conditions",
                result_id="information_sufficiency",
                reason_code="INFORMATION_MATERIAL_UNCERTAINTY",
                message=(
                    "uncertain:information_sufficiency:material:"
                    "INFORMATION_MATERIAL_UNCERTAINTY"
                ),
                evidence=(),
            ),
        )
    compatible_classifications = tuple(
        item
        for item in cv.position_classifications
        if item.classification_status in {"resolved", "manually_confirmed"}
        and item.taxonomy_version == position.taxonomy_version
        and item.position_code == position.position_code
    )
    scope_compatible = any(
        (position.career_level is None or item.career_level == position.career_level)
        and (
            position.leadership_scope is None
            or item.leadership_scope == position.leadership_scope
        )
        for item in compatible_classifications
    )
    strong_conclusion_allowed = bool(
        compatible_classifications
        and scope_compatible
        and position.classification_status in {"resolved", "manually_confirmed"}
        and position.sample_support_status == "sufficient"
    )
    # Inspect atomic required results rather than the normalized dimension.
    # A Requirement Graph may replace leaves in the score denominator, but it
    # must not hide whether the underlying required evidence is still unknown.
    required_results = tuple(
        item for item in evaluation.skill_results if item.importance_level == "required"
    )
    required_uncertain_count = sum(
        item.match_status in _UNCERTAIN for item in required_results
    )
    required_information_missing = bool(position.required_skills) and (
        not required_results or required_uncertain_count == len(required_results)
    )
    required_uncertainty_ratio = (
        required_uncertain_count / len(required_results)
        if required_results
        else 0.0
    )
    if required_uncertainty_ratio > 0.5:
        strong_conclusion_allowed = False
    # A high aggregate score must not override a missing or merely declared
    # mandatory capability.  Strong recommendations are reserved for cases
    # where every required skill is backed by an exact, demonstrated match;
    # this is the main precision guard against CVs that share many optional
    # keywords but miss a core requirement.
    required_precision_gate = bool(required_results) and all(
        item.match_status == "matched"
        and item.match_type == "exact"
        and item.proficiency_satisfied is not False
        and item.ownership_satisfied is not False
        and item.evidence_sufficient
        for item in required_results
    )
    if evaluation.skill_results and position.required_skills and not required_precision_gate:
        strong_conclusion_allowed = False
    # Evidence-aware coverage mapping. This intentionally uses the final
    # interpretable evidence state (not only the aggregate score), so a
    # strong/potential conclusion remains tied to claimed capability coverage.
    requirement_coverage = None
    responsibility_coverage = None
    if required_results:
        covered = sum(
            item.match_status in {"matched", "partial", "weak"}
            and item.evidence_sufficient
            for item in required_results
        )
        requirement_coverage = covered / len(required_results)
    if evaluation.responsibility_results:
        covered = sum(
            _responsibility_final_status(item) in {"matched", "partial"}
            for item in evaluation.responsibility_results
        )
        responsibility_coverage = covered / len(evaluation.responsibility_results)
    evidence_coverage = None
    if requirement_coverage is not None and responsibility_coverage is not None:
        evidence_coverage = 0.7 * requirement_coverage + 0.3 * responsibility_coverage
    elif requirement_coverage is not None:
        evidence_coverage = requirement_coverage
    elif responsibility_coverage is not None:
        evidence_coverage = responsibility_coverage
    strong_evidence_sufficient = _strong_evidence_sufficient(evaluation, scoring)
    recommendation = _recommendation(
        overall_score,
        confidence,
        hard_gate_status,
        scoring,
        strong_conclusion_allowed=strong_conclusion_allowed,
        strong_evidence_sufficient=strong_evidence_sufficient,
        coverage=evidence_coverage,
        requirement_coverage=requirement_coverage,
        responsibility_coverage=responsibility_coverage,
    )
    if evaluation.information_sufficiency_level == "blocking":
        recommendation = "insufficient_information"
    if required_information_missing:
        recommendation = "insufficient_information"
    if missing:
        strong_conclusion_allowed = False
        if recommendation in {"strong_match", "potential_match", "weak_match"}:
            recommendation = "insufficient_information"
    explanation = (
        "Weighted score uses only deterministically scored items; unknown and unresolved "
        "items are excluded from score denominators and reduce match confidence. "
        f"Hard gate is {hard_gate_status}; recommendation is {recommendation}. "
        "The projects dimension scores Applied Experience (综合实践证据): whether the "
        "candidate demonstrably used the position's required abilities in project, "
        "internship or work contexts. It is not a fixed 'project-experience requirement' "
        "of the position. "
        "Strong conclusions additionally require compatible position identity, "
        "career level, leadership scope, taxonomy version, and sufficient samples. "
        "Under material information uncertainty, strong conclusions also require "
        "at least one fully verified responsibility evidence path."
    )
    if cv.profile_id is None or position.profile_id is None:
        raise ValueError("scoring requires profile identities")
    # The two-level requirement normalization (v2) changed scoring semantics for
    # capped configurations: capped requirements are never re-amplified and the
    # unallocated mass is reported as residual.  Bump the emitted version strings
    # in this opt-in path so artifacts cannot masquerade as the legacy behavior.
    if two_level_normalization is not None:
        algorithm_version = f"{scoring.algorithm_version}-two-level.v2"
        scoring_config_version = f"{scoring.scoring_config_version}-two-level.v2"
    else:
        algorithm_version = scoring.algorithm_version
        scoring_config_version = scoring.scoring_config_version
    return FinalMatchResult(
        overall_score=overall_score,
        match_confidence=confidence,
        recommendation_level=recommendation,
        hard_gate_status=hard_gate_status,
        information_sufficient=evaluation.information_sufficient,
        information_sufficiency_level=evaluation.information_sufficiency_level,
        information_sufficiency_reasons=evaluation.information_sufficiency_reasons,
        dimension_scores=dimension_scores,
        expected_dimensions=tuple(sorted(expected)),
        produced_dimensions=tuple(sorted(produced)),
        missing_evaluation_dimensions=tuple(sorted(missing)),
        two_level_normalization=two_level_normalization,
        score_contributions=tuple(contributions),
        strengths=strengths,
        gaps=gaps,
        uncertain_items=uncertain,
        explanation=explanation,
        algorithm_version=algorithm_version,
        scoring_config_version=scoring_config_version,
        cv_profile_id=cv.profile_id,
        position_profile_id=position.profile_id,
        input_evaluation_algorithm_version=evaluation.algorithm_version,
        source_evaluation_id=evaluation.evaluation_id,
        cv_taxonomy_version=cv.taxonomy_version,
        cv_derivation_version=cv.derivation_version,
        position_taxonomy_version=position.taxonomy_version,
        position_graph_version=position.graph_version,
        position_quality_snapshot_id=position.quality_context.snapshot_id,
        position_trend_version=(
            position.trend_context.trend_version if position.trend_context else None
        ),
        vector_text_derivation_version=evaluation.vector_text_derivation_version,
        embedding_model=evaluation.embedding_model,
        embedding_version=evaluation.embedding_version,
        semantic_algorithm_version=evaluation.semantic_algorithm_version,
        semantic_threshold_config_version=evaluation.threshold_config_version,
    )
