from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.api.contracts import OfflineEvaluationRequest
from app.api.mapping import discovery_command_from_api
from app.application.contracts import AlgorithmEvaluationResult
from app.application.discovery import CONTRACT_VERSION
from app.domain.discovery import JDSnapshot
from app.domain.values import FrozenDict, freeze, thaw
from app.ports.providers import AlgorithmRegistryPort


DATASET_VERSION = "discovery-competition-fixed.v1"
REPORT_VERSION = "discovery-competition-report.v1"
ALGORITHM_VERSIONS = {
    "current": "multi_view:semantic+skill-cooccurrence+responsibility-tfidf-v1:evidence-gated-multi-view",
    "baseline": "baseline:tfidf-svd-v1:text=1.0,skill=0.0:agglomerative-average-link",
}
REQUIRED_CASE_FIELDS = {
    "case_id",
    "input",
    "expected",
    "source_metadata",
    "windows",
    "algorithm_config",
}


def load_fixed_dataset(path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"discovery evaluation dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"discovery evaluation dataset is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    validate_fixed_dataset(dataset)
    return dataset


def validate_fixed_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("discovery evaluation dataset root must be an object")
    if dataset.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"discovery dataset_version must be {DATASET_VERSION}")
    if not dataset.get("algorithm_config_version"):
        raise ValueError("discovery algorithm_config_version is required")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("discovery evaluation dataset cases must be a non-empty array")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"discovery case[{index}] must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(
                f"discovery case[{index}] missing required fields: {', '.join(missing)}"
            )
        case_id = str(case["case_id"]).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"discovery case[{index}] case_id must be non-empty and unique")
        seen.add(case_id)
        expected = case["expected"]
        if not isinstance(expected, dict) or expected.get("provenance") not in {
            "human_annotation",
            "explicit_rule",
        }:
            raise ValueError(
                f"discovery case {case_id} expected.provenance must be human_annotation or explicit_rule"
            )
        labels = expected.get("labels")
        positives = expected.get("positive_candidate_jd_ids")
        if not isinstance(labels, dict) or not labels:
            raise ValueError(f"discovery case {case_id} expected.labels must be a non-empty object")
        if not isinstance(positives, list) or not positives:
            raise ValueError(
                f"discovery case {case_id} expected.positive_candidate_jd_ids must be a non-empty array"
            )
        config = case["algorithm_config"]
        if not isinstance(config, dict) or not config.get("version"):
            raise ValueError(f"discovery case {case_id} algorithm_config.version is required")
        if config["version"] != dataset["algorithm_config_version"]:
            raise ValueError(
                f"discovery case {case_id} algorithm_config.version does not match dataset"
            )
        algorithms = config.get("comparison_algorithms")
        if algorithms != ["baseline", "multi_view"]:
            raise ValueError(
                f"discovery case {case_id} must compare baseline and multi_view in that order"
            )
        if not isinstance(config.get("evaluation_k"), int) or config["evaluation_k"] <= 0:
            raise ValueError(f"discovery case {case_id} algorithm_config.evaluation_k must be positive")
        if not isinstance(case["source_metadata"], dict) or not case["source_metadata"]:
            raise ValueError(f"discovery case {case_id} source_metadata must be an object")
        if not isinstance(case["input"], dict):
            raise ValueError(f"discovery case {case_id} input must be an object")
        snapshot_ids = {
            str(item.get("jd_id"))
            for item in case["input"].get("snapshots", [])
            if isinstance(item, dict)
        }
        if set(labels) != snapshot_ids:
            raise ValueError(
                f"discovery case {case_id} expected.labels must cover every input jd_id exactly"
            )
        if not set(positives) <= snapshot_ids:
            raise ValueError(
                f"discovery case {case_id} expected positives must reference input jd_id values"
            )
        try:
            OfflineEvaluationRequest.model_validate(
                {
                    **case["input"],
                    "time_windows": case["windows"],
                    "labels": labels,
                    "positive_candidate_jd_ids": positives,
                    "top_k": config["evaluation_k"],
                    "labeling_basis": expected["annotation_note"],
                    "comparison_algorithms": algorithms,
                    "algorithm_configs": config["algorithm_configs"],
                }
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"discovery case {case_id} is invalid: {exc}") from exc


def precision_at_k(retrieved: list[str], positives: set[str], k: int) -> float:
    if k <= 0:
        raise ValueError("precision_at_k requires k above zero")
    return round(len(set(retrieved[:k]) & positives) / k, 6)


