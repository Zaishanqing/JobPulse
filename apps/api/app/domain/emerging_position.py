from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping

from app.domain.values import FrozenDict, freeze


class EmergingPositionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class InvalidEmergingTransition(ValueError):
    pass


class ReleaseGateRejected(ValueError):
    def __init__(self, failures: tuple[str, ...]) -> None:
        self.failures = failures
        super().__init__("emerging position release gate failed")


@dataclass(frozen=True)
class GerminationAssessment:
    score: float
    dimensions: FrozenDict[str, object]
    qualified: bool
    level: str
    decision_reason: str
    evidence_package: FrozenDict[str, object]
    discovery_run_id: str | None

    @classmethod
    def from_values(
        cls, values: Mapping[str, object] | None, discovery_run_id: str | None
    ) -> "GerminationAssessment":
        data = values or {}
        if (
            data.get("source") == "formal_experiment_import"
            and data.get("experiment_id")
            == "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823"
        ):
            gates = data.get("gates") if isinstance(data.get("gates"), Mapping) else {}
            required_gates = (
                "structural_signal",
                "independent_posting_persistence",
                "diffusion",
                "temporal_persistence_growth_or_evolution",
                "any_temporal_evidence",
            )
            accepted = data.get("state") == "emerging" and all(
                gates.get(name) is True for name in required_gates
            )
            return cls(
                score=1.0 if accepted else 0.0,
                dimensions=freeze({}),
                qualified=accepted,
                level=str(data.get("state", "watchlist")),
                decision_reason=str(
                    data.get("reason", "正式实验结果未提供判定原因")
                ),
                evidence_package=freeze(
                    {
                        "algorithm_version": "EMERGE v3.2",
                        "formula_version": "正式实验五项发布门禁",
                        "formal_experiment": {
                            "experiment_id": data["experiment_id"],
                            "accepted": accepted,
                            "gates": dict(gates),
                            "counts": dict(data.get("counts") or {}),
                        },
                    }
                ),
                discovery_run_id=discovery_run_id,
            )
        return cls(
            score=float(data.get("germination_score", 0.0)),
            dimensions=freeze(data.get("score_dimensions", {})),
            qualified=bool(data.get("qualified_as_emerging", False)),
            level=str(data.get("level", "watchlist")),
            decision_reason=str(data.get("decision_reason", "发现服务未返回判定原因")),
            evidence_package=freeze(data.get("evidence_package", {})),
            discovery_run_id=discovery_run_id,
        )


@dataclass(frozen=True)
class ReleaseGateEvidence:
    run_succeeded: bool
    stability_score: float
    minimum_stability_score: float
    assessment: GerminationAssessment
    emerging_threshold: float
    evidence_jd_ids: tuple[str, ...]
    real_member_count: int = 0
    window_count: int = 0
    complete_score_dimensions: bool = False
    complete_definition: bool = False
    complete_claim_evidence: bool = False
    definition_unchanged_since_approval: bool = False
    formal_experiment_accepted: bool | None = None

    def failures(self) -> tuple[str, ...]:
        failures: list[str] = []
        if not self.run_succeeded:
            failures.append("discovery run must have succeeded")
        if self.stability_score < self.minimum_stability_score:
            failures.append("cluster stability is below threshold")
        if not self.assessment.qualified or self.assessment.score < self.emerging_threshold:
            failures.append("germination assessment does not satisfy the configured threshold")
        if not self.evidence_jd_ids:
            failures.append("traceable JD evidence is required")
        if self.real_member_count < 1:
            failures.append("cluster must contain at least one real JD member")
        if self.formal_experiment_accepted is None:
            if self.window_count < 3:
                failures.append("at least three windows or an equivalent trajectory are required")
            if not self.complete_score_dimensions:
                failures.append("emergence_score must contain all seven decomposed dimensions")
        elif not self.formal_experiment_accepted:
            failures.append("formal experiment publication gates are incomplete")
        if not self.complete_definition:
            failures.append("position definition fields are incomplete")
        if not self.complete_claim_evidence:
            failures.append("every responsibility, skill and difference claim requires valid Evidence")
        if not self.definition_unchanged_since_approval:
            failures.append("definition changed after approval and must be reviewed again")
        return tuple(failures)


