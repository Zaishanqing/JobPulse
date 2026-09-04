"""Application orchestration for deterministic and optional semantic evaluations."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from app.application.deepseek_semantic_candidates import (
    DeepSeekSemanticCandidateService,
)
from app.application.responsibility_ce import ResponsibilityCEVerifier
from app.application.responsibility_policy import (
    ResponsibilityDecisionPolicy,
    ResponsibilityDecisionPolicyConfig,
)
from app.application.semantic_retrieval import (
    SemanticRetrievalService,
    semantic_explanations,
)
from app.application.validation import ProfileValidationService
from app.domain.context_matching import verify_semantic_candidate
from app.domain.evaluation import MatchEvaluation, ResponsibilityResult
from app.domain.matching import (
    MatchingAlgorithmConfig,
    build_match_evaluation,
    prepare_effective_position,
)
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.requirement_graph import build_requirement_graph_from_jd
from app.domain.scoring import ScoringConfig, ScoringWeights, score_match_evaluation
from app.domain.semantic_retrieval import SemanticRetrievalEvidence
from app.domain.skill_relations import SkillRelation
from app.domain.vector_contracts import VectorContractViolation
from app.ports.llm_semantic_candidates import LLMSemanticCandidateError
from app.ports.skill_relations import SkillRelationSource


class MatchEvaluationService:
    def __init__(
        self,
        validation: ProfileValidationService | None = None,
        config: MatchingAlgorithmConfig | None = None,
        relation_source: SkillRelationSource | None = None,
        scoring_config: ScoringConfig | None = None,
        enterprise_scoring_config: ScoringConfig | None = None,
        semantic_retrieval: SemanticRetrievalService | None = None,
        semantic_candidates: DeepSeekSemanticCandidateService | None = None,
        semantic_candidate_minimum_similarity: float = 0.70,
        semantic_candidate_minimum_coverage: float = 0.25,
        semantic_candidate_minimum_specific_tokens: int = 2,
        responsibility_semantic_enabled: bool = False,
        responsibility_verifier: ResponsibilityCEVerifier | None = None,
        responsibility_policy: ResponsibilityDecisionPolicy | None = None,
        responsibility_policy_config: ResponsibilityDecisionPolicyConfig | None = None,
    ) -> None:
        self._validation = validation or ProfileValidationService()
        self._config = config or MatchingAlgorithmConfig()
        self._relation_source = relation_source
        self._scoring_config = scoring_config or ScoringConfig()
        self._enterprise_scoring_config = enterprise_scoring_config or ScoringConfig(
            scoring_config_version="scoring-config.enterprise.v3",
            weights=ScoringWeights(
                required_skills=0.30,
                responsibilities=0.25,
                projects=0.20,
                capability_level=0.10,
                hard_conditions=0.10,
                business_scenarios=0.05,
                bonus_transferable=0.0,
            ),
        )
        self._semantic_retrieval = semantic_retrieval
        self._semantic_candidates = semantic_candidates
        if not 0 <= semantic_candidate_minimum_similarity <= 1:
            raise ValueError("semantic candidate threshold must be within 0..1")
        if not 0 <= semantic_candidate_minimum_coverage <= 1:
            raise ValueError("semantic candidate coverage must be within 0..1")
        if semantic_candidate_minimum_specific_tokens < 1:
            raise ValueError(
                "semantic candidate token threshold must be positive"
            )
        self._semantic_candidate_minimum_similarity = semantic_candidate_minimum_similarity
        self._semantic_candidate_minimum_coverage = semantic_candidate_minimum_coverage
        self._semantic_candidate_minimum_specific_tokens = (
            semantic_candidate_minimum_specific_tokens
        )
        self._responsibility_semantic_enabled = responsibility_semantic_enabled
        self._responsibility_verifier = responsibility_verifier
        self._responsibility_policy = (
            (
                responsibility_policy
                or ResponsibilityDecisionPolicy(responsibility_policy_config)
            )
            if self._config.context.responsibility_matching_enabled
            else None
        )

    def scoring_config_version(self, use_enterprise_weights: bool) -> str:
        return (
            self._enterprise_scoring_config.scoring_config_version
            if use_enterprise_weights
            else self._scoring_config.scoring_config_version
        )

    def fetch_skill_relations(
        self, skill_ids: tuple[str, ...]
    ) -> tuple[SkillRelation, ...] | None:
        """Read versioned relations through the configured matching boundary.

        ``None`` distinguishes an unavailable relation source from a configured
        source that returned no edges, which is required for explainable path
        reachability decisions.
        """
        if self._relation_source is None:
            return None
        return self._relation_source.fetch_relations(tuple(sorted(set(skill_ids))))

    def evaluate(
        self,
        request_payload: object,
        *,
        include_semantic: bool = True,
    ) -> MatchEvaluation:
        if not isinstance(request_payload, Mapping):
            return self._rejected("EVALUATION_REQUEST_INVALID", "request must be an object")
        target_type = request_payload.get("target_type", "standard_position")
        use_enterprise_weights = request_payload.get("use_enterprise_weights", False)
        if not isinstance(use_enterprise_weights, bool):
            return self._rejected(
                "ENTERPRISE_WEIGHTS_INVALID", "use_enterprise_weights must be boolean"
            )
        if use_enterprise_weights and target_type != "enterprise_job":
            return self._rejected(
                "ENTERPRISE_WEIGHTS_TARGET_INVALID",
                "enterprise weights require target_type=enterprise_job",
            )
        scoring_config = (
            self._enterprise_scoring_config
            if use_enterprise_weights
            else self._scoring_config
        )
        cv_payload = request_payload.get("cv_profile")
        position_payload = request_payload.get("position_profile")
        if cv_payload is None:
            return self._rejected("CV_PROFILE_NOT_FOUND", "cv_profile is required")
        if position_payload is None:
            return self._rejected(
                "POSITION_PROFILE_NOT_FOUND", "position_profile is required"
            )

        cv_validation = self._validation.validate_cv(cv_payload)
        technical_context_allowed = bool(
            getattr(position_payload, "responsibility_requirements", None)
            or (
                isinstance(position_payload, dict)
                and position_payload.get("responsibility_requirements")
            )
        )
        position_validation = self._validation.validate_position(
            position_payload,
            technical_context_allowed=technical_context_allowed,
        )
        if cv_validation.profile_status == "invalid":
            return self._profile_rejection(
                "CV", cv_validation.validation_errors
            )
        if position_validation.profile_status == "invalid":
            return self._profile_rejection(
                "POSITION",
                position_validation.validation_errors,
            )

        cv = CVMatchProfile.model_validate(cv_payload)
        position = PositionMatchProfile.model_validate(position_payload)
        if position.requirement_graph is None:
            derived_graph = build_requirement_graph_from_jd(position)
            if derived_graph is not None:
                position = position.model_copy(
                    update={"requirement_graph": derived_graph}
                )
        has_requirement = any(
            item.resolution_status == "resolved" for item in position.hard_conditions
        ) or any(
            item.resolution_status == "resolved" and item.skill_id is not None
            for item in position.required_skills + position.preferred_skills
        )
        if not has_requirement:
            return self._rejected(
                "POSITION_REQUIREMENTS_EMPTY",
                "position must contain at least one resolved requirement",
            )
        skill_ids = tuple(
            sorted(
                {
                    item.skill_id
                    for item in (
                        cv.skills
                        + cv.capability_profiles
                        + position.required_skills
                        + position.preferred_skills
                    )
                    if item.skill_id is not None
                }
            )
        )
        relations = (
            self._relation_source.fetch_relations(skill_ids)
            if self._relation_source is not None and position.graph_mode != "disabled"
            else ()
        )
        effective_target_type = target_type if isinstance(target_type, str) else ""
        position, _route_selection = prepare_effective_position(
            cv,
            position,
            self._config,
            relations,
            target_type=effective_target_type,
        )
        evaluation = build_match_evaluation(
            cv,
            position,
            self._config,
            relations,
            target_type=effective_target_type,
        )
        if (
            include_semantic
            and self._semantic_candidates is not None
            and self._semantic_candidates.mode != "disabled"
        ):
            try:
                evaluation = self._semantic_candidates.apply(
                    cv=cv,
                    position=position,
                    relations=relations,
                    evaluation=evaluation,
                )
            except LLMSemanticCandidateError as exc:
                if self._semantic_candidates.mode == "enabled":
                    return self._rejected(
                        "DEEPSEEK_CANDIDATE_UNAVAILABLE",
                        exc.message,
                    )
                evaluation = evaluation.model_copy(
                    update={
                        "semantic_llm_status": "unavailable",
                        "semantic_llm_error_code": exc.code,
                    }
                )
        if (
            self._config.context.responsibility_matching_enabled
            and self._responsibility_verifier is not None
        ):
            evaluation = evaluation.model_copy(
                update={
                    "responsibility_results": self._responsibility_verifier.verify(
                        cv,
                        position,
                        self._config.context,
                    )
                }
            )
        if self._responsibility_policy is not None:
            evaluation = evaluation.model_copy(
                update={
                    "responsibility_results": self._responsibility_policy.apply(
                        evaluation.responsibility_results,
                        cv,
                        position,
                        self._config.context,
                    )
                }
            )
        final_result = score_match_evaluation(evaluation, cv, position, scoring_config)
        evaluation = evaluation.model_copy(update={"final_match_result": final_result})
        if not include_semantic or self._semantic_retrieval is None:
            return evaluation
        tenant_ref = request_payload.get("tenant_ref")
        user_ref = request_payload.get("user_ref")
        target_type = request_payload.get("target_type", "standard_position")
        if target_type not in {"standard_position", "enterprise_job"}:
            retrieval = self._semantic_retrieval.unavailable(
                "SEMANTIC_TARGET_TYPE_INVALID"
            )
        elif not isinstance(tenant_ref, str) or not tenant_ref:
            retrieval = self._semantic_retrieval.unavailable("SEMANTIC_TENANT_REQUIRED")
        else:
            try:
                retrieval = self._semantic_retrieval.retrieve(
                    tenant_ref=tenant_ref,
                    target_type=target_type,
                    cv=cv,
                    position=position,
                    user_ref=user_ref if isinstance(user_ref, str) else None,
                )
            except VectorContractViolation as exc:
                self._semantic_retrieval.record_signal("retrieval", "error")
                retrieval = self._semantic_retrieval.unavailable(exc.code)
        candidate = next(
            (item for item in retrieval.candidates if item.candidate_source_id == cv.cv_id),
            None,
        )
        evidence = candidate.evidence if candidate is not None else ()
        score = candidate.score if candidate is not None else None
        updates = {
            "semantic_status": retrieval.status,
            "semantic_error_code": retrieval.error_code,
            "semantic_shadow_status": retrieval.status,
            "semantic_shadow_score": score,
            "semantic_shadow_evidence": evidence,
            "semantic_candidates": retrieval.candidates,
            "semantic_latency_ms": retrieval.latency_ms,
            "semantic_retrieval_trace_id": retrieval.retrieval_trace_id,
            "semantic_embedding_model": retrieval.embedding_model,
            "semantic_embedding_revision": retrieval.embedding_revision,
            "semantic_embedding_dimension": retrieval.embedding_dimension,
            "semantic_embedding_normalized": retrieval.embedding_normalized,
            "semantic_embedding_normalization": retrieval.embedding_normalization,
            "semantic_vector_representation": retrieval.vector_representation,
            "semantic_vector_similarity": retrieval.vector_similarity,
            "semantic_text_derivation_version": retrieval.text_derivation_version,
            "semantic_index_revision": retrieval.index_revision,
            "semantic_collection": retrieval.collection,
            "semantic_target_type": (
                target_type
                if target_type in {"standard_position", "enterprise_job"}
                else None
            ),
            "semantic_explanations": semantic_explanations(evidence),
        }
        if retrieval.status == "available":
            updates["semantic_evidence"] = evidence
            updates["vector_profile_version"] = cv.profile_version
            updates["vector_text_derivation_version"] = retrieval.text_derivation_version
            updates["embedding_model"] = retrieval.embedding_model
            updates["embedding_version"] = retrieval.embedding_revision
            updates["semantic_algorithm_version"] = retrieval.algorithm_version
            updates["threshold_config_version"] = (
                self._semantic_retrieval.config.threshold_config_version
            )
            updates["final_match_result"] = final_result.model_copy(
                update={
                    "vector_text_derivation_version": retrieval.text_derivation_version,
                    "embedding_model": retrieval.embedding_model,
                    "embedding_version": retrieval.embedding_revision,
                    "semantic_algorithm_version": retrieval.algorithm_version,
                    "semantic_threshold_config_version": (
                        self._semantic_retrieval.config.threshold_config_version
                    ),
                    "semantic_index_revision": retrieval.index_revision,
                    "semantic_collection": retrieval.collection,
                    "semantic_embedding_dimension": retrieval.embedding_dimension,
                    "semantic_embedding_normalized": retrieval.embedding_normalized,
                    "semantic_embedding_normalization": retrieval.embedding_normalization,
                    "semantic_vector_representation": retrieval.vector_representation,
                    "semantic_vector_similarity": retrieval.vector_similarity,
                    "semantic_text_derivation_version": retrieval.text_derivation_version,
                    "semantic_weight": 0.0,
                }
            )
        combined = evaluation.model_copy(update=updates)
        semantic_evidence = tuple(
            item
            for candidate in retrieval.candidates
            for item in candidate.evidence
        )
        if self._responsibility_semantic_enabled and (
            self._config.context.responsibility_matching_enabled
        ):
            combined = combined.model_copy(
                update={
                    "responsibility_results": attach_semantic_responsibility_candidates(
                        combined.responsibility_results,
                        semantic_evidence,
                        cv,
                        position,
                        minimum_similarity=self._semantic_candidate_minimum_similarity,
                        minimum_coverage=self._semantic_candidate_minimum_coverage,
                        minimum_specific_tokens=self._semantic_candidate_minimum_specific_tokens,
                        semantic_concept_alias_enabled=(
                            self._config.context.semantic_concept_alias_enabled
                        ),
                    )
                }
            )
        combined_final = combined.final_match_result
        if combined_final is not None:
            combined = combined.model_copy(
                update={
                    "final_match_result": combined_final.model_copy(
                        update={
                            "source_evaluation_id": combined.evaluation_id
                        }
                    )
                }
            )
        return combined

    def _profile_rejection(
        self,
        side: str,
        errors: tuple[object, ...],
        cv_profile_id: str | None = None,
        position_profile_id: str | None = None,
    ) -> MatchEvaluation:
        error_types = {getattr(item, "error_type", "") for item in errors}
        if "pii_forbidden" in error_types:
            suffix = "PROFILE_CONTAINS_PII"
        elif errors:
            suffix = "PROFILE_INVALID"
        else:
            suffix = "PROFILE_NOT_READY"
        return self._rejected(
            f"{side}_{suffix}",
            f"{side.lower()} profile did not pass the ready gate",
            cv_profile_id,
            position_profile_id,
        )

    def _rejected(
        self,
        code: str,
        message: str,
        cv_profile_id: str | None = None,
        position_profile_id: str | None = None,
    ) -> MatchEvaluation:
        evaluation_id = "eval_rejected_" + uuid4().hex
        return MatchEvaluation(
            evaluation_id=evaluation_id,
            cv_profile_id=cv_profile_id,
            position_profile_id=position_profile_id,
            algorithm_version=self._config.algorithm_version,
            evaluation_status="rejected",
            error_code=code,
            error_message=message,
        )


def attach_semantic_responsibility_candidates(
    results: tuple[ResponsibilityResult, ...],
    evidence: tuple[SemanticRetrievalEvidence, ...],
    cv: CVMatchProfile,
    position: PositionMatchProfile,
    *,
    minimum_similarity: float = 0.70,
    minimum_coverage: float = 0.25,
    minimum_specific_tokens: int = 2,
    semantic_concept_alias_enabled: bool = True,
) -> tuple[ResponsibilityResult, ...]:
    """Attach evidence-bound responsibility retrieval hits in shadow fields.

    Deterministic status and evidence remain untouched. This makes semantic
    retrieval measurable in isolation and prevents ``semantic_weight=0``
    experiments from changing the production recommendation.
    """
    del cv  # Candidate evidence is already bound by the retrieval contract.
    by_requirement: dict[int, list[SemanticRetrievalEvidence]] = {}
    for item in evidence:
        if item.query_fragment_type != "responsibility":
            continue
        if item.candidate_fragment_type not in {
            "work_experience",
            "project_responsibility",
        }:
            continue
        score = item.rerank_score if item.rerank_score is not None else item.similarity
        if score < minimum_similarity:
            continue
        if not verify_semantic_candidate(
            item.position_evidence_ref.quote,
            item.evidence_ref.quote,
            minimum_coverage=minimum_coverage,
            minimum_specific_tokens=minimum_specific_tokens,
            semantic_concept_alias_enabled=semantic_concept_alias_enabled,
        ):
            continue
        requirement_index = next(
            (
                index
                for index, requirement in enumerate(position.core_responsibilities)
                if item.position_evidence_ref.quote == requirement
            ),
            None,
        )
        if requirement_index is not None:
            by_requirement.setdefault(requirement_index, []).append(item)

    output = []
    for index, result in enumerate(results):
        hits = sorted(
            by_requirement.get(index, ()),
            key=lambda item: (
                -(item.rerank_score if item.rerank_score is not None else item.similarity),
                item.rank,
                item.candidate_fragment_id,
            ),
        )
        if not hits:
            output.append(result)
            continue
        output.append(
            result.model_copy(
                update={
                    "semantic_candidate_evidence": tuple(
                        item.evidence_ref for item in hits
                    ),
                    "semantic_candidate_score": max(
                        item.rerank_score
                        if item.rerank_score is not None
                        else item.similarity
                        for item in hits
                    ),
                    "candidate_feature_id": hits[0].candidate_fragment_id,
                    "embedding_model": hits[0].embedding_model,
                    "embedding_version": hits[0].embedding_revision,
                    "semantic_reason_code": "RESPONSIBILITY_SEMANTIC_CANDIDATE",
                }
            )
        )
    return tuple(output)