def persistence_metrics(window_signatures: list[set[str]]) -> dict[str, Any]:
    overlaps = []
    for left, right in zip(window_signatures, window_signatures[1:], strict=False):
        union = left | right
        overlaps.append(round(len(left & right) / len(union), 6) if union else 1.0)
    counts = Counter(signature for values in window_signatures for signature in values)
    persistent = sorted(signature for signature, count in counts.items() if count >= 2)
    return {
        "formula": (
            "consecutive_top_k_jaccard = |signature_set_t ∩ signature_set_t-1| / "
            "|signature_set_t ∪ signature_set_t-1|; candidate_persistence_rate = "
            "signatures appearing in at least 2 windows / all distinct signatures"
        ),
        "consecutive_top_k_jaccard": overlaps,
        "mean_consecutive_top_k_jaccard": (
            round(sum(overlaps) / len(overlaps), 6) if overlaps else 1.0
        ),
        "persistent_signatures": persistent,
        "candidate_persistence_rate": (
            round(len(persistent) / len(counts), 6) if counts else 1.0
        ),
    }


def _signature(snapshot: JDSnapshot) -> str:
    skills = sorted(
        {
            str(skill.identity).strip().casefold()
            for skill in snapshot.structured_data.required_skills
            if skill.identity
        }
    )
    return "skills:" + "|".join(skills) if skills else f"title:{snapshot.title.casefold()}"


def _stable_algorithm_result(result: AlgorithmEvaluationResult) -> dict[str, Any]:
    return {
        "algorithm": result.algorithm,
        "result_type": (
            "non_semantic_statistical_baseline"
            if result.algorithm == "baseline"
            else "current_discovery_model"
        ),
        "feature_name": result.feature_name,
        "clustering_name": result.clustering_name,
        "parameters": thaw(result.parameters),
        "cluster_count": result.cluster_count,
        "noise_ratio": result.noise_ratio,
        "silhouette_coefficient": result.silhouette_coefficient,
        "clusters": [
            {
                "cluster_key": item.cluster_key,
                "member_jd_ids": list(item.member_jd_ids),
                "member_fact_ids": list(item.member_fact_ids),
            }
            for item in sorted(result.clusters, key=lambda value: value.cluster_key)
        ],
        "noise_points": [thaw(item) for item in result.noise_points],
        "enterprise_debias": thaw(result.enterprise_debias),
        "stability": thaw(result.stability_analysis),
    }


def _ranked_member_ids(result: AlgorithmEvaluationResult) -> list[str]:
    return [
        jd_id
        for cluster in sorted(
            result.clusters,
            key=lambda item: (-len(item.member_jd_ids), item.cluster_key),
        )
        for jd_id in sorted(cluster.member_jd_ids)
    ]


def _top_k_details(
    ranked: list[str],
    snapshots: dict[str, JDSnapshot],
    positives: set[str],
    k: int,
) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "jd_id": jd_id,
            "title": snapshots[jd_id].title,
            "candidate_signature": _signature(snapshots[jd_id]),
            "expected_positive": jd_id in positives,
            "outcome": "true_positive" if jd_id in positives else "false_positive",
        }
        for rank, jd_id in enumerate(ranked[:k], start=1)
    ]


def _metric_block(
    overall: AlgorithmEvaluationResult,
    by_window: list[tuple[str, AlgorithmEvaluationResult, tuple[JDSnapshot, ...]]],
    snapshots: dict[str, JDSnapshot],
    positives: set[str],
    k: int,
) -> dict[str, Any]:
    overall_ranked = _ranked_member_ids(overall)
    overall_top = overall_ranked[:k]
    windows = []
    signature_sets = []
    for window_id, result, members in by_window:
        member_map = {item.jd_id: item for item in members}
        window_positives = positives & set(member_map)
        ranked = _ranked_member_ids(result)
        details = _top_k_details(ranked, member_map, window_positives, k)
        signatures = {item["candidate_signature"] for item in details}
        signature_sets.append(signatures)
        windows.append(
            {
                "window_id": window_id,
                "sample_count": len(members),
                "precision_at_k": precision_at_k(ranked, window_positives, k),
                "top_k": details,
                "false_positive_jd_ids": sorted(
                    set(ranked[:k]) - window_positives
                ),
                "false_negative_jd_ids": sorted(
                    window_positives - set(ranked[:k])
                ),
            }
        )
    false_positives = sorted(set(overall_top) - positives)
    false_negatives = sorted(positives - set(overall_top))
    return {
        "k": k,
        "hit_definition": (
            "a Top-K JD is a hit only when its jd_id is in the independently human-annotated "
            "positive_candidate_jd_ids; Precision@K = hit_count / K"
        ),
        "overall_precision_at_k": precision_at_k(overall_ranked, positives, k),
        "overall_top_k": _top_k_details(overall_ranked, snapshots, positives, k),
        "windows": windows,
        "cross_window_persistence": persistence_metrics(signature_sets),
        "false_positive_jd_ids": false_positives,
        "false_negative_jd_ids": false_negatives,
        "representative_false_positive": (
            {
                "jd_id": false_positives[0],
                "title": snapshots[false_positives[0]].title,
                "reason": "retrieved in Top-K but human annotation marks it as a stable control",
            }
            if false_positives
            else None
        ),
        "representative_false_negative": (
            {
                "jd_id": false_negatives[0],
                "title": snapshots[false_negatives[0]].title,
                "reason": "human emerging positive ranked outside Top-K",
            }
            if false_negatives
            else None
        ),
        "failure_reasons": [
            *(["top_k_contains_human_negative"] if false_positives else []),
            *(["human_positive_outside_top_k"] if false_negatives else []),
        ],
    }


