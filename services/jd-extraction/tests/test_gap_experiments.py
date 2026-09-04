from __future__ import annotations

from src.gap_experiments import (
    build_blinded_annotation_pack,
    build_integrity_rejection_benchmark,
    build_stratified_coverage_report,
    evaluate_independent_annotations,
    evaluate_predictions_against_adjudicated_gold,
    title_family,
)


def _case(case_id: str, *, title: str = "Java 后端", length: int = 600) -> dict:
    text = "A" * length + "Python"
    start = len(text) - len("Python")
    requirement = {
        "requirement_id": "req-1",
        "kind": "skill",
        "modality": "required",
        "evidence": {
            "source_id": "src-1",
            "quote": "Python",
            "start": start,
            "end": len(text),
        },
    }
    return {
        "case_id": case_id,
        "source_version": "v1",
        "source_identity": {"source_ref": "output/runs_position_v3/jd_chunk_01"},
        "job_title": title,
        "jd_text": text,
        "source_blocks": [
            {"source_id": "src-1", "text": "Python", "start": start, "end": len(text)}
        ],
        "requirements": [requirement],
        "reviewed_requirements": [requirement],
    }


def test_stratified_report_marks_small_strata_insufficient() -> None:
    manifest = {"dataset_version": "fixture", "cases": [_case("a"), _case("b")]}
    span = {
        "aggregate": {},
        "cases": [
            {
                "case_id": case_id,
                "metrics": {
                    "exact_span_count": 1,
                    "relaxed_span_count": 1,
                    "prediction_count": 1,
                    "gold_count": 1,
                    "unresolved_count": 0,
                    "hallucinated_count": 0,
                },
            }
            for case_id in ("a", "b")
        ],
    }
    report = build_stratified_coverage_report(manifest, span)
    assert report["case_count"] == 2
    assert report["gate_passed"] is False
    backend = next(
        row
        for row in report["dimensions"]["title_proxy_family"]
        if row["stratum"] == "backend"
    )
    assert backend["exact_span_f1_micro"] == 1.0
    assert backend["sample_sufficient_ge_30_jds"] is False
    assert backend["metric_gate_passed"] is None


def test_stratified_report_includes_zero_sample_expected_families_and_plan() -> None:
    manifest = {"dataset_version": "fixture", "cases": [_case("a")]}
    span = {
        "aggregate": {},
        "cases": [
            {
                "case_id": "a",
                "metrics": {
                    "exact_span_count": 1,
                    "relaxed_span_count": 1,
                    "prediction_count": 1,
                    "gold_count": 1,
                    "unresolved_count": 0,
                    "hallucinated_count": 0,
                },
            }
        ],
    }
    report = build_stratified_coverage_report(manifest, span)
    design = next(
        row
        for row in report["dimensions"]["title_proxy_family"]
        if row["stratum"] == "design"
    )
    assert design["jd_count"] == 0
    assert design["exact_span_f1_micro"] is None
    assert design["additional_jds_needed"] == 30
    assert any(
        item["stratum"] == "design" and item["priority"] == "P0"
        for item in report["sampling_plan"]
    )
    assert report["challenge_coverage_gate_passed"] is False
    assert any(
        item["dimension"] == "coverage_mark::publication_rejection_candidate"
        and item["additional_jds_needed"] == 20
        for item in report["challenge_coverage_plan"]
    )


