"""Gap-focused JD extraction experiments.

This module deliberately separates three claims:

* stratified generalization of an already-frozen span benchmark;
* deterministic publication-boundary integrity rejection;
* independent human annotation, which remains incomplete until two humans
  submit annotations and an adjudicated gold file is frozen.

Synthetic integrity mutations must never be reported as semantic rejection
accuracy, and blank annotation packs must never be treated as gold.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence


_MODALITIES = {"required", "preferred", "bonus"}
_MINIMUM_JDS_PER_PRIMARY_STRATUM = 30
_MINIMUM_CHALLENGE_CASES = 20
_MINIMUM_EXACT_F1_CI_LOWER = 0.95
_EXPECTED_PRIMARY_STRATA = {
    "source_family": ("job_board_chunk", "liepin"),
    "title_proxy_family": (
        "ai_ml",
        "backend",
        "client_web",
        "data",
        "design",
        "product_operations",
        "quality_operations",
        "other",
    ),
    "text_length": ("short_le_500", "medium_501_1000", "long_gt_1000"),
    "requirement_count": ("simple_le_8", "medium_9_15", "complex_gt_15"),
}
_COVERAGE_MARKS = (
    "years_of_experience",
    "project_context",
    "combined_skills",
    "evidence_alignment_failure",
    "unresolved",
    "publication_rejection_candidate",
)


def source_family(case: Mapping[str, Any]) -> str:
    source_ref = str((case.get("source_identity") or {}).get("source_ref") or "").lower()
    if "liepin" in source_ref:
        return "liepin"
    if "jd_chunk" in source_ref:
        return "job_board_chunk"
    return "other"


def title_family(title: str) -> str:
    value = title.casefold()
    if _contains_any_title_token(value, ("算法", "ai", "llm", "大模型", "机器学习", "nlp")):
        return "ai_ml"
    if _contains_any_title_token(value, ("数据", "data", "数仓", "etl", "bi")):
        return "data"
    if _contains_any_title_token(value, ("java", "后端", "backend", "go", "php")):
        return "backend"
    if _contains_any_title_token(value, ("前端", "frontend", "web", "android", "ios")):
        return "client_web"
    if _contains_any_title_token(value, ("产品", "运营", "市场", "销售")):
        return "product_operations"
    if _contains_any_title_token(value, ("设计", "ui", "ux")):
        return "design"
    if _contains_any_title_token(value, ("测试", "运维", "安全", "devops", "qa", "sre")):
        return "quality_operations"
    return "other"


def _contains_any_title_token(value: str, tokens: Sequence[str]) -> bool:
    for token in tokens:
        if token.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", value):
                return True
        elif token in value:
            return True
    return False


def text_length_bucket(length: int) -> str:
    if length <= 500:
        return "short_le_500"
    if length <= 1000:
        return "medium_501_1000"
    return "long_gt_1000"


def requirement_count_bucket(count: int) -> str:
    if count <= 8:
        return "simple_le_8"
    if count <= 15:
        return "medium_9_15"
    return "complex_gt_15"


def _case_strata(case: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_family": source_family(case),
        "title_proxy_family": title_family(str(case.get("job_title") or "")),
        "text_length": text_length_bucket(len(str(case.get("jd_text") or ""))),
        "requirement_count": requirement_count_bucket(len(case.get("requirements") or [])),
    }


def _annotation_pack_strata_v1(case: Mapping[str, Any]) -> dict[str, str]:
    """Keep the already-frozen blind-pack sampling identity stable."""

    title = str(case.get("job_title") or "").casefold()
    if any(token in title for token in ("算法", "ai", "llm", "大模型", "机器学习", "nlp")):
        family = "ai_ml"
    elif any(token in title for token in ("java", "后端", "backend", "go", "php")):
        family = "backend"
    elif any(token in title for token in ("前端", "frontend", "web", "android", "ios")):
        family = "client_web"
    elif any(token in title for token in ("产品", "运营", "市场", "销售")):
        family = "product_operations"
    elif any(token in title for token in ("设计", "ui", "ux")):
        family = "design"
    elif any(token in title for token in ("测试", "运维", "安全", "devops", "qa")):
        family = "quality_operations"
    else:
        family = "other"
    return {
        "source_family": source_family(case),
        "title_family": family,
        "text_length": text_length_bucket(len(str(case.get("jd_text") or ""))),
        "requirement_count": requirement_count_bucket(len(case.get("requirements") or [])),
    }


def _diagnostic_strata(case: Mapping[str, Any]) -> dict[str, str]:
    primary = _case_strata(case)
    marks = case.get("coverage_marks") or {}
    result = {
        "source_x_title_proxy": (
            f"{primary['source_family']}::{primary['title_proxy_family']}"
        )
    }
    for mark in _COVERAGE_MARKS:
        result[f"coverage_mark::{mark}"] = "present" if marks.get(mark) is True else "absent"
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _cluster_bootstrap_interval(
    rows: Sequence[Mapping[str, int]],
    *,
    numerator_key: str,
    denominator_key: str,
    iterations: int = 1000,
    seed: int = 20260830,
) -> list[float] | None:
    if not rows:
        return None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        selected = [rows[rng.randrange(len(rows))] for _ in rows]
        denominator = sum(int(row[denominator_key]) for row in selected)
        if denominator:
            samples.append(
                sum(int(row[numerator_key]) for row in selected) / denominator
            )
    if not samples:
        return None
    samples.sort()
    low = samples[int(0.025 * (len(samples) - 1))]
    high = samples[int(0.975 * (len(samples) - 1))]
    return [round(low, 6), round(high, 6)]


def _mean(values: Iterable[float]) -> float | None:
    collected = list(values)
    return round(sum(collected) / len(collected), 6) if collected else None


def _aggregate_stratum(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gold_count = sum(int(row["gold_count"]) for row in rows)
    prediction_count = sum(int(row["prediction_count"]) for row in rows)
    exact = sum(int(row["exact_span_count"]) for row in rows)
    relaxed = sum(int(row["relaxed_span_count"]) for row in rows)
    unresolved = sum(int(row["unresolved_count"]) for row in rows)
    hallucinated = sum(int(row["hallucinated_count"]) for row in rows)
    exact_misses = sum(int(row["exact_miss_count"]) for row in rows)
    ci = _cluster_bootstrap_interval(
        rows,
        numerator_key="exact_f1_numerator",
        denominator_key="f1_denominator",
    )
    sample_sufficient = len(rows) >= _MINIMUM_JDS_PER_PRIMARY_STRATUM
    metric_gate = (
        sample_sufficient
        and ci is not None
        and ci[0] >= _MINIMUM_EXACT_F1_CI_LOWER
        and hallucinated == 0
    )
    return {
        "jd_count": len(rows),
        "gold_span_count": gold_count,
        "prediction_span_count": prediction_count,
        "exact_span_f1_micro": _ratio(2 * exact, prediction_count + gold_count),
        "exact_span_f1_jd_macro": _mean(float(row["case_exact_f1"]) for row in rows),
        "exact_span_f1_cluster_bootstrap_95ci": ci,
        "relaxed_span_f1": _ratio(2 * relaxed, prediction_count + gold_count),
        "case_exact_match_rate": _ratio(
            sum(int(row["exact_miss_count"]) == 0 for row in rows), len(rows)
        ),
        "error_case_count": sum(int(row["exact_miss_count"]) > 0 for row in rows),
        "exact_miss_count": exact_misses,
        "unresolved_rate": _ratio(unresolved, gold_count),
        "hallucinated_rate": _ratio(hallucinated, prediction_count),
        "sample_sufficient_ge_30_jds": sample_sufficient,
        "metric_gate_passed": metric_gate if sample_sufficient else None,
        "additional_jds_needed": max(0, _MINIMUM_JDS_PER_PRIMARY_STRATUM - len(rows)),
    }


def build_stratified_coverage_report(
    manifest: Mapping[str, Any], span_report: Mapping[str, Any]
) -> dict[str, Any]:
    cases = {str(case["case_id"]): case for case in manifest.get("cases") or []}
    metrics = {
        str(case["case_id"]): case.get("metrics") or {}
        for case in span_report.get("cases") or []
    }
    if set(cases) != set(metrics):
        missing_metrics = sorted(set(cases) - set(metrics))
        missing_cases = sorted(set(metrics) - set(cases))
        raise ValueError(
            "manifest/span case identities differ: "
            f"missing_metrics={missing_metrics[:5]}, missing_cases={missing_cases[:5]}"
        )

    dimensions: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    diagnostic_dimensions: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    case_rows: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        value = metrics[case_id]
        row = {
            "case_id": case_id,
            "exact_span_count": int(value.get("exact_span_count") or 0),
            "relaxed_span_count": int(value.get("relaxed_span_count") or 0),
            "prediction_count": int(value.get("prediction_count") or 0),
            "gold_count": int(value.get("gold_count") or 0),
            "unresolved_count": int(value.get("unresolved_count") or 0),
            "hallucinated_count": int(value.get("hallucinated_count") or 0),
        }
        row["exact_f1_numerator"] = 2 * row["exact_span_count"]
        row["f1_denominator"] = row["prediction_count"] + row["gold_count"]
        row["case_exact_f1"] = (
            row["exact_f1_numerator"] / row["f1_denominator"]
            if row["f1_denominator"]
            else 0.0
        )
        row["exact_miss_count"] = (
            max(row["prediction_count"], row["gold_count"])
            - row["exact_span_count"]
        )
        row["strata"] = _case_strata(case)
        case_rows.append(row)
        for dimension, stratum in _case_strata(case).items():
            dimensions[dimension][stratum].append(row)
        for dimension, stratum in _diagnostic_strata(case).items():
            diagnostic_dimensions[dimension][stratum].append(row)

    rendered_dimensions: dict[str, list[dict[str, Any]]] = {}
    insufficient: list[dict[str, Any]] = []
    metric_failures: list[dict[str, Any]] = []
    for dimension, expected_strata in sorted(_EXPECTED_PRIMARY_STRATA.items()):
        strata = dimensions.get(dimension, {})
        output_rows: list[dict[str, Any]] = []
        for stratum in expected_strata:
            rows = strata.get(stratum, [])
            output = {"stratum": stratum, **_aggregate_stratum(rows)}
            output_rows.append(output)
            if not output["sample_sufficient_ge_30_jds"]:
                insufficient.append(
                    {
                        "dimension": dimension,
                        "stratum": stratum,
                        "jd_count": len(rows),
                        "additional_jds_needed": output["additional_jds_needed"],
                    }
                )
            elif output["metric_gate_passed"] is False:
                metric_failures.append(
                    {
                        "dimension": dimension,
                        "stratum": stratum,
                        "jd_count": len(rows),
                        "exact_span_f1_cluster_bootstrap_95ci": output[
                            "exact_span_f1_cluster_bootstrap_95ci"
                        ],
                        "hallucinated_rate": output["hallucinated_rate"],
                    }
                )
        rendered_dimensions[dimension] = output_rows

    rendered_diagnostics: dict[str, list[dict[str, Any]]] = {}
    for dimension, strata in sorted(diagnostic_dimensions.items()):
        rendered_diagnostics[dimension] = [
            {"stratum": stratum, **_aggregate_stratum(rows)}
            for stratum, rows in sorted(strata.items())
        ]

    overall = _aggregate_stratum(case_rows)
    failing_cases = sorted(
        (row for row in case_rows if row["exact_miss_count"] > 0),
        key=lambda row: (-int(row["exact_miss_count"]), str(row["case_id"])),
    )
    total_misses = sum(int(row["exact_miss_count"]) for row in failing_cases)
    top_failures = [
        {
            "case_id": row["case_id"],
            "exact_span_f1": round(float(row["case_exact_f1"]), 6),
            "exact_miss_count": row["exact_miss_count"],
            "gold_span_count": row["gold_count"],
            "strata": row["strata"],
        }
        for row in failing_cases[:10]
    ]
    error_analysis = {
        "error_case_count": len(failing_cases),
        "exact_miss_count": total_misses,
        "top_10_error_concentration": _ratio(
            sum(item["exact_miss_count"] for item in top_failures), total_misses
        ),
        "top_failing_cases": top_failures,
    }
    sampling_plan = sorted(
        (
            {
                **item,
                "priority": (
                    "P0" if item["jd_count"] == 0 else "P1" if item["jd_count"] < 10 else "P2"
                ),
            }
            for item in insufficient
        ),
        key=lambda item: (
            {"P0": 0, "P1": 1, "P2": 2}[item["priority"]],
            -int(item["additional_jds_needed"]),
            str(item["dimension"]),
            str(item["stratum"]),
        ),
    )
    challenge_coverage_plan: list[dict[str, Any]] = []
    for mark in _COVERAGE_MARKS:
        dimension = f"coverage_mark::{mark}"
        present = next(
            (
                row
                for row in rendered_diagnostics.get(dimension, [])
                if row["stratum"] == "present"
            ),
            None,
        )
        count = int(present["jd_count"]) if present else 0
        if count < _MINIMUM_CHALLENGE_CASES:
            challenge_coverage_plan.append(
                {
                    "dimension": dimension,
                    "stratum": "present",
                    "jd_count": count,
                    "additional_jds_needed": _MINIMUM_CHALLENGE_CASES - count,
                    "priority": "P0" if count == 0 else "P1",
                }
            )

    canonical = json.dumps(
        {"manifest": manifest.get("dataset_version"), "span": span_report.get("aggregate")},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema_version": "jd-stratified-generalization.v2",
        "dataset_version": manifest.get("dataset_version"),
        "input_fingerprint": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        "case_count": len(cases),
        "method": {
            "strata": "fixed deterministic primary and diagnostic strata",
            "confidence_interval": (
                "JD-cluster bootstrap; spans within one JD are not independent"
            ),
            "title_proxy": (
                "deterministic raw-title keyword proxy because the frozen manifest "
                "does not contain normalized position classification"
            ),
            "micro_macro": (
                "micro weights spans; JD macro gives every document equal weight"
            ),
        },
        "gates": {
            "minimum_jds_per_primary_stratum": _MINIMUM_JDS_PER_PRIMARY_STRATUM,
            "minimum_positive_cases_per_challenge_mark": _MINIMUM_CHALLENGE_CASES,
            "minimum_exact_f1_ci_lower": _MINIMUM_EXACT_F1_CI_LOWER,
            "maximum_hallucinated_rate": 0.0,
        },
        "overall": overall,
        "dimensions": rendered_dimensions,
        "diagnostic_dimensions": rendered_diagnostics,
        "insufficient_strata": insufficient,
        "metric_gate_failures": metric_failures,
        "sampling_plan": sampling_plan,
        "challenge_coverage_plan": challenge_coverage_plan,
        "error_analysis": error_analysis,
        "coverage_gate_passed": not insufficient,
        "challenge_coverage_gate_passed": not challenge_coverage_plan,
        "performance_gate_passed": not metric_failures,
        "gate_passed": not insufficient and not challenge_coverage_plan and not metric_failures,
        "limitations": [
            "title_proxy_family is a raw-title heuristic, not authoritative position taxonomy",
            "source_family reflects archived run families, not a complete platform market distribution",
            "this evaluates span alignment generalization, not independent semantic extraction accuracy",
        ],
    }


def render_stratified_coverage_report(report: Mapping[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "# JD 抽取分层泛化审计",
        "",
        f"- 数据版本：`{report.get('dataset_version')}`",
        f"- JD 数：`{report.get('case_count')}`",
        "- 置信区间：按 JD 聚类 bootstrap，避免把同一 JD 内 span 当作独立样本。",
        "- 主分层门禁：每层至少 30 条 JD，Exact F1 95% CI 下界不低于 0.95，幻觉率为 0。",
        "- 岗位族是原始标题关键词代理，不是标准岗位分类。",
        "",
        "## 总体指标",
        "",
        f"- Exact Span F1（span micro）：`{overall['exact_span_f1_micro']}`",
        f"- Exact Span F1（JD macro）：`{overall['exact_span_f1_jd_macro']}`",
        f"- Case Exact Match：`{overall['case_exact_match_rate']}`",
        f"- 含严格边界错误的 JD：`{overall['error_case_count']}/{overall['jd_count']}`",
        "",
    ]
    for dimension, rows in (report.get("dimensions") or {}).items():
        lines.extend(
            [
                f"## {dimension}",
                "",
                "| 分层 | JD | 需补 | Gold spans | Exact micro/macro | 95% CI | Case exact | 错误 JD | 未解析率 | 指标门禁 |",
                "|---|---:|---:|---:|---|---|---:|---:|---:|---|",
            ]
        )
        for row in rows:
            ci = row.get("exact_span_f1_cluster_bootstrap_95ci")
            ci_text = "N/A" if ci is None else f"{ci[0]}–{ci[1]}"
            lines.append(
                f"| {row['stratum']} | {row['jd_count']} | {row['additional_jds_needed']} | "
                f"{row['gold_span_count']} | {row['exact_span_f1_micro']} / "
                f"{row['exact_span_f1_jd_macro']} | {ci_text} | "
                f"{row['case_exact_match_rate']} | {row['error_case_count']} | "
                f"{row['unresolved_rate']} | {_gate_label(row)} |"
            )
        lines.append("")
    lines.extend(
        [
            "## 难例特征诊断（不单独控制总门禁）",
            "",
            "| 维度 | 分层 | JD | Exact micro | Case exact | 错误 JD |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for dimension, rows in (report.get("diagnostic_dimensions") or {}).items():
        for row in rows:
            lines.append(
                f"| {dimension} | {row['stratum']} | {row['jd_count']} | "
                f"{row['exact_span_f1_micro']} | {row['case_exact_match_rate']} | "
                f"{row['error_case_count']} |"
            )
    error_analysis = report["error_analysis"]
    lines.extend(
        [
            "",
            "## 错误集中度",
            "",
            f"- 严格 span 未命中：`{error_analysis['exact_miss_count']}`，分布于 "
            f"`{error_analysis['error_case_count']}` 条 JD。",
            f"- Top 10 难例承载错误比例：`{error_analysis['top_10_error_concentration']}`。",
            "",
            "| case_id | Exact F1 | 未命中 | Gold spans | 岗位代理族 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for case in error_analysis["top_failing_cases"]:
        lines.append(
            f"| {case['case_id']} | {case['exact_span_f1']} | "
            f"{case['exact_miss_count']} | {case['gold_span_count']} | "
            f"{case['strata']['title_proxy_family']} |"
        )
    lines.extend(
        [
            "",
            "## 补样计划",
            "",
            "| 优先级 | 维度 | 分层 | 当前 | 至少补充 |",
            "|---|---|---|---:|---:|",
        ]
    )
    for item in report["sampling_plan"]:
        lines.append(
            f"| {item['priority']} | {item['dimension']} | {item['stratum']} | "
            f"{item['jd_count']} | {item['additional_jds_needed']} |"
        )
    lines.extend(
        [
            "",
            "### 难例特征补样",
            "",
            "每个既定难例标记至少需要 20 条阳性案例。",
            "",
            "| 优先级 | 难例标记 | 当前阳性 | 至少补充 |",
            "|---|---|---:|---:|",
        ]
    )
    for item in report["challenge_coverage_plan"]:
        lines.append(
            f"| {item['priority']} | {item['dimension']} | {item['jd_count']} | "
            f"{item['additional_jds_needed']} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 覆盖门禁：`{'PASS' if report['coverage_gate_passed'] else 'FAIL'}`。",
            f"- 难例特征覆盖门禁：`{'PASS' if report['challenge_coverage_gate_passed'] else 'FAIL'}`。",
            f"- 已具备样本量分层的性能门禁：`{'PASS' if report['performance_gate_passed'] else 'FAIL'}`。",
            f"- 总门禁：`{'PASS' if report['gate_passed'] else 'FAIL'}`。",
            "- 总门禁未通过时，不应使用总体 F1 代表所有岗位族。",
            "",
            "## 局限",
            "",
            *[f"- {item}" for item in report.get("limitations") or []],
            "",
        ]
    )
    return "\n".join(lines)


def _gate_label(row: Mapping[str, Any]) -> str:
    if not row["sample_sufficient_ge_30_jds"]:
        return "NOT_EVALUATED"
    return "PASS" if row["metric_gate_passed"] else "FAIL"


def build_blinded_annotation_pack(
    manifest: Mapping[str, Any], *, target: int = 200, seed: int = 20260830
) -> list[dict[str, Any]]:
    cases = list(manifest.get("cases") or [])
    if target < 1 or target > len(cases):
        raise ValueError(f"target must be between 1 and {len(cases)}")
    rng = random.Random(seed)
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        strata = _annotation_pack_strata_v1(case)
        key = tuple(strata[name] for name in sorted(strata))
        groups[key].append(case)
    for values in groups.values():
        rng.shuffle(values)
    selected: list[Mapping[str, Any]] = []
    keys = sorted(groups)
    while len(selected) < target:
        progressed = False
        for key in keys:
            if groups[key] and len(selected) < target:
                selected.append(groups[key].pop())
                progressed = True
        if not progressed:
            break

    pack: list[dict[str, Any]] = []
    for case in selected:
        pack.append(
            {
                "case_id": case["case_id"],
                "source_version": case.get("source_version"),
                "job_title": case.get("job_title"),
                "jd_text": case.get("jd_text"),
                "strata": _annotation_pack_strata_v1(case),
                "annotation": {
                    "annotator_id": None,
                    "publication_decision": None,
                    "reject_reason_codes": [],
                    "responsibilities": [],
                    "requirements": [],
                    "notes": None,
                },
            }
        )
    return pack


def _integrity_reasons(case: Mapping[str, Any]) -> list[str]:
    text = str(case.get("jd_text") or "")
    blocks = {
        str(block.get("source_id")): block for block in case.get("source_blocks") or []
    }
    requirements = case.get("reviewed_requirements") or case.get("requirements") or []
    reasons: set[str] = set()
    if not requirements:
        reasons.add("empty_requirements")
    for requirement in requirements:
        if requirement.get("modality") not in _MODALITIES:
            reasons.add("invalid_modality")
        evidence = requirement.get("evidence")
        if not isinstance(evidence, Mapping):
            reasons.add("missing_evidence")
            continue
        quote = evidence.get("quote")
        start = evidence.get("start")
        end = evidence.get("end")
        source_id = str(evidence.get("source_id") or "")
        if source_id not in blocks:
            reasons.add("unknown_source_id")
        if not isinstance(quote, str) or not quote:
            reasons.add("empty_evidence_quote")
        if not isinstance(start, int) or not isinstance(end, int):
            reasons.add("missing_evidence_offset")
        elif start < 0 or end <= start or end > len(text) or text[start:end] != quote:
            reasons.add("evidence_offset_mismatch")
    return sorted(reasons)


def build_integrity_rejection_benchmark(
    manifest: Mapping[str, Any], *, valid_count: int = 50, seed: int = 20260830
) -> dict[str, Any]:
    cases = list(manifest.get("cases") or [])
    if valid_count < 5 or valid_count > len(cases):
        raise ValueError("valid_count must be at least 5 and no greater than case count")
    rng = random.Random(seed)
    rng.shuffle(cases)
    valid_cases = [deepcopy(case) for case in cases[:valid_count]]
    mutation_types = (
        "evidence_quote_not_in_text",
        "evidence_offset_shift",
        "unknown_source_id",
        "invalid_modality",
        "missing_evidence",
    )
    benchmark_cases: list[dict[str, Any]] = []
    for index, case in enumerate(valid_cases):
        benchmark_cases.append(
            {
                "case_id": f"valid-{case['case_id']}",
                "label_reject": False,
                "mutation": None,
                "predicted_reject": bool(_integrity_reasons(case)),
                "reason_codes": _integrity_reasons(case),
            }
        )
        mutated = deepcopy(case)
        requirements = mutated.get("reviewed_requirements") or mutated.get("requirements")
        requirement = requirements[0]
        mutation = mutation_types[index % len(mutation_types)]
        if mutation == "evidence_quote_not_in_text":
            requirement["evidence"]["quote"] = str(requirement["evidence"]["quote"]) + "不存在"
        elif mutation == "evidence_offset_shift":
            requirement["evidence"]["start"] = int(requirement["evidence"]["start"]) + 1
        elif mutation == "unknown_source_id":
            requirement["evidence"]["source_id"] = "src_missing"
        elif mutation == "invalid_modality":
            requirement["modality"] = "unknown"
        elif mutation == "missing_evidence":
            requirement.pop("evidence", None)
        reasons = _integrity_reasons(mutated)
        benchmark_cases.append(
            {
                "case_id": f"reject-{mutation}-{case['case_id']}",
                "label_reject": True,
                "mutation": mutation,
                "predicted_reject": bool(reasons),
                "reason_codes": reasons,
            }
        )

    tp = sum(case["label_reject"] and case["predicted_reject"] for case in benchmark_cases)
    tn = sum(not case["label_reject"] and not case["predicted_reject"] for case in benchmark_cases)
    fp = sum(not case["label_reject"] and case["predicted_reject"] for case in benchmark_cases)
    fn = sum(case["label_reject"] and not case["predicted_reject"] for case in benchmark_cases)
    mutation_summary: dict[str, dict[str, int]] = {}
    for mutation in mutation_types:
        rows = [case for case in benchmark_cases if case.get("mutation") == mutation]
        mutation_summary[mutation] = {
            "case_count": len(rows),
            "rejected_count": sum(case["predicted_reject"] for case in rows),
        }
    return {
        "schema_version": "jd-publication-integrity-rejection.v1",
        "dataset_version": manifest.get("dataset_version"),
        "scope": (
            "synthetic contract/evidence integrity mutations over reviewed real JD cases; "
            "not semantic rejection accuracy"
        ),
        "case_count": len(benchmark_cases),
        "valid_case_count": valid_count,
        "reject_case_count": valid_count,
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "accuracy": _ratio(tp + tn, len(benchmark_cases)),
        "reject_precision": _ratio(tp, tp + fp),
        "reject_recall": _ratio(tp, tp + fn),
        "mutation_summary": mutation_summary,
        "cases": benchmark_cases,
    }


def render_integrity_rejection_report(report: Mapping[str, Any]) -> str:
    confusion = report["confusion"]
    lines = [
        "# JD 发布边界完整性拒绝实验",
        "",
        f"- 样本：{report['case_count']}（有效 {report['valid_case_count']} / 应拒绝 {report['reject_case_count']}）",
        f"- Accuracy：`{report['accuracy']}`",
        f"- Reject Precision：`{report['reject_precision']}`",
        f"- Reject Recall：`{report['reject_recall']}`",
        f"- TP/TN/FP/FN：`{confusion['tp']}/{confusion['tn']}/{confusion['fp']}/{confusion['fn']}`",
        "",
        "> 本实验只验证契约与 Evidence 完整性拒绝。拒绝样本由真实 JD 的确定性突变构造，不能当作真实语义拒绝准确率。",
        "",
        "| 突变 | 样本 | 被拒绝 |",
        "|---|---:|---:|",
    ]
    for mutation, values in report["mutation_summary"].items():
        lines.append(f"| {mutation} | {values['case_count']} | {values['rejected_count']} |")
    lines.append("")
    return "\n".join(lines)


def _requirement_signatures(record: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    annotation = record.get("annotation") or record
    result: set[tuple[Any, ...]] = set()
    for requirement in annotation.get("requirements") or []:
        canonical = deepcopy(requirement)
        canonical.pop("requirement_id", None)
        evidence = canonical.get("evidence") or {}
        canonical["evidence"] = {
            "start": evidence.get("start"),
            "end": evidence.get("end"),
            "quote": evidence.get("quote"),
        }
        result.add((json.dumps(canonical, ensure_ascii=False, sort_keys=True),))
    return result


def _set_metrics(expected: set[Any], predicted: set[Any]) -> dict[str, float]:
    overlap = len(expected & predicted)
    precision = overlap / len(predicted) if predicted else (1.0 if not expected else 0.0)
    recall = overlap / len(expected) if expected else (1.0 if not predicted else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    labels = sorted({value for pair in pairs for value in pair})
    observed = sum(left == right for left, right in pairs) / len(pairs)
    expected = sum(
        (sum(left == label for left, _ in pairs) / len(pairs))
        * (sum(right == label for _, right in pairs) / len(pairs))
        for label in labels
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 6)


def evaluate_independent_annotations(
    annotator_a: Iterable[Mapping[str, Any]],
    annotator_b: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    left = {str(record["case_id"]): record for record in annotator_a}
    right = {str(record["case_id"]): record for record in annotator_b}
    shared = sorted(set(left) & set(right))
    completed: list[str] = []
    decision_pairs: list[tuple[str, str]] = []
    aggregate_expected: set[tuple[str, Any]] = set()
    aggregate_predicted: set[tuple[str, Any]] = set()
    for case_id in shared:
        left_annotation = left[case_id].get("annotation") or left[case_id]
        right_annotation = right[case_id].get("annotation") or right[case_id]
        left_decision = left_annotation.get("publication_decision")
        right_decision = right_annotation.get("publication_decision")
        if left_decision not in {"publish", "reject"} or right_decision not in {
            "publish",
            "reject",
        }:
            continue
        completed.append(case_id)
        decision_pairs.append((left_decision, right_decision))
        aggregate_expected.update(
            (case_id, signature) for signature in _requirement_signatures(left[case_id])
        )
        aggregate_predicted.update(
            (case_id, signature) for signature in _requirement_signatures(right[case_id])
        )
    requirement_agreement = (
        _set_metrics(aggregate_expected, aggregate_predicted)
        if completed
        else {"precision": None, "recall": None, "f1": None}
    )
    return {
        "schema_version": "jd-independent-annotation-agreement.v1",
        "shared_case_count": len(shared),
        "completed_by_both_count": len(completed),
        "completion_rate": _ratio(len(completed), len(shared)),
        "publication_decision_cohen_kappa": _cohen_kappa(decision_pairs),
        "requirement_exact_agreement": requirement_agreement,
        "status": "complete" if shared and len(completed) == len(shared) else "pending_human_annotation",
    }


def evaluate_predictions_against_adjudicated_gold(
    manifest: Mapping[str, Any], gold_records: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    predictions = {str(case["case_id"]): case for case in manifest.get("cases") or []}
    gold = {str(record["case_id"]): record for record in gold_records}
    completed: list[str] = []
    predicted_requirements: set[tuple[str, Any]] = set()
    gold_requirements: set[tuple[str, Any]] = set()
    decision_pairs: list[tuple[str, str]] = []
    for case_id in sorted(set(predictions) & set(gold)):
        annotation = gold[case_id].get("annotation") or gold[case_id]
        decision = annotation.get("publication_decision")
        if decision not in {"publish", "reject"}:
            continue
        completed.append(case_id)
        predicted_record = {"requirements": predictions[case_id].get("requirements") or []}
        predicted_requirements.update(
            (case_id, signature)
            for signature in _requirement_signatures(predicted_record)
        )
        gold_requirements.update(
            (case_id, signature) for signature in _requirement_signatures(gold[case_id])
        )
        predicted_decision = (
            "reject"
            if (predictions[case_id].get("expected_graph") or {}).get("status")
            not in {None, "complete"}
            else "publish"
        )
        decision_pairs.append((decision, predicted_decision))
    publish_correct = sum(expected == predicted for expected, predicted in decision_pairs)
    return {
        "schema_version": "jd-independent-human-gold-evaluation.v1",
        "gold_case_count": len(gold),
        "evaluated_case_count": len(completed),
        "requirement_exact_semantic_metrics": _set_metrics(
            gold_requirements, predicted_requirements
        )
        if completed
        else {"precision": None, "recall": None, "f1": None},
        "publication_decision_accuracy": _ratio(publish_correct, len(decision_pairs)),
        "status": "complete" if gold and len(completed) == len(gold) else "pending_adjudicated_gold",
    }


__all__ = [
    "build_blinded_annotation_pack",
    "build_integrity_rejection_benchmark",
    "build_stratified_coverage_report",
    "evaluate_independent_annotations",
    "evaluate_predictions_against_adjudicated_gold",
    "render_integrity_rejection_report",
    "render_stratified_coverage_report",
    "requirement_count_bucket",
    "source_family",
    "text_length_bucket",
    "title_family",
]
