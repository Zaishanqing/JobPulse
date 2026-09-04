"""Synchronous v1 What-if orchestration over the frozen formal scorer."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from pydantic import ValidationError

from app.application.evaluation import MatchEvaluationService
from app.domain.evaluation import FinalMatchResult, MatchEvaluation
from app.domain.gaps import SkillPathEdge, SkillTransferPath
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.what_if import (
    ActionSetValidator,
    DimensionDelta,
    WhatIfAction,
    WhatIfActionSetError,
    WhatIfResult,
    apply_actions,
    as_projected_actions,
)
from app.ports.skill_relations import SkillTransferPathResolver
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError

_TRANSFER_RELATION_TYPES = frozenset({"equivalent", "parent_child", "transferable"})
_TRANSFER_SOURCE_VERIFICATION = frozenset(
    {"supported", "partially_supported", "experience_only"}
)
_TRANSFER_CONFIDENCE_ALGORITHM = "controlled-skill-transfer-confidence.v2"


class WhatIfService:
    def __init__(
        self,
        evaluation_service: MatchEvaluationService,
        *,
        transfer_path_resolver: SkillTransferPathResolver | None = None,
    ) -> None:
        self._evaluation_service = evaluation_service
        self._transfer_path_resolver = transfer_path_resolver

    def evaluate(self, payload: object) -> WhatIfResult:
        if not isinstance(payload, Mapping):
            return self._rejected("WHAT_IF_REQUEST_INVALID", "request must be an object")
        try:
            cv = CVMatchProfile.model_validate(payload.get("cv_profile"))
            position = PositionMatchProfile.model_validate(payload.get("position_profile"))
            raw_actions = payload.get("actions", ())
            actions = tuple(WhatIfAction.model_validate(item) for item in raw_actions)
            supplied_baseline = (
                MatchEvaluation.model_validate(payload.get("baseline_evaluation"))
                if payload.get("baseline_evaluation") is not None
                else None
            )
        except (ValidationError, TypeError):
            return self._rejected("WHAT_IF_INPUT_INVALID", "profiles or actions are invalid")
        try:
            actions = ActionSetValidator.validate(actions)
        except WhatIfActionSetError as exc:
            return self._rejected(exc.code, str(exc))
        target_type = payload.get("target_type", "standard_position")
        use_enterprise_weights = payload.get("use_enterprise_weights", False)
        if target_type not in {"standard_position", "enterprise_job"}:
            return self._rejected(
                "WHAT_IF_TARGET_TYPE_INVALID",
                "target_type must be standard_position or enterprise_job",
            )
        if not isinstance(use_enterprise_weights, bool):
            return self._rejected(
                "WHAT_IF_ENTERPRISE_WEIGHTS_INVALID",
                "use_enterprise_weights must be boolean",
            )
        try:
            actions = self._validate_controlled_transfers(cv, position, actions)
        except WhatIfActionSetError as exc:
            return self._rejected(exc.code, str(exc))
        common = {
            "position_profile": position.model_dump(mode="python"),
            "target_type": target_type,
            "use_enterprise_weights": use_enterprise_weights,
        }
        recomputed_baseline = self._evaluation_service.evaluate(
            {**common, "cv_profile": cv.model_dump(mode="python")},
            include_semantic=False,
        )
        if recomputed_baseline.evaluation_status != "completed":
            return self._rejected(
                recomputed_baseline.error_code or "WHAT_IF_EVALUATION_REJECTED",
                "baseline evaluation was rejected",
            )
        baseline = supplied_baseline or recomputed_baseline
        if supplied_baseline is not None and not self._baseline_matches(
            supplied_baseline, recomputed_baseline, cv, position
        ):
            return self._rejected(
                "WHAT_IF_BASELINE_MISMATCH",
                "baseline evaluation does not match the supplied profiles and scoring policy",
            )
        try:
            actions = ActionSetValidator.validate(
                actions,
                known_requirement_ids=self._known_requirement_ids(recomputed_baseline),
                known_hard_condition_ids=frozenset(
                    item.requirement_id
                    for item in recomputed_baseline.hard_constraint_results
                    if item.status != "not_required"
                ),
            )
        except WhatIfActionSetError as exc:
            return self._rejected(exc.code, str(exc))
        try:
            resolution = ActionSetValidator.resolve(actions)
        except WhatIfActionSetError as exc:
            return self._rejected(exc.code, str(exc))
        active_actions = resolution.active_actions
        scenario_id = self._scenario_id(
            baseline,
            position,
            active_actions,
            target_type=target_type,
            use_enterprise_weights=use_enterprise_weights,
        )
        scenario_cv = apply_actions(cv, position, active_actions, scenario_id=scenario_id)
        scenario = self._evaluation_service.evaluate(
            {**common, "cv_profile": scenario_cv.model_dump(mode="python")},
            include_semantic=False,
        )
        if scenario.evaluation_status != "completed":
            code = scenario.error_code or "WHAT_IF_EVALUATION_REJECTED"
            return self._rejected(code, "baseline or scenario evaluation was rejected", scenario_id)
        before = baseline.final_match_result
        after = scenario.final_match_result
        if before is None or after is None:
            return self._rejected("WHAT_IF_SCORE_MISSING", "formal score is missing", scenario_id)
        prospective = any(
            action.milestone_status not in {"demonstrated", "verified"}
            or (
                action.action_type == "controlled_skill_transfer"
                and action.score_credit_allowed is False
            )
            for action in active_actions
        )
        projected_actions = as_projected_actions(active_actions)
        projected_score = after.overall_score
        projected_score_delta = self._delta(before.overall_score, after.overall_score)
        projected_confidence = after.match_confidence
        projected_recommendation = after.recommendation_level
        projected_hard_gate = after.hard_gate_status
        if prospective:
            projected_cv = apply_actions(
                cv,
                position,
                projected_actions,
                scenario_id=f"{scenario_id}|projected",
            )
            covered_fields = {
                field
                for action in projected_actions
                for field in (
                    ("project_experience",)
                    if action.action_type == "add_project_experience"
                    else (
                        ("skills",)
                        if action.action_type == "add_skill"
                        else ()
                    )
                )
            }
            if covered_fields:
                projected_cv = projected_cv.model_copy(
                    update={
                        "unresolved_items": tuple(
                            item
                            for item in projected_cv.unresolved_items
                            if str(item.raw_value).casefold()
                            not in covered_fields
                        )
                    }
                )
            projected = self._evaluation_service.evaluate(
                {**common, "cv_profile": projected_cv.model_dump(mode="python")},
                include_semantic=False,
            )
            if projected.evaluation_status != "completed":
                code = projected.error_code or "WHAT_IF_PROJECTED_REJECTED"
                return self._rejected(
                    code,
                    "projected scenario evaluation was rejected",
                    scenario_id,
                )
            projected_final = projected.final_match_result
            if projected_final is None:
                return self._rejected(
                    "WHAT_IF_PROJECTED_SCORE_MISSING",
                    "projected formal score is missing",
                    scenario_id,
                )
            projected_score = projected_final.overall_score
            projected_score_delta = self._delta(
                before.overall_score, projected_final.overall_score
            )
            projected_confidence = projected_final.match_confidence
            projected_recommendation = projected_final.recommendation_level
            projected_hard_gate = projected_final.hard_gate_status
        dimensions_before = {item.dimension: item for item in before.dimension_scores}
        dimensions_after = {item.dimension: item for item in after.dimension_scores}
        deltas = tuple(
            DimensionDelta(
                dimension=name,
                baseline_score=dimensions_before[name].score,
                scenario_score=dimensions_after[name].score,
                delta=(
                    round(dimensions_after[name].score - dimensions_before[name].score, 4)
                    if dimensions_before[name].score is not None
                    and dimensions_after[name].score is not None
                    else None
                ),
            )
            for name in sorted(dimensions_before)
        )
        modeled_score_delta = self._delta(before.overall_score, after.overall_score)
        modeled_confidence_delta = round(
            after.match_confidence - before.match_confidence, 6
        )
        return WhatIfResult(
            generation_status="completed",
            scenario_id=scenario_id,
            baseline_evaluation=baseline,
            scenario_evaluation=scenario,
            projected_evaluation=(projected if prospective else None),
            actions=active_actions,
            baseline_score=before.overall_score,
            modeled_final_score=after.overall_score,
            modeled_score_delta=modeled_score_delta,
            modeled_confidence_delta=modeled_confidence_delta,
            scenario_score=after.overall_score,
            score_delta=modeled_score_delta,
            baseline_confidence=before.match_confidence,
            scenario_confidence=after.match_confidence,
            confidence_delta=modeled_confidence_delta,
            baseline_recommendation=before.recommendation_level,
            scenario_recommendation=after.recommendation_level,
            baseline_hard_gate_status=before.hard_gate_status,
            scenario_hard_gate_status=after.hard_gate_status,
            projected_if_completed=prospective,
            projected_actions=projected_actions,
            projected_score=projected_score,
            projected_score_delta=projected_score_delta,
            projected_confidence=projected_confidence,
            projected_recommendation=projected_recommendation,
            projected_hard_gate_status=projected_hard_gate,
            current_verified_outcome=before.recommendation_level,
            projected_if_completed_outcome=projected_recommendation,
            dimension_deltas=deltas,
            denominator_changed=any(
                dimensions_before[name].scored_count != dimensions_after[name].scored_count
                or dimensions_before[name].effective_weight
                != dimensions_after[name].effective_weight
                for name in dimensions_before
            ),
            score_effect_status="modeled",
            baseline_evaluation_id=baseline.evaluation_id,
            scoring_algorithm_version=before.algorithm_version,
            scoring_config_version=before.scoring_config_version,
            position_graph_version=position.graph_version,
            target_type=(
                target_type
                if target_type in {"standard_position", "enterprise_job"}
                else None
            ),
            use_enterprise_weights=(
                use_enterprise_weights
                if isinstance(use_enterprise_weights, bool)
                else None
            ),
        )

    def _validate_controlled_transfers(
        self,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        actions: tuple[WhatIfAction, ...],
    ) -> tuple[WhatIfAction, ...]:
        if not any(
            action.action_type == "controlled_skill_transfer" for action in actions
        ):
            return actions
        if self._transfer_path_resolver is None:
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_RESOLVER_UNAVAILABLE",
                "controlled_skill_transfer requires a SkillTransferPathResolver",
            )
        output: list[WhatIfAction] = []
        for action in actions:
            if action.action_type != "controlled_skill_transfer":
                output.append(action)
                continue
            if len(action.path_refs) != len(set(action.path_refs)):
                raise WhatIfActionSetError(
                    "WHAT_IF_TRANSFER_PATH_DUPLICATE",
                    f"action {action.action_id} repeats a path_ref",
                )
            try:
                resolved = self._transfer_path_resolver.resolve_paths(
                    action.path_refs,
                    graph_version=action.graph_version or "",
                )
            except (UpstreamResponseError, UpstreamTimeoutError) as exc:
                raise WhatIfActionSetError(
                    "WHAT_IF_TRANSFER_PATH_RESOLVER_ERROR",
                    f"graph path resolver rejected action {action.action_id}: {exc}",
                ) from exc
            output.append(
                self._annotate_transfer_action(cv, position, action, resolved)
            )
        return tuple(output)

    @staticmethod
    def _annotate_transfer_action(
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        action: WhatIfAction,
        resolved_paths: tuple[SkillTransferPath, ...],
    ) -> WhatIfAction:
        by_id = {item.path_id: item for item in resolved_paths}
        missing = [ref for ref in action.path_refs if ref not in by_id]
        if missing:
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_PATH_UNRESOLVED",
                f"action {action.action_id} references unknown path_ref: "
                + ", ".join(missing),
            )
        if action.graph_version != position.graph_version:
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_GRAPH_VERSION_MISMATCH",
                f"action {action.action_id} graph_version does not match the "
                "position snapshot",
            )
        source = next(
            (
                item
                for item in cv.capability_profiles
                if item.skill_id == action.source_skill_id
            ),
            None,
        )
        if source is None:
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_SOURCE_MISSING",
                f"action {action.action_id} source skill is not in the CV "
                "capability profile",
            )
        if (
            source.resolution_status != "resolved"
            or source.verification_status not in _TRANSFER_SOURCE_VERIFICATION
            or source.demonstrated_level == "unknown"
            or source.support_confidence <= 0
        ):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_SOURCE_NOT_OBSERVED",
                f"action {action.action_id} source capability is not observed",
            )
        validated_refs = tuple(sorted(action.path_refs))
        edges: list[SkillPathEdge] = []
        for ref in validated_refs:
            edges.extend(
                WhatIfService._validated_transfer_edges(action, by_id[ref])
            )
        paths = tuple(by_id[ref] for ref in validated_refs)
        edge_confidences = tuple(item.confidence for item in edges)
        path_quality = min(path.effective_confidence for path in paths)
        source_confidence = source.support_confidence
        target_confidence = round(source_confidence * path_quality, 6)
        hop_count = max(path.hop_count for path in paths)
        outcome_status = "partial" if hop_count == 2 else "eligible"
        relation_types = tuple(dict.fromkeys(item.relation_type for item in edges))
        return action.model_copy(
            update={
                "confidence_basis": (
                    f"{_TRANSFER_CONFIDENCE_ALGORITHM}:"
                    f"source_confidence({source_confidence})*"
                    f"effective_path_confidence({path_quality})"
                ),
                "source_confidence": source_confidence,
                "path_quality": path_quality,
                "edge_confidences": edge_confidences,
                "validated_path_refs": validated_refs,
                "target_confidence": target_confidence,
                "confidence_algorithm_version": _TRANSFER_CONFIDENCE_ALGORITHM,
                "transfer_hop_count": hop_count,
                "transfer_outcome_status": outcome_status,
                "transfer_relation_types": relation_types,
                "score_credit_allowed": all(
                    path.score_credit_allowed for path in paths
                ),
                "suitable_for_learning": True,
            }
        )

    @staticmethod
    def _validated_transfer_edges(
        action: WhatIfAction, path: SkillTransferPath
    ) -> tuple[SkillPathEdge, ...]:
        edges = path.edges
        if (
            not edges
            or path.hop_count != len(edges)
            or tuple(item.hop_number for item in edges)
            != tuple(range(1, len(edges) + 1))
        ):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_PATH_DISCONNECTED",
                f"action {action.action_id} path {path.path_id} has invalid hops",
            )
        nodes = tuple(item.source_skill_id for item in edges) + (
            edges[-1].target_skill_id,
        )
        if path.node_skill_ids != nodes:
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_PATH_DISCONNECTED",
                f"action {action.action_id} path {path.path_id} node ids are inconsistent",
            )
        if (
            path.source_skill_id != action.source_skill_id
            or edges[0].source_skill_id != action.source_skill_id
        ):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_ENDPOINT_MISMATCH",
                f"action {action.action_id} path {path.path_id} starts at the wrong skill",
            )
        if (
            path.target_skill_id != action.skill_id
            or edges[-1].target_skill_id != action.skill_id
        ):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_ENDPOINT_MISMATCH",
                f"action {action.action_id} path {path.path_id} ends at the wrong skill",
            )
        if any(
            previous.target_skill_id != current.source_skill_id
            for previous, current in zip(edges, edges[1:], strict=False)
        ):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_PATH_DISCONNECTED",
                f"action {action.action_id} path {path.path_id} has a discontinuity",
            )
        if any(item.relation_type not in _TRANSFER_RELATION_TYPES for item in edges):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_RELATION_TYPE_INVALID",
                f"action {action.action_id} path {path.path_id} uses a non-transfer relation",
            )
        if any(
            item.graph_version != action.graph_version
            or path.graph_version_id != action.graph_version
            for item in edges
        ):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_GRAPH_VERSION_MISMATCH",
                f"action {action.action_id} path {path.path_id} mixes graph versions",
            )
        if any(not item.evidence_refs for item in edges):
            raise WhatIfActionSetError(
                "WHAT_IF_TRANSFER_RELATION_EVIDENCE_MISSING",
                f"action {action.action_id} path {path.path_id} has an evidence-free edge",
            )
        return edges

    @staticmethod
    def _scenario_id(
        baseline: MatchEvaluation,
        position: PositionMatchProfile,
        actions: tuple[WhatIfAction, ...],
        *,
        target_type: object,
        use_enterprise_weights: object,
    ) -> str:
        final = baseline.final_match_result
        material = "|".join(
            (
                "counterfactual-profile.v2",
                baseline.evaluation_id,
                baseline.algorithm_version,
                final.algorithm_version if final else "scoring-unavailable",
                final.scoring_config_version if final else "config-unavailable",
                position.profile_id or position.position_id,
                position.profile_version or position.source_version,
                position.graph_version,
                (
                    position.requirement_graph.model_dump_json()
                    if position.requirement_graph is not None
                    else "requirement-graph:none"
                ),
                str(target_type),
                str(use_enterprise_weights).lower(),
                *(
                    item.model_dump_json(
                        exclude={
                            "estimated_score_delta",
                            "estimated_utility",
                            "score_effect_reason",
                            "confidence_basis",
                            "source_confidence",
                            "path_quality",
                            "edge_confidences",
                            "validated_path_refs",
                            "target_confidence",
                            "confidence_algorithm_version",
                        }
                    )
                    for item in sorted(actions, key=lambda value: value.action_id)
                ),
            )
        )
        return "scenario_" + sha256(material.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _known_requirement_ids(evaluation: MatchEvaluation) -> frozenset[str]:
        return frozenset(
            item.requirement_id
            for results in (
                evaluation.hard_constraint_results,
                evaluation.skill_results,
                evaluation.responsibility_results,
                evaluation.project_results,
                evaluation.scenario_results,
            )
            for item in results
        ) | frozenset(item.group_id for item in evaluation.requirement_group_results)

    @classmethod
    def _baseline_matches(
        cls,
        supplied: MatchEvaluation,
        recomputed: MatchEvaluation,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
    ) -> bool:
        if supplied.evaluation_status != "completed":
            return False
        if (
            supplied.cv_profile_id != cv.profile_id
            or supplied.cv_profile_version != cv.profile_version
            or supplied.position_profile_id != position.profile_id
            or supplied.position_profile_version != position.profile_version
            or supplied.algorithm_version != recomputed.algorithm_version
        ):
            return False
        supplied_final = supplied.final_match_result
        recomputed_final = recomputed.final_match_result
        if supplied_final is None or recomputed_final is None:
            return False
        if supplied_final.source_evaluation_id != supplied.evaluation_id:
            return False
        return cls._formal_snapshot(supplied_final) == cls._formal_snapshot(
            recomputed_final
        )

    @staticmethod
    def _formal_snapshot(result: FinalMatchResult) -> dict[str, object]:
        snapshot = result.model_dump(mode="python")
        snapshot.pop("source_evaluation_id", None)
        # Semantic retrieval is an optional shadow channel. What-if binds to the
        # frozen deterministic score and therefore excludes semantic metadata.
        for key in tuple(snapshot):
            if key.startswith("semantic_"):
                snapshot.pop(key)
        return snapshot

    @staticmethod
    def _delta(before: float | None, after: float | None) -> float | None:
        return round(after - before, 4) if before is not None and after is not None else None

    @staticmethod
    def _rejected(code: str, message: str, scenario_id: str = "scenario_rejected") -> WhatIfResult:
        return WhatIfResult(
            generation_status="rejected",
            scenario_id=scenario_id,
            error_code=code,
            error_message=message,
        )