def test_stratified_report_exposes_micro_macro_and_error_concentration() -> None:
    manifest = {
        "dataset_version": "fixture",
        "cases": [_case("a"), _case("b")],
    }
    span = {
        "aggregate": {},
        "cases": [
            {
                "case_id": "a",
                "metrics": {
                    "exact_span_count": 1,
                    "relaxed_span_count": 1,
                    "prediction_count": 1,
                    "gold_count": 1,
                    "unresolved_count": 0,
                    "hallucinated_count": 0,
                },
            },
            {
                "case_id": "b",
                "metrics": {
                    "exact_span_count": 0,
                    "relaxed_span_count": 1,
                    "prediction_count": 3,
                    "gold_count": 3,
                    "unresolved_count": 1,
                    "hallucinated_count": 0,
                },
            },
        ],
    }
    report = build_stratified_coverage_report(manifest, span)
    assert report["overall"]["exact_span_f1_micro"] == 0.25
    assert report["overall"]["exact_span_f1_jd_macro"] == 0.5
    assert report["error_analysis"]["error_case_count"] == 1
    assert report["error_analysis"]["top_10_error_concentration"] == 1.0


def test_blinded_pack_omits_predictions_and_gold() -> None:
    manifest = {"cases": [_case("a"), _case("b", title="算法工程师")]}
    pack = build_blinded_annotation_pack(manifest, target=2)
    assert {item["case_id"] for item in pack} == {"a", "b"}
    assert all("requirements" not in item for item in pack)
    assert all(item["annotation"]["publication_decision"] is None for item in pack)


def test_integrity_rejection_benchmark_detects_all_mutations() -> None:
    manifest = {"dataset_version": "fixture", "cases": [_case(str(i)) for i in range(5)]}
    report = build_integrity_rejection_benchmark(manifest, valid_count=5)
    assert report["case_count"] == 10
    assert report["confusion"] == {"tp": 5, "tn": 5, "fp": 0, "fn": 0}
    assert report["accuracy"] == 1.0


def test_independent_annotation_agreement_stays_pending_for_blank_pack() -> None:
    pack = build_blinded_annotation_pack({"cases": [_case("a")]}, target=1)
    result = evaluate_independent_annotations(pack, pack)
    assert result["completed_by_both_count"] == 0
    assert result["status"] == "pending_human_annotation"
    assert result["requirement_exact_agreement"]["f1"] is None


def test_independent_annotation_agreement_computes_kappa_and_requirement_f1() -> None:
    left = [
        {
            "case_id": "a",
            "annotation": {
                "publication_decision": "publish",
                "requirements": [
                    {
                        "kind": "skill",
                        "modality": "required",
                        "evidence": {"start": 0, "end": 6, "quote": "Python"},
                    }
                ],
            },
        },
        {"case_id": "b", "annotation": {"publication_decision": "reject", "requirements": []}},
    ]
    right = [dict(item) for item in left]
    result = evaluate_independent_annotations(left, right)
    assert result["status"] == "complete"
    assert result["publication_decision_cohen_kappa"] == 1.0
    assert result["requirement_exact_agreement"]["f1"] == 1.0


def test_prediction_evaluation_requires_completed_adjudicated_gold() -> None:
    manifest = {"cases": [_case("a")]}
    pack = build_blinded_annotation_pack(manifest, target=1)
    result = evaluate_predictions_against_adjudicated_gold(manifest, pack)
    assert result["status"] == "pending_adjudicated_gold"
    assert result["requirement_exact_semantic_metrics"]["f1"] is None


def test_prediction_evaluation_compares_structured_requirement_values() -> None:
    manifest = {"cases": [_case("a")]}
    gold = [
        {
            "case_id": "a",
            "annotation": {
                "publication_decision": "publish",
                "requirements": _case("a")["requirements"],
            },
        }
    ]
    result = evaluate_predictions_against_adjudicated_gold(manifest, gold)
    assert result["status"] == "complete"
    assert result["requirement_exact_semantic_metrics"]["f1"] == 1.0
    assert result["publication_decision_accuracy"] == 1.0


def test_title_family_prioritizes_ai_before_backend_tokens() -> None:
    assert title_family("AI Java 后端工程师") == "ai_ml"


def test_title_family_uses_ascii_token_boundaries() -> None:
    assert title_family("Detail Engineer") == "other"
    assert title_family("Data Engineer") == "data"
    assert title_family("SRE / QA") == "quality_operations"
