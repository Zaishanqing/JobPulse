"""Real complex JD evaluation for the Requirement Graph (B-JD-REAL-01).

The evaluation always keeps three graphs separate:

- ``original_extraction_graph``: frozen graph produced by the pre-fix v0
  builder from the pristine baseline requirements;
- ``challenger_graph``: the fixed v1 builder applied to the same pristine
  baseline requirements;
- ``expected_graph``: AI-reviewed gold built from the reviewed requirements.

Metrics are reported separately for original-vs-gold and challenger-vs-gold.
Relation/group/exact metrics include nested ``group_ref`` topology. Because
the gold graph and challenger graph are produced by the same v1 builder, these
structural metrics are builder-consistent diagnostics, not independent
structural annotations.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import JDExtractionResult
from .requirement_graph import build_requirement_graph
from .requirement_graph_evaluation import (
    _graph_edges,
    _group_signatures,
    _leaf_refs,
    _metrics,
)
from .requirement_graph_v0 import (
    BUILDER_IDENTITY as V0_BUILDER_IDENTITY,
    build_requirement_graph as build_requirement_graph_v0,
)


REQUIRED_CASE_FIELDS = {
    "case_id",
    "jd_text",
    "source_blocks",
    "requirements",
    "expected_graph",
}
REAL_REQUIRED_CASE_FIELDS = {
    "source_fact_id",
    "source_version",
    "source_identity",
    "gold_identity",
}
FROZEN_REQUIRED_CASE_FIELDS = {
    "baseline_prediction_frozen",
    "baseline_prediction_identity",
    "original_extraction_graph",
    "reviewed_requirements",
}
MIN_REAL_SAMPLE = 20
MAX_REAL_SAMPLE = 500


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _identity_hash(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def load_real_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"real JD gold manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"real JD gold manifest is invalid JSON: {exc}") from exc
    validate_real_manifest(manifest)
    return manifest


def _build_graph(case: Mapping[str, Any], requirements: list[dict[str, Any]]) -> Any:
    payload = {
        "document_id": str(case["case_id"]),
        "requirements": requirements,
        "company_facts": [],
        "employment_facts": [],
        "responsibilities": [],
    }
    result = JDExtractionResult.model_validate(payload)
    return build_requirement_graph(result, case["source_blocks"])


def _build_graph_v0(case: Mapping[str, Any], requirements: list[dict[str, Any]]) -> Any:
    payload = {
        "document_id": str(case["case_id"]),
        "requirements": requirements,
        "company_facts": [],
        "employment_facts": [],
        "responsibilities": [],
    }
    result = JDExtractionResult.model_validate(payload)
    return build_requirement_graph_v0(result, case["source_blocks"])


def _validate_frozen_case(case: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = str(case["case_id"])
    for field in FROZEN_REQUIRED_CASE_FIELDS:
        if field not in case:
            errors.append(f"case {case_id} missing frozen field: {field}")
            continue
    baseline = case.get("baseline_prediction_frozen")
    if baseline is not None:
        if _canonical_json(case["requirements"]) != _canonical_json(baseline):
            errors.append(f"case {case_id} baseline prediction was mutated")
        expected_identity = _identity_hash(case["requirements"])
        if case.get("baseline_prediction_identity") != expected_identity:
            errors.append(
                f"case {case_id} baseline identity hash mismatch "
                f"(expected {expected_identity})"
            )
    original = case.get("original_extraction_graph")
    if original is not None:
        rebuilt = _build_graph_v0(case, case["requirements"]).model_dump(mode="json")
        if _canonical_json(original) != _canonical_json(rebuilt):
            errors.append(
                f"case {case_id} original graph does not match frozen v0 rebuild"
            )
    return errors


def validate_real_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("real JD gold manifest root must be an object")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("real JD gold manifest requires a cases list")
    if not MIN_REAL_SAMPLE <= len(cases) <= MAX_REAL_SAMPLE:
        raise ValueError(
            f"real JD gold manifest requires {MIN_REAL_SAMPLE}-{MAX_REAL_SAMPLE} cases"
        )
    if "real_frozen" in manifest and not isinstance(manifest["real_frozen"], bool):
        raise ValueError("real JD gold manifest real_frozen must be a boolean")
    real_frozen = manifest.get("real_frozen") is True
    if real_frozen:
        if not str(manifest.get("gold_frozen_at") or "").strip():
            raise ValueError("real JD gold manifest requires gold_frozen_at")
        if not str(manifest.get("provenance") or "").strip():
            raise ValueError("real JD gold manifest requires provenance")
        coverage = manifest.get("coverage")
        if not isinstance(coverage, (list, dict)) or not coverage:
            raise ValueError(
                "real JD gold manifest requires non-empty coverage declaration"
            )
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case[{index}] must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(
                f"case[{index}] missing required fields: {', '.join(missing)}"
            )
        case_id = str(case["case_id"]).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"case[{index}] case_id must be non-empty and unique")
        seen.add(case_id)
        if not isinstance(case["source_blocks"], list):
            raise ValueError(f"case {case_id} source_blocks must be a list")
        if not isinstance(case["requirements"], list):
            raise ValueError(f"case {case_id} requirements must be a list")
        if not isinstance(case["expected_graph"], dict):
            raise ValueError(f"case {case_id} expected_graph must be an object")
        if not str(case.get("jd_text") or "").strip():
            raise ValueError(f"case {case_id} jd_text must be non-empty")
        if real_frozen:
            missing_real = sorted(REAL_REQUIRED_CASE_FIELDS - set(case))
            if missing_real:
                raise ValueError(
                    f"case {case_id} missing real-frozen fields: "
                    f"{', '.join(missing_real)}"
                )
            for field in REAL_REQUIRED_CASE_FIELDS:
                value = case.get(field)
                if not value or (isinstance(value, str) and not value.strip()):
                    raise ValueError(
                        f"case {case_id} {field} must be non-empty"
                    )
            frozen_errors = _validate_frozen_case(case)
            if frozen_errors:
                raise ValueError("; ".join(frozen_errors))


def _span(requirement: Mapping[str, Any]) -> tuple[str, str, Any, Any]:
    evidence = requirement.get("evidence", {})
    return (
        str(evidence.get("source_id") or ""),
        str(evidence.get("quote") or ""),
        evidence.get("start"),
        evidence.get("end"),
    )


def _requirement_evidence_span_metrics(
    prediction: Sequence[Mapping[str, Any]],
    gold: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    gold_by_id = {
        str(requirement["requirement_id"]): requirement for requirement in gold
    }
    exact = 0
    for requirement in prediction:
        gold_requirement = gold_by_id.get(str(requirement["requirement_id"]))
        if gold_requirement is not None and _span(requirement) == _span(gold_requirement):
            exact += 1
    prediction_count = len(prediction)
    gold_count = len(gold)
    precision = round(exact / prediction_count, 6) if prediction_count else None
    recall = round(exact / gold_count, 6) if gold_count else None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = round(2 * precision * recall / (precision + recall), 6)
    else:
        f1 = None
    return {
        "prediction_count": prediction_count,
        "gold_count": gold_count,
        "exact_span_count": exact,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _aggregate_span_metrics(
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    prediction_count = sum(case["requirement_evidence_span"]["prediction_count"] for case in cases)
    gold_count = sum(case["requirement_evidence_span"]["gold_count"] for case in cases)
    exact = sum(case["requirement_evidence_span"]["exact_span_count"] for case in cases)
    precision = round(exact / prediction_count, 6) if prediction_count else None
    recall = round(exact / gold_count, 6) if gold_count else None
    f1 = None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = round(2 * precision * recall / (precision + recall), 6)
    return {
        "prediction_count": prediction_count,
        "gold_count": gold_count,
        "exact_span_count": exact,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def evaluate_real_manifest(manifest: Mapping[str, object]) -> dict[str, Any]:
    validate_real_manifest(manifest)
    case_outputs: list[dict[str, Any]] = []
    original_rejection_correct = 0
    challenger_rejection_correct = 0
    rejection_total = 0
    original_predicted_reject = 0
    challenger_predicted_reject = 0
    original_false_reject = 0
    challenger_false_reject = 0
    for case in manifest["cases"]:
        expected = case["expected_graph"]
        original_graph = case.get("original_extraction_graph")
        if original_graph is None:
            original_graph = _build_graph_v0(case, case["requirements"]).model_dump(mode="json")
        challenger_graph = _build_graph(case, case["requirements"])
        original_metrics = _metrics(expected, original_graph)
        challenger_metrics = _metrics(
            expected, challenger_graph.model_dump(mode="json")
        )
        original_metrics.update(_f1_metrics(expected, original_graph))
        challenger_metrics.update(
            _f1_metrics(expected, challenger_graph.model_dump(mode="json"))
        )
        span_metrics = _requirement_evidence_span_metrics(
            case["requirements"], case.get("reviewed_requirements", [])
        )
        expected_reject = expected.get("status") != "complete" or bool(
            expected.get("unresolved_items")
        )
        original_reject = original_graph.get("status") != "complete" or bool(
            original_graph.get("unresolved_items")
        )
        challenger_reject = (
            challenger_graph.status != "complete"
            or bool(challenger_graph.unresolved_items)
        )
        rejection_total += 1
        original_rejection_correct += int(expected_reject == original_reject)
        challenger_rejection_correct += int(expected_reject == challenger_reject)
        original_predicted_reject += int(original_reject)
        challenger_predicted_reject += int(challenger_reject)
        if not expected_reject:
            original_false_reject += int(original_reject)
            challenger_false_reject += int(challenger_reject)
        case_outputs.append(
            {
                "case_id": case["case_id"],
                "source_version": case.get("source_version"),
                "original_graph_status": original_graph.get("status"),
                "challenger_graph_status": challenger_graph.status,
                "predicted_reject": original_reject,
                "challenger_reject": challenger_reject,
                "expected_reject": expected_reject,
                "metrics": original_metrics,
                "challenger_metrics": challenger_metrics,
                "requirement_evidence_span": span_metrics,
                "failures": _failures(case, original_graph, challenger_graph),
            }
        )
    aggregate_original = _aggregate(
        [case["metrics"] for case in case_outputs], "original"
    )
    aggregate_challenger = _aggregate(
        [case["challenger_metrics"] for case in case_outputs], "challenger"
    )
    reject_case_count = sum(1 for case in case_outputs if case["expected_reject"])
    if reject_case_count == 0:
        original_accuracy = None
        challenger_accuracy = None
    else:
        original_accuracy = _rejection_accuracy(
            original_rejection_correct, rejection_total
        )
        challenger_accuracy = _rejection_accuracy(
            challenger_rejection_correct, rejection_total
        )
    return {
        "report_version": "jd-requirement-graph-real-evaluation.v2",
        "dataset_version": manifest.get("dataset_version"),
        "provenance": manifest.get("provenance"),
        "gold_frozen_at": manifest.get("gold_frozen_at"),
        "gold_type": (
            (manifest.get("gold_policy") or {}).get("gold_type")
            or "ai_reviewed_extraction"
        ),
        "gold_version": (
            (manifest.get("gold_policy") or {}).get("gold_version")
            or "ai-reviewed-gold.v1"
        ),
        "case_count": len(case_outputs),
        "metrics": aggregate_original,
        "challenger_metrics": aggregate_challenger,
        "requirement_evidence_span": _aggregate_span_metrics(case_outputs),
        "structural_metrics_note": (
            "gold expected_graph and challenger_graph are both produced by the v1 "
            "builder from AI-reviewed flat requirements; relation/group/exact metrics "
            "are builder-consistent diagnostics, not independent structural annotation"
        ),
        "reject_case_count": reject_case_count,
        "publication_rejection_accuracy": original_accuracy,
        "challenger_publication_rejection_accuracy": challenger_accuracy,
        "publication_summary": {
            "expected_reject_cases": reject_case_count,
            "original_predicted_reject_cases": original_predicted_reject,
            "challenger_predicted_reject_cases": challenger_predicted_reject,
            "original_false_reject_cases": original_false_reject,
            "challenger_false_reject_cases": challenger_false_reject,
        },
        "cases": case_outputs,
        "limitations": [
            "Gold is AI-reviewed extraction (ai-reviewed-gold.v1): the formal "
            "extraction output was reviewed against the full JD text and corrections "
            "are recorded in review-decisions.jsonl; it is not a human expert gold.",
            "original_extraction_graph is rebuilt with the frozen v0 builder from "
            "the pristine baseline; no extraction-time graph artifact was stored in "
            "the run records.",
            "Publication rejection accuracy is N/A when the gold contains no reject "
            "samples.",
            "Relation/group/exact metrics include nested group_ref topology but are "
            "builder-consistent because the gold graph and challenger graph share "
            "the v1 builder.",
        ],
    }


def _rejection_accuracy(correct: int, total: int) -> float | None:
    if total == 0:
        return None
    return round(correct / total, 6)


def _f1_metrics(expected: Mapping[str, Any], predicted: Mapping[str, Any]) -> dict:
    expected_atomic = _leaf_refs(expected)
    actual_atomic = _leaf_refs(predicted)
    expected_relation = _graph_edges(expected)
    actual_relation = _graph_edges(predicted)
    expected_group = _group_signatures(expected)
    actual_group = _group_signatures(predicted)
    return {
        "atomic_f1": _f1(expected_atomic, actual_atomic),
        "relation_f1": _f1(expected_relation, actual_relation),
        "group_type_f1": _f1(expected_group, actual_group),
    }


def _f1(expected: Counter, actual: Counter) -> float | None:
    overlap = sum(min(expected[key], actual[key]) for key in expected)
    precision = overlap / sum(actual.values()) if sum(actual.values()) else 0.0
    recall = overlap / sum(expected.values()) if sum(expected.values()) else 0.0
    if precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 6)


def _failures(
    case: Mapping[str, Any],
    original: Mapping[str, Any],
    challenger: Any,
) -> list[str]:
    result: list[str] = []
    expected = case["expected_graph"]
    if expected.get("status") != "complete":
        result.append("gold graph is not complete")
    if original.get("status") != "complete":
        result.append(f"original graph status is {original.get('status')}")
    if challenger.status != "complete":
        result.append(f"challenger graph status is {challenger.status}")
    if not _metrics(expected, original)["graph_exact_match"]:
        result.append("original graph differs from gold graph")
    if not _metrics(expected, challenger.model_dump(mode="json"))["graph_exact_match"]:
        result.append("challenger graph differs from gold graph")
    return result


def _aggregate(cases: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
    keys = (
        "atomic_requirement_accuracy",
        "relation_accuracy",
        "group_type_accuracy",
        "group_evidence_reference_overlap",
        "atomic_f1",
        "relation_f1",
        "group_type_f1",
    )
    sums = {key: 0.0 for key in keys}
    counts = {key: 0 for key in keys}
    for case in cases:
        for key in keys:
            value = case.get(key)
            if value is not None:
                sums[key] += float(value)
                counts[key] += 1
    aggregate = {
        key: (round(sums[key] / counts[key], 6) if counts[key] else None)
        for key in keys
    }
    aggregate[f"{label}_graph_exact_match_count"] = sum(
        1 for case in cases if case.get("graph_exact_match")
    )
    return aggregate


def _fmt(value: Any) -> str:
    return "N/A" if value is None else str(value)


def render_real_report(report: Mapping[str, Any]) -> str:
    metrics = report["metrics"]
    challenger = report["challenger_metrics"]
    span = report["requirement_evidence_span"]
    lines = [
        "# B-JD-REAL-01 真实复杂 JD Requirement Graph 评测",
        "",
        f"- 报告版本：`{report['report_version']}`",
        f"- 数据版本：`{report['dataset_version']}`",
        f"- Gold 冻结时间：`{report['gold_frozen_at']}`",
        f"- Gold：`{report['gold_type']} / {report['gold_version']}`",
        f"- 样本数：`{report['case_count']}`",
        "",
        "## Metrics（原始图 vs Gold）",
        "",
        f"- Flat Facts Atomic F1：`{_fmt(metrics['atomic_f1'])}`",
        f"- Requirement Graph Relation F1：`{_fmt(metrics['relation_f1'])}`",
        f"- Requirement Graph Group Type F1：`{_fmt(metrics['group_type_f1'])}`",
        f"- Graph Exact Match：`{metrics['original_graph_exact_match_count']}/{report['case_count']}`",
        f"- Group Evidence Reference Overlap：`{_fmt(metrics['group_evidence_reference_overlap'])}`",
        f"- 发布拒收准确率：`{_fmt(report['publication_rejection_accuracy'])}`"
        f"（reject 样本数 `{report['reject_case_count']}`，"
        f"false reject `{report['publication_summary']['original_false_reject_cases']}`）",
        "",
        "## Metrics（修复后 challenger 图 vs Gold）",
        "",
        f"- Flat Facts Atomic F1：`{_fmt(challenger['atomic_f1'])}`",
        f"- Requirement Graph Relation F1：`{_fmt(challenger['relation_f1'])}`",
        f"- Requirement Graph Group Type F1：`{_fmt(challenger['group_type_f1'])}`",
        f"- Graph Exact Match：`{challenger['challenger_graph_exact_match_count']}/{report['case_count']}`",
        f"- Group Evidence Reference Overlap：`{_fmt(challenger['group_evidence_reference_overlap'])}`",
        f"- 发布拒收准确率：`{_fmt(report['challenger_publication_rejection_accuracy'])}`"
        f"（reject 样本数 `{report['reject_case_count']}`，"
        f"false reject `{report['publication_summary']['challenger_false_reject_cases']}`）",
        "",
        "## Requirement Evidence Span（baseline flat requirements vs Gold）",
        "",
        f"- exact span：`{span['exact_span_count']}`（prediction `{span['prediction_count']}` / gold `{span['gold_count']}`）",
        f"- precision：`{_fmt(span['precision'])}`",
        f"- recall：`{_fmt(span['recall'])}`",
        f"- f1：`{_fmt(span['f1'])}`",
        "",
        "> " + report["structural_metrics_note"],
        "",
        "## Cases",
        "",
        "| case_id | original | challenger | atomic_f1 | relation_f1 | group_f1 | exact | reject |",
        "|---|---|---|---:|---:|---:|---|---:|",
    ]
    for case in report["cases"]:
        value = case["metrics"]
        lines.append(
            f"| {case['case_id']} | {case['original_graph_status']} | "
            f"{case['challenger_graph_status']} | "
            f"{_fmt(value['atomic_f1'])} | {_fmt(value['relation_f1'])} | "
            f"{_fmt(value['group_type_f1'])} | "
            f"{value['graph_exact_match']} | {case['predicted_reject']} |"
        )
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
