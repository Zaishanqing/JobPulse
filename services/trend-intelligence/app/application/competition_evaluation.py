from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.application.credibility import CredibilityService


DATASET_VERSION = "trend-competition-fixed.v2"
REPORT_VERSION = "trend-competition-report.v1"
ALGORITHM_VERSION = "credibility-weighted-ranking.v1"
BASELINE_VERSION = "keyword-frequency.v1"
REQUIRED_CASE_FIELDS = {
    "case_id",
    "input",
    "expected",
    "source_metadata",
    "windows",
    "algorithm_config",
}


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_fixed_dataset(path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"trend evaluation dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"trend evaluation dataset is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    validate_fixed_dataset(dataset)
    return dataset


def validate_fixed_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("trend evaluation dataset root must be an object")
    if dataset.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"trend dataset_version must be {DATASET_VERSION}")
    if not dataset.get("algorithm_config_version"):
        raise ValueError("trend algorithm_config_version is required")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("trend evaluation dataset cases must be a non-empty array")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"trend case[{index}] must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"trend case[{index}] missing required fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"trend case[{index}] case_id must be non-empty and unique")
        seen.add(case_id)
        terms = case.get("input", {}).get("terms")
        if not isinstance(terms, list) or not terms:
            raise ValueError(f"trend case {case_id} input.terms must be a non-empty array")
        windows = case.get("windows", {}).get("historical")
        if not isinstance(windows, list) or len(windows) < 2:
            raise ValueError(f"trend case {case_id} requires at least two historical windows")
        window_ids = {str(item.get("window_id")) for item in windows}
        if len(window_ids) != len(windows):
            raise ValueError(f"trend case {case_id} historical window_id values must be unique")
        for window in windows:
            try:
                if _datetime(window["start"]) >= _datetime(window["end"]):
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"trend case {case_id} historical windows require valid ISO start/end"
                ) from exc
        for term_index, term in enumerate(terms):
            required = ("source", "source_type", "enterprise", "term", "published_at", "window_id")
            if not isinstance(term, dict) or not all(term.get(field) for field in required):
                raise ValueError(
                    f"trend case {case_id} input.terms[{term_index}] missing source metadata"
                )
            if term["window_id"] not in window_ids:
                raise ValueError(
                    f"trend case {case_id} input.terms[{term_index}] references unknown window"
                )
            try:
                _datetime(str(term["published_at"]))
            except ValueError as exc:
                raise ValueError(
                    f"trend case {case_id} input.terms[{term_index}].published_at must be ISO datetime"
                ) from exc
        expected = case["expected"]
        if not isinstance(expected, dict) or expected.get("provenance") != "human_annotation":
            raise ValueError(f"trend case {case_id} expected must be human_annotation")
        overall = expected.get("overall_ranking")
        by_window = expected.get("rankings_by_window")
        if not isinstance(overall, list) or len(overall) < 2:
            raise ValueError(f"trend case {case_id} expected.overall_ranking requires two items")
        if not isinstance(by_window, dict) or set(by_window) != window_ids:
            raise ValueError(
                f"trend case {case_id} expected.rankings_by_window must cover every window"
            )
        if any(set(value) != set(overall) for value in by_window.values()):
            raise ValueError(
                f"trend case {case_id} every human window ranking must cover all candidates"
            )
        timings = expected.get("trend_timings")
        if not isinstance(timings, dict) or set(timings) != set(overall):
            raise ValueError(f"trend case {case_id} expected.trend_timings must cover candidates")
        config = case["algorithm_config"]
        if not isinstance(config, dict) or config.get("version") != dataset[
            "algorithm_config_version"
        ]:
            raise ValueError(f"trend case {case_id} algorithm_config.version mismatch")
        if not isinstance(config.get("evaluation_k"), int) or config["evaluation_k"] <= 0:
            raise ValueError(f"trend case {case_id} algorithm_config.evaluation_k must be positive")
        if config["evaluation_k"] > len(overall):
            raise ValueError(f"trend case {case_id} evaluation_k exceeds candidate count")
        if not isinstance(config.get("job_knowledge"), dict) or not isinstance(
            config.get("source_weights"), dict
        ):
            raise ValueError(f"trend case {case_id} algorithm configuration is incomplete")
        metadata = case["source_metadata"]
        if not isinstance(metadata, dict) or not metadata.get("required_sources"):
            raise ValueError(f"trend case {case_id} source_metadata.required_sources is required")


def precision_at_k(actual: list[str], expected: list[str], k: int) -> float:
    if k <= 0:
        raise ValueError("precision_at_k requires k above zero")
    return round(len(set(actual[:k]) & set(expected[:k])) / k, 6)


