"""Pure one-hop graph relation matching for otherwise missing skills."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.evaluation import SkillResult
from app.domain.profiles import CapabilityProfile, CVMatchProfile, Evidence
from app.domain.skill_relations import SkillRelation

_SYMMETRIC_RELATIONS = frozenset({"equivalent", "parent_child", "related"})


@dataclass(frozen=True)
class SkillRelationMatchingConfig:
    minimum_relation_confidence: float = 0.7
    equivalent_weight: float = 1.0
    parent_child_weight: float = 0.6
    related_weight: float = 0.4
    transferable_weight: float = 0.7
    prerequisite_weight: float = 0.0
    partially_supported_confidence_factor: float = 0.75

    def __post_init__(self) -> None:
        values = (
            self.minimum_relation_confidence,
            self.equivalent_weight,
            self.parent_child_weight,
            self.related_weight,
            self.transferable_weight,
            self.prerequisite_weight,
            self.partially_supported_confidence_factor,
        )
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("relation thresholds and weights must be between 0 and 1")

    def weight(self, relation_type: str) -> float:
        return {
            "equivalent": self.equivalent_weight,
            "parent_child": self.parent_child_weight,
            "related": self.related_weight,
            "transferable": self.transferable_weight,
            "prerequisite": self.prerequisite_weight,
        }[relation_type]


def _dedupe_evidence(groups: tuple[tuple[Evidence, ...], ...]) -> tuple[Evidence, ...]:
    values = {
        (
            item.source_id,
            item.quote,
            item.start,
            item.end,
            item.alignment,
            item.occurrence_index,
        ): item
        for group in groups
        for item in group
    }
    return tuple(values[key] for key in sorted(values, key=str))


def _capability_evidence(
    cv: CVMatchProfile,
    capability: CapabilityProfile,
) -> tuple[Evidence, ...]:
    links = tuple(
        item
        for item in cv.capability_evidence_links
        if item.link_id in capability.evidence_link_ids
    )
    return _dedupe_evidence(tuple(item.evidence_refs for item in links))


def _verified_capabilities(cv: CVMatchProfile) -> dict[str, CapabilityProfile]:
    candidates: dict[str, CapabilityProfile] = {}
    for capability in cv.capability_profiles:
        if (
            capability.skill_id is None
            or capability.resolution_status != "resolved"
            or capability.verification_status
            not in {"supported", "partially_supported", "experience_only"}
            or capability.demonstrated_level == "unknown"
            or capability.support_confidence <= 0
            or not _capability_evidence(cv, capability)
        ):
            continue
        current = candidates.get(capability.skill_id)
        if current is None or (
            capability.support_confidence,
            capability.profile_id,
        ) > (
            current.support_confidence,
            current.profile_id,
        ):
            candidates[capability.skill_id] = capability
    return candidates


def _candidate_endpoint(
    relation: SkillRelation,
    required_skill_id: str,
    candidate_skill_ids: frozenset[str],
) -> str | None:
    if relation.relation_type in _SYMMETRIC_RELATIONS:
        if (
            relation.target_skill_id == required_skill_id
            and relation.source_skill_id in candidate_skill_ids
        ):
            return relation.source_skill_id
        if (
            relation.source_skill_id == required_skill_id
            and relation.target_skill_id in candidate_skill_ids
        ):
            return relation.target_skill_id
        return None
    if (
        relation.target_skill_id == required_skill_id
        and relation.source_skill_id in candidate_skill_ids
    ):
        return relation.source_skill_id
    return None


def _rank(level: str, levels: tuple[str, ...]) -> int | None:
    try:
        return levels.index(level.casefold())
    except ValueError:
        return None


def _relation_priority(relation_type: str) -> int:
    return {
        "equivalent": 5,
        "transferable": 4,
        "parent_child": 3,
        "related": 2,
        "prerequisite": 1,
    }[relation_type]


def _apply_relation(
    base: SkillResult,
    relation: SkillRelation,
    candidate_skill_id: str,
    capability: CapabilityProfile,
    candidate_evidence: tuple[Evidence, ...],
    levels: tuple[str, ...],
    config: SkillRelationMatchingConfig,
    prerequisite_skill_ids: tuple[str, ...] = (),
) -> SkillResult:
    confidence = relation.confidence * capability.support_confidence
    if capability.verification_status == "partially_supported":
        confidence *= config.partially_supported_confidence_factor
    relation_type = relation.relation_type
    status = "partial"
    reason = {
        "parent_child": "PARENT_CHILD_SKILL_PARTIAL_MATCH",
        "related": "RELATED_SKILL_PARTIAL_MATCH",
        "transferable": "TRANSFERABLE_SKILL_PARTIAL_MATCH",
    }.get(relation_type, "PREREQUISITE_ONLY")
    score = config.weight(relation_type)
    if relation_type == "equivalent":
        if base.required_level is None:
            status, reason, score = "matched", "EQUIVALENT_SKILL_MATCH", 1.0
        else:
            required_rank = _rank(base.required_level, levels)
            candidate_rank = _rank(capability.demonstrated_level, levels)
            if required_rank is None or candidate_rank is None:
                status, reason, score = "unknown", "SKILL_LEVEL_UNKNOWN", 0.0
            elif candidate_rank >= required_rank:
                status, reason, score = "matched", "EQUIVALENT_SKILL_MATCH", 1.0
            else:
                status, reason, score = "weak", "EQUIVALENT_SKILL_LEVEL_BELOW", 0.0
    elif relation_type == "prerequisite":
        status, reason, score = "missing", "PREREQUISITE_ONLY", 0.0
    elif relation_type == "parent_child":
        status, reason, score = (
            "missing",
            "PARENT_CHILD_LEARNING_ONLY",
            0.0,
        )
    elif relation_type == "related":
        status, reason, score = "missing", "RELATED_NO_SCORE_CREDIT", 0.0
    return base.model_copy(
        update={
            "candidate_declared_level": capability.declared_level,
            "candidate_demonstrated_level": capability.demonstrated_level,
            "verification_status": capability.verification_status,
            "match_status": status,
            "candidate_evidence": candidate_evidence,
            "reason_code": reason,
            "confidence": confidence,
            "match_type": relation_type,
            "related_candidate_skill_id": candidate_skill_id,
            "prerequisite_skill_ids": prerequisite_skill_ids,
            "relation_type": relation_type,
            "relation_confidence": relation.confidence,
            "relation_evidence": relation.evidence_refs,
            "relation_source": relation.source_system,
            "relation_graph_version": relation.graph_version,
            "transferability_score": score,
        }
    )


def apply_skill_relations(
    base_results: tuple[SkillResult, ...],
    cv: CVMatchProfile,
    relations: tuple[SkillRelation, ...],
    capability_levels: tuple[str, ...],
    config: SkillRelationMatchingConfig,
) -> tuple[SkillResult, ...]:
    """Enhance missing skills from eligible one-hop relations only."""

    capabilities = _verified_capabilities(cv)
    candidate_ids = frozenset(capabilities)
    output: list[SkillResult] = []
    for base in base_results:
        if base.match_status != "missing" or base.skill_id is None:
            output.append(base)
            continue
        valid: dict[tuple[str, str], tuple[SkillRelation, str]] = {}
        insufficient = False
        prerequisite_skill_ids: set[str] = set()
        for relation in relations:
            if (
                relation.relation_type == "prerequisite"
                and relation.target_skill_id == base.skill_id
                and relation.confidence >= config.minimum_relation_confidence
                and relation.evidence_refs
            ):
                prerequisite_skill_ids.add(relation.source_skill_id)
            candidate_id = _candidate_endpoint(relation, base.skill_id, candidate_ids)
            if candidate_id is None:
                continue
            if (
                relation.confidence < config.minimum_relation_confidence
                or not relation.evidence_refs
            ):
                insufficient = True
                continue
            key = (candidate_id, relation.relation_type)
            current = valid.get(key)
            if current is None or (relation.confidence, relation.relation_id) > (
                current[0].confidence,
                current[0].relation_id,
            ):
                valid[key] = (relation, candidate_id)
        if not valid:
            output.append(
                base.model_copy(
                    update={
                        "prerequisite_skill_ids": tuple(sorted(prerequisite_skill_ids)),
                        "reason_code": (
                            "RELATION_EVIDENCE_INSUFFICIENT"
                            if insufficient
                            else base.reason_code
                        )
                    }
                )
            )
            continue
        relation, candidate_id = max(
            valid.values(),
            key=lambda item: (
                _relation_priority(item[0].relation_type),
                config.weight(item[0].relation_type),
                item[0].confidence,
                item[1],
                item[0].relation_id,
            ),
        )
        capability = capabilities[candidate_id]
        output.append(
            _apply_relation(
                base,
                relation,
                candidate_id,
                capability,
                _capability_evidence(cv, capability),
                capability_levels,
                config,
                prerequisite_skill_ids=tuple(sorted(prerequisite_skill_ids)),
            )
        )
    return tuple(output)


def transferable_coverage(
    results: tuple[SkillResult, ...],
    importance: str,
) -> float | None:
    evaluable = tuple(
        item
        for item in results
        if item.importance_level == importance
        and item.match_status not in {"unknown", "unresolved"}
    )
    if not evaluable:
        return None
    return sum(item.transferability_score for item in evaluable) / len(evaluable)
