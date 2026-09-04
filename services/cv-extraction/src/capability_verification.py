from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .models import (
    CVCapabilityVerificationResult,
    CVMatchFeatureResult,
    CapabilityEvidenceLink,
    CapabilityProfile,
    ConfidenceBand,
    DemonstratedLevel,
    Evidence,
    MatchFeature,
)


CAPABILITY_VERIFICATION_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "capability_verification.yaml"
)


@lru_cache(maxsize=1)
def load_capability_verification_config() -> dict[str, Any]:
    payload = yaml.safe_load(CAPABILITY_VERIFICATION_CONFIG_PATH.read_text(encoding="utf-8"))
    required = {
        "version", "level_order", "display_labels", "declared_level_order",
        "level_thresholds", "signals",
        "confidence", "matching_bonus", "measurable_outcome_pattern",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError(f"capability_verification.yaml must contain: {sorted(required)}")
    return payload


CAPABILITY_VERIFICATION_DERIVATION_VERSION = str(
    load_capability_verification_config()["version"]
)


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{'_'.join(parts)}"


def _evidence_key(evidence: Evidence) -> tuple[Any, ...]:
    return (
        evidence.source_id,
        evidence.start,
        evidence.end,
        evidence.occurrence_index,
        evidence.quote,
    )


def _unique_evidence(features: list[MatchFeature]) -> list[Evidence]:
    found: dict[tuple[Any, ...], Evidence] = {}
    for feature in features:
        for evidence in feature.evidence_refs:
            found.setdefault(_evidence_key(evidence), evidence)
    return list(found.values())


def _confidence_band(value: float, config: dict[str, Any]) -> ConfidenceBand:
    confidence = config["confidence"]
    if value <= 0:
        return "none"
    if value >= float(confidence["high_threshold"]):
        return "high"
    if value >= float(confidence["medium_threshold"]):
        return "medium"
    return "low"


def _level_for_score(score: int, config: dict[str, Any]) -> DemonstratedLevel:
    thresholds = config["level_thresholds"]
    ordered = sorted(
        ((int(threshold), level) for level, threshold in thresholds.items()),
        reverse=True,
    )
    for threshold, level in ordered:
        if score >= threshold:
            return level
    return "unknown"


def _experience_context(
    occurrence: MatchFeature,
    features: list[MatchFeature],
) -> tuple[MatchFeature, list[MatchFeature]]:
    parts = occurrence.source_scope.split(":")
    if len(parts) < 3 or parts[0] not in {"work_experience", "project_experience"}:
        raise ValueError(f"Unsupported experience skill scope: {occurrence.source_scope}")
    scope, entry_id = parts[0], parts[1]
    experience = next(
        (
            feature
            for feature in features
            if feature.feature_type == "experience"
            and feature.source_scope == f"{scope}:{entry_id}"
        ),
        None,
    )
    if experience is None:
        raise ValueError(f"Missing experience MatchFeature for {occurrence.source_scope}")
    tasks = [
        feature
        for feature in features
        if feature.feature_type == "task"
        and feature.source_scope.startswith(f"{scope}:{entry_id}:")
    ]
    return experience, tasks


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _task_mentions_occurrence(task: MatchFeature, occurrence: MatchFeature) -> bool:
    skill_name = _normalized_text(occurrence.raw_text)
    task_text = _normalized_text(task.raw_text)
    if not skill_name:
        return False
    if skill_name == "c":
        pattern = r"(?<![A-Za-z0-9_])C(?![A-Za-z0-9_+#])"
    elif re.search(r"[\u4e00-\u9fff]", skill_name):
        return skill_name in task_text
    else:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(skill_name)}(?![A-Za-z0-9_])"
    return re.search(pattern, task_text) is not None


def _build_link(
    *,
    profile: CVMatchFeatureResult,
    aggregation_key: str,
    declared: list[MatchFeature],
    occurrence: MatchFeature,
    config: dict[str, Any],
) -> CapabilityEvidenceLink:
    experience, tasks = _experience_context(occurrence, profile.features)
    signals = config["signals"]
    confidence_weights = config["confidence"]
    score = int(signals["direct_experience_occurrence"])
    confidence = float(confidence_weights["direct_occurrence"])
    support_signals = ["direct_experience_occurrence"]
    direct_tasks = [
        task for task in tasks if _task_mentions_occurrence(task, occurrence)
    ]

    if direct_tasks:
        score += int(signals["direct_task_mention"])
        confidence += float(confidence_weights["direct_task_mention"])
        support_signals.append("direct_task_mention")
        additional_task_count = min(
            max(0, len(direct_tasks) - 1),
            int(signals["maximum_additional_direct_tasks"]),
        )
        if additional_task_count:
            score += int(signals["additional_direct_task"]) * additional_task_count
            confidence += (
                float(confidence_weights["additional_direct_task"])
                * additional_task_count
            )
            support_signals.append("additional_direct_task")
    if "duration_months" in experience.structured_values:
        score += int(signals["dated_experience"])
        confidence += float(confidence_weights["dated_experience"])
        support_signals.append("dated_experience")
    if experience.structured_values.get("project_role") or experience.structured_values.get("position"):
        score += int(signals["explicit_role"])
        confidence += float(confidence_weights["explicit_role"])
        support_signals.append("explicit_role")
    measurable_pattern = re.compile(config["measurable_outcome_pattern"])
    if any(measurable_pattern.search(task.raw_text) for task in direct_tasks):
        score += int(signals["directly_linked_measurable_outcome"])
        confidence += float(confidence_weights["directly_linked_measurable_outcome"])
        support_signals.append("directly_linked_measurable_outcome")

    related_features = [occurrence, experience, *direct_tasks]
    return CapabilityEvidenceLink(
        link_id=_stable_id("cap_link", profile.document_id, aggregation_key, occurrence.feature_id),
        document_id=profile.document_id,
        aggregation_key=aggregation_key,
        skill_id=occurrence.canonical_id,
        canonical_name=occurrence.canonical_name,
        declared_feature_ids=[feature.feature_id for feature in declared],
        experience_skill_feature_id=occurrence.feature_id,
        experience_feature_id=experience.feature_id,
        supporting_task_feature_ids=[feature.feature_id for feature in direct_tasks],
        support_signals=support_signals,
        support_score=score,
        demonstrated_level=_level_for_score(score, config),
        support_confidence=min(1.0, round(confidence, 4)),
        confidence_band=_confidence_band(min(1.0, confidence), config),
        evidence_refs=_unique_evidence(related_features),
        taxonomy_version=profile.taxonomy_version,
        derivation_version=CAPABILITY_VERIFICATION_DERIVATION_VERSION,
    )