def _request_and_command(case: dict[str, Any]):
    expected = case["expected"]
    config = case["algorithm_config"]
    request = OfflineEvaluationRequest.model_validate(
        {
            **case["input"],
            "time_windows": case["windows"],
            "labels": expected["labels"],
            "positive_candidate_jd_ids": expected["positive_candidate_jd_ids"],
            "top_k": config["evaluation_k"],
            "labeling_basis": expected["annotation_note"],
            "comparison_algorithms": config["comparison_algorithms"],
            "algorithm_configs": config["algorithm_configs"],
        }
    )
    command = discovery_command_from_api(
        contract_version=CONTRACT_VERSION,
        request_id=request.request_id,
        algorithm=request.algorithm,
        time_windows=[item.model_dump(mode="json") for item in request.time_windows],
        snapshots=[item.model_dump(mode="json") for item in request.snapshots],
        position_references=[
            item.model_dump(mode="json") for item in request.position_references
        ],
        config=request.config,
    )
    return request, command


def evaluate_fixed_dataset(
    dataset: dict[str, Any], registry: AlgorithmRegistryPort
) -> dict[str, Any]:
    validate_fixed_dataset(dataset)
    semantic_available = registry.semantic_provider.available
    cases = []
    for case in dataset["cases"]:
        request, command = _request_and_command(case)
        snapshots = {item.jd_id: item for item in command.snapshots}
        positives = set(request.positive_candidate_jd_ids)
        results: dict[str, dict[str, Any]] = {}
        metrics: dict[str, dict[str, Any]] = {}
        for algorithm in request.comparison_algorithms:
            raw_parameters = freeze(request.algorithm_configs.get(algorithm, {}))
            if not isinstance(raw_parameters, FrozenDict):
                raise ValueError(f"algorithm config for {algorithm} must be an object")
            overall = registry.evaluate(algorithm, command.snapshots, raw_parameters)
            window_results = []
            stable_windows = []
            for window in request.time_windows:
                members = tuple(
                    item
                    for item in command.snapshots
                    if item.publish_date is not None
                    and window.start <= item.publish_date <= window.end
                )
                actual = registry.evaluate(algorithm, members, raw_parameters)
                stable_windows.append(
                    {
                        "window_id": window.window_id,
                        "result": _stable_algorithm_result(actual),
                    }
                )
                window_results.append((window.window_id, actual, members))
            results[algorithm] = {
                "overall": _stable_algorithm_result(overall),
                "windows": stable_windows,
            }
            metrics[algorithm] = _metric_block(
                overall,
                window_results,
                snapshots,
                positives,
                request.top_k,
            )
        current = metrics["multi_view"]
        baseline = metrics["baseline"]
        if not semantic_available:
            current["failure_reasons"].append(
                "local_semantic_provider_unavailable_explicit_multiview_fallback_used"
            )
        cases.append(
            {
                "case_id": case["case_id"],
                "human_expected": case["expected"],
                "evaluation_rules": {
                    "ranking": "clusters ordered by member count descending then cluster_key; members ordered by jd_id",
                    "candidate_signature": "sorted normalized required-skill set; title fallback",
                    "expected_is_not_used_to_generate_rankings": True,
                },
                "model_results": {"multi_view": results["multi_view"]},
                "baseline_results": {"baseline": results["baseline"]},
                "metric_results": {
                    "multi_view": current,
                    "baseline": baseline,
                    "comparison": {
                        "precision_at_k_delta": round(
                            current["overall_precision_at_k"]
                            - baseline["overall_precision_at_k"],
                            6,
                        ),
                        "persistence_delta": round(
                            current["cross_window_persistence"][
                                "candidate_persistence_rate"
                            ]
                            - baseline["cross_window_persistence"][
                                "candidate_persistence_rate"
                            ],
                            6,
                        ),
                        "mean_consecutive_jaccard_delta": round(
                            current["cross_window_persistence"][
                                "mean_consecutive_top_k_jaccard"
                            ]
                            - baseline["cross_window_persistence"][
                                "mean_consecutive_top_k_jaccard"
                            ],
                            6,
                        ),
                    },
                },
            }
        )
    return {
        "report_version": REPORT_VERSION,
        "dataset_version": dataset["dataset_version"],
        "algorithm_versions": ALGORITHM_VERSIONS,
        "algorithm_config_version": dataset["algorithm_config_version"],
        "execution_context": {
            "semantic_provider_version": registry.semantic_provider.version,
            "semantic_provider_available": semantic_available,
            "semantic_failure_mode": "mark_unavailable",
        },
        "case_count": len(cases),
        "cases": cases,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Emerging Discovery 固定竞赛评估",
        "",
        f"- 报告版本：`{report['report_version']}`",
        f"- 数据集版本：`{report['dataset_version']}`",
        f"- 当前算法版本：`{report['algorithm_versions']['current']}`",
        f"- 基线版本：`{report['algorithm_versions']['baseline']}`",
        f"- 算法配置版本：`{report['algorithm_config_version']}`",
        *(
            [
                f"- 执行命令：`{report['execution']['command']}`",
                f"- 执行时间：`{report['execution']['executed_at']}`",
                f"- Git commit：`{report['execution']['git_commit']}`",
            ]
            if "execution" in report
            else []
        ),
        f"- 本地语义 Provider：`{'available' if report['execution_context']['semantic_provider_available'] else 'unavailable'}`",
        "",
    ]
    for case in report["cases"]:
        metrics = case["metric_results"]
        current = metrics["multi_view"]
        baseline = metrics["baseline"]
        comparison = metrics["comparison"]
        lines.extend(
            [
                f"## {case['case_id']}",
                "",
                f"命中定义：{current['hit_definition']}。",
                "",
                "| 算法 | 总体 Precision@K | 候选持续率 | 相邻窗口平均 Jaccard |",
                "|---|---:|---:|---:|",
                f"| multi_view | {current['overall_precision_at_k']:.4f} | "
                f"{current['cross_window_persistence']['candidate_persistence_rate']:.4f} | "
                f"{current['cross_window_persistence']['mean_consecutive_top_k_jaccard']:.4f} |",
                f"| baseline | {baseline['overall_precision_at_k']:.4f} | "
                f"{baseline['cross_window_persistence']['candidate_persistence_rate']:.4f} | "
                f"{baseline['cross_window_persistence']['mean_consecutive_top_k_jaccard']:.4f} |",
                "",
                f"Precision@K 差值：`{comparison['precision_at_k_delta']:+.4f}`；"
                f"候选持续率差值：`{comparison['persistence_delta']:+.4f}`；"
                f"相邻窗口平均 Jaccard 差值："
                f"`{comparison['mean_consecutive_jaccard_delta']:+.4f}`。",
                "",
                "### 每窗口结果",
                "",
                "| 窗口 | multi_view Precision@K | multi_view Top-K | baseline Precision@K | baseline Top-K |",
                "|---|---:|---|---:|---|",
            ]
        )
        for current_window, baseline_window in zip(
            current["windows"], baseline["windows"], strict=True
        ):
            lines.append(
                f"| {current_window['window_id']} | "
                f"{current_window['precision_at_k']:.4f} | "
                f"{', '.join(item['jd_id'] for item in current_window['top_k'])} | "
                f"{baseline_window['precision_at_k']:.4f} | "
                f"{', '.join(item['jd_id'] for item in baseline_window['top_k'])} |"
            )
        for name, values in (("multi_view", current), ("baseline", baseline)):
            lines.extend(["", f"### {name} Top-K 明细", ""])
            for item in values["overall_top_k"]:
                lines.append(
                    f"- #{item['rank']} `{item['jd_id']}` {item['title']} — {item['outcome']}"
                )
            lines.extend(
                [
                    "",
                    f"- 代表性 false positive：`{values['representative_false_positive']}`",
                    f"- 代表性 false negative：`{values['representative_false_negative']}`",
                    f"- 失败原因：`{values['failure_reasons'] or ['none']}`",
                ]
            )
        lines.extend(
            [
                "",
                "### 跨窗口持续性公式",
                "",
                current["cross_window_persistence"]["formula"],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
