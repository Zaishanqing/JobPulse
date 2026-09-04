from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace

from app.domain.profile_thresholds import DEFAULT_POSITION_PROFILE_THRESHOLDS
from app.domain.structured_facts import (
    CompanyFact,
    EmploymentFact,
    EvidenceFact,
    ExtractionFacts,
    SourcedTextFact,
    TaskRequirementFact,
)
from app.domain.value_types import JsonValue, SerializedPayload

EvidenceCoordinates = Mapping[str, int | str | None]
QualityMetrics = Mapping[str, float | str]


def _current_snapshot(snapshot: SerializedPayload) -> SerializedPayload:
    current = deepcopy(snapshot)
    if "skill_relations" not in current:
        current["skill_relations"] = deepcopy(current.get("skills", []))
    if "responsibilities" not in current:
        current["responsibilities"] = deepcopy(current.get("task_profile", []))
    if "algorithm_metadata" not in current:
        current["algorithm_metadata"] = deepcopy(current.get("algorithm_config", {}))
    for legacy in ("skills", "task_profile", "algorithm_config"):
        current.pop(legacy, None)
    return current


def calculate_trend_score(
    previous_snapshot: SerializedPayload | None,
    skill_id: str,
    current_normalized_score: float,
    current_sample_count: int,
    *,
    minimum_sample_count: int = 3,
) -> float | None:
    if previous_snapshot is None or current_sample_count < minimum_sample_count:
        return None
    previous = _current_snapshot(previous_snapshot)
    sample_stats = previous.get("sample_stats", {})
    if not isinstance(sample_stats, Mapping) or int(sample_stats.get("included_samples", 0)) < minimum_sample_count:
        return None
    relations = previous.get("skill_relations", [])
    prior = next(
        (
            item for item in relations
            if isinstance(item, Mapping) and item.get("skill_id") == skill_id
        ),
        None,
    )
    if prior is None:
        return round(current_normalized_score, 4)
    return round(current_normalized_score - float(prior["weight"]), 4)


def effective_weight(
    credibility: float, duplicate: float, copy: float, inflation: float
) -> float:
    return QualityScoringPolicy().effective_weight(
        credibility, duplicate, copy, inflation
    )


def quality_scores(
    raw: str, peers: Iterable[str] = ()
) -> SerializedPayload:
    score = QualityScoringPolicy().score(raw, peers)
    return {
        **score.as_dict(),
        "peer_comparisons": [],
    }


def version_diff(
    before_snapshot: SerializedPayload, after_snapshot: SerializedPayload
) -> SerializedPayload:
    before = _current_snapshot(before_snapshot)
    after = _current_snapshot(after_snapshot)
    before_relations = {
        str(item["skill_id"]): item
        for item in before.get("skill_relations", [])
        if isinstance(item, Mapping)
    }
    after_relations = {
        str(item["skill_id"]): item
        for item in after.get("skill_relations", [])
        if isinstance(item, Mapping)
    }

    def comparable(value: Mapping[str, JsonValue]) -> SerializedPayload:
        result = {key: item for key, item in value.items() if key != "relation_id"}
        if result.get("trend_score") in (None, 0, 0.0):
            result["trend_score"] = None
        return result

    def fields(
        left: Mapping[str, JsonValue], right: Mapping[str, JsonValue]
    ) -> SerializedPayload:
        first, second = comparable(left), comparable(right)
        return {
            key: {"before": first.get(key), "after": second.get(key)}
            for key in first.keys() | second.keys()
            if first.get(key) != second.get(key)
        }

    added = [after_relations[key] for key in sorted(after_relations.keys() - before_relations.keys())]
    removed = [before_relations[key] for key in sorted(before_relations.keys() - after_relations.keys())]
    changed = [
        {
            "skill_id": key,
            "before": before_relations[key],
            "after": after_relations[key],
            "changed_fields": fields(before_relations[key], after_relations[key]),
        }
        for key in sorted(before_relations.keys() & after_relations.keys())
        if comparable(before_relations[key]) != comparable(after_relations[key])
    ]
    return {"added": added, "removed": removed, "changed": changed, "context_changes": {}}


