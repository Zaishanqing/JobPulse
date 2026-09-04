"""Competition evaluation for trend change detection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.trend_change import (
    DEFAULT_ALGORITHM_VERSION,
    TrendWindowScore,
    analyze_trend_series,
)


DATASET_VERSION = "trend-change-competition.v1"
REPORT_VERSION = "trend-change-competition-report.v1"
FULL_VERSION = DEFAULT_ALGORITHM_VERSION
BASELINE_VERSION = "adjacent-delta-threshold.v1"
TREND_STATES = frozenset({"stable", "rising", "accelerating", "declining", "volatile"})
CP_TOLERANCE_WINDOWS = 1
REQUIRED_CASE_FIELDS = {
    "case_id",
    "subject_id",
    "subject_type",
    "scores",
    "expected_trend_state",
    "expected_change_point_windows",
}


def load_dataset(path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"trend change competition dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"trend change competition dataset is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("trend change competition dataset root must be an object")
    if dataset.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"dataset_version must be {DATASET_VERSION}")
    if dataset.get("provenance") != "synthetic":
        raise ValueError("provenance must be synthetic")
    if int(dataset.get("change_point_tolerance_windows", -1)) != CP_TOLERANCE_WINDOWS:
        raise ValueError(
            f"change_point_tolerance_windows must be {CP_TOLERANCE_WINDOWS}"
        )
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
        scores = case["scores"]
        if (
            not isinstance(scores, list)
            or len(scores) < 2
            or not all(isinstance(value, (int, float)) for value in scores)
        ):
            raise ValueError(f"case {case_id} scores must be a numeric array of length >= 2")
        window_ids = [
            str(value) for value in case.get("window_ids", [])
        ] or [f"w{index + 1}" for index in range(len(scores))]
        if len(window_ids) != len(set(window_ids)) or len(window_ids) != len(scores):
            raise ValueError(
                f"case {case_id} window_ids must be unique and match scores length"
            )
        if case["expected_trend_state"] not in TREND_STATES:
            raise ValueError(f"case {case_id} expected_trend_state is invalid")
        expected_cps = case["expected_change_point_windows"]
        if not isinstance(expected_cps, list) or not set(expected_cps) <= set(window_ids):
            raise ValueError(
                f"case {case_id} expected_change_point_windows must reference window_ids"
            )
        durations = case.get("duration_days")
        if durations is not None and (
            not isinstance(durations, list)
            or len(durations) != len(scores)
            or not all(isinstance(value, (int, float)) and value > 0 for value in durations)
        ):
            raise ValueError(
                f"case {case_id} duration_days must match scores and be positive"
            )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _population_std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def adjacent_delta_baseline(
    scores: list[float], window_ids: list[str]
) -> dict[str, Any]:
    deltas = [scores[index + 1] - scores[index] for index in range(len(scores) - 1)]
    threshold = 0.15
    change_points = [
        window_ids[index + 1]
        for index, delta in enumerate(deltas)
        if abs(delta) > threshold
    ]
    mean = _mean(scores)
    volatility = _population_std(scores) / abs(mean) if mean else 0.0
    total_growth = (
        (scores[-1] - scores[0]) / abs(scores[0]) if scores[0] else 0.0
    )
    split = len(scores) // 2
    first_half = scores[:split]
    second_half = scores[split:]
    acceleration_proxy = _mean(second_half) - _mean(first_half)
    if volatility > 0.4:
        trend_state = "volatile"
    elif total_growth > 0.05 and acceleration_proxy > 0.03:
        trend_state = "accelerating"
    elif total_growth > 0.05:
        trend_state = "rising"
    elif total_growth < -0.05:
        trend_state = "declining"
    else:
        trend_state = "stable"
    return {
        "trend_state": trend_state,
        "change_point_windows": change_points,
        "method": BASELINE_VERSION,
        "threshold": threshold,
        "volatility": round(volatility, 6),
        "total_growth": round(total_growth, 6),
    }


def _full_analysis(case: dict[str, Any]) -> dict[str, Any]:
    scores = [float(value) for value in case["scores"]]
    window_ids = [str(value) for value in case.get("window_ids", [])] or [
        f"w{index + 1}" for index in range(len(scores))
    ]
    durations = case.get("duration_days")
    windows = [
        TrendWindowScore(
            subject_id=str(case["subject_id"]),
            subject_type=str(case["subject_type"]),
            window=window_id,
            score=score,
            duration_days=float(durations[index]) if durations else 1.0,
            source_diversity=1,
        )
        for index, (window_id, score) in enumerate(zip(window_ids, scores, strict=True))
    ]
    analysis = analyze_trend_series(
        str(case["subject_id"]),
        str(case["subject_type"]),
        windows,
        algorithm_version=str(case.get("algorithm_version") or FULL_VERSION),
    )
    return {
        "trend_state": analysis.trend_state,
        "change_point_windows": [point.change_point_window for point in analysis.change_points],
        "growth_rate": analysis.growth_rate,
        "acceleration": analysis.acceleration,
        "volatility": analysis.volatility,
        "window_stability": analysis.window_stability,
        "method": FULL_VERSION,
        "change_point_evidence": [
            {
                "window": point.change_point_window,
                "direction": point.direction,
                "confidence": point.confidence,
                "persistent_windows": point.evidence.get("persistent_windows", []),
            }
            for point in analysis.change_points
        ],
    }


def change_point_metrics(
    expected: list[str],
    predicted: list[str],
    window_ids: list[str],
    tolerance: int = CP_TOLERANCE_WINDOWS,
) -> dict[str, Any]:
    index = {window_id: position for position, window_id in enumerate(window_ids)}
    expected_indices = sorted(index[item] for item in expected)
    predicted_indices = sorted(index[item] for item in predicted)
    used: set[int] = set()
    delays: list[int] = []
    tp = 0
    for expected_index in expected_indices:
        for predicted_index in predicted_indices:
            if predicted_index in used:
                continue
            if abs(predicted_index - expected_index) <= tolerance:
                used.add(predicted_index)
                tp += 1
                delays.append(predicted_index - expected_index)
                break
    fp = len(predicted_indices) - tp
    fn = len(expected_indices) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return {
        "tolerance_windows": tolerance,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alarm_rate": round(fp / len(predicted_indices), 6)
        if predicted_indices
        else 0.0,
        "detection_delay_windows": round(sum(delays) / len(delays), 6)
        if delays
        else None,
        "detection_delays": delays,
    }


def _failure_cases(
    case: dict[str, Any],
    full: dict[str, Any],
    baseline: dict[str, Any],
    full_cp_metrics: dict[str, Any],
    baseline_cp_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_state = case["expected_trend_state"]
    expected_cps = list(case["expected_change_point_windows"])
    failures: list[dict[str, Any]] = []
    for method, result, metrics in (
        ("full", full, full_cp_metrics),
        ("baseline", baseline, baseline_cp_metrics),
    ):
        if result["trend_state"] != expected_state:
            failures.append(
                {
                    "method": method,
                    "expected_event": f"trend_state={expected_state}",
                    "predicted_event": f"trend_state={result['trend_state']}",
                    "why_failed": "trend state classification differs from label",
                    "related_taxonomy/context": case.get("note", ""),
                }
            )
        window_ids = [str(value) for value in case.get("window_ids", [])] or [
            f"w{index + 1}" for index in range(len(case["scores"]))
        ]
        index = {window_id: position for position, window_id in enumerate(window_ids)}
        used: set[int] = set()
        for expected_cp in expected_cps:
            expected_index = index[expected_cp]
            matched = None
            for predicted_cp in result["change_point_windows"]:
                predicted_index = index[predicted_cp]
                if predicted_index not in used and abs(
                    predicted_index - expected_index
                ) <= CP_TOLERANCE_WINDOWS:
                    matched = predicted_cp
                    used.add(predicted_index)
                    break
            if matched is None:
                failures.append(
                    {
                        "method": method,
                        "expected_event": f"change_point={expected_cp}",
                        "predicted_event": None,
                        "why_failed": "change point missed or outside tolerance",
                        "related_taxonomy/context": case.get("note", ""),
                    }
                )
        for predicted_cp in result["change_point_windows"]:
            predicted_index = index[predicted_cp]
            if predicted_index not in used:
                failures.append(
                    {
                        "method": method,
                        "expected_event": None,
                        "predicted_event": f"change_point={predicted_cp}",
                        "why_failed": "false alarm change point",
                        "related_taxonomy/context": case.get("note", ""),
                    }
                )
    return failures


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    validate_dataset(dataset)
    case_outputs: list[dict[str, Any]] = []
    full_cp_metrics_list: list[dict[str, Any]] = []
    baseline_cp_metrics_list: list[dict[str, Any]] = []
    state_correct = 0
    baseline_state_correct = 0
    for case in dataset["cases"]:
        scores = [float(value) for value in case["scores"]]
        window_ids = [str(value) for value in case.get("window_ids", [])] or [
            f"w{index + 1}" for index in range(len(scores))
        ]
        full = _full_analysis(case)
        baseline = adjacent_delta_baseline(scores, window_ids)
        full_cp_metrics = change_point_metrics(
            case["expected_change_point_windows"],
            full["change_point_windows"],
            window_ids,
        )
        baseline_cp_metrics = change_point_metrics(
            case["expected_change_point_windows"],
            baseline["change_point_windows"],
            window_ids,
        )
        full_cp_metrics_list.append(full_cp_metrics)
        baseline_cp_metrics_list.append(baseline_cp_metrics)
        state_correct += int(full["trend_state"] == case["expected_trend_state"])
        baseline_state_correct += int(
            baseline["trend_state"] == case["expected_trend_state"]
        )
        case_outputs.append(
            {
                "case_id": case["case_id"],
                "expected_trend_state": case["expected_trend_state"],
                "expected_change_point_windows": case["expected_change_point_windows"],
                "note": case.get("note", ""),
                "model_results": {
                    "full": full,
                    "baseline": baseline,
                },
                "metric_results": {
                    "full": full_cp_metrics,
                    "baseline": baseline_cp_metrics,
                },
                "failure_cases": _failure_cases(
                    case,
                    full,
                    baseline,
                    full_cp_metrics,
                    baseline_cp_metrics,
                ),
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
        "change_point_tolerance_windows": CP_TOLERANCE_WINDOWS,
        "metrics": {
            "full": _aggregate_cp(full_cp_metrics_list, state_correct, len(case_outputs)),
            "baseline": _aggregate_cp(
                baseline_cp_metrics_list,
                baseline_state_correct,
                len(case_outputs),
            ),
        },
        "cases": case_outputs,
        "limitations": [
            "Dataset is synthetic; it reuses the six existing trend-change cases and adds "
            "accelerating, multiple change points, and irregular window length.",
            "Change point matching uses a +/-1 window tolerance, stated in both JSON and Markdown.",
            "The Full method uses the existing rolling-baseline detector; irregular durations "
            "normalize growth but the detector still scores raw change candidates.",
        ],
    }


def _aggregate_cp(
    case_metrics: list[dict[str, Any]], state_correct: int, case_count: int
) -> dict[str, Any]:
    totals = {
        key: sum(int(item[key]) for item in case_metrics)
        for key in ("tp", "fp", "fn")
    }
    predicted_total = sum(int(item["tp"]) + int(item["fp"]) for item in case_metrics)
    delays = [
        delay for item in case_metrics for delay in item["detection_delays"]
    ]
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
    return {
        "trend_state_accuracy": round(state_correct / case_count, 6),
        "change_point_precision": precision,
        "change_point_recall": recall,
        "change_point_f1": f1,
        "false_alarm_rate": (
            round(totals["fp"] / predicted_total, 6) if predicted_total else 0.0
        ),
        "detection_delay_windows": (
            round(sum(delays) / len(delays), 6) if delays else None
        ),
        "matched_change_point_count": totals["tp"],
        "formulas": {
            "trend_state_accuracy": "correct expected trend states / cases",
            "change_point_precision": "TP / (TP + FP) with +/-1 window tolerance",
            "change_point_recall": "TP / (TP + FN)",
            "false_alarm_rate": "FP / predicted change points",
            "detection_delay_windows": "mean(predicted_index - expected_index) for matched CPs",
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Trend Change 专项 Evaluation",
        "",
        f"- 报告版本：`{report['report_version']}`",
        f"- 数据集版本：`{report['dataset_version']}`",
        f"- 数据来源：`{report['provenance']}`",
        f"- Full 版本：`{report['method_versions']['full']}`",
        f"- Baseline 版本：`{report['method_versions']['baseline']}`",
        f"- Change Point 容差：`+/-{report['change_point_tolerance_windows']}` window",
        "",
        "## Dataset",
        "",
        "复用原有 stable / sudden-rise / slow-growth / decline / noise-spike / volatile，"
        "新增 accelerating、multiple change points、irregular window length。所有样本为 synthetic。",
        "",
        "## Baseline",
        "",
        "Simple adjacent delta threshold：单窗口前后差绝对值超过 0.15 即报 change point；"
        "状态由总增长、首尾窗口波动和 volatility 粗分。",
        "",
        "## Full Method",
        "",
        "使用现有 `analyze_trend_series`：growth + acceleration + persistence + "
        "rolling baseline z-score change point + window stability。",
        "",
        "## Metrics",
        "",
        "| 方法 | Trend State | CP P/R/F1 | False Alarm Rate | Detection Delay |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, label in (("full", "Full"), ("baseline", "Adjacent Delta")):
        value = metrics[key]
        p = value["change_point_precision"]
        r = value["change_point_recall"]
        f1 = value["change_point_f1"]
        delay = value["detection_delay_windows"]
        lines.append(
            f"| {label} | {value['trend_state_accuracy']:.4f} | "
            f"{'n/a' if p is None else f'{p:.4f}'}/"
            f"{'n/a' if r is None else f'{r:.4f}'}/"
            f"{'n/a' if f1 is None else f'{f1:.4f}'} | "
            f"{value['false_alarm_rate']:.4f} | "
            f"{'n/a' if delay is None else f'{delay:.4f}'} |"
        )
    lines.extend(
        [
            "",
            "Change Point 指标在 +/−1 window 容差内匹配；延迟为正表示 Full/Baseline 比标注晚。",
            "",
            "## Ablation",
            "",
            "Trend Change 评测不单独做组件消融；Full 相对 baseline 的对比已经体现 "
            "persistence、rolling baseline 与 stability 的作用。",
            "",
            "## Failure Cases",
            "",
        ]
    )
    for case in report["cases"]:
        failures = case["failure_cases"]
        lines.append(f"### {case['case_id']}")
        lines.append(
            f"- expected state `{case['expected_trend_state']}`；expected CP "
            f"`{case['expected_change_point_windows'] or '[]'}`"
        )
        lines.append(
            f"- Full：state `{case['model_results']['full']['trend_state']}`，CP "
            f"`{case['model_results']['full']['change_point_windows'] or '[]'}`"
        )
        lines.append(
            f"- Baseline：state `{case['model_results']['baseline']['trend_state']}`，CP "
            f"`{case['model_results']['baseline']['change_point_windows'] or '[]'}`"
        )
        if failures:
            for failure in failures[:5]:
                lines.append(
                    f"- `{failure['method']}`：expected `{failure['expected_event']}` → "
                    f"predicted `{failure['predicted_event']}`；{failure['why_failed']}"
                )
        else:
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
        description="Run the trend change competition evaluation."
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
