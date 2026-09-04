from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from src import real_requirement_graph_evaluation as real_eval
from src import requirement_graph_evaluation as graph_eval


def _graph_case(index: int) -> dict:
    return {
        "case_id": f"case-{index}",
        "jd_text": "Python engineer",
        "source_blocks": [],
        "requirements": [],
        "expected_graph": {"status": "complete", "groups": []},
    }


def _dataset() -> dict:
    return {
        "dataset_version": graph_eval.DATASET_VERSION,
        "provenance": "unit-test",
        "cases": [_graph_case(index) for index in range(20)],
    }


def _manifest(*, frozen: bool = False) -> dict:
    cases = [_graph_case(index) for index in range(real_eval.MIN_REAL_SAMPLE)]
    manifest = {"cases": cases}
    if frozen:
        manifest.update(
            {
                "real_frozen": True,
                "gold_frozen_at": "2026-08-31T00:00:00Z",
                "provenance": "unit-test",
                "coverage": ["backend"],
            }
        )
        for case in cases:
            case.update(
                {
                    "source_fact_id": f"fact-{case['case_id']}",
                    "source_version": "v1",
                    "source_identity": "source",
                    "gold_identity": "gold",
                    "baseline_prediction_frozen": None,
                    "baseline_prediction_identity": None,
                    "original_extraction_graph": None,
                    "reviewed_requirements": [],
                }
            )
    return manifest


def _group_graph() -> dict:
    return {
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "nested",
                "group_type": "and",
                "priority": "must",
                "min_count": None,
                "evidence": {"source_id": "s1", "quote": "Python"},
                "children": [
                    {
                        "node_type": "requirement_ref",
                        "ref_id": "r1",
                        "aspect": "skill",
                    }
                ],
            },
            {
                "requirement_group_id": "root",
                "group_type": "or",
                "priority": "should",
                "min_count": 1,
                "evidence": {"source_id": "s2", "quote": "Go or Rust"},
                "children": [
                    {"node_type": "group_ref", "ref_id": "nested"},
                    {"node_type": "group_ref", "ref_id": "missing"},
                ],
            },
        ],
    }


