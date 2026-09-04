"""Pure graph-build facts, rules and persistence plan."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.domain.profile_thresholds import PositionProfileThresholdConfig
from app.domain.review_tasks import ReviewReasonSet
from app.domain.value_types import SerializedPayload


@dataclass(frozen=True)
class RelationFormula:
    weighted_frequency_weight: float
    support_ratio_weight: float
    modality_strength_weight: float
    source_diversity_weight: float
    enterprise_coverage_weight: float
    freshness_weight: float
    trusted_evidence_weight: float
    required_modality_weight: float
    preferred_modality_weight: float
    bonus_modality_weight: float
    unknown_modality_weight: float
    confidence_frequency_weight: float
    confidence_support_weight: float
    confidence_trusted_weight: float
    confidence_diversity_weight: float
    source_diversity_cap: float
    enterprise_coverage_cap: float
    support_document_cap: float
    freshness_decay_days: float
    trusted_source_threshold: float


@dataclass(frozen=True)
class EvidenceFact:
    evidence_id: int
    owner_type: str
    owner_ref: str
    quote: str
    alignment: str
    start: int | None
    end: int | None


@dataclass(frozen=True)
class SkillOccurrenceFact:
    skill_id: str
    normalized_skill_id: int
    requirement_id: str
    source_requirement_id: int
    evidence_id: int
    extraction_record_id: int
    modality: str


@dataclass(frozen=True)
class TextAggregateFact:
    kind: str
    text: str
    evidence_id: int | None


@dataclass(frozen=True)
class BuildIssueFact:
    object_type: str
    object_id: str
    reason: str
    document_id: str | None = None


@dataclass(frozen=True)
class BuildDocumentFacts:
    document_id: str
    raw_text: str
    authoritative: bool
    source_name: str
    enterprise_name: str
    published_at: datetime | None
    observed_at: datetime
    duplicate_cluster_key: str | None
    source_credibility: float
    extraction_confirmed: bool
    normalization_present: bool
    quality_assessed: bool
    normalized_position_id: str | None
    effective_weight: float
    evidence: tuple[EvidenceFact, ...]
    skill_occurrences: tuple[SkillOccurrenceFact, ...]
    aggregates: tuple[TextAggregateFact, ...]
    issues: tuple[BuildIssueFact, ...]


@dataclass(frozen=True)
class ManualRelationOverride:
    skill_id: str
    weight: float | None
    confidence: float | None
    importance_level: str | None


@dataclass(frozen=True)
class PreviousRelationWeight:
    skill_id: str
    weight: float


@dataclass(frozen=True)
class GraphBuildFacts:
    position_id: str
    base_version_id: int | None
    algorithm_version: str
    formula: RelationFormula
    position_profile_thresholds: PositionProfileThresholdConfig
    document_deduplication: dict[str, str]
    previous_sample_count: int
    previous_relations: tuple[PreviousRelationWeight, ...]
    documents: tuple[BuildDocumentFacts, ...]
    manual_overrides: tuple[ManualRelationOverride, ...]


@dataclass(frozen=True)
class BuildWindow:
    start: datetime | None
    end: datetime | None
    minimum_weight: float
    minimum_samples: int
    authoritative_only: bool


@dataclass(frozen=True)
class SamplePlan:
    document_id: str
    included: bool
    exclusion_reasons: tuple[str, ...]
    effective_weight: float


@dataclass(frozen=True)
class SupportPlan:
    position_id: str
    skill_id: str
    document_id: str
    requirement_id: str
    normalized_skill_id: int
    evidence_id: int
    source_requirement_id: int
    extraction_record_id: int
    modality: str


@dataclass(frozen=True)
class RelationMetrics:
    supporting_jd_count: int
    deduplicated_jd_count: int
    enterprise_count: int
    source_count: int
    evidence_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    raw_frequency: float
    quality_adjusted_frequency: float
    support_document_count: int
    support_count: int
    included_sample_count: int
    support_ratio: float
    weighted_frequency: float
    required_ratio: float
    preferred_ratio: float
    bonus_ratio: float
    unknown_ratio: float
    modality_distribution: "ModalityDistribution"
    source_diversity: int
    enterprise_coverage: int
    freshness_score: float
    trusted_evidence_ratio: float
    modality_strength: float
    normalized_quality_inputs: "NormalizedQualityInputs"

    def serialized(self) -> SerializedPayload:
        return {
            "supporting_jd_count": self.supporting_jd_count,
            "deduplicated_jd_count": self.deduplicated_jd_count,
            "enterprise_count": self.enterprise_count,
            "source_count": self.source_count,
            "evidence_count": self.evidence_count,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "raw_frequency": self.raw_frequency,
            "quality_adjusted_frequency": self.quality_adjusted_frequency,
            "support_document_count": self.support_document_count,
            "support_count": self.support_count,
            "included_sample_count": self.included_sample_count,
            "support_ratio": self.support_ratio,
            "weighted_frequency": self.weighted_frequency,
            "required_ratio": self.required_ratio,
            "preferred_ratio": self.preferred_ratio,
            "bonus_ratio": self.bonus_ratio,
            "unknown_ratio": self.unknown_ratio,
            "modality_distribution": self.modality_distribution.serialized(),
            "source_diversity": self.source_diversity,
            "enterprise_coverage": self.enterprise_coverage,
            "freshness_score": self.freshness_score,
            "trusted_evidence_ratio": self.trusted_evidence_ratio,
            "modality_strength": self.modality_strength,
            "normalized_quality_inputs": self.normalized_quality_inputs.serialized(),
        }


@dataclass(frozen=True)
class ModalityDistribution:
    required: float
    preferred: float
    bonus: float
    unknown: float

    def serialized(self) -> SerializedPayload:
        return {
            "required": self.required,
            "preferred": self.preferred,
            "bonus": self.bonus,
            "unknown": self.unknown,
        }


@dataclass(frozen=True)
class NormalizedQualityInputs:
    source_diversity: float
    enterprise_coverage: float
    support_sufficiency: float

    def serialized(self) -> SerializedPayload:
        return {
            "source_diversity": self.source_diversity,
            "enterprise_coverage": self.enterprise_coverage,
            "support_sufficiency": self.support_sufficiency,
        }


@dataclass(frozen=True)
class RelationScore:
    auto_weight: float
    auto_confidence: float
    modality_strength: float
    normalized_quality_inputs: NormalizedQualityInputs


def score_relation(metrics: RelationMetrics, formula: RelationFormula) -> RelationScore:
    """Calculate graph-relation weight and confidence from explicit values."""
    source_diversity = min(1.0, metrics.source_diversity / formula.source_diversity_cap)
    enterprise_coverage = min(
        1.0, metrics.enterprise_coverage / formula.enterprise_coverage_cap
    )
    support_sufficiency = min(
        1.0, metrics.support_document_count / formula.support_document_cap
    )
    modality_strength = (
        metrics.required_ratio * formula.required_modality_weight
        + metrics.preferred_ratio * formula.preferred_modality_weight
        + metrics.bonus_ratio * formula.bonus_modality_weight
        + metrics.unknown_ratio * formula.unknown_modality_weight
    )
    weight = (
        metrics.weighted_frequency * formula.weighted_frequency_weight
        + metrics.support_ratio * formula.support_ratio_weight
        + modality_strength * formula.modality_strength_weight
        + source_diversity * formula.source_diversity_weight
        + enterprise_coverage * formula.enterprise_coverage_weight
        + metrics.freshness_score * formula.freshness_weight
        + metrics.trusted_evidence_ratio * formula.trusted_evidence_weight
    )
    confidence = (
        metrics.weighted_frequency * formula.confidence_frequency_weight
        + support_sufficiency * formula.confidence_support_weight
        + metrics.trusted_evidence_ratio * formula.confidence_trusted_weight
        + source_diversity * formula.confidence_diversity_weight
    )
    return RelationScore(
        round(max(0.0, min(1.0, weight)), 4),
        round(max(0.0, min(1.0, confidence)), 4),
        round(modality_strength, 4),
        NormalizedQualityInputs(
            round(source_diversity, 4),
            round(enterprise_coverage, 4),
            round(support_sufficiency, 4),
        ),
    )


@dataclass(frozen=True)
class RelationPlan:
    skill_id: str
    status: str
    metrics: RelationMetrics
    auto_weight: float
    manual_weight: float | None
    final_weight: float
    auto_confidence: float
    manual_confidence: float | None
    final_confidence: float
    auto_importance_level: str
    manual_importance_level: str | None
    final_importance_level: str
    trend_score: float | None
    review_reference: str
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class AggregatePlan:
    kind: str
    text: str
    document_ids: tuple[str, ...]
    evidence_ids: tuple[int, ...]
    review_reference: str
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class GraphBuildReviewIntent:
    object_type: str
    object_reference: str
    reasons: ReviewReasonSet
    object_id: str | None = None


@dataclass(frozen=True)
class GraphBuildPlan:
    position_id: str
    base_version_id: int | None
    algorithm_version: str
    window: BuildWindow
    position_profile_thresholds: PositionProfileThresholdConfig
    document_deduplication: dict[str, str]
    samples: tuple[SamplePlan, ...]
    supports: tuple[SupportPlan, ...]
    relations: tuple[RelationPlan, ...]
    aggregates: tuple[AggregatePlan, ...]
    review_intents: tuple[GraphBuildReviewIntent, ...]
    included_samples: int
    excluded_samples: int
    summary: "BuildSummary"


@dataclass(frozen=True)
class BuildSummary:
    input_samples: int
    included_samples: int
    deduplicated_samples: int
    duplicate_samples: int
    excluded_samples: int
    relations: int
    minimum_valid_samples: int
    exclusion_reasons: SerializedPayload
    risk_count: int
    risk_sample_count: int
    risk_reasons: SerializedPayload
    manual_relation_count: int
    manual_field_count: int
    skill_profile_available: bool = False
    task_profile_available: bool = False
    requirement_profile_available: bool = False

    def serialized(self) -> SerializedPayload:
        return {
            "input": {"samples": self.input_samples},
            "valid": {"samples": self.included_samples},
            "deduplication": {
                "samples": self.deduplicated_samples,
                "duplicates": self.duplicate_samples,
            },
            "excluded": {
                "samples": self.excluded_samples,
                "by_reason": dict(self.exclusion_reasons),
            },
            "risks": {
                "count": self.risk_count,
                "affected_samples": self.risk_sample_count,
                "by_reason": dict(self.risk_reasons),
            },
            "manual_modifications": {
                "relations": self.manual_relation_count,
                "fields": self.manual_field_count,
            },
            "included_samples": self.included_samples,
            "excluded_samples": self.excluded_samples,
            "relations": self.relations,
            "minimum_valid_samples": self.minimum_valid_samples,
            "skill_profile_available": self.skill_profile_available,
            "task_profile_available": self.task_profile_available,
            "requirement_profile_available": self.requirement_profile_available,
        }


@dataclass(frozen=True)
class PersistedBuildRun:
    build_run_id: int
    status: str
    summary: BuildSummary
    objects: tuple["PersistedBuildObject", ...] = ()


@dataclass(frozen=True)
class PersistedBuildObject:
    reference: str
    object_type: str
    object_id: str


def _deduplicate_review_intents(
    intents: list[GraphBuildReviewIntent],
) -> tuple[GraphBuildReviewIntent, ...]:
    grouped: dict[tuple[str, str, str | None], list[str]] = {}
    for intent in intents:
        key = (intent.object_type, intent.object_reference, intent.object_id)
        grouped.setdefault(key, []).extend(intent.reasons.values)
    return tuple(
        GraphBuildReviewIntent(
            object_type,
            reference,
            ReviewReasonSet(tuple(dict.fromkeys(reasons))),
            object_id,
        )
        for (object_type, reference, object_id), reasons in grouped.items()
    )


def _trend(facts: GraphBuildFacts, skill_id: str, weight: float, samples: int) -> float | None:
    if samples < 3 or facts.previous_sample_count < 3:
        return None
    previous = next((item.weight for item in facts.previous_relations if item.skill_id == skill_id), None)
    return round(weight if previous is None else weight - previous, 4)


def build_graph_plan(
    facts: GraphBuildFacts, window: BuildWindow, *, now: datetime | None = None
) -> GraphBuildPlan:
    """Apply inclusion, scoring, aggregation and review rules without persistence."""
    current_time = now or datetime.now(timezone.utc)
    samples: list[SamplePlan] = []
    supports: list[SupportPlan] = []
    review_intents: list[GraphBuildReviewIntent] = []
    included: list[BuildDocumentFacts] = []
    for document in facts.documents:
        reasons: list[str] = []
        if not document.extraction_confirmed: reasons.append("extraction_not_confirmed")
        if not document.normalization_present: reasons.append("normalization_missing")
        if not document.quality_assessed: reasons.append("quality_not_assessed")
        if window.start and (not document.published_at or document.published_at < window.start): reasons.append("outside_window")
        if window.end and (not document.published_at or document.published_at >= window.end): reasons.append("outside_window")
        if document.normalized_position_id != facts.position_id: reasons.append("position_mismatch_or_unresolved")
        if document.effective_weight < window.minimum_weight: reasons.append("weight_below_threshold")
        samples.append(SamplePlan(document.document_id, not reasons, tuple(reasons), document.effective_weight))
        if reasons:
            continue
        included.append(document)
        review_intents.extend(
            GraphBuildReviewIntent(
                issue.object_type,
                f"existing:{issue.object_type}:{issue.object_id}",
                ReviewReasonSet((issue.reason,)),
                issue.object_id,
            )
            for issue in document.issues
        )
        for occurrence in document.skill_occurrences:
            if occurrence.modality == "unknown":
                continue
            supports.append(SupportPlan(
                facts.position_id, occurrence.skill_id, document.document_id,
                occurrence.requirement_id, occurrence.normalized_skill_id,
                occurrence.evidence_id, occurrence.source_requirement_id,
                occurrence.extraction_record_id, occurrence.modality,
            ))

    occurrences_by_skill: dict[
        str, list[tuple[BuildDocumentFacts, SkillOccurrenceFact]]
    ] = defaultdict(list)
    for document in included:
        for occurrence in document.skill_occurrences:
            if occurrence.modality == "unknown":
                continue
            occurrences_by_skill[occurrence.skill_id].append(
                (document, occurrence)
            )
    support_count_by_skill = Counter(
        support.skill_id for support in supports
    )

    aggregate: dict[str, list[tuple[BuildDocumentFacts, SkillOccurrenceFact]]] = defaultdict(list)
    for document in included:
        per_skill: dict[str, list[SkillOccurrenceFact]] = defaultdict(list)
        for occurrence in document.skill_occurrences:
            if occurrence.modality == "unknown":
                continue
            per_skill[occurrence.skill_id].append(occurrence)
        for skill_id, occurrences in per_skill.items():
            priority = {"unknown": 0, "bonus": 1, "preferred": 2, "required": 3}
            aggregate[skill_id].append((document, max(occurrences, key=lambda item: priority[item.modality])))

    overrides = {item.skill_id: item for item in facts.manual_overrides}
    relations: list[RelationPlan] = []
    denominator = max(len(included), 1)
    for skill_id, occurrences in aggregate.items():
        if all(occurrence.modality == "unknown" for _, occurrence in occurrences):
            continue
        raw_occurrences = occurrences_by_skill.get(skill_id, [])
        total_weight = max(sum(doc.effective_weight for doc, _ in occurrences), 0.0001)
        modalities = {name: 0.0 for name in ("required", "preferred", "bonus", "unknown")}
        freshness: list[float] = []
        sources: set[str] = set()
        enterprises: set[str] = set()
        trusted = 0
        for document, occurrence in occurrences:
            modalities[occurrence.modality] += document.effective_weight
            sources.add(document.source_name)
            if document.enterprise_name: enterprises.add(document.enterprise_name)
            published = document.published_at or current_time
            if published.tzinfo is None: published = published.replace(tzinfo=timezone.utc)
            freshness.append(math.exp(-max(0, (current_time - published).days) / facts.formula.freshness_decay_days))
            trusted += int(document.source_credibility >= facts.formula.trusted_source_threshold)
        required_ratio = round(modalities["required"] / total_weight, 4)
        preferred_ratio = round(modalities["preferred"] / total_weight, 4)
        bonus_ratio = round(modalities["bonus"] / total_weight, 4)
        unknown_ratio = round(modalities["unknown"] / total_weight, 4)
        if unknown_ratio > max(required_ratio, preferred_ratio, bonus_ratio):
            continue
        metrics = RelationMetrics(
            supporting_jd_count=len(raw_occurrences),
            deduplicated_jd_count=len({doc.document_id for doc, _ in raw_occurrences}),
            enterprise_count=len(enterprises),
            source_count=len(sources),
            evidence_count=len({
                occurrence.evidence_id for _, occurrence in raw_occurrences
            }),
            first_seen_at=(
                min(doc.observed_at for doc, _ in raw_occurrences).isoformat()
                if raw_occurrences else None
            ),
            last_seen_at=(
                max(doc.observed_at for doc, _ in raw_occurrences).isoformat()
                if raw_occurrences else None
            ),
            raw_frequency=round(len(occurrences) / denominator, 4),
            quality_adjusted_frequency=round(
                min(1.0, total_weight / denominator), 4
            ),
            support_document_count=len(occurrences),
            support_count=support_count_by_skill.get(skill_id, 0),
            included_sample_count=len(included),
            support_ratio=round(len(occurrences) / denominator, 4),
            weighted_frequency=round(min(1.0, total_weight / denominator), 4),
            required_ratio=required_ratio,
            preferred_ratio=preferred_ratio,
            bonus_ratio=bonus_ratio,
            unknown_ratio=unknown_ratio,
            modality_distribution=ModalityDistribution(
                required_ratio, preferred_ratio, bonus_ratio, unknown_ratio
            ),
            source_diversity=len(sources),
            enterprise_coverage=len(enterprises),
            freshness_score=round(sum(freshness) / max(len(freshness), 1), 4),
            trusted_evidence_ratio=round(trusted / max(len(occurrences), 1), 4),
            modality_strength=0,
            normalized_quality_inputs=NormalizedQualityInputs(0, 0, 0),
        )
        score = score_relation(metrics, facts.formula)
        metrics = replace(
            metrics,
            modality_strength=score.modality_strength,
            normalized_quality_inputs=score.normalized_quality_inputs,
        )
        weight = score.auto_weight
        confidence = score.auto_confidence
        level = "core" if weight >= 0.7 else "important" if weight >= 0.4 else "supplementary"
        override = overrides.get(skill_id)
        manual_weight = override.weight if override else None
        manual_confidence = override.confidence if override else None
        manual_level = override.importance_level if override else None
        reasons = []
        if confidence < 0.7: reasons.append("medium_or_low_confidence")
        if metrics.unknown_ratio > 0: reasons.append("unknown_modality")
        if len(occurrences) < 2: reasons.append("insufficient_evidence")
        relations.append(RelationPlan(
            skill_id, "draft" if override else "in_review" if reasons else "approved",
            metrics, weight, manual_weight, manual_weight if manual_weight is not None else weight,
            confidence, manual_confidence, manual_confidence if manual_confidence is not None else confidence,
            level, manual_level, manual_level or level,
            _trend(facts, skill_id, weight, len(included)),
            f"relation:{skill_id}",
            tuple(reasons or (("manually_carried_override",) if override else ())),
        ))

    grouped: dict[tuple[str, str], list[tuple[str, int | None]]] = defaultdict(list)
    for document in included:
        for item in document.aggregates:
            grouped[(item.kind, item.text)].append((document.document_id, item.evidence_id))
    kind_counts: dict[str, int] = defaultdict(int)
    for kind, _ in grouped: kind_counts[kind] += 1
    aggregate_plans = []
    for aggregate_index, ((kind, text), rows) in enumerate(grouped.items()):
        reasons = []
        if len(rows) < 2: reasons.append("low_confidence_merge" if kind == "responsibility" else "low_aggregate_confidence")
        if kind in ("education", "experience", "certificate") and kind_counts[kind] > 1: reasons.append("conflicting_requirements")
        aggregate_plans.append(AggregatePlan(
            kind,
            text,
            tuple(sorted({row[0] for row in rows})),
            tuple(row[1] for row in rows if row[1] is not None),
            f"aggregate:{aggregate_index}",
            tuple(reasons),
        ))
    review_intents.extend(
        GraphBuildReviewIntent(
            "position_skill_relation",
            relation.review_reference,
            ReviewReasonSet(relation.review_reasons),
        )
        for relation in relations
        if relation.review_reasons
    )
    review_intents.extend(
        GraphBuildReviewIntent(
            "position_task" if aggregate.kind == "responsibility" else "position_requirement",
            aggregate.review_reference,
            ReviewReasonSet(aggregate.review_reasons),
        )
        for aggregate in aggregate_plans
        if aggregate.review_reasons
    )
    if relations or aggregate_plans:
        review_intents.append(
            GraphBuildReviewIntent(
                "graph_version",
                "build",
                ReviewReasonSet(("pre_publish_overall_review",)),
            )
        )
    exclusion_counts: dict[str, int] = defaultdict(int)
    for sample in samples:
        for reason in sample.exclusion_reasons:
            exclusion_counts[reason] += 1
    risk_counts: dict[str, int] = defaultdict(int)
    risk_documents: set[str] = set()
    for document in facts.documents:
        for issue in document.issues:
            risk_counts[issue.reason] += 1
            risk_documents.add(document.document_id)
    deduplication_keys = {
        document.duplicate_cluster_key or f"document:{document.document_id}"
        for document in included
    }
    manual_field_count = sum(
        int(value is not None)
        for override in facts.manual_overrides
        for value in (override.weight, override.confidence, override.importance_level)
    )
    summary = BuildSummary(
        input_samples=len(samples),
        included_samples=len(included),
        deduplicated_samples=len(deduplication_keys),
        duplicate_samples=max(0, len(included) - len(deduplication_keys)),
        excluded_samples=len(samples) - len(included),
        relations=len(relations),
        minimum_valid_samples=window.minimum_samples,
        exclusion_reasons=dict(sorted(exclusion_counts.items())),
        risk_count=sum(risk_counts.values()),
        risk_sample_count=len(risk_documents),
        risk_reasons=dict(sorted(risk_counts.items())),
        manual_relation_count=len(overrides),
        manual_field_count=manual_field_count,
        skill_profile_available=bool(relations),
        task_profile_available=any(
            aggregate.kind == "responsibility"
            for aggregate in aggregate_plans
        ),
        requirement_profile_available=any(
            aggregate.kind != "responsibility"
            for aggregate in aggregate_plans
        ),
    )
    return GraphBuildPlan(
        facts.position_id, facts.base_version_id, facts.algorithm_version, window,
        facts.position_profile_thresholds,
        facts.document_deduplication,
        tuple(samples), tuple(supports), tuple(relations), tuple(aggregate_plans),
        _deduplicate_review_intents(review_intents),
        len(included), len(samples) - len(included),
        summary,
    )
