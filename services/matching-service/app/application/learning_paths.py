"""Application orchestration and freshness gates for learning-path generation."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from app.application.evaluation import MatchEvaluationService
from app.application.learning_catalog import lookup_learning_asset
from app.application.route_planning import LearningRoutePlanner
from app.application.skill_paths import ControlledSkillPathPlanner
from app.application.what_if import WhatIfService
from app.domain.evaluation import MatchEvaluation
from app.domain.gap_analysis import GapAnalysisConfig, build_gap_analysis, gap_policy_hash
from app.domain.gaps import (
    GapAnalysis,
    LearningRoute,
    LearningStep,
    MinimalActionSet,
    PrerequisiteState,
    ProfileReferences,
)
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.skill_relations import SkillRelation
from app.domain.what_if import WhatIfAction


class LearningPathService:
    def __init__(
        self,
        evaluation_service: MatchEvaluationService | None = None,
        config: GapAnalysisConfig | None = None,
        *,
        expected_evaluation_algorithm: str = "deterministic-matching.v9",
        expected_scoring_algorithm: str = "explainable-scoring.v4",
        expected_scoring_config: str = "scoring-config.v3",
    ) -> None:
        self._evaluation_service = evaluation_service or MatchEvaluationService()
        self._route_planner = LearningRoutePlanner(WhatIfService(self._evaluation_service))
        self._skill_path_planner = ControlledSkillPathPlanner()
        self._config = config or GapAnalysisConfig()
        self._expected_evaluation_algorithm = expected_evaluation_algorithm
        self._expected_scoring_algorithm = expected_scoring_algorithm
        self._expected_scoring_configs = frozenset(
            {expected_scoring_config, "scoring-config.enterprise.v3"}
        )

    def generate(
        self,
        payload: object,
        *,
        include_route_scenarios: bool = True,
    ) -> GapAnalysis:
        if not isinstance(payload, Mapping):
            return self._rejected("LEARNING_PATH_REQUEST_INVALID", "request must be an object")
        if (
            "use_enterprise_weights" in payload
            and not isinstance(payload["use_enterprise_weights"], bool)
        ):
            return self._rejected(
                "LEARNING_PATH_OPTION_INVALID",
                "use_enterprise_weights must be boolean",
            )
        has_evaluation = "evaluation" in payload
        has_profiles = "cv_profile" in payload or "position_profile" in payload
        cv: CVMatchProfile | None = None
        position: PositionMatchProfile | None = None
        if has_profiles:
            try:
                cv = CVMatchProfile.model_validate(payload.get("cv_profile"))
                position = PositionMatchProfile.model_validate(payload.get("position_profile"))
            except ValidationError:
                return self._rejected(
                    "LEARNING_PATH_PROFILE_INVALID", "both profile contracts are required"
                )
        if has_evaluation:
            try:
                evaluation = MatchEvaluation.model_validate(payload.get("evaluation"))
            except ValidationError:
                return self._rejected("MATCH_EVALUATION_INVALID", "evaluation contract is invalid")
            if (
                cv is not None
                and position is not None
                and (
                    evaluation.cv_profile_id != cv.profile_id
                    or evaluation.position_profile_id != position.profile_id
                    or evaluation.cv_profile_version != cv.profile_version
                    or evaluation.position_profile_version != position.profile_version
                )
            ):
                return self._rejected(
                    "EVALUATION_PROFILE_MISMATCH",
                    "evaluation and profile identifiers must match",
                    ProfileReferences(
                        cv_profile_id=evaluation.cv_profile_id,
                        position_profile_id=evaluation.position_profile_id,
                    ),
                )
        elif cv is not None and position is not None:
            evaluation = self._evaluation_service.evaluate(
                {
                    "cv_profile": cv.model_dump(mode="python"),
                    "position_profile": position.model_dump(mode="python"),
                    "tenant_ref": payload.get("tenant_ref"),
                    "target_type": payload.get("target_type", "standard_position"),
                }
            )
        else:
            return self._rejected(
                "LEARNING_PATH_INPUT_MISSING",
                "evaluation or both cv_profile and position_profile are required",
            )
        rejection = self._validate_evaluation(evaluation)
        if rejection is not None:
            return rejection
        time_budget = payload.get("time_budget_hours")
        if time_budget is not None and (
            not isinstance(time_budget, int | float) or time_budget <= 0
        ):
            return self._rejected(
                "TIME_BUDGET_INVALID",
                "time_budget_hours must be a positive number",
                ProfileReferences(
                    cv_profile_id=evaluation.cv_profile_id,
                    position_profile_id=evaluation.position_profile_id,
                ),
            )
        analysis = build_gap_analysis(
            evaluation,
            self._config,
            time_budget_hours=float(time_budget) if time_budget is not None else None,
            include_learning_steps=False,
        )
        if cv is not None and position is not None:
            skill_path_decisions = self._skill_path_planner.plan(
                cv,
                analysis.prioritized_gaps,
                self._fetch_skill_relations,
                graph_enabled=position.graph_mode != "disabled",
                expected_graph_version=position.graph_version,
            )
            if include_route_scenarios:
                actions, routes, minimal_action_set = self._route_planner.plan(
                    cv,
                    position,
                    evaluation,
                    analysis,
                    time_budget_hours=(
                        float(time_budget) if time_budget is not None else None
                    ),
                    target_type=str(payload.get("target_type", "standard_position")),
                    use_enterprise_weights=bool(
                        payload.get("use_enterprise_weights", False)
                    ),
                    skill_path_decisions=skill_path_decisions,
                )
            else:
                # Faithfulness diagnostics only compare deterministic Gap and
                # candidate Action changes. Route optimization is independent
                # output and can require hundreds of scenario evaluations.
                actions = self._route_planner.candidate_actions(
                    position,
                    analysis,
                    skill_path_decisions=skill_path_decisions,
                    evaluation=evaluation,
                )
                routes = ()
                minimal_action_set = None
            # The Learning Catalog upgrades display content (title, deliverable
            # and acceptance criteria) only. Planning decisions, costs, order,
            # dependencies, gains and the minimal action set are already fixed
            # at this point and are never re-derived.
            actions = self._enrich_catalog_actions(actions, evaluation)
            analysis = analysis.model_copy(
                update={
                    "candidate_actions": actions,
                    "learning_routes": routes,
                    "minimal_action_set": minimal_action_set,
                    "skill_path_decisions": skill_path_decisions,
                    "learning_path": self._learning_steps_from_actions(
                        actions, routes, minimal_action_set, evaluation
                    ),
                }
            )
            analysis = analysis.model_copy(
                update={
                    "learning_path": self._annotate_prerequisites(
                        analysis.learning_path, cv, evaluation
                    )
                }
            )
        return analysis

    @staticmethod
    def _enrich_catalog_actions(
        actions: tuple[WhatIfAction, ...],
        evaluation: MatchEvaluation,
    ) -> tuple[WhatIfAction, ...]:
        """Apply deterministic catalog content to already-planned actions.

        A miss keeps the action exactly as planned, so unconfigured skills
        always fall back to the existing planner templates.  Only title,
        deliverable and acceptance criteria may change; costs, dependencies,
        order and score deltas are untouched.
        """
        skill_names = {
            item.skill_id: item.skill_name
            for item in evaluation.skill_results
            if item.skill_id and item.skill_name
        }
        output = []
        for action in actions:
            asset = lookup_learning_asset(
                skill_id=action.skill_id,
                canonical_name=skill_names.get(action.skill_id),
                stage=action.stage,
                target_level=action.target_level,
            )
            if asset is None:
                output.append(action)
                continue
            updates: dict[str, object] = {}
            if asset.title:
                updates["learning_title"] = asset.title
            if asset.deliverable:
                updates["deliverable"] = asset.deliverable
            if asset.acceptance_criteria:
                updates["acceptance_criteria"] = asset.acceptance_criteria
            output.append(action.model_copy(update=updates))
        return tuple(output)

    @staticmethod
    def catalog_coverage(
        actions: tuple[WhatIfAction, ...],
        evaluation: MatchEvaluation,
    ) -> dict[str, float | int]:
        """Deterministic Catalog hit/fallback statistics for a path.

        Useful for demo coverage checks: how many learning actions hit a
        concrete Learning Catalog asset, how many fall back to planner
        templates, and the hit rate. Never changes planning output.
        """
        skill_names = {
            item.skill_id: item.skill_name
            for item in evaluation.skill_results
            if item.skill_id and item.skill_name
        }
        hits = 0
        fallbacks = 0
        for action in actions:
            asset = lookup_learning_asset(
                skill_id=action.skill_id,
                canonical_name=skill_names.get(action.skill_id),
                stage=action.stage,
                target_level=action.target_level,
            )
            if asset is None:
                fallbacks += 1
            else:
                hits += 1
        total = hits + fallbacks
        return {
            "total_actions": total,
            "catalog_hits": hits,
            "fallback_count": fallbacks,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }

    def _fetch_skill_relations(
        self, skill_ids: tuple[str, ...]
    ) -> tuple[SkillRelation, ...] | None:
        """Keep evaluation test doubles and disabled graph sources explicit."""
        fetch = getattr(self._evaluation_service, "fetch_skill_relations", None)
        if not callable(fetch):
            return None
        return fetch(skill_ids)

    @staticmethod
    def _learning_steps_from_actions(
        actions: tuple[WhatIfAction, ...],
        routes: tuple[LearningRoute, ...],
        minimal_action_set: MinimalActionSet | None,
        evaluation: MatchEvaluation,
    ) -> tuple[LearningStep, ...]:
        """Build the formal Learning Path from the selected Minimal Action Set.

        A gap-only fixed-hour template must not be used here. Costs, targets,
        completion criteria and identity all come from the same action records
        that were used for route/minimal selection.
        """
        if minimal_action_set is None:
            return ()
        selected_action_ids = minimal_action_set.selected_action_ids
        if not selected_action_ids:
            preferred_route = next(
                (item for item in routes if item.route_type == "budget_max_gain"),
                routes[0] if routes else None,
            )
            selected_action_ids = (
                preferred_route.action_ids if preferred_route is not None else ()
            )
        if not selected_action_ids:
            return ()
        by_id = {item.action_id: item for item in actions}
        selected: dict[str, WhatIfAction] = {}

        def add(action_id: str) -> None:
            action = by_id.get(action_id)
            if action is None or action.action_id in selected:
                return
            for required_id in action.requires_action_ids:
                add(required_id)
            selected[action.action_id] = action

        for action_id in selected_action_ids:
            add(action_id)
        order = {item.action_id: index for index, item in enumerate(actions)}
        ordered = tuple(
            sorted(selected.values(), key=lambda item: order[item.action_id])
        )
        output = []
        for step_order, action in enumerate(ordered, 1):
            requirement_ids = action.target_requirement_ids or (action.action_id,)
            target_skills = tuple(
                by_id[required_id].skill_id or required_id
                for required_id in action.requires_action_ids
                if required_id in by_id
            )
            target_name = (
                action.deliverable
                or action.skill_id
                or " ".join(requirement_ids)
            )
            criteria = action.acceptance_criteria
            if not criteria:
                criteria = tuple(
                    f"evidence_linked:{requirement_id}"
                    for requirement_id in requirement_ids
                )
            output.append(
                LearningStep(
                    step_order=step_order,
                    source_action_id=action.action_id,
                    target_skill_id=action.skill_id,
                    objective=target_name,
                    prerequisite_skill_ids=target_skills,
                    basis=(
                        f"action:{action.action_id}",
                        f"cost_model:{action.cost_model}",
                        *(f"evidence:{ref}" for ref in action.path_refs),
                    ),
                    estimated_hours=action.estimated_hours,
                    cost_source_type="heuristic",
                    cost_source_ref=action.cost_model,
                    estimate_status="estimated",
                    cost_model=action.cost_model,
                    completion_criteria=criteria,
                    source_requirement_ids=requirement_ids,
                    reason_codes=(
                        "ACTION_SELECTED",
                        *(action.score_effect_reason or "MODELED_SCORE_CHANGE",),
                    ),
                )
            )
        return tuple(output)

    @staticmethod
    def _annotate_prerequisites(
        steps: tuple[LearningStep, ...],
        cv: CVMatchProfile | None,
        evaluation: MatchEvaluation,
    ) -> tuple[LearningStep, ...]:
        """Expose external prerequisites instead of treating them as satisfied."""
        planned_targets = {item.target_skill_id for item in steps if item.target_skill_id}
        capabilities = {
            item.skill_id: item
            for item in (cv.capability_profiles if cv is not None else ())
            if item.skill_id is not None
        }
        links = {
            item.link_id: item for item in (cv.capability_evidence_links if cv is not None else ())
        }
        results = {
            item.skill_id: item for item in evaluation.skill_results if item.skill_id is not None
        }
        output = []
        for step in steps:
            states = []
            for skill_id in step.prerequisite_skill_ids:
                if skill_id in planned_targets:
                    continue
                capability = capabilities.get(skill_id)
                if cv is not None and capability is None:
                    state = PrerequisiteState(
                        skill_id=skill_id,
                        status="missing",
                        source="candidate_profile",
                    )
                elif capability is not None:
                    evidence = tuple(
                        evidence
                        for link_id in capability.evidence_link_ids
                        if (link := links.get(link_id)) is not None
                        for evidence in link.evidence_refs
                    )
                    satisfied = (
                        capability.resolution_status == "resolved"
                        and capability.verification_status
                        in {"supported", "partially_supported", "experience_only"}
                        and capability.demonstrated_level != "unknown"
                        and bool(evidence)
                    )
                    state = PrerequisiteState(
                        skill_id=skill_id,
                        status="satisfied" if satisfied else "unknown",
                        source="candidate_profile",
                        evidence_refs=evidence,
                    )
                else:
                    result = results.get(skill_id)
                    if result is None:
                        status = "unknown"
                        evidence = ()
                    elif result.match_status == "missing":
                        status = "missing"
                        evidence = result.candidate_evidence
                    elif (
                        result.match_status in {"matched", "partial", "weak"}
                        and result.candidate_evidence
                    ):
                        status = "satisfied"
                        evidence = result.candidate_evidence
                    else:
                        status = "unknown"
                        evidence = result.candidate_evidence
                    state = PrerequisiteState(
                        skill_id=skill_id,
                        status=status,
                        source="evaluation" if result is not None else "unavailable",
                        evidence_refs=evidence,
                    )
                states.append(state)
            blocked = tuple(
                sorted(
                    {
                        f"PREREQUISITE_{item.status.upper()}"
                        for item in states
                        if item.status != "satisfied"
                    }
                )
            )
            output.append(
                step.model_copy(
                    update={
                        "prerequisite_states": tuple(states),
                        "planning_status": "blocked" if blocked else step.planning_status,
                        "blocked_reason_codes": tuple(
                            dict.fromkeys((*step.blocked_reason_codes, *blocked))
                        ),
                    }
                )
            )
        return tuple(output)

    def _validate_evaluation(self, evaluation: MatchEvaluation) -> GapAnalysis | None:
        references = ProfileReferences(
            cv_profile_id=evaluation.cv_profile_id,
            position_profile_id=evaluation.position_profile_id,
        )
        if evaluation.evaluation_status != "completed":
            return self._rejected(
                "MATCH_EVALUATION_REJECTED",
                "rejected or incomplete evaluations cannot generate a learning path",
                references,
            )
        final = evaluation.final_match_result
        if final is None:
            return self._rejected(
                "FINAL_MATCH_RESULT_MISSING",
                "a completed FinalMatchResult is required",
                references,
            )
        if (
            final.cv_profile_id != evaluation.cv_profile_id
            or final.position_profile_id != evaluation.position_profile_id
            or final.input_evaluation_algorithm_version != evaluation.algorithm_version
            or final.source_evaluation_id != evaluation.evaluation_id
        ):
            return self._rejected(
                "EVALUATION_STALE",
                "evaluation versions or source algorithm no longer match",
                references,
            )
        if (
            evaluation.algorithm_version != self._expected_evaluation_algorithm
            or final.algorithm_version != self._expected_scoring_algorithm
            or final.scoring_config_version not in self._expected_scoring_configs
        ):
            return self._rejected(
                "EVALUATION_VERSION_INCOMPATIBLE",
                "evaluation or scoring version is not supported",
                references,
            )
        semantic_fields_match = (
            final.vector_text_derivation_version == evaluation.vector_text_derivation_version
            and final.embedding_model == evaluation.embedding_model
            and final.embedding_version == evaluation.embedding_version
            and final.semantic_algorithm_version == evaluation.semantic_algorithm_version
            and final.semantic_threshold_config_version == evaluation.threshold_config_version
        )
        semantic_results = tuple(
            item
            for item in (
                evaluation.responsibility_results
                + evaluation.project_results
                + evaluation.scenario_results
            )
            if item.match_type in {"semantic", "semantic_candidate"}
        )
        semantic_metadata_complete = not semantic_results or (
            evaluation.vector_profile_version == evaluation.cv_profile_version
            and evaluation.vector_text_derivation_version is not None
            and evaluation.embedding_model is not None
            and evaluation.embedding_version is not None
            and evaluation.semantic_algorithm_version is not None
            and evaluation.threshold_config_version is not None
            and all(
                item.embedding_model == evaluation.embedding_model
                and item.embedding_version == evaluation.embedding_version
                for item in semantic_results
            )
        )
        if not semantic_fields_match or not semantic_metadata_complete:
            return self._rejected(
                "SEMANTIC_VERSION_INCOMPATIBLE",
                "semantic result metadata is missing or version-incompatible",
                references,
            )
        return None

    def _rejected(
        self,
        code: str,
        message: str,
        references: ProfileReferences | None = None,
    ) -> GapAnalysis:
        return GapAnalysis(
            generation_status="rejected",
            profile_references=references or ProfileReferences(),
            algorithm_version=self._config.algorithm_version,
            config_version=self._config.config_version,
            gap_policy_version=self._config.gap_policy_version,
            gap_policy_hash=gap_policy_hash(self._config),
            error_code=code,
            error_message=message,
        )
