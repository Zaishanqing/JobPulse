"""Independent Responsibility Evidence/Decision Policy layer.

The frozen Cross-Encoder only answers the binary semantic question
``matched / not_matched``.  The product-facing four decision states are derived
here, after CE, from the CE result plus retrieval/evidence properties.  This
module never loads, changes or re-scores the CE model.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.context_matching import (
    ContextMatchingConfig,
    _candidate_texts,
    evaluate_responsibilities,
)
from app.domain.evaluation import ResponsibilityResult
from app.domain.profiles import CVMatchProfile, PositionMatchProfile


@dataclass(frozen=True)
class ResponsibilityDecisionPolicyConfig:
    evidence_retrieval_floor: float = 0.35
    strong_retrieval_floor: float = 0.55

    def __post_init__(self) -> None:
        if not (
            0
            <= self.evidence_retrieval_floor
            <= self.strong_retrieval_floor
            <= 1
        ):
            raise ValueError(
                "retrieval floors must satisfy 0 <= evidence <= strong <= 1"
            )


class ResponsibilityDecisionPolicy:
    """Post-CE decision states: matched / partial / insufficient_evidence / not_observed."""

    algorithm_version = "responsibility-decision-policy.v1"

    def __init__(
        self,
        config: ResponsibilityDecisionPolicyConfig | None = None,
    ) -> None:
        self.config = config or ResponsibilityDecisionPolicyConfig()

    def apply(
        self,
        results: tuple[ResponsibilityResult, ...],
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        context_config: ContextMatchingConfig | None = None,
    ) -> tuple[ResponsibilityResult, ...]:
        config = context_config or ContextMatchingConfig()
        has_candidate_text = bool(_candidate_texts(cv, config))
        deterministic = {
            item.requirement_id: item
            for item in evaluate_responsibilities(
                cv,
                position,
                config,
            )
        }
        return tuple(
            self._apply_one(
                result,
                deterministic.get(result.requirement_id),
                has_candidate_text=has_candidate_text,
            )
            for result in results
        )

    def _apply_one(
        self,
        result: ResponsibilityResult,
        deterministic: ResponsibilityResult | None,
        *,
        has_candidate_text: bool,
    ) -> ResponsibilityResult:
        if result.match_status == "matched":
            state = self._decide(
                result,
                has_candidate_text=has_candidate_text,
            )
            return result.model_copy(update={"status_detail": state})
        # CE not matched: fuse deterministic partial evidence when present.
        if (
            deterministic is not None
            and deterministic.match_status in {"matched", "partial"}
            and deterministic.candidate_evidence
        ):
            return result.model_copy(
                update={
                    "match_status": "partial",
                    "status_detail": "partial",
                    "candidate_experience_id": (
                        deterministic.candidate_experience_id
                        or result.candidate_experience_id
                    ),
                    "candidate_experience": (
                        deterministic.candidate_experience
                        or result.candidate_experience
                    ),
                    "candidate_evidence": (
                        deterministic.candidate_evidence
                        or result.candidate_evidence
                    ),
                    "reason_code": "RESPONSIBILITY_DETERMINISTIC_PARTIAL_FUSION",
                    "confidence": max(result.confidence, deterministic.confidence),
                }
            )
        state = self._decide(
            result,
            has_candidate_text=has_candidate_text,
        )
        confidence = (
            1.0
            if state == "not_observed"
            else 0.0
            if state == "insufficient_evidence"
            else result.confidence
        )
        return result.model_copy(
            update={
                "status_detail": state,
                "confidence": confidence,
            }
        )

    def _decide(
        self,
        result: ResponsibilityResult,
        *,
        has_candidate_text: bool,
    ) -> str:
        best = result.top_candidates[0] if result.top_candidates else None
        retrieval = best.retrieval_score if best is not None else 0.0
        has_evidence = bool(best is not None and best.evidence_refs)
        if result.match_status == "matched":
            if best is None:
                # Deterministic rules matched without CE retrieval candidates;
                # the rule-based evidence is already sufficient.
                return "matched"
            if (
                not has_evidence
                or retrieval < self.config.evidence_retrieval_floor
            ):
                return "insufficient_evidence"
            if (
                retrieval >= self.config.strong_retrieval_floor
                and result.threshold_margin is not None
                and result.threshold_margin >= 0.0
            ):
                return "matched"
            return "partial"
        # CE not matched.
        if not has_candidate_text or best is None or not has_evidence:
            return "insufficient_evidence"
        if retrieval < self.config.evidence_retrieval_floor:
            return "insufficient_evidence"
        return "not_observed"
