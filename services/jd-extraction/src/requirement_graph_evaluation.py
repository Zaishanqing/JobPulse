"""Evaluation for the deterministic Requirement Graph layer."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .models import JDExtractionResult
from .requirement_graph import build_requirement_graph


DATASET_VERSION = "requirement-graph-competition.v1"
REPORT_VERSION = "requirement-graph-competition-report.v1"
REQUIRED_CASE_FIELDS = {"case_id", "jd_text", "source_blocks", "requirements", "expected_graph"}


def load_dataset(path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"requirement graph evaluation dataset not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"requirement graph evaluation dataset is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: object) -> None:
    if not isinstance(dataset, dict):
        raise ValueError("requirement graph evaluation dataset root must be an object")
    if dataset.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"dataset_version must be {DATASET_VERSION}")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) < 20:
        raise ValueError("requirement graph evaluation dataset requires at least 20 cases")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case[{index}] must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case[{index}] missing required fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"case[{index}] case_id must be non-empty and unique")
        seen.add(case_id)
        if not isinstance(case["requirements"], list) or not isinstance(
            case["expected_graph"], dict
        ):
            raise ValueError(f"case {case_id} requirements/expected_graph are invalid")


def _leaf_edges(data: Any) -> Counter[tuple[str, str, str | None]]:
    edges: Counter[tuple[str, str, str | None]] = Counter()
    for group in data.get("groups", []):
        for child in group.get("children", []):
            if child.get("node_type") == "requirement_ref":
                edges[(group.get("group_type"), child.get("ref_id"), child.get("aspect"))] += 1
    return edges


def _leaf_refs(data: Any) -> Counter[tuple[str, str | None]]:
    refs: Counter[tuple[str, str | None]] = Counter()
    for edge, count in _leaf_edges(data).items():
        refs[(edge[1], edge[2])] += count
    return refs


def _child_canonical(
    child: Mapping[str, Any], groups_by_id: Mapping[str, Any]
) -> tuple[Any, ...]:
    if child.get("node_type") == "requirement_ref":
        return ("req", child.get("ref_id"), child.get("aspect"))
    nested = groups_by_id.get(child.get("ref_id"))
    if nested is None:
        return ("grp", ("missing",))
    return ("grp", _group_canonical(nested, groups_by_id))


def _group_canonical(
    group: Mapping[str, Any], groups_by_id: Mapping[str, Any]
) -> tuple[Any, ...]:
    children = tuple(
        sorted(_child_canonical(child, groups_by_id) for child in group.get("children", []))
    )
    return (
        group.get("group_type"),
        group.get("priority"),
        group.get("min_count"),
        children,
    )


def _group_signatures(data: Any) -> Counter[tuple[Any, ...]]:
    groups_by_id = {
        str(group.get("requirement_group_id")): group for group in data.get("groups", [])
    }
    return Counter(
        _group_canonical(group, groups_by_id) for group in data.get("groups", [])
    )


def _graph_edges(data: Any) -> Counter[tuple[Any, ...]]:
    """All parent-child edges including nested ``group_ref`` topology."""
    groups_by_id = {
        str(group.get("requirement_group_id")): group for group in data.get("groups", [])
    }
    edges: Counter[tuple[Any, ...]] = Counter()
    for group in data.get("groups", []):
        for child in group.get("children", []):
            if child.get("node_type") == "requirement_ref":
                edges[
                    (
                        group.get("group_type"),
                        "requirement_ref",
                        child.get("ref_id"),
                        child.get("aspect"),
                    )
                ] += 1
            else:
                nested = groups_by_id.get(str(child.get("ref_id")))
                nested_key = (
                    _group_canonical(nested, groups_by_id) if nested is not None else None
                )
                edges[(group.get("group_type"), "group_ref", nested_key)] += 1
    return edges


def _evidence_signatures(data: Any) -> Counter[tuple[str, str]]:
    return Counter(
        (
            group.get("evidence", {}).get("source_id"),
            group.get("evidence", {}).get("quote"),
        )
        for group in data.get("groups", [])
    )


def _overlap(expected: Counter, actual: Counter) -> tuple[int, int]:
    return sum(min(expected[key], actual[key]) for key in expected), sum(expected.values())


def _metrics(expected: dict[str, Any], predicted: dict[str, Any]) -> dict[str, Any]:
    expected_edges = _graph_edges(expected)
    actual_edges = _graph_edges(predicted)
    expected_refs = _leaf_refs(expected)
    actual_refs = _leaf_refs(predicted)
    expected_groups = _group_signatures(expected)
    actual_groups = _group_signatures(predicted)
    expected_evidence = _evidence_signatures(expected)
    actual_evidence = _evidence_signatures(predicted)
    atomic_correct, atomic_total = _overlap(expected_refs, actual_refs)
    relation_correct, relation_total = _overlap(expected_edges, actual_edges)
    group_correct, group_total = _overlap(expected_groups, actual_groups)
    evidence_correct, evidence_total = _overlap(expected_evidence, actual_evidence)
    exact = (
        expected_refs == actual_refs
        and expected_edges == actual_edges
        and expected_groups == actual_groups
        and expected_evidence == actual_evidence
        and len(expected.get("groups", [])) == len(predicted.get("groups", []))
    )
    return {
        "atomic_requirement_accuracy": round(atomic_correct / atomic_total, 6)
        if atomic_total
        else None,
        "relation_accuracy": round(relation_correct / relation_total, 6)
        if relation_total
        else None,
        "group_type_accuracy": round(group_correct / group_total, 6)
        if group_total
        else None,
        "group_evidence_reference_overlap": round(evidence_correct / evidence_total, 6)
        if evidence_total
        else None,
        "graph_exact_match": exact,
    }


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    validate_dataset(dataset)
    case_outputs: list[dict[str, Any]] = []
    aggregate_sums: dict[str, float] = {
        "atomic_requirement_accuracy": 0.0,
        "relation_accuracy": 0.0,
        "group_type_accuracy": 0.0,
        "group_evidence_reference_overlap": 0.0,
    }
    aggregate_counts: dict[str, int] = {
        "atomic_requirement_accuracy": 0,
        "relation_accuracy": 0,
        "group_type_accuracy": 0,
        "group_evidence_reference_overlap": 0,
    }
    exact_match_count = 0
    for case in dataset["cases"]:
        payload = {
            "document_id": "jd-1",
            "requirements": case["requirements"],
            "company_facts": [],
            "employment_facts": [],
            "responsibilities": [],
        }
        result = JDExtractionResult.model_validate(payload)
        graph = build_requirement_graph(result, case["source_blocks"])
        metrics = _metrics(case["expected_graph"], graph.model_dump(mode="json"))
        for key in (
            "atomic_requirement_accuracy",
            "relation_accuracy",
            "group_type_accuracy",
            "group_evidence_reference_overlap",
        ):
            value = metrics[key]
            if value is not None:
                aggregate_sums[key] += float(value)
                aggregate_counts[key] += 1
        exact_match_count += int(metrics["graph_exact_match"])
        failures = []
        if graph.status != "complete":
            failures.append(f"graph status is {graph.status}")
        if not metrics["graph_exact_match"]:
            failures.append("predicted graph differs from expected graph")
        case_outputs.append(
            {
                "case_id": case["case_id"],
                "status": graph.status,
                "metrics": metrics,
                "failures": failures,
            }
        )
    aggregate = {
        key: (
            round(aggregate_sums[key] / aggregate_counts[key], 6)
            if aggregate_counts[key]
            else None
        )
        for key in aggregate_sums
    }
    aggregate["graph_exact_match_count"] = exact_match_count
    return {
        "report_version": REPORT_VERSION,
        "dataset_version": dataset["dataset_version"],
        "provenance": dataset["provenance"],
        "case_count": len(case_outputs),
        "metrics": aggregate,
        "cases": case_outputs,
        "limitations": [
            "Evaluation uses the existing flat facts as input; it does not call a remote model.",
            "Graph group IDs are internal and excluded from exact-match comparison.",
            "The deterministic builder supports must/should/and/or/one_of/min_count; "
            "DEPENDENCY is not implemented in this batch.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# JD Requirement Graph 专项 Evaluation",
        "",
        f"- 报告版本：`{report['report_version']}`",
        f"- 数据集版本：`{report['dataset_version']}`",
        f"- 数据来源：`{report['provenance']}`",
        f"- 样本数：`{report['case_count']}`",
        "",
        "## Metrics",
        "",
        f"- Atomic Requirement Accuracy：`{metrics['atomic_requirement_accuracy']}`",
        f"- Relation Accuracy：`{metrics['relation_accuracy']}`",
        f"- Group Type Accuracy：`{metrics['group_type_accuracy']}`",
        f"- Group Evidence Reference Overlap：`{metrics['group_evidence_reference_overlap']}`",
        f"- Graph Exact Match：`{metrics['graph_exact_match_count']}/{report['case_count']}`",
        "",
        "## Cases",
        "",
        "| case_id | status | atomic | relation | group_type | evidence | exact |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        value = case["metrics"]
        lines.append(
            f"| {case['case_id']} | {case['status']} | {value['atomic_requirement_accuracy']} | "
            f"{value['relation_accuracy']} | {value['group_type_accuracy']} | "
            f"{value['group_evidence_reference_overlap']} | {value['graph_exact_match']} |"
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the JD Requirement Graph competition evaluation."
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    service_root = Path(__file__).resolve().parents[1]
    dataset_path = service_root / "evaluation" / f"{DATASET_VERSION}.json"
    report = evaluate_dataset(load_dataset(dataset_path))
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
