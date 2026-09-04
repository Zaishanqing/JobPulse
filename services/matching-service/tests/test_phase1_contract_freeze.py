"""Phase-1 contract freeze for matching evaluation and evidence references."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.evaluation import EvaluationStatus, FinalMatchResult, MatchEvaluation
from app.domain.profiles import Evidence, PositionMatchProfile
from app.domain.tasks import PersistedEvaluation, PersistenceVersions


def test_evaluation_status_and_required_fields_are_frozen():
    assert set(EvaluationStatus.__args__) == {"completed", "rejected"}
    fields = set(MatchEvaluation.model_fields)
    assert {
        "evaluation_id",
        "algorithm_version",
        "evaluation_status",
        "error_code",
        "error_message",
        "semantic_shadow_status",
        "semantic_status",
        "final_match_result",
    } <= fields


def test_manifest_success_status_matches_matching_evaluation_status():
    manifest_path = (
        Path(__file__).parents[3]
        / "config"
        / "competition-demo-v1"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["success_case"]["expected_status"] in set(
        EvaluationStatus.__args__
    )
    matching = next(
        item
        for item in manifest["expected_resources"]
        if item["resource_type"] == "matching_evaluation"
    )
    assert matching["expected_status"] == "completed"
    assert manifest["success_case"]["expected_status"] == "completed"


def test_match_evaluation_constructs_with_minimal_required_fields():
    evaluation = MatchEvaluation(
        evaluation_id="evaluation-demo-001",
        algorithm_version="matching-evaluation.v1",
        evaluation_status="completed",
    )
    assert evaluation.semantic_status == "disabled"
    assert evaluation.semantic_shadow_status == "disabled"
    assert evaluation.semantic_weight == 0.0


def test_match_evaluation_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="Extra inputs"):
        MatchEvaluation(
            evaluation_id="evaluation-demo-001",
            algorithm_version="matching-evaluation.v1",
            evaluation_status="completed",
            unexpected=True,
        )


def test_final_match_result_carries_graph_version_and_semantic_lineage():
    fields = set(FinalMatchResult.model_fields)
    assert {
        "position_graph_version",
        "position_quality_snapshot_id",
        "cv_profile_id",
        "position_profile_id",
        "embedding_model",
        "embedding_version",
        "semantic_algorithm_version",
        "semantic_index_revision",
        "semantic_collection",
    } <= fields


def test_position_match_profile_carries_graph_version():
    fields = set(PositionMatchProfile.model_fields)
    assert {
        "graph_version",
        "graph_mode",
        "taxonomy_version",
        "trend_context",
        "profile_version",
    } <= fields


def test_persisted_evaluation_and_versions_carry_graph_version_and_tenant():
    fields = set(PersistedEvaluation.model_fields)
    assert {"evaluation_id", "task_id", "idempotency_key", "versions"} <= fields
    version_fields = set(PersistenceVersions.model_fields)
    assert {
        "position_graph_version",
        "cv_source_version",
        "position_source_version",
        "target_type",
        "semantic_index_revision",
        "position_requirement_graph_version",
    } <= version_fields


def test_semantic_shadow_status_invariants_are_frozen():
    base = {
        "evaluation_id": "evaluation-demo-001",
        "algorithm_version": "matching-evaluation.v1",
        "evaluation_status": "completed",
    }
    with pytest.raises(ValidationError, match="available semantic shadow"):
        MatchEvaluation(
            **base,
            semantic_shadow_status="available",
            semantic_error_code="EMBEDDING_TIMEOUT",
        )
    with pytest.raises(ValidationError, match="disabled semantic shadow"):
        MatchEvaluation(
            **base,
            semantic_shadow_status="disabled",
            semantic_shadow_score=0.8,
        )
    with pytest.raises(ValidationError, match="unavailable semantic shadow"):
        MatchEvaluation(
            **base,
            semantic_shadow_status="unavailable",
            semantic_shadow_score=0.8,
        )


def test_evidence_reference_requires_identity_and_valid_span():
    with pytest.raises(ValidationError, match="Extra inputs"):
        Evidence(source_id="evidence-1", quote="Python", unexpected=True)
    with pytest.raises(ValidationError, match="supplied together"):
        Evidence(source_id="evidence-1", quote="Python", start=0)
    with pytest.raises(ValidationError, match="must not precede"):
        Evidence(source_id="evidence-1", quote="Python", start=10, end=2)
    evidence = Evidence(source_id="evidence-1", quote="Python", start=0, end=6)
    assert evidence.alignment == "unresolved"
