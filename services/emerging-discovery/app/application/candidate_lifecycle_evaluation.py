"""Competition evaluation for emerging candidate identity and lifecycle."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.candidate_identity import (
    CandidateIdentityComponents,
    CandidateIdentityMatch,
    CandidateIdentitySpec,
    DEFAULT_CANDIDATE_IDENTITY_CONFIG,
    match_candidate_identity,
)
from app.domain.candidate_lifecycle import (
    CANDIDATE_STATUSES,
    DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
    transition_candidate,
    transition_for_missing_windows,
)


DATASET_VERSION = "candidate-lifecycle-competition.v1"
REPORT_VERSION = "candidate-lifecycle-competition-report.v1"
ALGORITHM_VERSION = "candidate-identity-v1+candidate-lifecycle-v1"
BASELINE_EXACT_VERSION = "title-exact-match.v1"
BASELINE_SIMILARITY_VERSION = "title-similarity-only.v1"
ABLATION_VERSION = "identity-component-ablation.v1"

METHODS = (
    "full",
    "baseline_exact_title",
    "baseline_title_similarity",
    "ablation_no_title",
    "ablation_no_skill",
    "ablation_no_responsibility",
    "ablation_no_membership",
)

REQUIRED_CASE_FIELDS = {"case_id", "scenario", "windows", "expected"}
REQUIRED_OBSERVATION_FIELDS = {
    "observation_id",
    "window_id",
    "title",
    "skills",
    "responsibilities",
    "support_count",
    "company_count",
    "emergence_score",
    "expected_candidate_id",
}


@dataclass(frozen=True)
class ObservationInput:
    observation_id: str
    window_id: str
    window_index: int
    expected_candidate_id: str
    title: str
    skills: frozenset[str]
    responsibilities: frozenset[str]
    member_jd_ids: frozenset[str]
    semantic_centroid: tuple[float, ...]
    support_count: int
    company_count: int
    emergence_score: float


@dataclass
class _CandidateState:
    candidate_id: str
    status: str = "weak_signal"
    observed_window_ids: list[str] = field(default_factory=list)
    titles: set[str] = field(default_factory=set)
    skills: set[str] = field(default_factory=set)
    responsibilities: set[str] = field(default_factory=set)
    member_jd_ids: set[str] = field(default_factory=set)
    semantic_centroid: tuple[float, ...] = ()
    identity_stability: int = 0
    missed_windows: int = 0


def load_dataset(path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"candidate lifecycle evaluation dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"candidate lifecycle evaluation dataset is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("candidate lifecycle evaluation dataset root must be an object")
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
        windows = case["windows"]
        if not isinstance(windows, list) or not windows:
            raise ValueError(f"case {case_id} windows must be a non-empty array")
        window_ids = [str(item["window_id"]) for item in windows if isinstance(item, dict)]
        if len(window_ids) != len(set(window_ids)) or not window_ids:
            raise ValueError(f"case {case_id} window_id values must be unique and non-empty")
        observation_ids: set[str] = set()
        expected_candidates: set[str] = set()
        for window in windows:
            if not isinstance(window, dict) or not str(window.get("window_id", "")).strip():
                raise ValueError(f"case {case_id} windows must contain objects with window_id")
            observations = window.get("observations", [])
            if not isinstance(observations, list):
                raise ValueError(f"case {case_id} window observations must be an array")
            for item in observations:
                if not isinstance(item, dict):
                    raise ValueError(f"case {case_id} observation must be an object")
                obs_missing = sorted(REQUIRED_OBSERVATION_FIELDS - set(item))
                if obs_missing:
                    raise ValueError(
                        f"case {case_id} observation missing fields: {', '.join(obs_missing)}"
                    )
                observation_id = str(item["observation_id"]).strip()
                if not observation_id or observation_id in observation_ids:
                    raise ValueError(f"case {case_id} observation_id must be unique")
                observation_ids.add(observation_id)
                expected_candidates.add(str(item["expected_candidate_id"]).strip())
                if item["window_id"] not in window_ids:
                    raise ValueError(
                        f"case {case_id} observation {observation_id} references unknown window"
                    )
                if not isinstance(item["skills"], list) or not isinstance(
                    item["responsibilities"], list
                ):
                    raise ValueError(
                        f"case {case_id} observation {observation_id} skills/responsibilities "
                        "must be arrays"
                    )
                for numeric_field in ("support_count", "company_count", "emergence_score"):
                    value = item[numeric_field]
                    if not isinstance(value, (int, float)) or value < 0:
                        raise ValueError(
                            f"case {case_id} observation {observation_id} {numeric_field} "
                            "must be non-negative"
                        )
                centroid = item.get("semantic_centroid")
                if centroid is not None and (
                    not isinstance(centroid, list)
                    or not all(isinstance(value, (int, float)) for value in centroid)
                ):
                    raise ValueError(
                        f"case {case_id} observation {observation_id} semantic_centroid "
                        "must be a numeric array"
                    )
        expected = case["expected"]
        if not isinstance(expected, dict) or not isinstance(expected.get("lifecycle_states"), list):
            raise ValueError(f"case {case_id} expected.lifecycle_states must be an array")
        state_keys: set[tuple[str, str]] = set()
        for state in expected["lifecycle_states"]:
            if not isinstance(state, dict):
                raise ValueError(f"case {case_id} lifecycle state must be an object")
            window_id = str(state.get("window_id", ""))
            candidate_id = str(state.get("candidate_id", ""))
            status = str(state.get("state", ""))
            if window_id not in window_ids:
                raise ValueError(f"case {case_id} lifecycle state references unknown window")
            if candidate_id not in expected_candidates:
                raise ValueError(f"case {case_id} lifecycle state references unknown candidate")
            if status not in CANDIDATE_STATUSES:
                raise ValueError(f"case {case_id} lifecycle state {status} is invalid")
            key = (window_id, candidate_id)
            if key in state_keys:
                raise ValueError(f"case {case_id} lifecycle state is duplicated")
            state_keys.add(key)
        for window in windows:
            for item in window.get("observations", []):
                key = (item["window_id"], str(item["expected_candidate_id"]).strip())
                if key not in state_keys:
                    raise ValueError(
                        f"case {case_id} observation {item['observation_id']} has no "
                        "expected lifecycle state"
                    )
        for config_name in ("identity_config", "lifecycle_config"):
            config = case.get(config_name)
            if config is not None and not isinstance(config, dict):
                raise ValueError(f"case {case_id} {config_name} must be an object")


def _observations(case: dict[str, Any]) -> list[ObservationInput]:
    result: list[ObservationInput] = []
    for window_index, window in enumerate(case["windows"]):
        for item in window.get("observations", []):
            result.append(
                ObservationInput(
                    observation_id=str(item["observation_id"]),
                    window_id=str(item["window_id"]),
                    window_index=window_index,
                    expected_candidate_id=str(item["expected_candidate_id"]).strip(),
                    title=str(item["title"]),
                    skills=frozenset(str(value).casefold() for value in item["skills"]),
                    responsibilities=frozenset(
                        str(value).casefold() for value in item["responsibilities"]
                    ),
                    member_jd_ids=frozenset(
                        str(value) for value in item.get("member_jd_ids", [])
                    ),
                    semantic_centroid=tuple(item.get("semantic_centroid", [])),
                    support_count=int(item["support_count"]),
                    company_count=int(item["company_count"]),
                    emergence_score=float(item["emergence_score"]),
                )
            )
    return result


def _state_spec(state: _CandidateState, semantic_available: bool) -> CandidateIdentitySpec:
    return CandidateIdentitySpec(
        titles=frozenset(state.titles),
        skills=frozenset(state.skills),
        responsibilities=frozenset(state.responsibilities),
        member_jd_ids=frozenset(state.member_jd_ids),
        semantic_centroid=state.semantic_centroid if semantic_available else (),
        candidate_id=state.candidate_id,
    )


def _exact_title_match(
    current: CandidateIdentitySpec,
    candidates: tuple[CandidateIdentitySpec, ...],
    threshold: float,
) -> CandidateIdentityMatch:
    current_titles = {value.casefold() for value in current.titles}
    best = None
    for candidate in sorted(candidates, key=lambda item: item.candidate_id or ""):
        candidate_titles = {value.casefold() for value in candidate.titles}
        similarity = 1.0 if current_titles & candidate_titles else 0.0
        if best is None or similarity > best[0]:
            best = (similarity, candidate)
    if best is None:
        return CandidateIdentityMatch(
            candidate_id=None,
            identity_similarity=1.0,
            components=CandidateIdentityComponents(1.0, 1.0, 1.0, 1.0, None),
            threshold=threshold,
            matched=False,
            decision_reason="no historical candidate",
        )
    similarity, candidate = best
    return CandidateIdentityMatch(
        candidate_id=candidate.candidate_id,
        identity_similarity=similarity,
        components=CandidateIdentityComponents(
            similarity, 0.0, 0.0, 0.0, None
        ),
        threshold=threshold,
        matched=similarity >= threshold,
        decision_reason=f"title exact similarity {similarity} >= {threshold}",
    )


def _method_identity_config(method: str, base: dict[str, Any]) -> dict[str, Any]:
    config = dict(base)
    if method == "baseline_title_similarity":
        return {
            "title_similarity_weight": 1.0,
            "skill_similarity_weight": 0.0,
            "responsibility_similarity_weight": 0.0,
            "membership_overlap_weight": 0.0,
            "semantic_similarity_weight": 0.0,
            "identity_match_threshold": 0.45,
        }
    removed = {
        "ablation_no_title": "title_similarity_weight",
        "ablation_no_skill": "skill_similarity_weight",
        "ablation_no_responsibility": "responsibility_similarity_weight",
        "ablation_no_membership": "membership_overlap_weight",
    }.get(method)
    if removed:
        config[removed] = 0.0
        weight_keys = (
            "title_similarity_weight",
            "skill_similarity_weight",
            "responsibility_similarity_weight",
            "membership_overlap_weight",
            "semantic_similarity_weight",
        )
        remaining = sum(float(config[key]) for key in weight_keys)
        if remaining <= 0:
            raise ValueError("ablation cannot remove every identity component")
        for key in weight_keys:
            if float(config[key]) > 0:
                config[key] = float(config[key]) / remaining
    return config


def _match(
    method: str,
    observation: ObservationInput,
    candidates: tuple[CandidateIdentitySpec, ...],
    identity_config: dict[str, Any],
) -> CandidateIdentityMatch:
    current = CandidateIdentitySpec(
        titles=frozenset({observation.title}),
        skills=observation.skills,
        responsibilities=observation.responsibilities,
        member_jd_ids=observation.member_jd_ids,
        semantic_centroid=observation.semantic_centroid,
    )
    if method == "baseline_exact_title":
        return _exact_title_match(
            current, candidates, float(identity_config.get("identity_match_threshold", 0.55))
        )
    return match_candidate_identity(
        current, candidates, _method_identity_config(method, identity_config)
    )


def run_case(
    case: dict[str, Any], method: str, semantic_available: bool | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observations = _observations(case)
    lifecycle_config = {
        **DEFAULT_CANDIDATE_LIFECYCLE_CONFIG,
        **case.get("lifecycle_config", {}),
    }
    identity_config = {
        **DEFAULT_CANDIDATE_IDENTITY_CONFIG,
        **case.get("identity_config", {}),
    }
    use_semantic = (
        case.get("semantic_available", True) if semantic_available is None else semantic_available
    )
    registry: dict[str, _CandidateState] = {}
    expected_to_predicted: dict[str, str] = {}
    states_by_window: dict[tuple[str, str], str] = {}
    results: list[dict[str, Any]] = []

    for window_index, window in enumerate(case["windows"]):
        window_id = str(window["window_id"])
        present_ids: set[str] = set()
        for item in sorted(
            window.get("observations", []), key=lambda value: str(value["observation_id"])
        ):
            observation = next(
                value
                for value in observations
                if value.observation_id == str(item["observation_id"])
            )
            candidates = tuple(
                _state_spec(state, use_semantic) for state in registry.values()
            )
            match = _match(method, observation, candidates, identity_config)
            if match.matched and match.candidate_id:
                candidate_id = match.candidate_id
                state = registry[candidate_id]
            else:
                candidate_id = f"cand-{window_id}-{len(registry) + 1}"
                state = _CandidateState(candidate_id=candidate_id)
                registry[candidate_id] = state

            observed_windows = sorted(set(state.observed_window_ids) | {window_id})
            state.observed_window_ids = observed_windows
            state.titles.add(observation.title)
            state.skills.update(observation.skills)
            state.responsibilities.update(observation.responsibilities)
            state.member_jd_ids.update(observation.member_jd_ids)
            if use_semantic and observation.semantic_centroid:
                state.semantic_centroid = observation.semantic_centroid
            identity_stability = (
                state.identity_stability + 1
                if match.identity_similarity
                >= float(lifecycle_config.get("identity_stability_threshold", 0.60))
                else 0
            )
            state.identity_stability = identity_stability
            transition = transition_candidate(
                state.status,
                supported_window_count=len(observed_windows),
                support_count=observation.support_count,
                company_count=observation.company_count,
                emergence_score=observation.emergence_score,
                identity_similarity=match.identity_similarity,
                identity_stability=identity_stability,
                config=lifecycle_config,
            )
            state.status = transition.to_status
            state.missed_windows = 0
            present_ids.add(candidate_id)
            expected_to_predicted[observation.expected_candidate_id] = candidate_id
            expected_state = next(
                (
                    str(item_state["state"])
                    for item_state in case["expected"]["lifecycle_states"]
                    if item_state["window_id"] == window_id
                    and item_state["candidate_id"] == observation.expected_candidate_id
                ),
                None,
            )
            results.append(
                {
                    "observation_id": observation.observation_id,
                    "window_id": window_id,
                    "window_index": window_index,
                    "expected_candidate_id": observation.expected_candidate_id,
                    "predicted_candidate_id": candidate_id,
                    "expected_state": expected_state,
                    "predicted_state": state.status,
                    "identity_similarity": match.identity_similarity,
                    "components": {
                        "title": match.components.title_similarity,
                        "skill": match.components.skill_similarity,
                        "responsibility": match.components.responsibility_similarity,
                        "membership": match.components.membership_overlap,
                        "semantic": match.components.semantic_similarity,
                    },
                    "matched": match.matched,
                    "decision_reason": match.decision_reason,
                }
            )

        for candidate_id, state in registry.items():
            if candidate_id not in present_ids:
                state.missed_windows += 1
                missed = transition_for_missing_windows(
                    state.status, state.missed_windows, lifecycle_config
                )
                if missed.changed:
                    state.status = missed.to_status
        for candidate_id, state in registry.items():
            states_by_window[(window_id, candidate_id)] = state.status

    state_entries: list[dict[str, Any]] = []
    for item in case["expected"]["lifecycle_states"]:
        window_id = str(item["window_id"])
        expected_candidate_id = str(item["candidate_id"])
        expected_state = str(item["state"])
        predicted_candidate_id = expected_to_predicted.get(expected_candidate_id)
        observation_result = next(
            (
                value
                for value in results
                if value["window_id"] == window_id
                and value["expected_candidate_id"] == expected_candidate_id
            ),
            None,
        )
        if observation_result is not None:
            predicted_state = observation_result["predicted_state"]
        elif predicted_candidate_id is not None:
            predicted_state = states_by_window.get((window_id, predicted_candidate_id))
        else:
            predicted_state = None
        state_entries.append(
            {
                "window_id": window_id,
                "expected_candidate_id": expected_candidate_id,
                "expected_state": expected_state,
                "predicted_state": predicted_state,
                "available": predicted_state is not None,
            }
        )
    return results, state_entries


def _pair_metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for left_index in range(len(observations)):
        for right_index in range(left_index + 1, len(observations)):
            left = observations[left_index]
            right = observations[right_index]
            same_truth = left["expected_candidate_id"] == right["expected_candidate_id"]
            same_pred = left["predicted_candidate_id"] == right["predicted_candidate_id"]
            if same_truth and same_pred:
                tp += 1
            elif not same_truth and same_pred:
                fp += 1
            elif same_truth and not same_pred:
                fn += 1
            else:
                tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "total_pairs": tp + fp + fn + tn}


def _false_new_merge(observations: list[dict[str, Any]]) -> dict[str, Any]:
    seen_truth: set[str] = set()
    truth_predicted: dict[str, set[str]] = defaultdict(set)
    all_predicted: set[str] = set()
    false_new = 0
    false_merge = 0
    continuation = 0
    first_occurrence = 0
    for item in observations:
        truth = item["expected_candidate_id"]
        predicted = item["predicted_candidate_id"]
        if truth in seen_truth:
            continuation += 1
            if predicted not in truth_predicted[truth]:
                if predicted not in all_predicted:
                    false_new += 1
        else:
            first_occurrence += 1
            if predicted in all_predicted:
                false_merge += 1
        seen_truth.add(truth)
        truth_predicted[truth].add(predicted)
        all_predicted.add(predicted)
    return {
        "false_new_errors": false_new,
        "false_new_denominator": continuation,
        "false_merge_errors": false_merge,
        "false_merge_denominator": first_occurrence,
    }


def _stability(observations: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in observations:
        grouped[item["expected_candidate_id"]].append(item)
    correct = 0
    total = 0
    for values in grouped.values():
        values = sorted(values, key=lambda item: item["window_index"])
        for left, right in zip(values, values[1:], strict=False):
            total += 1
            correct += left["predicted_candidate_id"] == right["predicted_candidate_id"]
    return {"correct": correct, "total": total}


def case_metrics(
    observations: list[dict[str, Any]], state_entries: list[dict[str, Any]]
) -> dict[str, Any]:
    pairs = _pair_metrics(observations)
    false_metrics = _false_new_merge(observations)
    stability = _stability(observations)
    available_states = [item for item in state_entries if item["available"]]
    lifecycle_correct = sum(
        item["expected_state"] == item["predicted_state"] for item in available_states
    )
    return {
        **pairs,
        **false_metrics,
        "lifecycle_correct": lifecycle_correct,
        "lifecycle_total": len(available_states),
        "stability_correct": stability["correct"],
        "stability_total": stability["total"],
        "observation_count": len(observations),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 6)


def aggregate_metrics(case_metric_list: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        key: sum(item[key] for item in case_metric_list)
        for key in (
            "tp",
            "fp",
            "fn",
            "tn",
            "total_pairs",
            "false_new_errors",
            "false_new_denominator",
            "false_merge_errors",
            "false_merge_denominator",
            "lifecycle_correct",
            "lifecycle_total",
            "stability_correct",
            "stability_total",
            "observation_count",
        )
    }
    precision = _rate(totals["tp"], totals["tp"] + totals["fp"])
    recall = _rate(totals["tp"], totals["tp"] + totals["fn"])
    accuracy = _rate(
        totals["tp"] + totals["tn"], totals["total_pairs"]
    )
    return {
        "identity_accuracy": accuracy,
        "identity_precision": precision,
        "identity_recall": recall,
        "identity_f1": _f1(precision, recall),
        "false_new_candidate_rate": _rate(
            totals["false_new_errors"], totals["false_new_denominator"]
        ),
        "false_merge_rate": _rate(
            totals["false_merge_errors"], totals["false_merge_denominator"]
        ),
        "lifecycle_state_accuracy": _rate(
            totals["lifecycle_correct"], totals["lifecycle_total"]
        ),
        "cross_window_identity_stability": _rate(
            totals["stability_correct"], totals["stability_total"]
        ),
        "observation_count": totals["observation_count"],
        "pair_count": totals["total_pairs"],
        "formulas": {
            "identity_accuracy": "(TP + TN) / all observation pairs",
            "identity_precision": "TP / (TP + FP) over same-candidate link predictions",
            "identity_recall": "TP / (TP + FN) over true same-candidate links",
            "false_new_candidate_rate": (
                "continuation observations assigned a brand-new id / all continuation observations"
            ),
            "false_merge_rate": (
                "first-occurrence observations assigned a previously used id / all "
                "first-occurrence observations"
            ),
            "cross_window_identity_stability": (
                "correct adjacent-window same-candidate links / adjacent links per true candidate"
            ),
        },
    }


def _failure_cases(
    method: str,
    observations: list[dict[str, Any]],
    state_entries: list[dict[str, Any]],
    case: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    seen_truth: set[str] = set()
    truth_predicted: dict[str, set[str]] = defaultdict(set)
    all_predicted: set[str] = set()
    for item in observations:
        truth = item["expected_candidate_id"]
        predicted = item["predicted_candidate_id"]
        if truth in seen_truth and predicted not in truth_predicted[truth]:
            failure_type = "false_new_candidate" if predicted not in all_predicted else "identity_break"
            failures.append(
                {
                    "method": method,
                    "observation_id": item["observation_id"],
                    "window_id": item["window_id"],
                    "expected_candidate_id": truth,
                    "predicted_candidate_id": predicted,
                    "failure_type": failure_type,
                    "expected_event": f"continue candidate {truth}",
                    "predicted_event": f"assigned to {predicted}",
                    "why_failed": (
                        "identity matcher did not link this observation to the expected "
                        "candidate; components "
                        f"{item['components']}, identity_similarity="
                        f"{item['identity_similarity']:.4f}"
                    ),
                    "related_taxonomy/context": case.get("scenario", ""),
                }
            )
        if truth not in seen_truth and predicted in all_predicted:
            failures.append(
                {
                    "method": method,
                    "observation_id": item["observation_id"],
                    "window_id": item["window_id"],
                    "expected_candidate_id": truth,
                    "predicted_candidate_id": predicted,
                    "failure_type": "false_merge",
                    "expected_event": f"new candidate {truth}",
                    "predicted_event": f"merged into existing {predicted}",
                    "why_failed": "identity matcher reused an existing candidate id for a new role",
                    "related_taxonomy/context": case.get("scenario", ""),
                }
            )
        seen_truth.add(truth)
        truth_predicted[truth].add(predicted)
        all_predicted.add(predicted)
    for item in state_entries:
        if item["available"] and item["expected_state"] != item["predicted_state"]:
            failures.append(
                {
                    "method": method,
                    "window_id": item["window_id"],
                    "candidate_id": item["expected_candidate_id"],
                    "failure_type": "lifecycle_state",
                    "expected_event": item["expected_state"],
                    "predicted_event": item["predicted_state"],
                    "why_failed": "lifecycle transition did not match the labelled state",
                    "related_taxonomy/context": case.get("scenario", ""),
                }
            )
    return failures


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    validate_dataset(dataset)
    case_outputs: list[dict[str, Any]] = []
    per_method: dict[str, list[dict[str, Any]]] = {method: [] for method in METHODS}
    semantic_cases = 0
    for case in dataset["cases"]:
        case_methods: dict[str, Any] = {}
        case_failures: list[dict[str, Any]] = []
        for method in METHODS:
            observations, state_entries = run_case(case, method)
            metrics = case_metrics(observations, state_entries)
            per_method[method].append(metrics)
            case_methods[method] = {
                "metrics": metrics,
                "observations": observations,
                "lifecycle_states": state_entries,
            }
            case_failures.extend(_failure_cases(method, observations, state_entries, case))
        if case.get("semantic_available", True):
            semantic_cases += 1
        case_outputs.append(
            {
                "case_id": case["case_id"],
                "scenario": case["scenario"],
                "source_metadata": case.get("source_metadata", {}),
                "results": case_methods,
                "failure_cases": case_failures,
            }
        )
    return {
        "report_version": REPORT_VERSION,
        "dataset_version": dataset["dataset_version"],
        "provenance": dataset["provenance"],
        "method_versions": {
            "full": ALGORITHM_VERSION,
            "baseline_exact_title": BASELINE_EXACT_VERSION,
            "baseline_title_similarity": BASELINE_SIMILARITY_VERSION,
            "ablation": ABLATION_VERSION,
        },
        "semantic_availability": {
            "cases_with_semantic": semantic_cases,
            "total_cases": len(case_outputs),
        },
        "metrics": {
            method: aggregate_metrics(values) for method, values in per_method.items()
        },
        "ablation": {
            "method": "remove one identity component by setting its weight to 0; "
            "remaining weights are renormalized inside the identity matcher",
            "full": aggregate_metrics(per_method["full"]),
            "no_title": aggregate_metrics(per_method["ablation_no_title"]),
            "no_skill": aggregate_metrics(per_method["ablation_no_skill"]),
            "no_responsibility": aggregate_metrics(
                per_method["ablation_no_responsibility"]
            ),
            "no_membership": aggregate_metrics(per_method["ablation_no_membership"]),
        },
        "cases": case_outputs,
        "limitations": [
            "Dataset is synthetic/manually labelled; it is a small cross-window benchmark, "
            "not a production-scale sample.",
            "Lifecycle labels depend on the configured promotion thresholds and therefore "
            "only compare the same lifecycle policy used in the benchmark.",
            "Semantic embeddings are supplied only where semantic_available is true; "
            "other cases run the non-semantic renormalized path.",
            "False-new/false-merge rates measure link-level behavior and do not claim "
            "document-level precision.",
        ],
    }


def _metric_cell(metrics: dict[str, Any]) -> str:
    def value(key: str) -> str:
        item = metrics[key]
        return "n/a" if item is None else f"{item:.4f}"

    return (
        f"F1 {value('identity_f1')} | Acc {value('identity_accuracy')} | "
        f"State {value('lifecycle_state_accuracy')} | "
        f"FalseNew {value('false_new_candidate_rate')} | "
        f"FalseMerge {value('false_merge_rate')} | "
        f"Stability {value('cross_window_identity_stability')}"
    )


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    ablation = report["ablation"]
    lines = [
        "# Emerging Candidate Lifecycle 专项 Evaluation",
        "",
        f"- 报告版本：`{report['report_version']}`",
        f"- 数据集版本：`{report['dataset_version']}`",
        f"- 数据来源：`{report['provenance']}`",
        f"- Full 版本：`{report['method_versions']['full']}`",
        f"- Baseline A：`{report['method_versions']['baseline_exact_title']}`",
        f"- Baseline B：`{report['method_versions']['baseline_title_similarity']}`",
        "",
        "## Dataset",
        "",
        "小型跨窗口评测集，覆盖 title 改名、skill/责任变化、相似 title 不同岗位、"
        "持续成长、衰退、noise、dead、新 candidate 出现与语义改名。所有样本均为"
        " synthetic/manually labelled。",
        "",
        f"语义可用案例：`{report['semantic_availability']['cases_with_semantic']}` / "
        f"`{report['semantic_availability']['total_cases']}`",
        "",
        "## Baseline",
        "",
        "- Baseline A：title exact match，只按规范化后的完整 title 是否相等判断同一 candidate。",
        "- Baseline B：title similarity only，只使用 title token Jaccard，阈值 0.45。",
        "",
        "## Full Method",
        "",
        "使用现有 `match_candidate_identity`：title + skill + responsibility + membership "
        "overlap + semantic（semantic 可用时）。生命周期使用现有 `transition_candidate` 与 "
        "`transition_for_missing_windows`。",
        "",
        "## Metrics",
        "",
        "| 方法 | Identity F1 | Accuracy | Lifecycle State | False New | False Merge | Cross-window Stability |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method, label in (
        ("full", "Full"),
        ("baseline_exact_title", "Baseline A (exact title)"),
        ("baseline_title_similarity", "Baseline B (title similarity)"),
    ):
        value = metrics[method]
        lines.append(
            f"| {label} | {_metric_cell(value)} |"
        )
    lines.extend(
        [
            "",
            "指标公式：identity 链接级 Precision/Recall/F1；False New 表示续接观察被分配全新 id；"
            "False Merge 表示新岗位被合并进已有 id；Cross-window Stability 为真实 candidate 的"
            "相邻窗口正确链接比例。",
            "",
            "## Ablation",
            "",
            "| 配置 | Identity F1 | Lifecycle State Accuracy |",
            "|---|---:|---:|",
        ]
    )
    for key, label in (
        ("full", "Full"),
        ("no_title", "Full - title"),
        ("no_skill", "Full - skill"),
        ("no_responsibility", "Full - responsibility"),
        ("no_membership", "Full - membership"),
    ):
        value = ablation[key]
        f1 = value["identity_f1"]
        state = value["lifecycle_state_accuracy"]
        lines.append(
            f"| {label} | {'n/a' if f1 is None else f'{f1:.4f}'} | "
            f"{'n/a' if state is None else f'{state:.4f}'} |"
        )
    lines.extend(["", "## Failure Cases", ""])
    for case in report["cases"]:
        failures = case["failure_cases"]
        lines.append(f"### {case['case_id']}（{case['scenario']}）")
        if not failures:
            lines.append("- 无")
            continue
        for failure in failures[:5]:
            lines.append(
                f"- `{failure['method']}`：expected `{failure['expected_event']}` → "
                f"predicted `{failure['predicted_event']}`；{failure['why_failed']}"
            )
        if len(failures) > 5:
            lines.append(f"- 另有 {len(failures) - 5} 条，见 JSON。")
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
        description="Run the candidate lifecycle competition evaluation."
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