RELATION_ALGORITHM_CONFIG = {
    "weight_coefficients": {
        "weighted_frequency": 0.35, "modality_strength": 0.20,
        "source_diversity": 0.15, "enterprise_coverage": 0.10,
        "freshness_score": 0.10, "trusted_evidence_ratio": 0.10,
    },
    "modality_coefficients": {
        "required_ratio": 1.00, "preferred_ratio": 0.75,
        "bonus_ratio": 0.55, "unknown_ratio": 0.25,
    },
    "confidence_coefficients": {
        "weighted_frequency": 0.35, "support_sufficiency": 0.30,
        "trusted_evidence_ratio": 0.25, "source_diversity": 0.10,
    },
    "normalization": {
        "source_diversity_cap": 3, "enterprise_coverage_cap": 3,
        "support_document_cap": 3,
    },
    "freshness_decay_days": 365,
    "trusted_source_threshold": 0.70,
    "position_profile_thresholds": DEFAULT_POSITION_PROFILE_THRESHOLDS.serialized(),
}


def merged_relation_config(payload: SerializedPayload | None = None) -> SerializedPayload:
    return _deep_merge(RELATION_ALGORITHM_CONFIG, payload or {})


def relation_scores(
    metrics: SerializedPayload, config: SerializedPayload | None = None
) -> SerializedPayload:
    """Calculate explainable weight and independently-scoped confidence."""
    config = merged_relation_config(config)
    normalization = config["normalization"]
    source_diversity = min(
        1.0, metrics["source_diversity"] / normalization["source_diversity_cap"]
    )
    enterprise_coverage = min(
        1.0,
        metrics["enterprise_coverage"] / normalization["enterprise_coverage_cap"],
    )
    support_sufficiency = min(
        1.0,
        metrics["support_document_count"] / normalization["support_document_cap"],
    )
    modality_strength = sum(
        metrics[name] * coefficient
        for name, coefficient in config["modality_coefficients"].items()
    )
    weight_inputs = {
        "weighted_frequency": metrics["weighted_frequency"],
        "support_ratio": metrics.get("support_ratio", metrics["weighted_frequency"]),
        "modality_strength": modality_strength,
        "source_diversity": source_diversity,
        "enterprise_coverage": enterprise_coverage,
        "freshness_score": metrics["freshness_score"],
        "trusted_evidence_ratio": metrics["trusted_evidence_ratio"],
    }
    confidence_inputs = {
        "weighted_frequency": metrics["weighted_frequency"],
        "support_sufficiency": support_sufficiency,
        "trusted_evidence_ratio": metrics["trusted_evidence_ratio"],
        "source_diversity": source_diversity,
    }
    weight = sum(
        weight_inputs[name] * coefficient
        for name, coefficient in config["weight_coefficients"].items()
    )
    confidence = sum(
        confidence_inputs[name] * coefficient
        for name, coefficient in config["confidence_coefficients"].items()
    )
    return {
        "auto_weight": round(max(0.0, min(1.0, weight)), 4),
        "auto_confidence": round(max(0.0, min(1.0, confidence)), 4),
        "modality_strength": round(modality_strength, 4),
        "normalized_quality_inputs": {
            "source_diversity": round(source_diversity, 4),
            "enterprise_coverage": round(enterprise_coverage, 4),
            "support_sufficiency": round(support_sufficiency, 4),
        },
    }


def normalize_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def text_similarity(left: str, right: str) -> float:
    def grams(value: str) -> set[str]:
        normalized = normalize_key(value)
        return {
            normalized[index : index + 3]
            for index in range(max(0, len(normalized) - 2))
        }

    left_grams = grams(left)
    right_grams = grams(right)
    union = left_grams | right_grams
    return (len(left_grams & right_grams) / len(union)) if union else 1.0


