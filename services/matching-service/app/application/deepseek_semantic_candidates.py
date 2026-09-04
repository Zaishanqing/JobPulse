"""LLM semantic candidate validation and skill-result application."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.deepseek_candidates import (
    DeepSeekSemanticMode,
    LLMSemanticCandidateBatch,
    RawDeepSeekSkillCandidate,
    SkillSemanticCandidate,
)
from app.domain.evaluation import MatchEvaluation, SkillResult
from app.domain.matching import _coverage, transferable_coverage
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.relation_matching import _capability_evidence, _verified_capabilities
from app.domain.skill_relations import SkillRelation
from app.ports.llm_semantic_candidates import (
    LLMSemanticCandidateError,
    LLMSemanticCandidateSource,
)


@dataclass(frozen=True)
class DeepSeekSemanticCandidateConfig:
    mode: DeepSeekSemanticMode = "disabled"
    algorithm_version: str = "deepseek-semantic-candidates.v1"
    minimum_relation_confidence: float = 0.7
    max_candidates_per_requirement: int = 3

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "shadow", "enabled"}:
            raise ValueError("DeepSeek semantic mode is invalid")
        if not 0 <= self.minimum_relation_confidence <= 1:
            raise ValueError("DeepSeek relation confidence threshold must be within 0..1")
        if not 1 <= self.max_candidates_per_requirement <= 10:
            raise ValueError("DeepSeek candidate limit must be within 1..10")


_RELATION_PRIORITY = {"equivalent": 3, "transferable": 2, "related": 1}
_ALLOWED_RELATIONS = frozenset({"equivalent", "related", "transferable"})


class DeepSeekSemanticCandidateService:
    def __init__(
        self,
        source: LLMSemanticCandidateSource,
        config: DeepSeekSemanticCandidateConfig | None = None,
        relations: tuple[SkillRelation, ...] = (),
    ) -> None:
        self._source = source
        self.config = config or DeepSeekSemanticCandidateConfig()
        self._relations = relations

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def algorithm_version(self) -> str:
        return self.config.algorithm_version

    def apply(
        self,
        *,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        relations: tuple[SkillRelation, ...],
        evaluation: MatchEvaluation,
    ) -> MatchEvaluation:
        if self.config.mode == "disabled":
            return evaluation
        try:
            batch = self._source.generate_candidates(cv=cv, position=position)
        except LLMSemanticCandidateError:
            raise
        effective_relations = relations or self._relations
        validated = self._validate(cv, position, effective_relations, evaluation, batch)
        if self.config.mode == "shadow":
            return evaluation.model_copy(
                update={
                    "semantic_llm_status": "available",
                    "semantic_llm_error_code": None,
                    "semantic_llm_model": batch.model,
                    "semantic_llm_algorithm_version": batch.algorithm_version,
                    "semantic_llm_candidates": validated,
                }
            )
        updated = self._apply_candidates(evaluation, validated)
        return self._refresh(updated, validated, batch)

    def _validate(
        self,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        relations: tuple[SkillRelation, ...],
        evaluation: MatchEvaluation,
        batch: LLMSemanticCandidateBatch,
    ) -> tuple[SkillSemanticCandidate, ...]:
        capabilities = _verified_capabilities(cv)
        by_requirement: dict[str, list[RawDeepSeekSkillCandidate]] = {}
        for raw in batch.candidates:
            by_requirement.setdefault(raw.requirement_id, []).append(raw)

        results: list[SkillSemanticCandidate] = []
        base_by_id = {item.requirement_id: item for item in evaluation.skill_results}
        for requirement_id, raw_candidates in by_requirement.items():
            base = base_by_id.get(requirement_id)
            if base is None or base.skill_id is None:
                continue
            for raw in raw_candidates[: self.config.max_candidates_per_requirement]:
                capability = capabilities.get(raw.candidate_skill_id)
                candidate_evidence = (
                    _capability_evidence(cv, capability) if capability is not None else ()
                )
                position_evidence = base.position_evidence
                relation = self._find_relation(
                    base.skill_id,
                    raw.candidate_skill_id,
                    relations,
                    raw.proposed_relation_type,
                )
                if (
                    capability is None
                    or not position_evidence
                    or not candidate_evidence
                    or relation is None
                ):
                    results.append(
                        SkillSemanticCandidate(
                            requirement_id=requirement_id,
                            required_skill_id=base.skill_id,
                            required_skill_name=base.skill_name or base.skill_id,
                            candidate_skill_id=raw.candidate_skill_id,
                            candidate_skill_name=raw.candidate_skill_name,
                            proposed_relation_type=raw.proposed_relation_type,
                            relation_type="unknown",
                            status="unknown",
                            reason_code=(
                                "DEEPSEEK_KG_RELATION_MISSING"
                                if relation is None and capability is not None
                                and position_evidence and candidate_evidence
                                else "DEEPSEEK_CANDIDATE_UNVERIFIED"
                            ),
                            confidence=0.0,
                            position_evidence=position_evidence,
                            candidate_evidence=candidate_evidence,
                            relation_evidence=relation.evidence_refs if relation else (),
                            relation_source=(
                                f"deepseek:{relation.source_system}" if relation else None
                            ),
                            relation_graph_version=(
                                relation.graph_version if relation else None
                            ),
                            model=batch.model,
                            algorithm_version=batch.algorithm_version,
                        )
                    )
                    continue
                relation_evidence = relation.evidence_refs
                confidence = relation.confidence * capability.support_confidence
                results.append(
                    SkillSemanticCandidate(
                        requirement_id=requirement_id,
                        required_skill_id=base.skill_id,
                        required_skill_name=base.skill_name or base.skill_id,
                        candidate_skill_id=raw.candidate_skill_id,
                        candidate_skill_name=raw.candidate_skill_name,
                        proposed_relation_type=raw.proposed_relation_type,
                        relation_type=relation.relation_type,
                        status="valid",
                        reason_code=f"DEEPSEEK_{relation.relation_type.upper()}_VALIDATED",
                        confidence=confidence,
                        position_evidence=position_evidence,
                        candidate_evidence=candidate_evidence,
                        relation_evidence=relation_evidence,
                        relation_source=f"deepseek:{relation.source_system}",
                        relation_graph_version=relation.graph_version,
                        model=batch.model,
                        algorithm_version=batch.algorithm_version,
                    )
                )
        return tuple(results)

    @staticmethod
    def _find_relation(
        required_skill_id: str,
        candidate_skill_id: str,
        relations: tuple[SkillRelation, ...],
        proposed_type: str,
    ) -> SkillRelation | None:
        if required_skill_id == candidate_skill_id:
            return None
        candidates = [
            item
            for item in relations
            if item.relation_type in _ALLOWED_RELATIONS
            and item.evidence_refs
            and (
                (
                    item.source_skill_id == candidate_skill_id
                    and item.target_skill_id == required_skill_id
                )
                or (
                    item.source_skill_id == required_skill_id
                    and item.target_skill_id == candidate_skill_id
                )
            )
        ]
        if not candidates:
            return None
        scored = []
        for item in candidates:
            priority = _RELATION_PRIORITY.get(item.relation_type, 0)
            match_bonus = 1 if item.relation_type == proposed_type else 0
            scored.append((priority, match_bonus, item.confidence, item.relation_id))
        return max(zip(scored, candidates, strict=True), key=lambda pair: pair[0])[1]

    def _apply_candidates(
        self,
        evaluation: MatchEvaluation,
        candidates: tuple[SkillSemanticCandidate, ...],
    ) -> MatchEvaluation:
        by_requirement: dict[str, list[SkillSemanticCandidate]] = {}
        for item in candidates:
            by_requirement.setdefault(item.requirement_id, []).append(item)
        skill_by_id = {item.requirement_id: item for item in evaluation.skill_results}
        updated: list[SkillResult] = []
        for requirement_id, base in skill_by_id.items():
            items = by_requirement.get(requirement_id, ())
            if not items or base.match_status != "missing":
                updated.append(base)
                continue
            valid = [item for item in items if item.status == "valid"]
            selected = (
                max(
                    valid,
                    key=lambda item: (
                        _RELATION_PRIORITY.get(item.relation_type, 0),
                        item.confidence,
                        item.candidate_skill_id,
                    ),
                )
                if valid
                else min(
                    items,
                    key=lambda item: (item.candidate_skill_id, item.reason_code),
                )
            )
            if selected.status != "valid":
                updated.append(
                    base.model_copy(
                        update={
                            "match_status": "unknown",
                            "match_type": "semantic_candidate",
                            "relation_type": "unknown",
                            "reason_code": selected.reason_code,
                            "confidence": 0.0,
                            "candidate_evidence": selected.candidate_evidence,
                            "relation_evidence": selected.relation_evidence,
                            "relation_source": selected.relation_source,
                            "relation_graph_version": selected.relation_graph_version,
                            "semantic_model": selected.model,
                            "semantic_algorithm_version": selected.algorithm_version,
                        }
                    )
                )
                continue
            transferability = {
                "equivalent": 1.0,
                "transferable": 0.7,
                "related": 0.4,
            }.get(selected.relation_type, 0.0)
            updated.append(
                base.model_copy(
                    update={
                        "match_status": (
                            "matched"
                            if selected.relation_type == "equivalent"
                            else "partial"
                        ),
                        "match_type": selected.relation_type,
                        "related_candidate_skill_id": selected.candidate_skill_id,
                        "relation_type": selected.relation_type,
                        "relation_confidence": selected.confidence,
                        "relation_evidence": selected.relation_evidence,
                        "relation_source": selected.relation_source,
                        "relation_graph_version": selected.relation_graph_version,
                        "candidate_evidence": selected.candidate_evidence,
                        "reason_code": selected.reason_code,
                        "confidence": selected.confidence,
                        "transferability_score": transferability,
                        "semantic_model": selected.model,
                        "semantic_algorithm_version": selected.algorithm_version,
                        "semantic_candidate_id": selected.candidate_skill_id,
                    }
                )
            )
        return evaluation.model_copy(update={"skill_results": tuple(updated)})

    def _refresh(
        self,
        evaluation: MatchEvaluation,
        candidates: tuple[SkillSemanticCandidate, ...],
        batch: LLMSemanticCandidateBatch,
    ) -> MatchEvaluation:
        skills = evaluation.skill_results
        unresolved = sum(item.status == "unresolved" for item in evaluation.hard_constraint_results)
        unresolved += sum(item.match_status == "unresolved" for item in skills)
        unknown = sum(item.status == "unknown" for item in evaluation.hard_constraint_results)
        unknown += sum(item.match_status == "unknown" for item in skills)
        for group in (
            evaluation.responsibility_results,
            evaluation.project_results,
            evaluation.scenario_results,
        ):
            unresolved += sum(item.match_status == "unresolved" for item in group)
            unknown += sum(item.match_status == "unknown" for item in group)
        summary = evaluation.summary
        if summary is not None:
            summary = summary.model_copy(
                update={
                    "required_skill_matched_count": sum(
                        item.importance_level == "required"
                        and item.match_status == "matched"
                        for item in skills
                    ),
                    "required_skill_missing_count": sum(
                        item.importance_level == "required"
                        and item.match_status == "missing"
                        for item in skills
                    ),
                    "bonus_skill_matched_count": sum(
                        item.importance_level == "bonus"
                        and item.match_status == "matched"
                        for item in skills
                    ),
                    "bonus_skill_missing_count": sum(
                        item.importance_level == "bonus"
                        and item.match_status == "missing"
                        for item in skills
                    ),
                }
            )
        return evaluation.model_copy(
            update={
                "skill_results": skills,
                "summary": summary,
                "required_skill_coverage": _coverage(skills, "required"),
                "bonus_skill_coverage": _coverage(skills, "bonus"),
                "required_transferable_coverage": transferable_coverage(
                    skills, "required"
                ),
                "bonus_transferable_coverage": transferable_coverage(skills, "bonus"),
                "unresolved_count": unresolved,
                "unknown_count": unknown,
                "semantic_llm_status": "available",
                "semantic_llm_error_code": None,
                "semantic_llm_model": batch.model,
                "semantic_llm_algorithm_version": batch.algorithm_version,
                "semantic_llm_candidates": candidates,
            }
        )


__all__ = [
    "DeepSeekSemanticCandidateConfig",
    "DeepSeekSemanticCandidateService",
]
