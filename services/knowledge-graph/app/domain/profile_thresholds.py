"""Position-profile publication thresholds and deterministic filtering."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.value_types import SerializedPayload

VALID_IMPORTANCE_LEVELS = ("core", "important", "supplementary")
VALID_MODALITIES = ("required", "preferred", "bonus")

LOW_MARKET_REQUIRED_PREVALENCE = "LOW_MARKET_REQUIRED_PREVALENCE"
INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT = (
    "INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT"
)
LOW_REQUIRED_PURITY = "LOW_REQUIRED_PURITY"
INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT = (
    "INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT"
)


@dataclass(frozen=True)
class SkillImportanceThreshold:
    min_support_ratio: float
    min_supporting_jd_count: int


@dataclass(frozen=True)
class ResponsibilityThreshold:
    min_support_ratio: float
    min_supporting_jd_count: int


@dataclass(frozen=True)
class RequiredSkillGate:
    min_required_prevalence: float
    min_required_prevalence_jd_count: int
    min_required_purity: float


@dataclass(frozen=True)
class RequirementMarketCalibration:
    status: str
    reason_codes: tuple[str, ...]

    @property
    def inflation_risk(self) -> bool:
        return self.status == "inflation_risk"


def classify_requirement_inflation(
    *,
    modality: str,
    supporting_jd_count: int,
    required_supporting_jd_count: int,
    required_prevalence: float,
    required_purity: float,
    enterprise_count: int,
    gate: RequiredSkillGate,
) -> RequirementMarketCalibration:
    """Calibrate one JD requirement against deduplicated market evidence.

    This preserves the source JD's claim. The result only controls whether that
    claim is representative enough to become a standard-position requirement.
    """
    if modality != "required":
        return RequirementMarketCalibration("not_applicable", ())

    reason_codes: list[str] = []
    if required_prevalence < gate.min_required_prevalence:
        reason_codes.append(LOW_MARKET_REQUIRED_PREVALENCE)
    if required_supporting_jd_count < gate.min_required_prevalence_jd_count:
        reason_codes.append(INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT)
    if required_purity < gate.min_required_purity:
        reason_codes.append(LOW_REQUIRED_PURITY)

    if not reason_codes:
        return RequirementMarketCalibration("market_supported", ())

    if enterprise_count >= 2 or supporting_jd_count >= 3:
        return RequirementMarketCalibration(
            "enterprise_specific", tuple(reason_codes)
        )

    reason_codes.append(INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT)
    return RequirementMarketCalibration("inflation_risk", tuple(reason_codes))


def requirement_inflation_risk_level(ratio: float) -> str:
    """Return the product-facing risk band; these are policy, not truth labels."""
    if ratio <= 0.20:
        return "low"
    if ratio <= 0.40:
        return "medium"
    return "high"


@dataclass(frozen=True)
class PositionProfileThresholdConfig:
    skill_importance: Mapping[str, SkillImportanceThreshold]
    responsibility: Mapping[str, ResponsibilityThreshold]
    required_skill_gate: RequiredSkillGate

    def serialized(self) -> SerializedPayload:
        return {
            "skill_importance": {
                level: {
                    "min_support_ratio": item.min_support_ratio,
                    "min_supporting_jd_count": item.min_supporting_jd_count,
                }
                for level, item in self.skill_importance.items()
            },
            "responsibility": {
                level: {
                    "min_support_ratio": item.min_support_ratio,
                    "min_supporting_jd_count": item.min_supporting_jd_count,
                }
                for level, item in self.responsibility.items()
            },
            "required_skill_gate": {
                "min_required_prevalence": self.required_skill_gate.min_required_prevalence,
                "min_required_prevalence_jd_count": (
                    self.required_skill_gate.min_required_prevalence_jd_count
                ),
                "min_required_purity": self.required_skill_gate.min_required_purity,
            },
        }

    @classmethod
    def from_serialized(
        cls, payload: Mapping | None
    ) -> PositionProfileThresholdConfig:
        default = DEFAULT_POSITION_PROFILE_THRESHOLDS.serialized()
        payload = payload or {}
        skill_payload = payload.get("skill_importance") or default["skill_importance"]
        responsibility_payload = (
            payload.get("responsibility") or default["responsibility"]
        )
        gate_payload = (
            payload.get("required_skill_gate") or default["required_skill_gate"]
        )
        return cls(
            skill_importance={
                level: SkillImportanceThreshold(
                    min_support_ratio=float(item["min_support_ratio"]),
                    min_supporting_jd_count=int(item["min_supporting_jd_count"]),
                )
                for level, item in skill_payload.items()
            },
            responsibility={
                level: ResponsibilityThreshold(
                    min_support_ratio=float(item["min_support_ratio"]),
                    min_supporting_jd_count=int(
                        item.get("min_supporting_jd_count", 0)
                    ),
                )
                for level, item in responsibility_payload.items()
            },
            required_skill_gate=RequiredSkillGate(
                min_required_prevalence=float(
                    gate_payload.get("min_required_prevalence", 0)
                ),
                min_required_prevalence_jd_count=int(
                    gate_payload.get("min_required_prevalence_jd_count", 0)
                ),
                min_required_purity=float(
                    gate_payload.get("min_required_purity", 0)
                ),
            ),
        )

DEFAULT_POSITION_PROFILE_THRESHOLDS = PositionProfileThresholdConfig(
    skill_importance={
        "core": SkillImportanceThreshold(0.15, 3),
        "important": SkillImportanceThreshold(0.10, 2),
        "supplementary": SkillImportanceThreshold(0.05, 2),
    },
    responsibility={
        "core": ResponsibilityThreshold(0.15, 3),
        "important": ResponsibilityThreshold(0.10, 2),
        "supplementary": ResponsibilityThreshold(0.05, 2),
    },
    required_skill_gate=RequiredSkillGate(0.15, 3, 0.5),
)


def build_config_version(
    algorithm_version: str,
    minimum_effective_weight: float,
    minimum_valid_samples: int,
    thresholds: PositionProfileThresholdConfig,
) -> str:
    payload = json.dumps(
        {
            "algorithm_version": algorithm_version,
            "minimum_effective_weight": minimum_effective_weight,
            "minimum_valid_samples": minimum_valid_samples,
            "position_profile_thresholds": thresholds.serialized(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{algorithm_version}:build:{digest}"


@dataclass(frozen=True)
class PositionProfileFilterResult:
    skill_relations: tuple[dict, ...]
    responsibilities: tuple[dict, ...]
    skill_retained_by_level: dict[str, int]
    skill_filtered_by_level: dict[str, int]
    responsibility_retained_by_level: dict[str, int]
    responsibility_filtered_by_level: dict[str, int]


def responsibility_key(item: Mapping) -> str:
    return str(item.get("aggregate_id") or item.get("text") or "")


def _responsibility_level(
    support_ratio: float,
    supporting_count: int,
    thresholds: Mapping[str, ResponsibilityThreshold],
) -> str | None:
    for level in ("core", "important", "supplementary"):
        level_threshold = thresholds[level]
        if (
            supporting_count >= level_threshold.min_supporting_jd_count
            and support_ratio >= level_threshold.min_support_ratio
        ):
            return level
    return None


def _profile_tier(
    supporting_count: int,
    support_ratio: float,
    thresholds: PositionProfileThresholdConfig,
) -> str | None:
    for level, tier in (
        ("core", "market_core"),
        ("important", "specialty"),
        ("supplementary", "observed"),
    ):
        level_threshold = thresholds.skill_importance[level]
        if (
            supporting_count >= level_threshold.min_supporting_jd_count
            and support_ratio >= level_threshold.min_support_ratio
        ):
            return tier
    return None


def apply_position_profile_thresholds(
    skill_relations: list[dict] | tuple[dict, ...],
    responsibilities: list[dict] | tuple[dict, ...],
    *,
    skill_supporting_jd_count: Mapping[str, int],
    skill_required_jd_count: Mapping[str, int],
    responsibility_supporting_jd_count: Mapping[str, int],
    total_dedup_jd_count: int,
    thresholds: PositionProfileThresholdConfig,
    skill_enterprise_count: Mapping[str, int] | None = None,
    skill_source_count: Mapping[str, int] | None = None,
) -> PositionProfileFilterResult:
    denominator = max(total_dedup_jd_count, 1)
    retained_skills: list[dict] = []
    retained_responsibilities: list[dict] = []
    skill_retained = {level: 0 for level in VALID_IMPORTANCE_LEVELS}
    skill_filtered = {level: 0 for level in VALID_IMPORTANCE_LEVELS}
    responsibility_retained = {level: 0 for level in VALID_IMPORTANCE_LEVELS}
    responsibility_filtered = {level: 0 for level in VALID_IMPORTANCE_LEVELS}

    for item in skill_relations:
        level = str(
            item.get("final_importance_level") or item.get("importance_level") or ""
        )
        if level not in thresholds.skill_importance:
            skill_filtered["invalid"] = skill_filtered.get("invalid", 0) + 1
            continue
        modality = str(item.get("primary_modality") or item.get("modality") or "")
        if modality not in VALID_MODALITIES:
            skill_filtered["invalid"] = skill_filtered.get("invalid", 0) + 1
            continue
        skill_id = str(item.get("skill_id") or "")
        supporting_count = int(skill_supporting_jd_count.get(skill_id, 0))
        required_count = int(skill_required_jd_count.get(skill_id, 0))
        support_ratio = supporting_count / denominator
        required_prevalence = required_count / denominator
        required_purity = (
            required_count / supporting_count if supporting_count else 0.0
        )
        level_threshold = thresholds.skill_importance[level]
        if (
            supporting_count < level_threshold.min_supporting_jd_count
            or support_ratio < level_threshold.min_support_ratio
        ):
            skill_filtered[level] = skill_filtered.get(level, 0) + 1
            continue
        tier = _profile_tier(supporting_count, support_ratio, thresholds)
        if tier is None:
            skill_filtered["invalid"] = skill_filtered.get("invalid", 0) + 1
            continue
        enterprise_count = int((skill_enterprise_count or {}).get(skill_id, 0))
        source_count = int((skill_source_count or {}).get(skill_id, 0))
        calibration = classify_requirement_inflation(
            modality=modality,
            supporting_jd_count=supporting_count,
            required_supporting_jd_count=required_count,
            required_prevalence=required_prevalence,
            required_purity=required_purity,
            enterprise_count=enterprise_count,
            gate=thresholds.required_skill_gate,
        )
        retained_skills.append(
            {
                **item,
                "profile_tier": tier,
                "support_ratio": round(support_ratio, 4),
                "supporting_jd_count": supporting_count,
                "required_supporting_jd_count": required_count,
                "required_prevalence": round(required_prevalence, 4),
                "required_purity": round(required_purity, 4),
                "enterprise_count": enterprise_count,
                "source_count": source_count,
                "requirement_market_status": calibration.status,
                "inflation_risk": calibration.inflation_risk,
                "inflation_reason_codes": list(calibration.reason_codes),
            }
        )
        skill_retained[level] = skill_retained.get(level, 0) + 1

    for item in responsibilities:
        supporting_count = int(
            responsibility_supporting_jd_count.get(responsibility_key(item), 0)
        )
        support_ratio = supporting_count / denominator
        level = _responsibility_level(
            support_ratio, supporting_count, thresholds.responsibility
        )
        if level is None:
            responsibility_filtered["supplementary"] = (
                responsibility_filtered.get("supplementary", 0) + 1
            )
            continue
        retained_responsibilities.append(
            {
                **item,
                "importance_level": level,
                "support_ratio": round(support_ratio, 4),
                "supporting_jd_count": supporting_count,
            }
        )
        responsibility_retained[level] = (
            responsibility_retained.get(level, 0) + 1
        )

    return PositionProfileFilterResult(
        tuple(retained_skills),
        tuple(retained_responsibilities),
        skill_retained,
        skill_filtered,
        responsibility_retained,
        responsibility_filtered,
    )