def duplicate_cluster_key(*document_ids: str) -> str:
    content = json.dumps(
        sorted(set(document_ids)),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "duplicate:" + hashlib.sha256(content).hexdigest()


def _deep_merge(base: Mapping, override: Mapping) -> Mapping:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True)
class EvidenceAlignment(Mapping[str, int | str | None]):
    """Typed evidence coordinates with read-only mapping compatibility."""

    start: int | None
    end: int | None
    alignment: str
    occurrence_index: int | None

    def as_dict(self) -> EvidenceCoordinates:
        return {
            "start": self.start,
            "end": self.end,
            "alignment": self.alignment,
            "occurrence_index": self.occurrence_index,
        }

    def __getitem__(self, key: str) -> int | str | None:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 4


class EvidenceAligner:
    @staticmethod
    def align_quote(
        raw: str, quote: str, occurrence_index: int | None = None
    ) -> EvidenceAlignment:
        starts = [match.start() for match in re.finditer(re.escape(quote), raw)]
        if not starts:
            return EvidenceAlignment(None, None, "unresolved", None)
        if occurrence_index is not None and not 0 <= occurrence_index < len(starts):
            raise ValueError("occurrence_index is outside the available quote occurrences")
        index = occurrence_index or 0
        return EvidenceAlignment(
            starts[index], starts[index] + len(quote), "exact", index
        )

    def align(self, raw: str, payload: SerializedPayload) -> SerializedPayload:
        result = deepcopy(payload)

        def walk(value: JsonValue) -> None:
            if isinstance(value, dict):
                if "quote" in value and "alignment" in value:
                    value.update(
                        self.align_quote(
                            raw, value["quote"], value.get("occurrence_index")
                        ).as_dict()
                    )
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)
        return result

    def align_facts(self, raw: str, facts: ExtractionFacts) -> ExtractionFacts:
        """Return extraction facts with every evidence coordinate recalculated."""

        def evidence(item: EvidenceFact) -> EvidenceFact:
            aligned = self.align_quote(raw, item.quote, item.occurrence_index)
            return replace(
                item,
                start=aligned.start,
                end=aligned.end,
                alignment=aligned.alignment,
                occurrence_index=aligned.occurrence_index,
            )

        return replace(
            facts,
            job_title=(
                SourcedTextFact(facts.job_title.text, evidence(facts.job_title.evidence))
                if facts.job_title
                else None
            ),
            responsibilities=tuple(
                TaskRequirementFact(item.requirement_id, item.text, evidence(item.evidence))
                for item in facts.responsibilities
            ),
            requirements=tuple(
                replace(item, evidence=evidence(item.evidence))
                for item in facts.requirements
            ),
            company_facts=tuple(
                CompanyFact(item.fact_id, item.text, evidence(item.evidence))
                for item in facts.company_facts
            ),
            employment_facts=tuple(
                EmploymentFact(
                    item.fact_id, item.fact_type, item.text, evidence(item.evidence)
                )
                for item in facts.employment_facts
            ),
        )


def align_quote(
    raw: str, quote: str, occurrence_index: int | None = None
) -> EvidenceCoordinates:
    """Compatibility function backed by the single EvidenceAligner implementation."""
    return EvidenceAligner.align_quote(raw, quote, occurrence_index).as_dict()


def align_extraction(raw: str, payload: SerializedPayload) -> SerializedPayload:
    """Compatibility function backed by the single EvidenceAligner implementation."""
    return EvidenceAligner().align(raw, payload)


