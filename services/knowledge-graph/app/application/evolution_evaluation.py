"""Competition evaluation for evolution event detection over graph snapshots."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.evolution import (
    EVENT_TYPES,
    detect_evolution_events,
    _normalize_snapshot,
    _position_name,
    _responsibility_set,
    _weight,
)


DATASET_VERSION = "evolution-event-competition.v1"
REPORT_VERSION = "evolution-event-competition-report.v1"
FULL_VERSION = "position-evolution-events-v4"
BASELINE_VERSION = "snapshot-diff-added-removed-changed.v1"
NOT_COVERED: list[str] = ["position_split", "position_merge"]
JsonObject = dict[str, Any]
JsonRows = list[JsonObject]

REQUIRED_CASE_FIELDS = {
    "case_id",
    "snapshots",
    "expected_events",
    "taxonomy_context",
    "annotation_note",
}


def load_dataset(path: Path) -> JsonObject:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"evolution evaluation dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"evolution evaluation dataset is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("evolution evaluation dataset root must be an object")
    if dataset.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"dataset_version must be {DATASET_VERSION}")
    if dataset.get("provenance") != "synthetic_manually_labelled":
        raise ValueError("provenance must be synthetic_manually_labelled")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty array")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case[{index}] must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case[{index}] missing required fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"case[{index}] case_id must be non-empty and unique")
        seen_ids.add(case_id)
        snapshots = case["snapshots"]
        if not isinstance(snapshots, dict) or not isinstance(
            snapshots.get("before"), dict
        ) or not isinstance(snapshots.get("after"), dict):
            raise ValueError(f"case {case_id} snapshots.before/after must be objects")
        expected_events = case["expected_events"]
        if not isinstance(expected_events, list):
            raise ValueError(f"case {case_id} expected_events must be an array")
        signatures: set[tuple[str, ...]] = set()
        for event_index, event in enumerate(expected_events):
            if not isinstance(event, dict):
                raise ValueError(f"case {case_id} expected_events[{event_index}] must be an object")
            event_type = event.get("event_type")
            if event_type not in EVENT_TYPES:
                raise ValueError(
                    f"case {case_id} expected_events[{event_index}] has invalid event_type"
                )
            if not isinstance(event.get("source_keys", []), list) or not isinstance(
                event.get("target_keys", []), list
            ):
                raise ValueError(
                    f"case {case_id} expected_events[{event_index}] source/target_keys must be arrays"
                )
            signature = (
                str(event_type),
                tuple(sorted(str(value) for value in event.get("source_keys", []))),
                tuple(sorted(str(value) for value in event.get("target_keys", []))),
            )
            if signature in signatures:
                raise ValueError(f"case {case_id} expected event is duplicated")
            signatures.add(signature)
        if not isinstance(case["taxonomy_context"], dict) or not case["taxonomy_context"]:
            raise ValueError(f"case {case_id} taxonomy_context must be a non-empty object")
        if not isinstance(case.get("annotation_note"), str) or not case["annotation_note"]:
            raise ValueError(f"case {case_id} annotation_note must be a non-empty string")
        for version_field in ("from_version_id", "to_version_id"):
            value = case.get(version_field)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"case {case_id} {version_field} must be a non-negative integer")


def _event(
    *,
    event_type: str,
    source_keys: list[str],
    target_keys: list[str],
    basis: str,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "source_keys": sorted(source_keys),
        "target_keys": sorted(target_keys),
        "basis": basis,
    }


def _normalized_snapshot(snapshot: Mapping[str, object]) -> dict:
    return _normalize_snapshot(snapshot)


def _baseline_skill_events(
    before: dict, after: dict, position_id: str, from_version_id: int, to_version_id: int
) -> list[dict[str, Any]]:
    before_relations = {
        str(item["skill_id"]): item
        for item in before.get("skill_relations", [])
        if isinstance(item, dict) and item.get("skill_id")
    }
    after_relations = {
        str(item["skill_id"]): item
        for item in after.get("skill_relations", [])
        if isinstance(item, dict) and item.get("skill_id")
    }
    events: list[dict[str, Any]] = []
    for skill_id in sorted(before_relations.keys() | after_relations.keys()):
        before_item = before_relations.get(skill_id)
        after_item = after_relations.get(skill_id)
        if before_item is None:
            if _weight(after_item) >= 0.1:
                events.append(
                    _event(
                        event_type="skill_emergence",
                        source_keys=[],
                        target_keys=[skill_id],
                        basis="added_skill",
                    )
                )
        elif after_item is None:
            if _weight(before_item) >= 0.1:
                events.append(
                    _event(
                        event_type="skill_decline",
                        source_keys=[skill_id],
                        target_keys=[],
                        basis="removed_skill",
                    )
                )
        else:
            delta = _weight(after_item) - _weight(before_item)
            if delta >= 0.05:
                events.append(
                    _event(
                        event_type="skill_emergence",
                        source_keys=[],
                        target_keys=[skill_id],
                        basis="weight_increased",
                    )
                )
            elif delta <= -0.05:
                events.append(
                    _event(
                        event_type="skill_decline",
                        source_keys=[skill_id],
                        target_keys=[],
                        basis="weight_decreased",
                    )
                )
    return events


def _baseline_responsibility_event(
    before: dict, after: dict, position_id: str, from_version_id: int, to_version_id: int
) -> dict[str, Any] | None:
    before_set = _responsibility_set(before)
    after_set = _responsibility_set(after)
    removed = sorted(before_set - after_set)
    added = sorted(after_set - before_set)
    union = before_set | after_set
    similarity = len(before_set & after_set) / len(union) if union else 1.0
    if len(removed) + len(added) < 2 or similarity >= 0.6:
        return None
    return _event(
        event_type="responsibility_shift",
        source_keys=list(removed),
        target_keys=list(added),
        basis="responsibility_added_removed",
    )


def _baseline_role_event(
    before: dict, after: dict, position_id: str, from_version_id: int, to_version_id: int
) -> dict[str, Any] | None:
    before_skills = len(before.get("skill_relations", []))
    after_skills = len(after.get("skill_relations", []))
    before_responsibilities = len(_responsibility_set(before))
    after_responsibilities = len(_responsibility_set(after))
    delta = (after_skills - before_skills) + (
        after_responsibilities - before_responsibilities
    )
    if delta > 0:
        return _event(
            event_type="role_expansion",
            source_keys=[],
            target_keys=[],
            basis="breadth_increased",
        )
    if delta < 0:
        return _event(
            event_type="role_contraction",
            source_keys=[],
            target_keys=[],
            basis="breadth_decreased",
        )
    return None


def snapshot_diff_baseline(
    before_snapshot: JsonObject,
    after_snapshot: JsonObject,
    *,
    position_id: str,
    from_version_id: int,
    to_version_id: int,
) -> JsonRows:
    before = _normalized_snapshot(before_snapshot)
    after = _normalized_snapshot(after_snapshot)
    events = _baseline_skill_events(
        before, after, position_id, from_version_id, to_version_id
    )
    responsibility_event = _baseline_responsibility_event(
        before, after, position_id, from_version_id, to_version_id
    )
    if responsibility_event is not None:
        events.append(responsibility_event)
    role_event = _baseline_role_event(
        before, after, position_id, from_version_id, to_version_id
    )
    if role_event is not None:
        events.append(role_event)
    before_name = _position_name(before)
    after_name = _position_name(after)
    if before_name and after_name and before_name != after_name:
        events.append(
            _event(
                event_type="position_rename",
                source_keys=[before_name],
                target_keys=[after_name],
                basis="position_name_changed",
            )
        )
    return sorted(events, key=lambda item: (item["event_type"], item["source_keys"]))


def _predicted_keys(event: dict[str, Any]) -> tuple[list[str], list[str]]:
    event_type = event["event_type"]
    if event_type == "position_rename":
        return (
            [str(value) for value in event["source_entities"]],
            [str(value) for value in event["target_entities"]],
        )
    if event_type == "skill_emergence":
        return (
            [],
            [
                str(item.get("skill_id"))
                for item in event["target_entities"]
                if isinstance(item, dict) and item.get("skill_id")
            ],
        )
    if event_type == "skill_decline":
        return (
            [
                str(item.get("skill_id"))
                for item in event["source_entities"]
                if isinstance(item, dict) and item.get("skill_id")
            ],
            [],
        )
    if event_type in {"skill_replacement", "technology_stack_migration"}:
        return (
            [
                str(item.get("skill_id"))
                for item in event["source_entities"]
                if isinstance(item, dict) and item.get("skill_id")
            ],
            [
                str(item.get("skill_id"))
                for item in event["target_entities"]
                if isinstance(item, dict) and item.get("skill_id")
            ],
        )
    if event_type == "responsibility_shift":
        return (
            [str(value) for value in event["source_entities"]],
            [str(value) for value in event["target_entities"]],
        )
    return [], []


def _normalize_event(event: dict[str, Any], *, raw: bool = False) -> dict[str, Any]:
    source_keys, target_keys = _predicted_keys(event)
    normalized = {
        "event_type": event["event_type"],
        "source_keys": sorted(source_keys),
        "target_keys": sorted(target_keys),
        "confidence": float(event.get("confidence", 1.0)),
        "magnitude": float(event.get("magnitude", 0.0)),
        "reason": event.get("reason", ""),
    }
    if raw:
        normalized["raw_event"] = event
    return normalized


def _normalize_expected(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": str(event["event_type"]),
        "source_keys": sorted(str(value) for value in event.get("source_keys", [])),
        "target_keys": sorted(str(value) for value in event.get("target_keys", [])),
    }


def _events_match(expected: dict[str, Any], predicted: dict[str, Any]) -> bool:
    if expected["event_type"] != predicted["event_type"]:
        return False
    event_type = expected["event_type"]
    if event_type in {"role_expansion", "role_contraction"}:
        return True
    if event_type == "responsibility_shift":
        expected_keys = set(expected["source_keys"]) | set(expected["target_keys"])
        predicted_keys = set(predicted["source_keys"]) | set(predicted["target_keys"])
        if not expected_keys:
            return True
        return bool(expected_keys & predicted_keys)
    return (
        set(expected["source_keys"]) == set(predicted["source_keys"])
        and set(expected["target_keys"]) == set(predicted["target_keys"])
    )


def _is_evidence(predicted: dict[str, Any], expected: dict[str, Any]) -> bool:
    event_type = expected["event_type"]
    if event_type in {"skill_replacement", "technology_stack_migration"}:
        if predicted["event_type"] in {"skill_emergence", "skill_decline", "skill_replacement"}:
            expected_keys = set(expected["source_keys"]) | set(expected["target_keys"])
            predicted_keys = set(predicted["source_keys"]) | set(predicted["target_keys"])
            return bool(expected_keys & predicted_keys)
    if event_type == "role_expansion":
        return predicted["event_type"] == "skill_emergence"
    if event_type == "role_contraction":
        return predicted["event_type"] == "skill_decline"
    return False


def event_metrics(
    expected_events: JsonRows,
    predicted_events: JsonRows,
    *,
    suppress_evidence: bool,
) -> JsonObject:
    expected_norm = [_normalize_expected(item) for item in expected_events]
    predicted_norm = [dict(item) for item in predicted_events]
    matched_expected: set[int] = set()
    matched_pred: set[int] = set()
    matched_pairs: list[tuple[int, int]] = []
    used_pred: set[int] = set()
    for expected_index, expected in enumerate(expected_norm):
        for predicted_index, predicted in enumerate(predicted_norm):
            if predicted_index in used_pred:
                continue
            if _events_match(expected, predicted):
                matched_expected.add(expected_index)
                matched_pred.add(predicted_index)
                matched_pairs.append((expected_index, predicted_index))
                used_pred.add(predicted_index)
                break
    primary_indices = list(used_pred)
    for predicted_index, predicted in enumerate(predicted_norm):
        if predicted_index in used_pred:
            continue
        if suppress_evidence and any(
            _is_evidence(predicted, expected_norm[expected_index])
            for expected_index in matched_expected
        ):
            continue
        primary_indices.append(predicted_index)
    primary_events = [predicted_norm[index] for index in primary_indices]
    tp = len(matched_expected)
    fp = len(primary_events) - tp
    fn = len(expected_norm) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    type_correct = sum(
        expected_norm[expected_index]["event_type"]
        == predicted_norm[predicted_index]["event_type"]
        for expected_index, predicted_index in matched_pairs
    )
    type_accuracy = (
        round(type_correct / len(matched_pred), 6) if matched_pred else None
    )
    expected_replacements = [
        item for item in expected_norm if item["event_type"] == "skill_replacement"
    ]
    predicted_replacements = [
        item for item in primary_events if item["event_type"] == "skill_replacement"
    ]
    matched_replacements = [
        item
        for expected_index, item in enumerate(expected_norm)
        if expected_index in matched_expected
        and item["event_type"] == "skill_replacement"
    ]
    replacement_precision = (
        round(len(matched_replacements) / len(predicted_replacements), 6)
        if predicted_replacements
        else None
    )
    replacement_recall = (
        round(len(matched_replacements) / len(expected_replacements), 6)
        if expected_replacements
        else None
    )
    replacement_f1 = (
        round(
            2
            * replacement_precision
            * replacement_recall
            / (replacement_precision + replacement_recall),
            6,
        )
        if replacement_precision is not None
        and replacement_recall is not None
        and replacement_precision + replacement_recall > 0
        else None
    )
    coverage_total = 0.0
    for expected_index, expected in enumerate(expected_norm):
        expected_keys = set(expected["source_keys"]) | set(expected["target_keys"])
        if expected_keys:
            found_keys = {
                key
                for predicted in predicted_norm
                for key in set(predicted["source_keys"]) | set(predicted["target_keys"])
            }
            coverage = len(expected_keys & found_keys) / len(expected_keys)
        else:
            coverage = 1.0 if expected_index in matched_expected else 0.0
        coverage_total += coverage
    evidence_coverage = (
        round(coverage_total / len(expected_norm), 6) if expected_norm else None
    )
    return {
        "expected_event_count": len(expected_norm),
        "predicted_primary_event_count": len(primary_events),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "event_precision": precision,
        "event_recall": recall,
        "event_f1": f1,
        "event_type_accuracy": type_accuracy,
        "replacement_expected_count": len(expected_replacements),
        "replacement_predicted_count": len(predicted_replacements),
        "replacement_matched_count": len(matched_replacements),
        "replacement_precision": replacement_precision,
        "replacement_recall": replacement_recall,
        "replacement_f1": replacement_f1,
        "replacement_false_positive_count": len(predicted_replacements)
        - len(matched_replacements),
        "false_event_rate": round(fp / len(primary_events), 6) if primary_events else 0.0,
        "evidence_coverage": evidence_coverage,
    }


def _failure_cases(
    expected_events: list[dict[str, Any]],
    predicted_events: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    method: str,
    case: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_norm = [_normalize_expected(item) for item in expected_events]
    predicted_norm = [dict(item) for item in predicted_events]
    used_pred: set[int] = set()
    matched_expected: set[int] = set()
    for expected_index, expected in enumerate(expected_norm):
        for predicted_index, predicted in enumerate(predicted_norm):
            if predicted_index in used_pred:
                continue
            if _events_match(expected, predicted):
                used_pred.add(predicted_index)
                matched_expected.add(expected_index)
                break
    failures: list[dict[str, Any]] = []
    for expected_index, expected in enumerate(expected_norm):
        if expected_index in matched_expected:
            continue
        failures.append(
            {
                "method": method,
                "expected_event": expected,
                "predicted_event": None,
                "why_failed": "no matching predicted high-level event",
                "related_taxonomy/context": case["taxonomy_context"],
            }
        )
    for predicted_index, predicted in enumerate(predicted_norm):
        if predicted_index in used_pred:
            continue
        failures.append(
            {
                "method": method,
                "expected_event": None,
                "predicted_event": predicted,
                "why_failed": "false positive predicted event",
                "related_taxonomy/context": case["taxonomy_context"],
            }
        )
    return failures


def _aggregate(case_metric_list: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = defaultdict(int)
    coverage_total = 0.0
    coverage_count = 0
    type_total = 0
    type_count = 0
    for metrics in case_metric_list:
        for key in (
            "tp",
            "fp",
            "fn",
            "replacement_expected_count",
            "replacement_predicted_count",
            "replacement_matched_count",
            "replacement_false_positive_count",
        ):
            totals[key] += int(metrics[key])
        totals["predicted_primary_event_count"] += int(
            metrics["predicted_primary_event_count"]
        )
        if metrics["evidence_coverage"] is not None:
            coverage_total += float(metrics["evidence_coverage"]) * float(
                metrics["expected_event_count"]
            )
            coverage_count += int(metrics["expected_event_count"])
        if metrics["event_type_accuracy"] is not None:
            type_total += float(metrics["event_type_accuracy"]) * int(
                metrics["tp"]
            )
            type_count += int(metrics["tp"])
    precision = (
        round(totals["tp"] / (totals["tp"] + totals["fp"]), 6)
        if totals["tp"] + totals["fp"]
        else None
    )
    recall = (
        round(totals["tp"] / (totals["tp"] + totals["fn"]), 6)
        if totals["tp"] + totals["fn"]
        else None
    )
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    replacement_precision = (
        round(
            totals["replacement_matched_count"]
            / totals["replacement_predicted_count"],
            6,
        )
        if totals["replacement_predicted_count"]
        else None
    )
    replacement_recall = (
        round(
            totals["replacement_matched_count"]
            / totals["replacement_expected_count"],
            6,
        )
        if totals["replacement_expected_count"]
        else None
    )
    replacement_f1 = (
        round(
            2
            * replacement_precision
            * replacement_recall
            / (replacement_precision + replacement_recall),
            6,
        )
        if replacement_precision is not None
        and replacement_recall is not None
        and replacement_precision + replacement_recall > 0
        else None
    )
    return {
        "event_precision": precision,
        "event_recall": recall,
        "event_f1": f1,
        "event_type_accuracy": (
            round(type_total / type_count, 6) if type_count else None
        ),
        "replacement_precision": replacement_precision,
        "replacement_recall": replacement_recall,
        "replacement_f1": replacement_f1,
        "replacement_false_positive_count": totals["replacement_false_positive_count"],
        "false_event_rate": (
            round(
                totals["fp"] / totals["predicted_primary_event_count"], 6
            )
            if totals["predicted_primary_event_count"]
            else 0.0
        ),
        "evidence_coverage": (
            round(coverage_total / coverage_count, 6) if coverage_count else None
        ),
        "total_expected_events": sum(
            int(item["expected_event_count"]) for item in case_metric_list
        ),
        "total_predicted_primary_events": totals["predicted_primary_event_count"],
        "formulas": {
            "event_precision": "matched primary events / predicted primary events",
            "event_recall": "matched primary events / expected events",
            "event_type_accuracy": "matched events with correct event_type / matched events",
            "replacement_accuracy": (
                "matched skill_replacement events / expected skill_replacement events"
            ),
            "false_event_rate": "unmatched primary predicted events / predicted primary events",
            "evidence_coverage": (
                "expected source/target entity keys found in any predicted event / expected keys"
            ),
        },
    }


def evaluate_dataset(dataset: JsonObject) -> JsonObject:
    validate_dataset(dataset)
    case_outputs: list[dict[str, Any]] = []
    full_metrics: list[dict[str, Any]] = []
    baseline_metrics: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        snapshots = case["snapshots"]
        position_id = str(case.get("position_id", "POS_EVAL"))
        from_version_id = int(case.get("from_version_id", 1))
        to_version_id = int(case.get("to_version_id", 2))
        baseline_events = snapshot_diff_baseline(
            snapshots["before"],
            snapshots["after"],
            position_id=position_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
        )
        full_raw_events = detect_evolution_events(
            snapshots["before"],
            snapshots["after"],
            position_id=position_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
        )
        full_events = [_normalize_event(item, raw=True) for item in full_raw_events]
        expected_events = case["expected_events"]
        full_metric = event_metrics(
            expected_events, full_events, suppress_evidence=True
        )
        baseline_metric = event_metrics(
            expected_events, baseline_events, suppress_evidence=False
        )
        full_metrics.append(full_metric)
        baseline_metrics.append(baseline_metric)
        case_outputs.append(
            {
                "case_id": case["case_id"],
                "taxonomy_context": case["taxonomy_context"],
                "annotation_note": case["annotation_note"],
                "expected_events": [
                    _normalize_expected(item) for item in expected_events
                ],
                "model_results": {
                    "evolution_event_detector": {
                        "events": full_events,
                        "metrics": full_metric,
                    },
                    "snapshot_diff_baseline": {
                        "events": baseline_events,
                        "metrics": baseline_metric,
                    },
                },
                "failure_cases": {
                    "evolution_event_detector": _failure_cases(
                        expected_events,
                        full_events,
                        full_metric,
                        method="evolution_event_detector",
                        case=case,
                    ),
                    "snapshot_diff_baseline": _failure_cases(
                        expected_events,
                        baseline_events,
                        baseline_metric,
                        method="snapshot_diff_baseline",
                        case=case,
                    ),
                },
            }
        )
    return {
        "report_version": REPORT_VERSION,
        "dataset_version": dataset["dataset_version"],
        "provenance": dataset["provenance"],
        "method_versions": {
            "full": FULL_VERSION,
            "baseline": BASELINE_VERSION,
        },
        "not_covered": NOT_COVERED,
        "metrics": {
            "full": _aggregate(full_metrics),
            "baseline": _aggregate(baseline_metrics),
        },
        "cases": case_outputs,
        "limitations": [
            "Dataset is synthetic/manually labelled; it contains ten version pairs, not a "
            "production-scale sample.",
            "Event metrics count high-level events; atomic skill signals that are evidence of a "
            "matched replacement/stack/role event are suppressed for the Full method.",
            "position_split and position_merge are not implemented and are outside this evaluation.",
            "Baseline is intentionally a raw added/removed/changed diff and cannot recover "
            "replacement or stack migration semantics.",
        ],
    }


def _format_metrics(metrics: dict[str, Any]) -> str:
    def value(key: str) -> str:
        item = metrics[key]
        return "n/a" if item is None else f"{item:.4f}"

    return (
        f"P {value('event_precision')} R {value('event_recall')} "
        f"F1 {value('event_f1')} Type {value('event_type_accuracy')} "
        f"ReplacementF1 {value('replacement_f1')} "
        f"FalseEventRate {value('false_event_rate')} "
        f"Evidence {value('evidence_coverage')}"
    )


def render_markdown(report: JsonObject) -> str:
    metrics = report["metrics"]
    lines = [
        "# Evolution Event 专项 Evaluation",
        "",
        f"- 报告版本：`{report['report_version']}`",
        f"- 数据集版本：`{report['dataset_version']}`",
        f"- 数据来源：`{report['provenance']}`",
        f"- Full 版本：`{report['method_versions']['full']}`",
        f"- Baseline 版本：`{report['method_versions']['baseline']}`",
        "",
        "## Dataset",
        "",
        "人工标注版本对覆盖：skill_emergence、skill_decline、skill_replacement、"
        "technology_stack_migration、responsibility_shift、role_expansion、role_contraction、"
        "position_rename、no meaningful event，以及 unrelated skill change 作为 replacement "
        "误报对照。",
        "",
        "## Baseline",
        "",
        "Snapshot Diff Baseline 只输出 added / removed / changed 对应的低层事件，"
        "不推断 skill replacement 或 technology stack migration。",
        "",
        "## Full Method",
        "",
        "使用现有 `detect_evolution_events`：原子 skill 信号 + 语义相关 replacement + "
        "stack migration + responsibility shift + role breadth + position rename。",
        "",
        "## Metrics",
        "",
        "| 方法 | Event P/R/F1 | Type Accuracy | Replacement F1 | Replacement FP | False Event Rate | Evidence Coverage |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Full | {_format_metrics(metrics['full'])} |",
        f"| Snapshot Diff | {_format_metrics(metrics['baseline'])} |",
        "",
        "Replacement 单独统计：Full 的 replacement 指标基于匹配到正确 source/target skill_id "
        "的事件；baseline 不生成 replacement 事件，因此其 recall 为 0 是预期行为。",
        "",
        "## Ablation",
        "",
        "Evolution Event 评测不适用组件消融；Full 与 baseline 的对比已经体现语义组件的作用。",
        "",
        "## Failure Cases",
        "",
    ]
    for case in report["cases"]:
        full_failures = case["failure_cases"]["evolution_event_detector"]
        baseline_failures = case["failure_cases"]["snapshot_diff_baseline"]
        lines.append(f"### {case['case_id']}")
        lines.append(
            f"- expected：{', '.join(item['event_type'] for item in case['expected_events']) or 'none'}"
        )
        lines.append(
            "- Full predicted："
            + (
                ", ".join(
                    item["event_type"] for item in case["model_results"]["evolution_event_detector"]["events"]
                )
                or "none"
            )
        )
        lines.append(
            "- Baseline predicted："
            + (
                ", ".join(
                    item["event_type"] for item in case["model_results"]["snapshot_diff_baseline"]["events"]
                )
                or "none"
            )
        )
        if full_failures:
            lines.append("- Full failures:")
            for failure in full_failures[:4]:
                lines.append(
                    f"  - expected `{failure['expected_event']}`, predicted "
                    f"`{failure['predicted_event']}`；{failure['why_failed']}"
                )
        if baseline_failures:
            lines.append("- Baseline failures:")
            for failure in baseline_failures[:4]:
                lines.append(
                    f"  - expected `{failure['expected_event']}`, predicted "
                    f"`{failure['predicted_event']}`；{failure['why_failed']}"
                )
        if not full_failures and not baseline_failures:
            lines.append("- 无")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the evolution event competition evaluation."
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    service_root = Path(__file__).resolve().parents[2]
    dataset_path = service_root / "evaluation" / f"{DATASET_VERSION}.json"
    try:
        report = evaluate_dataset(load_dataset(dataset_path))
    except ValueError as exc:
        parser.error(str(exc))
    report["execution"] = {
        "command": subprocess.list2cmdline(["python", *sys.argv]),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
    }
    output_dir = args.output_dir or service_root / "evaluation"
    results_dir = output_dir / "results"
    reports_dir = output_dir / "reports"
    results_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"{REPORT_VERSION}.json"
    markdown_path = reports_dir / f"{REPORT_VERSION}.md"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "dataset_version": report["dataset_version"],
                "report_version": report["report_version"],
                "json_report": str(result_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