@dataclass(frozen=True)
class EmergingCandidate:
    candidate_id: str
    cluster_id: str
    position_name: str
    core_responsibilities: tuple[str, ...]
    required_skills: tuple[FrozenDict[str, object], ...]
    bonus_skills: tuple[FrozenDict[str, object], ...]
    industry_scenarios: tuple[str, ...]
    germination_score: float | None
    score_dimensions: FrozenDict[str, object]
    evidence_jd_ids: tuple[str, ...]
    status: EmergingPositionStatus
    field_evidence: FrozenDict[str, object] = FrozenDict()
    review_history: tuple[FrozenDict[str, object], ...] = ()
    published_snapshot: FrozenDict[str, object] = FrozenDict()

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        cluster_id: str,
        position_name: str,
        core_responsibilities: list[str] | tuple[str, ...],
        required_skills: list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
        bonus_skills: list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
        industry_scenarios: list[str] | tuple[str, ...],
        germination_score: float | None,
        score_dimensions: Mapping[str, object],
        evidence_jd_ids: list[str] | tuple[str, ...],
        status: str | EmergingPositionStatus,
        field_evidence: Mapping[str, object] | None = None,
        review_history: tuple[Mapping[str, object], ...] | list[Mapping[str, object]] = (),
        published_snapshot: Mapping[str, object] | None = None,
    ) -> "EmergingCandidate":
        return cls(
            candidate_id=candidate_id,
            cluster_id=cluster_id,
            position_name=position_name,
            core_responsibilities=tuple(core_responsibilities),
            required_skills=tuple(freeze(item) for item in required_skills),
            bonus_skills=tuple(freeze(item) for item in bonus_skills),
            industry_scenarios=tuple(industry_scenarios),
            germination_score=germination_score,
            score_dimensions=freeze(score_dimensions),
            evidence_jd_ids=tuple(evidence_jd_ids),
            status=EmergingPositionStatus(status),
            field_evidence=freeze(field_evidence or {}),
            review_history=tuple(freeze(item) for item in review_history),
            published_snapshot=freeze(published_snapshot or {}),
        )

    def edit_definition(
        self,
        *,
        position_name: str | None = None,
        core_responsibilities: tuple[str, ...] | None = None,
        required_skills: tuple[Mapping[str, object], ...] | None = None,
        bonus_skills: tuple[Mapping[str, object], ...] | None = None,
        industry_scenarios: tuple[str, ...] | None = None,
        field_evidence: Mapping[str, object] | None = None,
    ) -> "EmergingCandidate":
        return replace(
            self,
            position_name=self.position_name if position_name is None else position_name,
            core_responsibilities=(
                self.core_responsibilities
                if core_responsibilities is None
                else tuple(core_responsibilities)
            ),
            required_skills=(
                self.required_skills
                if required_skills is None
                else tuple(freeze(item) for item in required_skills)
            ),
            bonus_skills=(
                self.bonus_skills
                if bonus_skills is None
                else tuple(freeze(item) for item in bonus_skills)
            ),
            industry_scenarios=(
                self.industry_scenarios
                if industry_scenarios is None
                else tuple(industry_scenarios)
            ),
            field_evidence=(
                self.field_evidence if field_evidence is None else freeze(field_evidence)
            ),
            status=(
                EmergingPositionStatus.PENDING_REVIEW
                if self.status in {EmergingPositionStatus.APPROVED, EmergingPositionStatus.PUBLISHED}
                else self.status
            ),
        )

    def review(self, decision: EmergingPositionStatus) -> "EmergingCandidate":
        if self.status is not EmergingPositionStatus.PENDING_REVIEW:
            raise InvalidEmergingTransition("only a pending definition can be reviewed")
        if decision not in {EmergingPositionStatus.APPROVED, EmergingPositionStatus.REJECTED}:
            raise InvalidEmergingTransition("review decision must be approved or rejected")
        return replace(self, status=decision)

    def publish(self, evidence: ReleaseGateEvidence) -> "EmergingCandidate":
        if self.status is not EmergingPositionStatus.APPROVED:
            raise InvalidEmergingTransition("explicit human review approval is required")
        failures = evidence.failures()
        if failures:
            raise ReleaseGateRejected(failures)
        return replace(self, status=EmergingPositionStatus.PUBLISHED)

    def assert_promotable(self, evidence: ReleaseGateEvidence) -> None:
        if self.status is not EmergingPositionStatus.PUBLISHED:
            raise InvalidEmergingTransition("only a published position can be promoted")
        failures = evidence.failures()
        if failures:
            raise ReleaseGateRejected(failures)
