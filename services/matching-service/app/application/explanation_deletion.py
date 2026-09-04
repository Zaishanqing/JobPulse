"""Formal Evidence deletion recomputation for explanation faithfulness."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal

from pydantic import ValidationError

from app.application.evaluation import MatchEvaluationService
from app.application.learning_paths import LearningPathService
from app.application.what_if import WhatIfService
from app.domain.counterfactual import (
    CounterfactualContributionEngine,
)
from app.domain.evaluation import MatchEvaluation
from app.domain.explanation_deletion import (
    EvidenceDeletionResult,
    FeatureContributionGroup,
    ExplanationFactor,
    FeatureAblationCertificate,
)
from app.domain.gaps import GapAnalysis
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.scoring import build_contribution_ledger
from app.domain.what_if import DimensionDelta


def _factor_type(dimension: str) -> str:
    mapping = {
        "required_skills": "required_skill",
        "bonus_transferable": "preferred_skill",
        "capability_level": "required_skill",
        "hard_conditions": "hard_constraint",
        "responsibilities": "responsibility",
        "projects": "project",
        "business_scenarios": "scenario",
        "requirement_groups": "project",
    }
    return mapping.get(dimension, "project")


def group_feature_contributions(
    ledger,
) -> tuple[FeatureContributionGroup, ...]:
    """Aggregate requirement contributions into explanation-level groups."""

    grouped: dict[str, dict] = {}
    for item in ledger.requirement_contributions:
        key = item.canonical_feature_id
        group = grouped.setdefault(
            key,
            {
                "canonical_feature": item.canonical_feature,
                "member_requirement_ids": set(),
                "dimensions": set(),
                "weighted_points": 0.0,
                "evidence_source_ids": set(),
                "confidence": 0.0,
            },
        )
        group["member_requirement_ids"].add(item.requirement_id)
        group["dimensions"].add(str(item.dimension))
        group["weighted_points"] += item.weighted_points
        group["evidence_source_ids"].update(
            evidence.source_id for evidence in item.candidate_evidence
        )
        group["confidence"] = max(
            group["confidence"], item.confidence or 0.0
        )
    return tuple(
        sorted(
            (
                FeatureContributionGroup(
                    canonical_feature_id=key,
                    canonical_feature=group["canonical_feature"],
                    member_requirement_ids=tuple(
                        sorted(group["member_requirement_ids"])
                    ),
                    dimensions=tuple(sorted(group["dimensions"])),
                    baseline_weighted_points=round(
                        group["weighted_points"], 6
                    ),
                    evidence_source_ids=tuple(
                        sorted(group["evidence_source_ids"])
                    ),
                    confidence=round(group["confidence"], 6),
                )
                for key, group in grouped.items()
            ),
            key=lambda item: (
                -item.baseline_weighted_points,
                item.canonical_feature_id,
            ),
        )
    )


class ExplanationDeletionService:
    """Delete classified Evidence and replay the formal decision chain."""

    def __init__(
        self,
        evaluation_service: MatchEvaluationService,
        learning_path_service: LearningPathService,
        *,
        stability_threshold_points: float = 1.0,
    ) -> None:
        self._evaluation_service = evaluation_service
        self._learning_path_service = learning_path_service
        self._stability_threshold = stability_threshold_points

    def evaluate(self, payload: object) -> EvidenceDeletionResult:
        if not isinstance(payload, Mapping):
            return self._rejected("EVIDENCE_DELETION_REQUEST_INVALID", "request must be an object")
        try:
            cv = CVMatchProfile.model_validate(payload.get("cv_profile"))
            position = PositionMatchProfile.model_validate(payload.get("position_profile"))
            supplied_baseline = MatchEvaluation.model_validate(
                payload.get("baseline_evaluation")
            )
        except ValidationError:
            return self._rejected(
                "EVIDENCE_DELETION_INPUT_INVALID",
                "baseline evaluation and both profiles are required",
            )
        deletion_kind = payload.get("deletion_kind")
        raw_ids = payload.get("evidence_source_ids")
        if deletion_kind not in {"critical", "noncritical"}:
            return self._rejected(
                "EVIDENCE_DELETION_KIND_INVALID",
                "deletion_kind must be critical or noncritical",
            )
        if not isinstance(raw_ids, list | tuple) or not raw_ids or not all(
            isinstance(item, str) and item.strip() for item in raw_ids
        ):
            return self._rejected(
                "EVIDENCE_DELETION_SET_INVALID",
                "evidence_source_ids must be a non-empty string list",
            )
        deleted_ids = tuple(sorted(set(raw_ids)))
        target_type = payload.get("target_type", "standard_position")
        use_enterprise_weights = payload.get("use_enterprise_weights", False)
        if target_type not in {"standard_position", "enterprise_job"} or not isinstance(
            use_enterprise_weights, bool
        ):
            return self._rejected(
                "EVIDENCE_DELETION_POLICY_INVALID",
                "target_type or enterprise weight policy is invalid",
            )

        common = {
            "position_profile": position.model_dump(mode="python"),
            "target_type": target_type,
            "use_enterprise_weights": use_enterprise_weights,
        }
        recomputed = self._evaluation_service.evaluate(
            {**common, "cv_profile": cv.model_dump(mode="python")},
            include_semantic=False,
        )
        if not WhatIfService._baseline_matches(
            supplied_baseline, recomputed, cv, position
        ):
            return self._rejected(
                "EVIDENCE_DELETION_BASELINE_MISMATCH",
                "baseline does not match the supplied profiles and scoring policy",
            )
        factors, critical_ids, noncritical_ids = self._factors(recomputed, cv, position)
        allowed = critical_ids if deletion_kind == "critical" else noncritical_ids
        unknown = tuple(sorted(set(deleted_ids) - allowed))
        if unknown:
            return self._rejected(
                "EVIDENCE_DELETION_CLASSIFICATION_MISMATCH",
                "deletion set is not classified as requested: " + ", ".join(unknown),
            )

        run_id = self._run_id(
            supplied_baseline,
            cv,
            position,
            deletion_kind,
            deleted_ids,
            target_type=str(target_type),
            use_enterprise_weights=use_enterprise_weights,
        )
        ablated_cv = self._delete_cv(cv, frozenset(deleted_ids), run_id, "delete")
        ablated_position = self._delete_position(
            position, frozenset(deleted_ids), run_id, "delete"
        )
        ablated = self._evaluation_service.evaluate(
            {
                "cv_profile": ablated_cv.model_dump(mode="python"),
                "position_profile": ablated_position.model_dump(mode="python"),
                "target_type": target_type,
                "use_enterprise_weights": use_enterprise_weights,
            },
            include_semantic=False,
        )
        if ablated.evaluation_status != "completed":
            return self._rejected(
                ablated.error_code or "EVIDENCE_DELETION_RECOMPUTE_REJECTED",
                "ablated profiles could not be formally recomputed",
                run_id,
            )
        baseline_gap = self._gap(
            supplied_baseline, cv, position, target_type, use_enterprise_weights
        )
        ablated_gap = self._gap(
            ablated,
            ablated_cv,
            ablated_position,
            target_type,
            use_enterprise_weights,
        )
        if (
            baseline_gap.generation_status != "completed"
            or ablated_gap.generation_status != "completed"
        ):
            return self._rejected(
                "EVIDENCE_DELETION_GAP_RECOMPUTE_REJECTED",
                "baseline or ablated Gap/Action recomputation failed",
                run_id,
            )
        retained_score = self._retained_only_score(
            cv,
            position,
            frozenset(deleted_ids),
            frozenset(critical_ids | noncritical_ids),
            run_id,
            str(target_type),
            use_enterprise_weights,
        )
        return self._result(
            run_id=run_id,
            deletion_kind=deletion_kind,
            deleted_ids=deleted_ids,
            factors=factors,
            critical_ids=critical_ids,
            noncritical_ids=noncritical_ids,
            baseline=supplied_baseline,
            ablated=ablated,
            baseline_gap=baseline_gap,
            ablated_gap=ablated_gap,
            retained_score=retained_score,
        )

    def evaluate_contribution_v2(
        self,
        payload: object,
    ) -> EvidenceDeletionResult:
        """Contribution-ledger deletion for the faithful explanation v2 challenger.

        Critical factors are the top real score contributors; noncritical
        factors are requirements whose contribution is near zero.  The deleted
        feature is removed from the CV profile before the formal evaluator
        recomputes, so the delta is the real scorer effect, not an estimate.
        """

        if not isinstance(payload, Mapping):
            return self._rejected(
                "EVIDENCE_DELETION_REQUEST_INVALID", "request must be an object"
            )
        try:
            cv = CVMatchProfile.model_validate(payload.get("cv_profile"))
            position = PositionMatchProfile.model_validate(payload.get("position_profile"))
            supplied_baseline = MatchEvaluation.model_validate(
                payload.get("baseline_evaluation")
            )
        except ValidationError:
            return self._rejected(
                "EVIDENCE_DELETION_INPUT_INVALID",
                "baseline evaluation and both profiles are required",
            )
        deletion_kind = payload.get("deletion_kind")
        if deletion_kind not in {"critical", "noncritical"}:
            return self._rejected(
                "EVIDENCE_DELETION_KIND_INVALID",
                "deletion_kind must be critical or noncritical",
            )
        target_type = payload.get("target_type", "standard_position")
        use_enterprise_weights = payload.get("use_enterprise_weights", False)
        if target_type not in {"standard_position", "enterprise_job"} or not isinstance(
            use_enterprise_weights, bool
        ):
            return self._rejected(
                "EVIDENCE_DELETION_POLICY_INVALID",
                "target_type or enterprise weight policy is invalid",
            )
        common = {
            "position_profile": position.model_dump(mode="python"),
            "target_type": target_type,
            "use_enterprise_weights": use_enterprise_weights,
        }
        recomputed = self._evaluation_service.evaluate(
            {**common, "cv_profile": cv.model_dump(mode="python")},
            include_semantic=False,
        )
        if not WhatIfService._baseline_matches(
            supplied_baseline, recomputed, cv, position
        ):
            return self._rejected(
                "EVIDENCE_DELETION_BASELINE_MISMATCH",
                "baseline does not match the supplied profiles and scoring policy",
            )
        final = recomputed.final_match_result
        if final is None:
            return self._rejected(
                "EVIDENCE_DELETION_SCORE_MISSING", "formal score is missing"
            )
        ledger = build_contribution_ledger(recomputed, cv, position)
        engine = CounterfactualContributionEngine()
        critical, noncritical = engine.classify_factors(ledger)
        selected = critical if deletion_kind == "critical" else noncritical
        if not selected:
            return self._rejected(
                "EVIDENCE_DELETION_CLASSIFICATION_EMPTY",
                f"no {deletion_kind} factors in the contribution ledger",
            )
        factor = selected[0]
        run_id = self._run_id(
            supplied_baseline,
            cv,
            position,
            deletion_kind,
            (factor.requirement_id,),
            target_type=str(target_type),
            use_enterprise_weights=use_enterprise_weights,
        )
        ablated_cv = self._delete_requirement_feature(
            cv, factor.canonical_feature_id, run_id
        )
        ablated = self._evaluation_service.evaluate(
            {
                **common,
                "cv_profile": ablated_cv.model_dump(mode="python"),
            },
            include_semantic=False,
        )
        if ablated.evaluation_status != "completed":
            return self._rejected(
                ablated.error_code or "EVIDENCE_DELETION_RECOMPUTE_REJECTED",
                "ablated profile could not be formally recomputed",
                run_id,
            )
        before = supplied_baseline.final_match_result
        after = ablated.final_match_result
        if before is None or after is None:
            return self._rejected(
                "EVIDENCE_DELETION_SCORE_MISSING", "formal score is missing", run_id
            )
        score_delta = self._delta(before.overall_score, after.overall_score)
        explanation_factors = tuple(
            ExplanationFactor(
                factor_id=item.requirement_id,
                factor_type=_factor_type(item.dimension),
                requirement_id=item.requirement_id,
                reason_code=item.reason_code,
                criticality=item.criticality,
                evidence_source_ids=item.evidence_source_ids,
                used_by_scorer=True,
                evidence_supported=item.evidence_supported,
            )
            for item in (*critical, *noncritical)
        )
        return EvidenceDeletionResult(
            generation_status="completed",
            deletion_run_id=run_id,
            deletion_kind=deletion_kind,
            deleted_evidence_source_ids=(
                factor.requirement_id,
            ),
            critical_evidence_source_ids=tuple(
                item.requirement_id for item in critical
            ),
            noncritical_evidence_source_ids=tuple(
                item.requirement_id for item in noncritical
            ),
            explanation_factors=explanation_factors,
            baseline_evaluation=supplied_baseline,
            ablated_evaluation=ablated,
            baseline_score=before.overall_score,
            ablated_score=after.overall_score,
            score_delta=score_delta,
            dimension_deltas=self._dimension_deltas(before, after),
            baseline_hard_gate_status=before.hard_gate_status,
            ablated_hard_gate_status=after.hard_gate_status,
            hard_gate_delta=(
                f"{before.hard_gate_status}->{after.hard_gate_status}"
                if before.hard_gate_status != after.hard_gate_status
                else None
            ),
            comprehensiveness=self._normalized_drop(
                before.overall_score, after.overall_score
            ),
            unsupported_reason_rate=0.0,
            faithfulness_status=(
                "faithful"
                if score_delta is not None and score_delta < 0
                else "possibly_unfaithful"
            ),
            baseline_evaluation_id=supplied_baseline.evaluation_id,
            cv_profile_version=supplied_baseline.cv_profile_version,
            position_profile_version=supplied_baseline.position_profile_version,
            scoring_algorithm_version=before.algorithm_version,
            scoring_config_version=before.scoring_config_version,
            classification_policy_version="contribution-ledger.v2",
            algorithm_version="evidence-deletion-recompute.v2",
        )

    @classmethod
    def _delete_requirement_feature(
        cls,
        cv: CVMatchProfile,
        feature_id: str,
        run_id: str,
    ) -> CVMatchProfile:
        """Remove one canonical feature from the CV before formal recompute."""

        payload = cv.model_dump(mode="python")
        prefix, _separator, value = feature_id.partition(":")
        value_lower = value.casefold() if _separator else feature_id.casefold()
        payload["skills"] = [
            item
            for item in payload.get("skills", ())
            if item.get("skill_id") != feature_id
            and item.get("aggregation_key") != f"skill:{feature_id}"
            and item.get("aggregation_key") != f"skill:{value}"
            and item.get("skill_id") != value
        ]
        payload["capability_profiles"] = [
            item
            for item in payload.get("capability_profiles", ())
            if item.get("skill_id") != feature_id
            and item.get("skill_id") != value
        ]
        payload["capability_evidence_links"] = [
            item
            for item in payload.get("capability_evidence_links", ())
            if item.get("skill_id") != feature_id
            and item.get("skill_id") != value
        ]
        payload["match_features"] = [
            item
            for item in payload.get("match_features", ())
            if item.get("canonical_id") != feature_id
            and item.get("feature_type") != feature_id
            and not (
                prefix
                and item.get("feature_type") == prefix
                and str(item.get("canonical_name") or "").casefold() == value_lower
            )
        ]
        payload["certificates"] = [
            item
            for item in payload.get("certificates", ())
            if str(item.get("name") or "").casefold() != value_lower
            and item.get("credential_id") != value
            and item.get("credential_id") != feature_id
        ]
        payload["education"] = [
            item
            for item in payload.get("education", ())
            if str(item.get("degree_level") or "").casefold() != value_lower
            and str(item.get("field_of_study") or "").casefold() != value_lower
            and item.get("education_id") != value
            and item.get("education_id") != feature_id
        ]
        payload["languages"] = [
            item
            for item in payload.get("languages", ())
            if str(item.get("name") or "").casefold() != value_lower
            and str(item.get("language") or "").casefold() != value_lower
            and item.get("language_id") != feature_id
        ]
        base_profile = cv.profile_version.split("|cc-v2:")[0]
        base_source = cv.source_version.split("|cc-v2:")[0]
        payload["profile_version"] = f"{base_profile}|cc-v2:{run_id}"
        payload["source_version"] = f"{base_source}|cc-v2:{run_id}"
        return CVMatchProfile.model_validate(payload)

    @classmethod
    def ablate_contribution(
        cls,
        cv: CVMatchProfile,
        contribution,
    ) -> tuple[CVMatchProfile, FeatureAblationCertificate]:
        """Ablate a ledger contribution and certify that the deletion landed."""

        run_id = (
            f"abl-{contribution.requirement_id}"
            if getattr(contribution, "requirement_id", None)
            else f"abl-{contribution.canonical_feature_id}"
        )
        before_fingerprint = cls._fingerprint(cv)
        evidence_ids = tuple(
            getattr(contribution, "evidence_source_ids", ()) or ()
        )
        ablated = cls._delete_requirement_feature(
            cv, contribution.canonical_feature_id, run_id
        )
        if evidence_ids:
            ablated = cls._delete_cv(
                ablated, frozenset(evidence_ids), run_id, "delete"
            )
        fingerprint_changed = before_fingerprint != cls._fingerprint(ablated)
        evidence_before = cls._count_references(cv, evidence_ids)
        evidence_after = cls._count_references(ablated, evidence_ids)
        closure_keys = cls._scorer_closure_keys(contribution)
        closure_before = cls._count_references_in_keys(
            cv, closure_keys, (contribution.canonical_feature_id,)
        )
        closure_after = cls._count_references_in_keys(
            ablated, closure_keys, (contribution.canonical_feature_id,)
        )
        input_closure_removed = (
            closure_before > 0 and closure_after == 0
        )
        evidence_disappeared = evidence_before > 0 and evidence_after == 0
        candidate_evidence_removed = evidence_disappeared
        residual_trace_refs = cls._residual_trace_refs(
            ablated, (contribution.canonical_feature_id,)
        )
        noop = (
            not fingerprint_changed
            or (evidence_before > 0 and not evidence_disappeared)
            or (closure_before > 0 and not input_closure_removed)
        )
        if not fingerprint_changed:
            reason = "profile fingerprint unchanged"
        elif evidence_before > 0 and not evidence_disappeared:
            reason = "target evidence still present"
        elif closure_before > 0 and not input_closure_removed:
            reason = "scorer-consumed target input still present"
        else:
            reason = "target removed"
        return ablated, FeatureAblationCertificate(
            status="noop" if noop else "ablated",
            profile_fingerprint_changed=fingerprint_changed,
            input_closure_removed=input_closure_removed,
            candidate_evidence_removed=candidate_evidence_removed,
            residual_trace_refs=residual_trace_refs,
            reason=reason,
        )

    @staticmethod
    def _scorer_closure_keys(contribution) -> tuple[str, ...]:
        dimension = str(getattr(contribution, "dimension", "") or "")
        feature_id = str(
            getattr(contribution, "canonical_feature_id", "") or ""
        )
        if (
            dimension
            in {
                "required_skills",
                "preferred_skills",
                "capability_level",
            }
            or feature_id.startswith("skill_")
        ):
            return (
                "skills",
                "capability_profiles",
                "capability_evidence_links",
            )
        if "education" in feature_id or "education" in dimension:
            return ("education",)
        if "certificate" in feature_id or "certificate" in dimension:
            return ("certificates",)
        if "language" in feature_id or "language" in dimension:
            return ("languages",)
        if "experience" in feature_id or "experience" in dimension:
            return ("work_experiences",)
        if "location" in dimension or "availability" in dimension:
            return ("match_features",)
        if any(
            token in dimension
            for token in ("project", "responsibilit", "scenario")
        ):
            return ("projects", "match_features", "work_experiences")
        return (
            "skills",
            "capability_profiles",
            "capability_evidence_links",
            "match_features",
        )

    @staticmethod
    def _count_references_in_keys(
        cv: CVMatchProfile,
        keys: tuple[str, ...],
        ids: tuple[str, ...],
    ) -> int:
        target = set(ids)
        count = 0
        payload = cv.model_dump(mode="python")

        def walk(value: Any) -> None:
            nonlocal count
            if isinstance(value, str):
                if value in target:
                    count += 1
            elif isinstance(value, Mapping):
                for item in value.values():
                    walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)

        for key in keys:
            for item in payload.get(key, ()) or ():
                walk(item)
        return count

    @staticmethod
    def _residual_trace_refs(
        cv: CVMatchProfile, ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        target = set(ids)
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                if value in target and value not in found:
                    found.append(value)
            elif isinstance(value, Mapping):
                for item in value.values():
                    walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)

        walk(cv.model_dump(mode="python"))
        return tuple(found[:20])

    @staticmethod
    def _fingerprint(cv: CVMatchProfile) -> str:
        payload = json.dumps(
            cv.model_dump(mode="python"),
            default=str,
            ensure_ascii=False,
            sort_keys=True,
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _count_references(cv: CVMatchProfile, ids: tuple[str, ...]) -> int:
        target = set(ids)
        count = 0

        def walk(value: Any) -> None:
            nonlocal count
            if isinstance(value, str):
                if value in target:
                    count += 1
            elif isinstance(value, Mapping):
                for item in value.values():
                    walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)

        walk(cv.model_dump(mode="python"))
        return count

    def _gap(
        self,
        evaluation: MatchEvaluation,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        target_type: object,
        use_enterprise_weights: bool,
    ) -> GapAnalysis:
        return self._learning_path_service.generate(
            {
                "evaluation": evaluation.model_dump(mode="python"),
                "cv_profile": cv.model_dump(mode="python"),
                "position_profile": position.model_dump(mode="python"),
                "target_type": target_type,
                "use_enterprise_weights": use_enterprise_weights,
            },
            include_route_scenarios=False,
        )

    def _result(
        self,
        *,
        run_id: str,
        deletion_kind: Literal["critical", "noncritical"],
        deleted_ids: tuple[str, ...],
        factors: tuple[ExplanationFactor, ...],
        critical_ids: frozenset[str],
        noncritical_ids: frozenset[str],
        baseline: MatchEvaluation,
        ablated: MatchEvaluation,
        baseline_gap: GapAnalysis,
        ablated_gap: GapAnalysis,
        retained_score: float | None,
    ) -> EvidenceDeletionResult:
        before = baseline.final_match_result
        after = ablated.final_match_result
        if before is None or after is None:
            return self._rejected(
                "EVIDENCE_DELETION_SCORE_MISSING", "formal score is missing", run_id
            )
        score_delta = self._delta(before.overall_score, after.overall_score)
        gap_before = self._gap_ids(baseline_gap)
        gap_after = self._gap_ids(ablated_gap)
        actions_before = frozenset(item.action_id for item in baseline_gap.candidate_actions)
        actions_after = frozenset(item.action_id for item in ablated_gap.candidate_actions)
        hard_delta = (
            f"{before.hard_gate_status}->{after.hard_gate_status}"
            if before.hard_gate_status != after.hard_gate_status
            else None
        )
        structural_change = bool(
            gap_before ^ gap_after or actions_before ^ actions_after or hard_delta
        )
        if deletion_kind == "critical":
            faithful = bool(
                structural_change or (score_delta is not None and abs(score_delta) > 1e-9)
            )
            faithfulness_status = "faithful" if faithful else "possibly_unfaithful"
        else:
            unstable = bool(
                structural_change
                or (score_delta is not None and abs(score_delta) > self._stability_threshold)
            )
            faithfulness_status = "unstable" if unstable else "faithful"
        positive_factors = tuple(
            item
            for item in factors
            if item.used_by_scorer and item.factor_type != "unused_evidence"
        )
        unsupported = sum(not item.evidence_supported for item in positive_factors)
        baseline_score = before.overall_score
        return EvidenceDeletionResult(
            generation_status="completed",
            deletion_run_id=run_id,
            deletion_kind=deletion_kind,
            deleted_evidence_source_ids=deleted_ids,
            critical_evidence_source_ids=tuple(sorted(critical_ids)),
            noncritical_evidence_source_ids=tuple(sorted(noncritical_ids)),
            explanation_factors=factors,
            baseline_evaluation=baseline,
            ablated_evaluation=ablated,
            baseline_gap_analysis=baseline_gap,
            ablated_gap_analysis=ablated_gap,
            baseline_score=baseline_score,
            ablated_score=after.overall_score,
            retained_only_score=retained_score,
            score_delta=score_delta,
            dimension_deltas=self._dimension_deltas(before, after),
            baseline_hard_gate_status=before.hard_gate_status,
            ablated_hard_gate_status=after.hard_gate_status,
            hard_gate_delta=hard_delta,
            added_gap_ids=tuple(sorted(gap_after - gap_before)),
            removed_gap_ids=tuple(sorted(gap_before - gap_after)),
            added_action_ids=tuple(sorted(actions_after - actions_before)),
            removed_action_ids=tuple(sorted(actions_before - actions_after)),
            comprehensiveness=self._normalized_drop(baseline_score, after.overall_score),
            sufficiency=self._normalized_drop(baseline_score, retained_score),
            unsupported_reason_rate=(
                round(unsupported / len(positive_factors), 6) if positive_factors else 0.0
            ),
            faithfulness_status=faithfulness_status,
            baseline_evaluation_id=baseline.evaluation_id,
            cv_profile_version=baseline.cv_profile_version,
            position_profile_version=baseline.position_profile_version,
            scoring_algorithm_version=before.algorithm_version,
            scoring_config_version=before.scoring_config_version,
            stability_threshold_points=self._stability_threshold,
        )

    def _retained_only_score(
        self,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        retained: frozenset[str],
        known: frozenset[str],
        run_id: str,
        target_type: str,
        use_enterprise_weights: bool,
    ) -> float | None:
        remove = known - retained
        kept_cv = self._delete_cv(cv, remove, run_id, "retain")
        kept_position = self._delete_position(position, remove, run_id, "retain")
        result = self._evaluation_service.evaluate(
            {
                "cv_profile": kept_cv.model_dump(mode="python"),
                "position_profile": kept_position.model_dump(mode="python"),
                "target_type": target_type,
                "use_enterprise_weights": use_enterprise_weights,
            },
            include_semantic=False,
        )
        return (
            result.final_match_result.overall_score
            if result.evaluation_status == "completed" and result.final_match_result
            else None
        )

    @classmethod
    def _factors(
        cls,
        evaluation: MatchEvaluation,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
    ) -> tuple[tuple[ExplanationFactor, ...], frozenset[str], frozenset[str]]:
        factors: list[ExplanationFactor] = []

        def add(
            factor_type: str,
            requirement_id: str,
            reason_code: str,
            evidence: tuple[Any, ...],
        ) -> None:
            source_ids = tuple(sorted({item.source_id for item in evidence}))
            factors.append(
                ExplanationFactor(
                    factor_id=f"{factor_type}:{requirement_id}",
                    factor_type=factor_type,
                    requirement_id=requirement_id,
                    reason_code=reason_code,
                    criticality="critical",
                    evidence_source_ids=source_ids,
                    used_by_scorer=True,
                    evidence_supported=bool(source_ids),
                )
            )

        for item in evaluation.hard_constraint_results:
            if item.status in {"pass", "partial"}:
                add(
                    "hard_constraint",
                    item.requirement_id,
                    item.reason_code,
                    item.candidate_evidence,
                )
        for item in evaluation.skill_results:
            if item.match_status not in {"missing", "unknown", "unresolved"}:
                add(
                    "required_skill" if item.importance_level == "required" else "preferred_skill",
                    item.requirement_id,
                    item.reason_code,
                    item.candidate_evidence,
                )
        for factor_type, results in (
            ("responsibility", evaluation.responsibility_results),
            ("project", evaluation.project_results),
            ("scenario", evaluation.scenario_results),
        ):
            for item in results:
                if item.match_status not in {"missing", "unknown", "unresolved"}:
                    add(factor_type, item.requirement_id, item.reason_code, item.candidate_evidence)
        critical = frozenset(
            source_id for item in factors for source_id in item.evidence_source_ids
        )
        all_ids = cls._all_evidence_ids(cv.model_dump(mode="python")) | cls._all_evidence_ids(
            position.model_dump(mode="python")
        )
        noncritical = frozenset(all_ids - critical)
        factors.extend(
            ExplanationFactor(
                factor_id=f"unused_evidence:{source_id}",
                factor_type="unused_evidence",
                reason_code="NOT_USED_BY_FORMAL_SCORER",
                criticality="noncritical",
                evidence_source_ids=(source_id,),
                used_by_scorer=False,
                evidence_supported=True,
            )
            for source_id in sorted(noncritical)
        )
        return tuple(factors), critical, noncritical

    @classmethod
    def _delete_cv(
        cls,
        cv: CVMatchProfile,
        deleted: frozenset[str],
        run_id: str,
        mode: str,
    ) -> CVMatchProfile:
        payload = cv.model_dump(mode="python")
        for key in (
            "skills",
            "match_features",
            "capability_evidence_links",
            "projects",
            "work_experiences",
            "education",
            "certificates",
            "languages",
            "research_outputs",
        ):
            payload[key] = [
                cls._strip_evidence(item, deleted)
                for item in payload.get(key, ())
                if not cls._fully_deleted(item, deleted)
            ]
        valid_links = {item["link_id"] for item in payload["capability_evidence_links"]}
        payload["capability_profiles"] = [
            cls._strip_evidence(item, deleted)
            for item in payload.get("capability_profiles", ())
            if not item.get("evidence_link_ids")
            or set(item["evidence_link_ids"]).intersection(valid_links)
        ]
        for item in payload["capability_profiles"]:
            item["evidence_link_ids"] = [
                link_id for link_id in item.get("evidence_link_ids", ()) if link_id in valid_links
            ]
        payload = cls._strip_evidence(payload, deleted)
        base_profile = cv.profile_version.split("|evid09:")[0]
        base_source = cv.source_version.split("|evid09:")[0]
        payload["profile_version"] = f"{base_profile}|evid09:{run_id}:{mode}"
        payload["source_version"] = f"{base_source}|evid09:{run_id}:{mode}"
        return CVMatchProfile.model_validate(payload)

    @classmethod
    def _delete_position(
        cls,
        position: PositionMatchProfile,
        deleted: frozenset[str],
        run_id: str,
        mode: str,
    ) -> PositionMatchProfile:
        payload = cls._strip_evidence(position.model_dump(mode="python"), deleted)
        payload["profile_version"] = f"{position.profile_version}|evid09:{run_id}:{mode}"
        payload["source_version"] = f"{position.source_version}|evid09:{run_id}:{mode}"
        return PositionMatchProfile.model_validate(payload)

    @classmethod
    def _strip_evidence(cls, value: Any, deleted: frozenset[str]) -> Any:
        if isinstance(value, list | tuple):
            return [cls._strip_evidence(item, deleted) for item in value]
        if not isinstance(value, dict):
            return value
        output = {}
        for key, item in value.items():
            if key == "evidence_refs" and isinstance(item, list | tuple):
                output[key] = [
                    cls._strip_evidence(evidence, deleted)
                    for evidence in item
                    if isinstance(evidence, Mapping)
                    and evidence.get("source_id") not in deleted
                ]
            else:
                output[key] = cls._strip_evidence(item, deleted)
        return output

    @classmethod
    def _fully_deleted(cls, item: Mapping[str, Any], deleted: frozenset[str]) -> bool:
        evidence = item.get("evidence_refs")
        return bool(evidence) and cls._all_evidence_ids(evidence).issubset(deleted)

    @classmethod
    def _all_evidence_ids(cls, value: Any) -> frozenset[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            if isinstance(value.get("source_id"), str) and "quote" in value:
                found.add(value["source_id"])
            for item in value.values():
                found.update(cls._all_evidence_ids(item))
        elif isinstance(value, list | tuple):
            for item in value:
                found.update(cls._all_evidence_ids(item))
        return frozenset(found)

    @staticmethod
    def _gap_ids(analysis: GapAnalysis) -> frozenset[str]:
        return frozenset(
            f"{item.gap_type}:{item.requirement_id}" for item in analysis.prioritized_gaps
        )

    @staticmethod
    def _dimension_deltas(before: Any, after: Any) -> tuple[DimensionDelta, ...]:
        before_by_name = {item.dimension: item for item in before.dimension_scores}
        after_by_name = {item.dimension: item for item in after.dimension_scores}
        return tuple(
            DimensionDelta(
                dimension=name,
                baseline_score=before_by_name[name].score,
                scenario_score=after_by_name[name].score,
                delta=ExplanationDeletionService._delta(
                    before_by_name[name].score, after_by_name[name].score
                ),
            )
            for name in sorted(before_by_name.keys() & after_by_name.keys())
        )

    @staticmethod
    def _normalized_drop(before: float | None, after: float | None) -> float | None:
        if before is None or after is None:
            return None
        return round(max(0.0, before - after) / max(abs(before), 1.0), 6)

    @staticmethod
    def _delta(before: float | None, after: float | None) -> float | None:
        return round(after - before, 4) if before is not None and after is not None else None

    @staticmethod
    def _run_id(
        baseline: MatchEvaluation,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        deletion_kind: str,
        deleted_ids: tuple[str, ...],
        *,
        target_type: str,
        use_enterprise_weights: bool,
    ) -> str:
        material = "|".join(
            (
                "evidence-deletion-recompute.v1",
                baseline.evaluation_id,
                cv.profile_version or cv.source_version,
                position.profile_version or position.source_version,
                position.graph_version,
                deletion_kind,
                target_type,
                str(use_enterprise_weights).lower(),
                *deleted_ids,
            )
        )
        return "deletion_" + sha256(material.encode("utf-8")).hexdigest()[:20]

    def _rejected(
        self,
        code: str,
        message: str,
        run_id: str = "deletion_rejected",
    ) -> EvidenceDeletionResult:
        return EvidenceDeletionResult(
            generation_status="rejected",
            deletion_run_id=run_id,
            stability_threshold_points=self._stability_threshold,
            error_code=code,
            error_message=message,
        )
