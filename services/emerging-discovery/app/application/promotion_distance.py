"""Read-only certificates explaining distance to the production stable gate."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.candidate_lifecycle import PromotionCondition, assess_stable_gate
from app.ports.providers import DiscoveryUnitOfWork
from app.ports.records import CandidatePromotionContextRecord


@dataclass(frozen=True)
class PromotionDistanceCertificate:
    candidate_id: str
    current_state: str
    target_state: str
    outcome: str
    eligible_state: bool
    gate_satisfied: bool
    conditions: tuple[PromotionCondition, ...]
    missing_conditions: tuple[str, ...]
    lifecycle_version: str
    config_snapshot_id: str | None
    config_run_id: str | None
    config_request_id: str | None
    algorithm_version: str | None
    formula_version: str | None


def build_promotion_distance_certificate(
    context: CandidatePromotionContextRecord,
) -> PromotionDistanceCertificate:
    candidate = context.candidate
    assessment = assess_stable_gate(
        candidate.status,
        supported_window_count=len(set(candidate.observed_window_ids)),
        support_count=candidate.support_count,
        company_count=candidate.company_coverage,
        emergence_score=candidate.emergence_score,
        identity_stability=candidate.identity_stability,
        config=context.lifecycle_config,
    )
    outcome = "ready_for_stable" if assessment.gate_satisfied else "not_ready"
    missing_conditions = assessment.missing_conditions
    if candidate.status == "stable_emerging_role":
        outcome = "already_stable"
        missing_conditions = ()
    elif candidate.status == "official_position":
        outcome = "already_beyond_stable"
        missing_conditions = ()
    elif candidate.status in {"dead", "noise"}:
        outcome = "terminal_state"
    elif candidate.status != "emerging_candidate":
        outcome = "requires_prior_promotions"

    window = context.window
    return PromotionDistanceCertificate(
        candidate_id=candidate.id,
        current_state=candidate.status,
        target_state="stable_emerging_role",
        outcome=outcome,
        eligible_state=assessment.eligible_state,
        gate_satisfied=assessment.gate_satisfied
        or candidate.status == "official_position",
        conditions=assessment.conditions,
        missing_conditions=missing_conditions,
        lifecycle_version=assessment.lifecycle_version,
        config_snapshot_id=context.config_snapshot_id,
        config_run_id=window.run_id if window else None,
        config_request_id=window.request_id if window else None,
        algorithm_version=window.algorithm_version if window else None,
        formula_version=window.formula_version if window else None,
    )


@dataclass(frozen=True)
class EvaluatePromotionDistance:
    uow: DiscoveryUnitOfWork

    def execute(
        self,
        *,
        candidate_id: str | None = None,
    ) -> tuple[PromotionDistanceCertificate, ...]:
        with self.uow:
            contexts = self.uow.candidates.promotion_contexts(candidate_id)
        return tuple(build_promotion_distance_certificate(item) for item in contexts)