def build_capability_verification(
    match_profile: CVMatchFeatureResult,
) -> CVCapabilityVerificationResult:
    config = load_capability_verification_config()
    groups: dict[str, list[MatchFeature]] = {}
    for feature in match_profile.features:
        if feature.feature_type not in {"skill", "soft_skill", "language"}:
            continue
        aggregation_key = feature.structured_values.get("aggregation_key")
        if isinstance(aggregation_key, str):
            groups.setdefault(aggregation_key, []).append(feature)

    profiles: list[CapabilityProfile] = []
    links: list[CapabilityEvidenceLink] = []
    level_order = config["level_order"]
    bonus_rules = config["matching_bonus"]
    for aggregation_key, occurrences in sorted(groups.items()):
        declared = [
            feature
            for feature in occurrences
            if feature.structured_values.get("occurrence_kind") == "declared"
        ]
        experienced = [
            feature
            for feature in occurrences
            if feature.structured_values.get("occurrence_kind") in {"work", "project"}
        ]
        group_links = [
            _build_link(
                profile=match_profile,
                aggregation_key=aggregation_key,
                declared=declared,
                occurrence=occurrence,
                config=config,
            )
            for occurrence in experienced
        ]
        links.extend(group_links)
        independent_experience_count = len(
            {link.experience_feature_id for link in group_links}
        )

        declared_levels = [feature.candidate_level for feature in declared if feature.candidate_level]
        declared_level = (
            max(
                declared_levels,
                key=lambda value: int(config["declared_level_order"][value]),
            )
            if declared_levels
            else None
        )
        if group_links:
            strongest = max(
                group_links,
                key=lambda link: (
                    link.support_score,
                    link.support_confidence,
                ),
            )
            repeated_experience_count = min(
                independent_experience_count,
                int(config["signals"]["maximum_repeated_experiences"]),
            ) - 1
            aggregate_support_score = strongest.support_score + (
                max(0, repeated_experience_count)
                * int(config["signals"]["repeated_independent_experience"])
            )
            demonstrated_level = _level_for_score(aggregate_support_score, config)
            confidence = strongest.support_confidence
            if repeated_experience_count > 0:
                confidence = min(
                    1.0,
                    confidence
                    + repeated_experience_count
                    * float(config["confidence"]["repeated_independent_experience"]),
                )
            status = (
                "experience_only"
                if not declared
                else "partially_supported"
                if all(not link.supporting_task_feature_ids for link in group_links)
                else "supported"
            )
        else:
            demonstrated_level = "unknown"
            aggregate_support_score = 0
            confidence = 0.0
            status = "not_observed"

        resolution_status = (
            "resolved" if any(feature.resolution_status == "resolved" for feature in occurrences)
            else "unresolved"
        )
        if resolution_status != "resolved":
            status = "unresolved"
        bonus = 0.0
        if resolution_status == "resolved" and group_links:
            if confidence >= float(config["confidence"]["high_threshold"]):
                bonus += float(bonus_rules["high_support"])
            elif confidence >= float(config["confidence"]["medium_threshold"]):
                bonus += float(bonus_rules["medium_support"])
            else:
                bonus += float(bonus_rules["partial_support"])
            if int(level_order[demonstrated_level]) >= int(level_order["advanced"]):
                bonus += float(bonus_rules["advanced_level"])
            if independent_experience_count >= 2:
                bonus += float(bonus_rules["repeated_experience"])
        bonus = min(float(bonus_rules["maximum"]), bonus)
        representative = next(
            (feature for feature in occurrences if feature.canonical_id is not None),
            occurrences[0],
        )
        profiles.append(
            CapabilityProfile(
                profile_id=_stable_id("cap_profile", match_profile.document_id, aggregation_key),
                document_id=match_profile.document_id,
                aggregation_key=aggregation_key,
                skill_id=representative.canonical_id,
                canonical_name=representative.canonical_name,
                declared_feature_ids=[feature.feature_id for feature in declared],
                experience_skill_feature_ids=[feature.feature_id for feature in experienced],
                evidence_link_ids=[link.link_id for link in group_links],
                declared_level=declared_level,
                demonstrated_level=demonstrated_level,
                demonstrated_level_label=config["display_labels"][demonstrated_level],
                verification_status=status,
                support_confidence=round(confidence, 4),
                confidence_band=_confidence_band(confidence, config),
                independent_experience_count=independent_experience_count,
                aggregate_support_score=aggregate_support_score,
                evidence_bonus=round(bonus, 4),
                resolution_status=resolution_status,
            )
        )

    return CVCapabilityVerificationResult(
        document_id=match_profile.document_id,
        taxonomy_version=match_profile.taxonomy_version,
        derivation_version=CAPABILITY_VERIFICATION_DERIVATION_VERSION,
        profiles=profiles,
        evidence_links=links,
    )