@dataclass(frozen=True)
class QualityAssessment(Mapping[str, float | str]):
    """Quality calculation result passed from Domain to persistence."""

    normalization_version: str
    duplicate_score: float
    copy_risk_score: float
    inflation_score: float

    def as_dict(self) -> QualityMetrics:
        return {
            "normalization_version": self.normalization_version,
            "duplicate_score": self.duplicate_score,
            "copy_risk_score": self.copy_risk_score,
            "inflation_score": self.inflation_score,
        }

    def __getitem__(self, key: str) -> float | str:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 4


@dataclass(frozen=True)
class QualityScoringPolicy:
    def effective_weight(
        self, credibility: float, duplicate: float, copy: float, inflation: float
    ) -> float:
        value = credibility * (1 - 0.60 * duplicate) * (1 - 0.40 * copy) * (
            1 - 0.50 * inflation
        )
        return round(max(0.05, min(1.0, value)), 6)

    def score(self, raw: str, peers: Iterable[str] = ()) -> QualityAssessment:
        duplicate = max((text_similarity(raw, peer) for peer in peers), default=0.0)
        markers = len(
            re.findall(r"Python|SQL|Docker|React|PyTorch|技能|熟悉|精通", raw, re.IGNORECASE)
        )
        return QualityAssessment(
            normalization_version="normalization-v1",
            duplicate_score=round(duplicate, 4),
            copy_risk_score=round(
                duplicate if duplicate >= 0.8 else duplicate * 0.75, 4
            ),
            inflation_score=round(min(1.0, max(0.0, (markers - 8) / 12)), 4),
        )


@dataclass(frozen=True)
class ModalitySelectionPolicy:
    priority: Mapping[str, int] | None = None

    def select(self, values: Iterable[str]) -> str:
        priority = self.priority or {"unknown": 0, "bonus": 1, "preferred": 2, "required": 3}
        return max(values, key=lambda value: priority[value], default="unknown")


@dataclass(frozen=True)
class RelationScoringPolicy:
    config: SerializedPayload

    def score(self, metrics: SerializedPayload) -> SerializedPayload:
        normalization = self.config["normalization"]
        source_diversity = min(
            1.0, metrics["source_diversity"] / normalization["source_diversity_cap"]
        )
        enterprise_coverage = min(
            1.0,
            metrics["enterprise_coverage"] / normalization["enterprise_coverage_cap"],
        )
        support_sufficiency = min(
            1.0,
            metrics["support_document_count"] / normalization["support_document_cap"],
        )
        modality_strength = sum(
            metrics[name] * coefficient
            for name, coefficient in self.config["modality_coefficients"].items()
        )
        weight_inputs = {
            "weighted_frequency": metrics["weighted_frequency"],
            "support_ratio": metrics.get("support_ratio", metrics["weighted_frequency"]),
            "modality_strength": modality_strength,
            "source_diversity": source_diversity,
            "enterprise_coverage": enterprise_coverage,
            "freshness_score": metrics["freshness_score"],
            "trusted_evidence_ratio": metrics["trusted_evidence_ratio"],
        }
        confidence_inputs = {
            "weighted_frequency": metrics["weighted_frequency"],
            "support_sufficiency": support_sufficiency,
            "trusted_evidence_ratio": metrics["trusted_evidence_ratio"],
            "source_diversity": source_diversity,
        }
        return {
            "auto_weight": round(
                sum(
                    weight_inputs[name] * coefficient
                    for name, coefficient in self.config["weight_coefficients"].items()
                ),
                4,
            ),
            "auto_confidence": round(
                sum(
                    confidence_inputs[name] * coefficient
                    for name, coefficient in self.config["confidence_coefficients"].items()
                ),
                4,
            ),
            "modality_strength": round(modality_strength, 4),
        }


class VersionDiffPolicy:
    @staticmethod
    def diff(
        before: SerializedPayload, after: SerializedPayload
    ) -> SerializedPayload:
        keys = sorted(set(before) | set(after))
        return {
            "changed_sections": [key for key in keys if before.get(key) != after.get(key)],
            "before_hashable": before,
            "after_hashable": after,
        }
