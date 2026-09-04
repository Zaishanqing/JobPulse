"""Lightweight, deterministic input checks performed before clustering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.discovery import JDSnapshot
from app.domain.values import FrozenDict, JsonObject, freeze


INPUT_PRECHECK_POLICY_VERSION = "window-dedup-v1"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class InputPrecheckResult:
    snapshots: tuple[JDSnapshot, ...]
    report: JsonObject


def _enterprise_value(snapshot: JDSnapshot) -> str | None:
    extensions = snapshot.structured_data.extensions
    for key in ("enterprise_id", "company_id", "company_name"):
        value = extensions.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _complete_evidence(snapshot: JDSnapshot) -> bool | None:
    extensions = snapshot.structured_data.extensions
    for key in ("evidence_ids", "evidence", "evidences"):
        if key in extensions:
            return bool(extensions[key])
    return None


def _source_platform(snapshot: JDSnapshot) -> str | None:
    value = snapshot.structured_data.extensions.get("source_platform")
    return str(value).strip().casefold() if value is not None and str(value).strip() else None


def _quality_metrics(snapshots: tuple[JDSnapshot, ...]) -> dict[str, object]:
    enterprises = [_enterprise_value(item) for item in snapshots]
    sources = [_source_platform(item) for item in snapshots]
    dates = [item.publish_date for item in snapshots]
    evidence = [_complete_evidence(item) for item in snapshots]
    skills = [
        skill
        for item in snapshots
        for skill in (item.structured_data.required_skills + item.structured_data.bonus_skills)
    ]
    return {
        "enterprise_count": (
            len(set(enterprises)) if enterprises and all(enterprises) else UNAVAILABLE
        ),
        "source_count": (len(set(sources)) if sources and all(sources) else UNAVAILABLE),
        "time_coverage": (
            {
                "start": min(dates).isoformat(),
                "end": max(dates).isoformat(),
            }
            if dates and all(dates)
            else UNAVAILABLE
        ),
        "evidence_completeness_rate": (
            round(sum(bool(value) for value in evidence) / len(evidence), 4)
            if evidence and all(value is not None for value in evidence)
            else UNAVAILABLE
        ),
        "unresolved_skill_ratio": (
            round(
                sum(not skill.normalized_skill_id for skill in skills) / len(skills),
                4,
            )
            if skills
            else UNAVAILABLE
        ),
    }


def precheck_discovery_input(
    snapshots: tuple[JDSnapshot, ...],
    *,
    time_window_start: date | None,
    time_window_end: date | None,
    policy_version: str = INPUT_PRECHECK_POLICY_VERSION,
) -> InputPrecheckResult:
    ordered = tuple(
        sorted(
            snapshots,
            key=lambda item: (
                item.jd_id,
                item.source_fact_id,
                item.source_fact_version,
            ),
        )
    )
    duplicate_jd_count = len(ordered) - len({item.jd_id for item in ordered})
    seen_jd_ids: set[str] = set()
    valid: list[JDSnapshot] = []
    excluded: list[dict[str, object]] = []

    for item in ordered:
        reasons: list[str] = []
        if item.jd_id in seen_jd_ids:
            reasons.append("duplicate_jd_id")
        else:
            seen_jd_ids.add(item.jd_id)
        if item.publish_date is not None:
            if time_window_start and item.publish_date < time_window_start:
                reasons.append("outside_time_window")
            if time_window_end and item.publish_date > time_window_end:
                reasons.append("outside_time_window")
        if reasons:
            excluded.append(
                {
                    "jd_id": item.jd_id,
                    "source_fact_id": item.source_fact_id,
                    "reasons": sorted(set(reasons)),
                }
            )
        else:
            valid.append(item)

    deduplicated: list[JDSnapshot] = []
    for item in valid:
        deduplicated.append(item)

    raw_metrics = _quality_metrics(ordered)
    effective_metrics = _quality_metrics(tuple(deduplicated))
    report_value = {
        "policy_version": policy_version,
        "raw_jd_count": len(ordered),
        "valid_jd_count": len(valid),
        "deduplicated_jd_count": len(deduplicated),
        "duplicate_jd_count": duplicate_jd_count,
        **raw_metrics,
        "raw": {
            "jd_count": len(ordered),
            **raw_metrics,
        },
        "effective": {
            "jd_count": len(deduplicated),
            **effective_metrics,
        },
        "excluded_samples": sorted(
            excluded,
            key=lambda item: (
                str(item["jd_id"]),
                str(item["source_fact_id"]),
            ),
        ),
    }
    report = freeze(report_value)
    if not isinstance(report, FrozenDict):
        raise TypeError("input quality report must be a JSON object")
    return InputPrecheckResult(tuple(deduplicated), report)