def spearman_rank_correlation(actual: list[str], expected: list[str]) -> dict[str, Any]:
    if len(expected) < 2:
        return {"status": "unavailable", "value": None, "reason": "fewer_than_two_ranked_items"}
    if len(actual) != len(expected) or set(actual) != set(expected):
        return {"status": "unavailable", "value": None, "reason": "rankings_cover_different_candidates"}
    actual_rank = {candidate: index + 1 for index, candidate in enumerate(actual)}
    squared = sum(
        (actual_rank[candidate] - expected_rank) ** 2
        for expected_rank, candidate in enumerate(expected, start=1)
    )
    count = len(expected)
    value = 1 - (6 * squared) / (count * (count * count - 1))
    return {"status": "available", "value": round(value, 6), "reason": None}


def _rank_names(rows: list[dict[str, Any]]) -> list[str]:
    return [str(item["candidate_key"]) for item in rows]


def _frequency_baseline(
    terms: list[dict[str, Any]], knowledge: dict[str, Any]
) -> list[dict[str, Any]]:
    results = []
    for domain, specification in knowledge.items():
        keywords = [str(item).casefold() for item in specification.get("research_keywords", [])]
        matched = [
            item
            for item in terms
            if any(
                keyword in str(item["term"]).casefold()
                or str(item["term"]).casefold() in keyword
                for keyword in keywords
            )
        ]
        contributions = Counter(str(item["source"]) for item in matched)
        for role in specification.get("jobs", []):
            results.append(
                {
                    "candidate_key": str(role["name"]),
                    "industry_domain": domain,
                    "score": len(matched),
                    "source_contributions": dict(sorted(contributions.items())),
                }
            )
    return sorted(results, key=lambda item: (-int(item["score"]), item["candidate_key"]))


def _coverage(
    terms: list[dict[str, Any]], ranking: list[dict[str, Any]], k: int
) -> dict[str, Any]:
    valid_sources = sorted({str(item["source"]) for item in terms})
    source_types = sorted({str(item["source_type"]) for item in terms})
    enterprises = sorted({str(item["enterprise"]) for item in terms})
    multi_source = sum(
        len(
            {
                source
                for source, contribution in item["source_contributions"].items()
                if float(contribution) > 0
            }
        )
        >= 2
        for item in ranking[:k]
    )
    return {
        "valid_source_count": len(valid_sources),
        "valid_sources": valid_sources,
        "source_type_count": len(source_types),
        "source_types": source_types,
        "enterprise_count": len(enterprises),
        "enterprises": enterprises,
        "top_k_multi_source_evidence_ratio": round(multi_source / k, 6),
        "multi_source_definition": "Top-K candidate has positive contribution from at least two sources",
    }


def _ranking_metrics(actual: list[str], expected: list[str], k: int) -> dict[str, Any]:
    return {
        "k": k,
        "precision_at_k": precision_at_k(actual, expected, k),
        "precision_hit_definition": "candidate appears in the first K positions of the complete human ranking",
        "spearman": spearman_rank_correlation(actual, expected),
        "spearman_formula": "1 - 6 * sum((actual_rank - expected_rank)^2) / (n * (n^2 - 1))",
    }


def _leading_time(
    rankings: list[tuple[dict[str, Any], list[str]]],
    timings: dict[str, dict[str, Any]],
    k: int,
) -> dict[str, Any]:
    detected: dict[str, str] = {}
    for window, ranked in rankings:
        for candidate in ranked[:k]:
            detected.setdefault(candidate, window["end"])
    items = []
    available_days = []
    for candidate, timing in timings.items():
        endpoint = timing.get("confirmed_at") or timing.get("first_obvious_at")
        basis = "confirmed_at" if timing.get("confirmed_at") else "first_obvious_at"
        if not endpoint:
            items.append(
                {
                    "candidate": candidate,
                    "status": "unavailable",
                    "days": None,
                    "reason": timing.get("unavailable_reason") or "trend timing is unavailable",
                }
            )
            continue
        if candidate not in detected:
            items.append(
                {
                    "candidate": candidate,
                    "status": "unavailable",
                    "days": None,
                    "reason": "candidate never entered algorithm Top-K",
                }
            )
            continue
        days = (_datetime(endpoint) - _datetime(detected[candidate])).days
        available_days.append(days)
        items.append(
            {
                "candidate": candidate,
                "status": "available",
                "start": detected[candidate],
                "end": endpoint,
                "end_basis": basis,
                "days": days,
                "reason": None,
            }
        )
    return {
        "definition": (
            "start = end of the first historical window where the algorithm ranks the candidate "
            "in Top-K; end = human confirmed_at, otherwise first_obvious_at; positive days mean "
            "the algorithm led human confirmation, negative days mean it lagged"
        ),
        "available_count": len(available_days),
        "unavailable_count": len(items) - len(available_days),
        "mean_days": (
            round(sum(available_days) / len(available_days), 6)
            if available_days
            else None
        ),
        "items": items,
    }