def test_load_dataset_reports_file_and_json_errors(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="not found"):
        graph_eval.load_dataset(missing)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        graph_eval.load_dataset(invalid)

    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(_dataset()), encoding="utf-8")
    assert graph_eval.load_dataset(valid)["provenance"] == "unit-test"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "root must be an object"),
        ({"dataset_version": "wrong", "cases": []}, "dataset_version"),
        (
            {"dataset_version": graph_eval.DATASET_VERSION, "cases": {}},
            "at least 20 cases",
        ),
    ],
)
def test_validate_dataset_rejects_invalid_roots(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        graph_eval.validate_dataset(payload)


def test_validate_dataset_rejects_invalid_cases() -> None:
    dataset = _dataset()
    dataset["cases"][0] = None
    with pytest.raises(ValueError, match=r"case\[0\] must be an object"):
        graph_eval.validate_dataset(dataset)

    dataset = _dataset()
    dataset["cases"][0].pop("jd_text")
    with pytest.raises(ValueError, match="missing required fields"):
        graph_eval.validate_dataset(dataset)

    for case_id in (" ", "case-1"):
        dataset = _dataset()
        dataset["cases"][0]["case_id"] = case_id
        with pytest.raises(ValueError, match="non-empty and unique"):
            graph_eval.validate_dataset(dataset)

    for field, value in (("requirements", {}), ("expected_graph", [])):
        dataset = _dataset()
        dataset["cases"][0][field] = value
        with pytest.raises(ValueError, match="requirements/expected_graph are invalid"):
            graph_eval.validate_dataset(dataset)

    graph_eval.validate_dataset(_dataset())


def test_graph_helpers_cover_leaf_nested_missing_and_empty_branches() -> None:
    graph = _group_graph()
    assert graph_eval._leaf_edges(graph)[("and", "r1", "skill")] == 1
    assert graph_eval._leaf_refs(graph)[("r1", "skill")] == 1
    assert len(graph_eval._group_signatures(graph)) == 2
    edges = graph_eval._graph_edges(graph)
    assert sum(edges.values()) == 3
    assert any(edge[1:] == ("group_ref", None) for edge in edges)
    assert graph_eval._evidence_signatures(graph)[("s1", "Python")] == 1
    assert graph_eval._overlap(Counter({"a": 2}), Counter({"a": 1})) == (1, 2)

    empty_metrics = graph_eval._metrics({"groups": []}, {"groups": []})
    assert empty_metrics["atomic_requirement_accuracy"] is None
    assert empty_metrics["relation_accuracy"] is None
    assert empty_metrics["group_type_accuracy"] is None
    assert empty_metrics["group_evidence_reference_overlap"] is None
    assert empty_metrics["graph_exact_match"] is True

    exact_metrics = graph_eval._metrics(graph, deepcopy(graph))
    assert exact_metrics["atomic_requirement_accuracy"] == 1.0
    assert exact_metrics["relation_accuracy"] == 1.0
    assert exact_metrics["group_type_accuracy"] == 1.0
    assert exact_metrics["group_evidence_reference_overlap"] == 1.0
    assert exact_metrics["graph_exact_match"] is True


def test_render_markdown_covers_case_and_limitation_rows() -> None:
    report = {
        "report_version": "r1",
        "dataset_version": "d1",
        "provenance": "unit-test",
        "case_count": 1,
        "metrics": {
            "atomic_requirement_accuracy": 1.0,
            "relation_accuracy": 1.0,
            "group_type_accuracy": 1.0,
            "group_evidence_reference_overlap": 1.0,
            "graph_exact_match_count": 1,
        },
        "cases": [
            {
                "case_id": "case-1",
                "status": "complete",
                "metrics": {
                    "atomic_requirement_accuracy": 1.0,
                    "relation_accuracy": 1.0,
                    "group_type_accuracy": 1.0,
                    "group_evidence_reference_overlap": 1.0,
                    "graph_exact_match": True,
                },
            }
        ],
        "limitations": ["synthetic"],
    }
    markdown = graph_eval.render_markdown(report)
    assert "| case-1 | complete |" in markdown
    assert "- synthetic" in markdown
    assert markdown.endswith("\n")


def test_load_real_manifest_reports_file_and_json_errors(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="not found"):
        real_eval.load_real_manifest(missing)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        real_eval.load_real_manifest(invalid)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "root must be an object"),
        ({}, "requires a cases list"),
        ({"cases": []}, "requires 20-500 cases"),
        ({"cases": [_graph_case(index) for index in range(20)], "real_frozen": "yes"}, "must be a boolean"),
    ],
)
def test_validate_real_manifest_rejects_invalid_roots(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        real_eval.validate_real_manifest(payload)


def test_validate_real_manifest_requires_frozen_metadata() -> None:
    manifest = _manifest()
    manifest["real_frozen"] = True
    with pytest.raises(ValueError, match="gold_frozen_at"):
        real_eval.validate_real_manifest(manifest)

    manifest["gold_frozen_at"] = "now"
    with pytest.raises(ValueError, match="provenance"):
        real_eval.validate_real_manifest(manifest)

    manifest["provenance"] = "unit-test"
    with pytest.raises(ValueError, match="coverage"):
        real_eval.validate_real_manifest(manifest)


def test_validate_real_manifest_rejects_invalid_case_shapes() -> None:
    manifest = _manifest()
    manifest["cases"][0] = None
    with pytest.raises(ValueError, match=r"case\[0\] must be an object"):
        real_eval.validate_real_manifest(manifest)

    manifest = _manifest()
    manifest["cases"][0].pop("jd_text")
    with pytest.raises(ValueError, match="missing required fields"):
        real_eval.validate_real_manifest(manifest)

    for field, value, message in (
        ("case_id", " ", "non-empty and unique"),
        ("case_id", "case-1", "non-empty and unique"),
        ("source_blocks", {}, "source_blocks must be a list"),
        ("requirements", {}, "requirements must be a list"),
        ("expected_graph", [], "expected_graph must be an object"),
        ("jd_text", " ", "jd_text must be non-empty"),
    ):
        manifest = _manifest()
        manifest["cases"][0][field] = value
        with pytest.raises(ValueError, match=message):
            real_eval.validate_real_manifest(manifest)

    real_eval.validate_real_manifest(_manifest())


def test_validate_real_manifest_checks_frozen_case_fields_and_values() -> None:
    manifest = _manifest(frozen=True)
    manifest["cases"][0].pop("source_fact_id")
    with pytest.raises(ValueError, match="missing real-frozen fields"):
        real_eval.validate_real_manifest(manifest)

    manifest = _manifest(frozen=True)
    manifest["cases"][0]["source_fact_id"] = " "
    with pytest.raises(ValueError, match="source_fact_id must be non-empty"):
        real_eval.validate_real_manifest(manifest)

    manifest = _manifest(frozen=True)
    manifest["cases"][0].pop("reviewed_requirements")
    with pytest.raises(ValueError, match="missing frozen field"):
        real_eval.validate_real_manifest(manifest)

    real_eval.validate_real_manifest(_manifest(frozen=True))


def test_validate_frozen_case_detects_mutation_hash_and_graph_mismatch() -> None:
    case = _manifest(frozen=True)["cases"][0]
    case["baseline_prediction_frozen"] = [{"requirement_id": "different"}]
    case["baseline_prediction_identity"] = "wrong"
    case["original_extraction_graph"] = {}
    errors = real_eval._validate_frozen_case(case)
    assert any("baseline prediction was mutated" in error for error in errors)
    assert any("baseline identity hash mismatch" in error for error in errors)
    assert any("original graph does not match" in error for error in errors)


def test_span_f1_rejection_and_aggregate_helpers_cover_zero_and_value_paths() -> None:
    requirement = {
        "requirement_id": "r1",
        "evidence": {"source_id": "s1", "quote": "Python", "start": 0, "end": 6},
    }
    mismatch = {"requirement_id": "r2", "evidence": {}}
    exact = real_eval._requirement_evidence_span_metrics([requirement, mismatch], [requirement])
    assert exact == {
        "prediction_count": 2,
        "gold_count": 1,
        "exact_span_count": 1,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 0.666667,
    }
    empty = real_eval._requirement_evidence_span_metrics([], [])
    assert empty["f1"] is None
    assert real_eval._aggregate_span_metrics([])["precision"] is None
    aggregate_span = real_eval._aggregate_span_metrics(
        [{"requirement_evidence_span": exact}, {"requirement_evidence_span": empty}]
    )
    assert aggregate_span["exact_span_count"] == 1
    assert aggregate_span["f1"] == 0.666667

    assert real_eval._rejection_accuracy(0, 0) is None
    assert real_eval._rejection_accuracy(2, 4) == 0.5
    assert real_eval._f1(Counter(), Counter()) is None
    assert real_eval._f1(Counter({"a": 2}), Counter({"a": 1, "b": 1})) == 0.5
    assert real_eval._fmt(None) == "N/A"
    assert real_eval._fmt(0.5) == "0.5"

    aggregate = real_eval._aggregate(
        [
            {
                "atomic_requirement_accuracy": 1.0,
                "relation_accuracy": None,
                "group_type_accuracy": 0.5,
                "group_evidence_reference_overlap": 1.0,
                "atomic_f1": 1.0,
                "relation_f1": None,
                "group_type_f1": 0.5,
                "graph_exact_match": True,
            }
        ],
        "original",
    )
    assert aggregate["atomic_requirement_accuracy"] == 1.0
    assert aggregate["relation_accuracy"] is None
    assert aggregate["original_graph_exact_match_count"] == 1


def test_f1_metrics_and_failures_cover_complete_and_incomplete_graphs() -> None:
    expected = _group_graph()
    assert real_eval._f1_metrics(expected, deepcopy(expected)) == {
        "atomic_f1": 1.0,
        "relation_f1": 1.0,
        "group_type_f1": 1.0,
    }

    incomplete = {"status": "incomplete", "groups": []}
    challenger = SimpleNamespace(
        status="incomplete",
        model_dump=lambda **_kwargs: incomplete,
    )
    failures = real_eval._failures(
        {"expected_graph": incomplete}, incomplete, challenger
    )
    assert "gold graph is not complete" in failures
    assert "original graph status is incomplete" in failures
    assert "challenger graph status is incomplete" in failures


def test_render_real_report_covers_case_and_limitation_rows() -> None:
    metric_values = {
        "atomic_f1": None,
        "relation_f1": 1.0,
        "group_type_f1": 1.0,
        "group_evidence_reference_overlap": 1.0,
        "original_graph_exact_match_count": 0,
    }
    report = {
        "report_version": "r1",
        "dataset_version": "d1",
        "gold_frozen_at": "now",
        "gold_type": "reviewed",
        "gold_version": "v1",
        "case_count": 1,
        "metrics": metric_values,
        "challenger_metrics": {
            **metric_values,
            "challenger_graph_exact_match_count": 1,
        },
        "requirement_evidence_span": {
            "exact_span_count": 0,
            "prediction_count": 0,
            "gold_count": 0,
            "precision": None,
            "recall": None,
            "f1": None,
        },
        "publication_rejection_accuracy": None,
        "challenger_publication_rejection_accuracy": 1.0,
        "reject_case_count": 0,
        "publication_summary": {
            "original_false_reject_cases": 0,
            "challenger_false_reject_cases": 0,
        },
        "structural_metrics_note": "diagnostic only",
        "cases": [
            {
                "case_id": "case-1",
                "original_graph_status": "incomplete",
                "challenger_graph_status": "complete",
                "predicted_reject": True,
                "metrics": {
                    "atomic_f1": None,
                    "relation_f1": 1.0,
                    "group_type_f1": 1.0,
                    "graph_exact_match": False,
                },
            }
        ],
        "limitations": ["synthetic"],
    }
    markdown = real_eval.render_real_report(report)
    assert "| case-1 | incomplete | complete |" in markdown
    assert "`N/A`" in markdown
    assert "- synthetic" in markdown
