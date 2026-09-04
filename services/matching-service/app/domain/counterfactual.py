"""Counterfactual contribution engine shared by What-if and Explanation.

The engine derives every candidate action and explanation factor from the
requirement-level contribution ledger of the formal scorer.  It never invents
reasons that are absent from the score; the actual counterfactual delta is
still produced by re-running the formal evaluator after the profile mutation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.profiles import ImmutableDTO
from app.domain.scoring import ContributionLedger, RequirementContribution


class ContributionFactor(ImmutableDTO):
    """One requirement-level factor classified from the contribution ledger."""

    requirement_id: str = Field(min_length=1)
    canonical_feature: str = Field(min_length=1)
    canonical_feature_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    match_value: float | None = None
    weight: float = Field(ge=0)
    weighted_points: float = Field(ge=0)
    reason_code: str = Field(min_length=1)
    criticality: Literal["critical", "noncritical"]
    evidence_source_ids: tuple[str, ...] = ()
    used_by_scorer: bool = True
    evidence_supported: bool = False


class ContributionAction(ImmutableDTO):
    """A candidate action generated from an unmet requirement contribution."""

    action_id: str = Field(min_length=1)
    action_type: Literal[
        "add_skill",
        "satisfy_hard_condition",
        "add_project_experience",
        "evidence_required",
    ]
    requirement_id: str = Field(min_length=1)
    canonical_feature: str = Field(min_length=1)
    canonical_feature_id: str = Field(min_length=1)
    target_level: str | None = None
    estimated_hours: float = Field(ge=0)
    expected_score_delta: float
    utility: float
    current_match_value: float | None = None
    evidence_source_ids: tuple[str, ...] = ()
    target_requirement_ids: tuple[str, ...] = ()
    reason_code: str = Field(min_length=1)
    uncertainty: float = 0.0
    required_level: str | None = None
    current_level: str | None = None


class CounterfactualContributionEngine:
    """Pure ranking and classification over a frozen contribution ledger."""

    algorithm_version: str = "counterfactual-contribution.v2"

    def __init__(
        self,
        *,
        unmet_match_threshold: float = 0.5,
        noncritical_point_threshold: float = 0.05,
        cost_coefficient: float = 0.01,
        uncertainty_coefficient: float = 0.1,
        top_k: int = 5,
    ) -> None:
        if not 0 <= unmet_match_threshold <= 1:
            raise ValueError("unmet_match_threshold must be between 0 and 1")
        if noncritical_point_threshold < 0:
            raise ValueError("noncritical_point_threshold must not be negative")
        if cost_coefficient < 0 or uncertainty_coefficient < 0:
            raise ValueError("utility coefficients must not be negative")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self._unmet_match_threshold = unmet_match_threshold
        self._noncritical_point_threshold = noncritical_point_threshold
        self._cost_coefficient = cost_coefficient
        self._uncertainty_coefficient = uncertainty_coefficient
        self._top_k = top_k

    def candidate_actions(
        self,
        ledger: ContributionLedger,
    ) -> tuple[ContributionAction, ...]:
        """Rank contribution-guided actions for unmet requirements."""

        ranked: list[ContributionAction] = []
        for contribution in ledger.requirement_contributions:
            if not self._is_unmet(contribution):
                continue
            action = self._action_for(contribution)
            if action is None:
                continue
            ranked.append(action)
        # required_skills and capability_level both contribute to the same
        # canonical feature; aggregate the expected gain per concrete action
        # target so the estimate matches the full formal recompute.  Cost is
        # the max hours of the merged actions because this is one action that
        # can improve several requirements at once, not repeated work.
        by_target: dict[tuple[str, str, str], list[ContributionAction]] = {}
        for action in ranked:
            by_target.setdefault(
                (
                    action.action_type,
                    action.canonical_feature_id,
                    action.target_level or "",
                ),
                [],
            ).append(action)
        aggregated: list[ContributionAction] = []
        for key, actions in by_target.items():
            base = max(actions, key=lambda item: item.expected_score_delta)
            total_expected = round(
                sum(item.expected_score_delta for item in actions), 6
            )
            total_cost = round(
                max(item.estimated_hours for item in actions), 2
            )
            requirement_ids = tuple(
                sorted({item.requirement_id for item in actions})
            )
            uncertainty = round(
                max(item.uncertainty for item in actions),
                6,
            )
            aggregated.append(
                ContributionAction(
                    action_id=(
                        f"cc-{key[0]}-{key[1].replace(':', '-')}"
                        f"-{key[2].replace(' ', '-')}"
                    ),
                    action_type=base.action_type,
                    requirement_id=requirement_ids[0],
                    canonical_feature=base.canonical_feature,
                    canonical_feature_id=key[1],
                    target_level=base.target_level,
                    estimated_hours=total_cost,
                    expected_score_delta=total_expected,
                    utility=round(
                        total_expected
                        - self._cost_coefficient * total_cost
                        - self._uncertainty_coefficient * uncertainty,
                        6,
                    ),
                    current_match_value=base.current_match_value,
                    target_requirement_ids=requirement_ids,
                    evidence_source_ids=tuple(
                        sorted(
                            {
                                source_id
                                for item in actions
                                for source_id in item.evidence_source_ids
                            }
                        )
                    ),
                    reason_code=base.reason_code,
                    uncertainty=uncertainty,
                    required_level=base.required_level,
                    current_level=base.current_level,
                )
            )
        return tuple(
            sorted(
                aggregated,
                key=lambda item: (-item.utility, item.action_id),
            )
        )

    def top_k_actions(
        self,
        ledger: ContributionLedger,
    ) -> tuple[ContributionAction, ...]:
        return self.candidate_actions(ledger)[: self._top_k]

    def review_required_actions(
        self,
        ledger: ContributionLedger,
    ) -> tuple[ContributionAction, ...]:
        """Actions for evidence-insufficient requirements, not learning tasks."""

        actions: list[ContributionAction] = []
        for contribution in ledger.requirement_contributions:
            if contribution.match_value is not None:
                continue
            evidence_ids = tuple(
                sorted(
                    {
                        item.source_id
                        for item in (
                            *contribution.position_evidence,
                            *contribution.candidate_evidence,
                            *contribution.relation_evidence,
                        )
                    }
                )
            )
            actions.append(
                ContributionAction(
                    action_id=(
                        "cc-evidence-"
                        f"{contribution.requirement_id.replace(':', '-')}"
                    ),
                    action_type="evidence_required",
                    requirement_id=contribution.requirement_id,
                    canonical_feature=contribution.canonical_feature,
                    canonical_feature_id=contribution.canonical_feature_id,
                    target_level=contribution.required_level,
                    estimated_hours=0.0,
                    expected_score_delta=0.0,
                    utility=0.0,
                    current_match_value=None,
                    evidence_source_ids=evidence_ids,
                    target_requirement_ids=(contribution.requirement_id,),
                    reason_code=(
                        contribution.reason_code or "evidence_insufficient"
                    ),
                    uncertainty=1.0,
                    required_level=contribution.required_level,
                    current_level=contribution.current_level,
                )
            )
        return tuple(
            sorted(actions, key=lambda item: item.action_id)
        )

    def classify_factors(
        self,
        ledger: ContributionLedger,
        *,
        top_k: int | None = None,
    ) -> tuple[tuple[ContributionFactor, ...], tuple[ContributionFactor, ...]]:
        """Return (critical, noncritical) factors by real score contribution."""

        scored = tuple(
            item
            for item in ledger.requirement_contributions
            if item.match_value is not None
        )
        critical_candidates = tuple(
            sorted(
                scored,
                key=lambda item: (-item.weighted_points, item.requirement_id),
            )
        )
        critical_limit = top_k or self._top_k
        critical = tuple(
            self._factor(item, "critical")
            for item in critical_candidates[:critical_limit]
            if item.weighted_points > 0
        )
        noncritical = tuple(
            self._factor(item, "noncritical")
            for item in scored
            if item.weighted_points <= self._noncritical_point_threshold
            and item.match_value is not None
            and item.match_value <= self._unmet_match_threshold
        )
        return critical, noncritical

    def faithfulness_separation(
        self,
        *,
        critical_delta: float | None,
        noncritical_delta: float | None,
    ) -> float | None:
        """Δ_critical - Δ_noncritical (negative = critical deletion drops score).

        Noncritical deletion is expected to be zero for genuinely unused
        requirements, so zero is a valid denominator boundary: the separation
        is simply the critical drop.
        """

        if critical_delta is None or noncritical_delta is None:
            return None
        return round(critical_delta - noncritical_delta, 6)

    @staticmethod
    def expected_delta(
        contribution: RequirementContribution,
        target_match_value: float = 1.0,
    ) -> float:
        current = contribution.match_value or 0.0
        return round(
            (target_match_value - current) * contribution.weight * 100,
            6,
        )

    def _is_unmet(self, contribution: RequirementContribution) -> bool:
        if contribution.match_value is None:
            return False
        if contribution.match_value <= self._unmet_match_threshold:
            return True
        return contribution.status in {
            "missing",
            "fail",
            "not_observed",
            "unknown",
            "unresolved",
        }

    def _action_for(
        self,
        contribution: RequirementContribution,
    ) -> ContributionAction | None:
        action_type: str | None = None
        target_level: str | None = None
        hours = 8.0
        if contribution.dimension in {"required_skills", "bonus_transferable", "capability_level"}:
            action_type = "add_skill"
            target_level = contribution.required_level or "working"
            hours = 8.0
        elif contribution.dimension == "hard_conditions":
            action_type = "satisfy_hard_condition"
            hours = 4.0
        elif contribution.dimension in {
            "responsibilities",
            "projects",
            "business_scenarios",
            "requirement_groups",
        }:
            action_type = "add_project_experience"
            hours = 6.0
        if action_type is None:
            return None
        expected_delta = self.expected_delta(contribution)
        uncertainty = round(
            (1.0 - contribution.confidence)
            + (0.2 if not contribution.candidate_evidence else 0.0),
            6,
        )
        utility = round(
            expected_delta
            - self._cost_coefficient * hours
            - self._uncertainty_coefficient * uncertainty,
            6,
        )
        return ContributionAction(
            action_id=f"cc-{contribution.requirement_id.replace(':', '-')}",
            action_type=action_type,  # type: ignore[arg-type]
            requirement_id=contribution.requirement_id,
            canonical_feature=contribution.canonical_feature,
            canonical_feature_id=contribution.canonical_feature_id,
            target_level=target_level,
            estimated_hours=hours,
            expected_score_delta=expected_delta,
            utility=utility,
            current_match_value=contribution.match_value,
            evidence_source_ids=tuple(
                sorted(
                    {
                        item.source_id
                        for item in (
                            *contribution.position_evidence,
                            *contribution.candidate_evidence,
                            *contribution.relation_evidence,
                        )
                    }
                )
            ),
            reason_code=contribution.reason_code,
            uncertainty=uncertainty,
            required_level=contribution.required_level,
            current_level=contribution.current_level,
        )

    @staticmethod
    def _factor(
        contribution: RequirementContribution,
        criticality: Literal["critical", "noncritical"],
    ) -> ContributionFactor:
        evidence = tuple(
            sorted(
                {
                    item.source_id
                    for item in (
                        *contribution.position_evidence,
                        *contribution.candidate_evidence,
                        *contribution.relation_evidence,
                    )
                }
            )
        )
        return ContributionFactor(
            requirement_id=contribution.requirement_id,
            canonical_feature=contribution.canonical_feature,
            canonical_feature_id=contribution.canonical_feature_id,
            dimension=contribution.dimension,
            match_value=contribution.match_value,
            weight=contribution.weight,
            weighted_points=contribution.weighted_points,
            reason_code=contribution.reason_code,
            criticality=criticality,
            evidence_source_ids=evidence,
            used_by_scorer=True,
            evidence_supported=bool(evidence),
        )