def _representatives(
    actual: list[str], expected: list[str], baseline: list[str], k: int
) -> dict[str, Any]:
    successes = [item for item in actual[:k] if item in expected[:k]]
    false_positives = [item for item in actual[:k] if item not in expected[:k]]
    false_negatives = [item for item in expected[:k] if item not in actual[:k]]
    baseline_false_positives = [item for item in baseline[:k] if item not in expected[:k]]
    return {
        "success": successes[0] if successes else None,
        "current_false_positive": false_positives[0] if false_positives else None,
        "current_false_negative": false_negatives[0] if false_negatives else None,
        "baseline_false_positive": (
            baseline_false_positives[0] if baseline_false_positives else None
        ),
        "failure_reasons": [
            *(["current_top_k_contains_human_negative"] if false_positives else []),
            *(["human_top_k_candidate_missed"] if false_negatives else []),
            *(["frequency_baseline_ignores_source_weights"] if baseline_false_positives else []),
        ],
    }


def evaluate_fixed_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    validate_fixed_dataset(dataset)
    cases = []
    for case in dataset["cases"]:
        config = case["algorithm_config"]
        expected = case["expected"]
        terms = case["input"]["terms"]
        k = int(config["evaluation_k"])
        actual_overall = CredibilityService._rank(
            terms, config["job_knowledge"], config["source_weights"]
        )
        baseline_overall = _frequency_baseline(terms, config["job_knowledge"])
        current_windows = []
        baseline_windows = []
        lead_rankings = []
        for window in case["windows"]["historical"]:
            window_terms = [item for item in terms if item["window_id"] == window["window_id"]]
            current = CredibilityService._rank(
                window_terms, config["job_knowledge"], config["source_weights"]
            )
            baseline = _frequency_baseline(window_terms, config["job_knowledge"])
            expected_ranking = expected["rankings_by_window"][window["window_id"]]
            current_windows.append(
                {
                    "window": window,
                    "ranking": current,
                    "metrics": _ranking_metrics(_rank_names(current), expected_ranking, k),
                    "source_coverage": _coverage(window_terms, current, k),
                }
            )
            baseline_windows.append(
                {
                    "window": window,
                    "ranking": baseline,
                    "metrics": _ranking_metrics(_rank_names(baseline), expected_ranking, k),
                }
            )
            lead_rankings.append((window, _rank_names(current)))
        current_metrics = _ranking_metrics(
            _rank_names(actual_overall), expected["overall_ranking"], k
        )
        baseline_metrics = _ranking_metrics(
            _rank_names(baseline_overall), expected["overall_ranking"], k
        )
        cases.append(
            {
                "case_id": case["case_id"],
                "human_annotations": expected,
                "evaluation_rules": {
                    "current_ranking": "existing Trend credibility backtest weighted ranking",
                    "baseline_ranking": "matched keyword frequency without source weights",
                    "human_annotations_do_not_generate_algorithm_rankings": True,
                },
                "model_results": {
                    "current_algorithm": {
                        "overall_ranking": actual_overall,
                        "windows": current_windows,
                    }
                },
                "rule_results": {
                    "frequency_baseline": {
                        "overall_ranking": baseline_overall,
                        "windows": baseline_windows,
                    }
                },
                "metric_results": {
                    "current_algorithm": current_metrics,
                    "frequency_baseline": baseline_metrics,
                    "comparison": {
                        "precision_at_k_delta": round(
                            current_metrics["precision_at_k"]
                            - baseline_metrics["precision_at_k"],
                            6,
                        ),
                        "spearman_delta": round(
                            float(current_metrics["spearman"]["value"])
                            - float(baseline_metrics["spearman"]["value"]),
                            6,
                        ),
                    },
                    "leading_time": _leading_time(
                        lead_rankings, expected["trend_timings"], k
                    ),
                    "source_coverage": {
                        "overall": _coverage(terms, actual_overall, k),
                        "windows": [
                            {
                                "window_id": item["window"]["window_id"],
                                **item["source_coverage"],
                            }
                            for item in current_windows
                        ],
                    },
                    "representative_cases": _representatives(
                        _rank_names(actual_overall),
                        expected["overall_ranking"],
                        _rank_names(baseline_overall),
                        k,
                    ),
                },
            }
        )
    available_leads = [
        item["days"]
        for case in cases
        for item in case["metric_results"]["leading_time"]["items"]
        if item["status"] == "available"
    ]
    return {
        "report_version": REPORT_VERSION,
        "dataset_version": dataset["dataset_version"],
        "algorithm_versions": {
            "current": ALGORITHM_VERSION,
            "baseline": BASELINE_VERSION,
        },
        "algorithm_config_version": dataset["algorithm_config_version"],
        "case_count": len(cases),
        "overall_metrics": {
            "mean_current_precision_at_k": round(
                sum(case["metric_results"]["current_algorithm"]["precision_at_k"] for case in cases)
                / len(cases),
                6,
            ),
            "mean_baseline_precision_at_k": round(
                sum(case["metric_results"]["frequency_baseline"]["precision_at_k"] for case in cases)
                / len(cases),
                6,
            ),
            "mean_available_leading_days": (
                round(sum(available_leads) / len(available_leads), 6)
                if available_leads
                else None
            ),
        },
        "cases": cases,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Trend Intelligence 固定竞赛评估",
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
        "",
        "## 总体指标",
        "",
        f"- 当前算法平均 Precision@K：`{report['overall_metrics']['mean_current_precision_at_k']:.4f}`",
        f"- 频次基线平均 Precision@K：`{report['overall_metrics']['mean_baseline_precision_at_k']:.4f}`",
        f"- 可计算候选平均领先时间：`{report['overall_metrics']['mean_available_leading_days']}` 天",
        "",
    ]
    for case in report["cases"]:
        metrics = case["metric_results"]
        current = metrics["current_algorithm"]
        baseline = metrics["frequency_baseline"]
        lines.extend(
            [
                f"## {case['case_id']}",
                "",
                "| 方法 | Precision@K | Spearman |",
                "|---|---:|---:|",
                f"| 当前 Trend 算法 | {current['precision_at_k']:.4f} | {current['spearman']['value']:.4f} |",
                f"| 关键词频次基线 | {baseline['precision_at_k']:.4f} | {baseline['spearman']['value']:.4f} |",
                "",
                f"Precision@K 差值：`{metrics['comparison']['precision_at_k_delta']:+.4f}`；"
                f"Spearman 差值：`{metrics['comparison']['spearman_delta']:+.4f}`。",
                "",
                "### 每窗口排名",
                "",
                "| 窗口 | 当前算法排名 | 当前 P@K | 基线排名 | 基线 P@K |",
                "|---|---|---:|---|---:|",
            ]
        )
        current_windows = case["model_results"]["current_algorithm"]["windows"]
        baseline_windows = case["rule_results"]["frequency_baseline"]["windows"]
        for current_window, baseline_window in zip(
            current_windows, baseline_windows, strict=True
        ):
            lines.append(
                f"| {current_window['window']['window_id']} | "
                f"{', '.join(_rank_names(current_window['ranking']))} | "
                f"{current_window['metrics']['precision_at_k']:.4f} | "
                f"{', '.join(_rank_names(baseline_window['ranking']))} | "
                f"{baseline_window['metrics']['precision_at_k']:.4f} |"
            )
        lines.extend(["", "### 领先时间", "", metrics["leading_time"]["definition"], ""])
        for item in metrics["leading_time"]["items"]:
            if item["status"] == "available":
                lines.append(
                    f"- {item['candidate']}：{item['days']} 天（{item['start']} → {item['end']}，{item['end_basis']}）"
                )
            else:
                lines.append(f"- {item['candidate']}：unavailable（{item['reason']}）")
        coverage = metrics["source_coverage"]["overall"]
        representatives = metrics["representative_cases"]
        lines.extend(
            [
                "",
                "### 来源覆盖",
                "",
                f"- 有效来源数：{coverage['valid_source_count']}；来源类型数：{coverage['source_type_count']}；企业数：{coverage['enterprise_count']}。",
                f"- Top-K 多来源证据比例：{coverage['top_k_multi_source_evidence_ratio']:.4f}。",
                "",
                "### 代表性案例与失败原因",
                "",
                f"- 成功案例：`{representatives['success']}`",
                f"- 当前算法 false positive：`{representatives['current_false_positive']}`",
                f"- 当前算法 false negative：`{representatives['current_false_negative']}`",
                f"- 基线 false positive：`{representatives['baseline_false_positive']}`",
                f"- 失败原因：`{representatives['failure_reasons'] or ['none']}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
