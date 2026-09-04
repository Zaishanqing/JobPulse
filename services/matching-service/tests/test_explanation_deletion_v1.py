from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from app.application.evaluation import MatchEvaluationService
from app.application.explanation_deletion import ExplanationDeletionService
from app.application.learning_paths import LearningPathService
from app.main import app

client = TestClient(app)
client.headers.update({"Authorization": "Bearer test-token"})


def _service() -> tuple[MatchEvaluationService, ExplanationDeletionService]:
    evaluation = MatchEvaluationService()
    learning = LearningPathService(evaluation)
    return evaluation, ExplanationDeletionService(evaluation, learning)


def _baseline(service, cv: dict, position: dict):
    return service.evaluate(
        {"cv_profile": cv, "position_profile": position}, include_semantic=False
    )


def _payload(baseline, cv: dict, position: dict, kind: str, source_ids: list[str]):
    return {
        "baseline_evaluation": baseline.model_dump(mode="python"),
        "cv_profile": cv,
        "position_profile": position,
        "deletion_kind": kind,
        "evidence_source_ids": source_ids,
    }


def test_critical_evidence_deletion_recomputes_score_gap_and_actions(
    ready_cv_json, ready_position_json
):
    evaluation, deletion = _service()
    baseline = _baseline(evaluation, ready_cv_json, ready_position_json)
    required = next(
        item
        for item in baseline.skill_results
        if item.importance_level == "required" and item.candidate_evidence
    )
    source_id = required.candidate_evidence[0].source_id
    original = deepcopy(ready_cv_json)

    result = deletion.evaluate(
        _payload(
            baseline,
            ready_cv_json,
            ready_position_json,
            "critical",
            [source_id],
        )
    )

    assert result.generation_status == "completed"
    assert result.faithfulness_status == "faithful"
    assert result.baseline_evaluation_id == baseline.evaluation_id
    assert result.deleted_evidence_source_ids == (source_id,)
    assert source_id in result.critical_evidence_source_ids
    assert result.ablated_score < result.baseline_score
    assert result.comprehensiveness > 0
    assert result.added_gap_ids or result.added_action_ids
    assert result.scoring_algorithm_version == "explainable-scoring.v4"
    assert ready_cv_json == original

    replay = deletion.evaluate(
        _payload(
            baseline,
            ready_cv_json,
            ready_position_json,
            "critical",
            [source_id],
        )
    )
    assert replay.deletion_run_id == result.deletion_run_id


def test_noncritical_evidence_deletion_is_stable(ready_cv_json, ready_position_json):
    cv = deepcopy(ready_cv_json)
    unused = {
        "source_id": "cv:unused:negative-control",
        "quote": "unused negative control",
        "start": 0,
        "end": len("unused negative control"),
        "alignment": "exact",
    }
    cv["evidence_refs"].append(unused)
    evaluation, deletion = _service()
    baseline = _baseline(evaluation, cv, ready_position_json)

    result = deletion.evaluate(
        _payload(
            baseline,
            cv,
            ready_position_json,
            "noncritical",
            [unused["source_id"]],
        )
    )

    assert result.generation_status == "completed"
    assert result.faithfulness_status == "faithful"
    assert result.score_delta == 0
    assert result.hard_gate_delta is None
    assert result.added_gap_ids == ()
    assert result.removed_gap_ids == ()
    assert result.added_action_ids == ()
    assert result.removed_action_ids == ()


def test_deletion_kind_must_match_registered_classification(
    ready_cv_json, ready_position_json
):
    cv = deepcopy(ready_cv_json)
    cv["evidence_refs"].append(
        {
            "source_id": "cv:unused:mismatch",
            "quote": "unused mismatch control",
            "start": 0,
            "end": len("unused mismatch control"),
            "alignment": "exact",
        }
    )
    evaluation, deletion = _service()
    baseline = _baseline(evaluation, cv, ready_position_json)

    result = deletion.evaluate(
        _payload(
            baseline,
            cv,
            ready_position_json,
            "critical",
            ["cv:unused:mismatch"],
        )
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "EVIDENCE_DELETION_CLASSIFICATION_MISMATCH"


def test_explanation_deletion_api_exposes_the_formal_recompute(
    ready_cv_json, ready_position_json
):
    evaluation, _ = _service()
    baseline = _baseline(evaluation, ready_cv_json, ready_position_json)
    source_id = next(
        item.candidate_evidence[0].source_id
        for item in baseline.skill_results
        if item.importance_level == "required" and item.candidate_evidence
    )

    response = client.post(
        "/api/v1/explanation-deletions",
        json=_payload(
            baseline,
            ready_cv_json,
            ready_position_json,
            "critical",
            [source_id],
        ),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generation_status"] == "completed"
    assert data["deleted_evidence_source_ids"] == [source_id]
    assert data["baseline_evaluation_id"] == baseline.evaluation_id
