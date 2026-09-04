import pytest

from app.domain.policies import (
    RELATION_ALGORITHM_CONFIG,
    EvidenceAligner,
    ModalitySelectionPolicy,
    QualityScoringPolicy,
    RelationScoringPolicy,
    VersionDiffPolicy,
    duplicate_cluster_key,
    normalize_key,
)
from app.domain.publishing import PublishGateFacts, RelationGateFact, evaluate_publish_gate


def test_evidence_aligner_is_pure_and_validates_occurrence():
    aligner = EvidenceAligner()
    assert normalize_key(" Ｐｙｔｈｏｎ  ") == "python"
    alignment = aligner.align_quote("xx Python yy Python", "Python", 1)
    assert (
        alignment.start,
        alignment.end,
        alignment.alignment,
        alignment.occurrence_index,
    ) == (13, 19, "exact", 1)
    assert aligner.align_quote("text", "missing")["alignment"] == "unresolved"
    with pytest.raises(ValueError):
        aligner.align_quote("Python", "Python", -1)
    payload = {
        "items": [
            {"evidence": {"quote": "Python", "alignment": "unresolved"}}
        ]
    }
    result = aligner.align("use Python", payload)
    assert result["items"][0]["evidence"]["start"] == 4
    assert payload["items"][0]["evidence"].get("start") is None


def test_quality_and_modality_policies_are_deterministic():
    policy = QualityScoringPolicy()
    score = policy.score("Python SQL Docker React PyTorch 熟悉 精通 技能 技能", [
        "Python SQL Docker React PyTorch 熟悉 精通 技能 技能"
    ])
    assert score["duplicate_score"] == 1
    assert score["copy_risk_score"] == 1
    assert score.normalization_version == "normalization-v1"
    assert policy.effective_weight(1, 1, 1, 1) == pytest.approx(0.12)
    modality = ModalitySelectionPolicy()
    assert modality.select(["bonus", "required", "preferred"]) == "required"
    assert modality.select([]) == "unknown"


def test_relation_publish_and_version_policies():
    metrics = {
        "source_diversity": 3,
        "enterprise_coverage": 3,
        "support_document_count": 3,
        "weighted_frequency": 0.8,
        "required_ratio": 1,
        "preferred_ratio": 0,
        "bonus_ratio": 0,
        "unknown_ratio": 0,
        "freshness_score": 1,
        "trusted_evidence_ratio": 1,
    }
    score = RelationScoringPolicy(RELATION_ALGORITHM_CONFIG).score(metrics)
    assert 0 <= score["auto_weight"] <= 1
    assert 0 <= score["auto_confidence"] <= 1
    gate = evaluate_publish_gate(
        PublishGateFacts(
            build_status="approved",
            valid_sample_count=0,
            minimum_valid_samples=1,
            position_active=True,
            supports=(),
            relations=(RelationGateFact(1, "approved", 1.0, 0.0),),
            review_tasks=(),
            unresolved_count=0,
            non_exact_evidence_count=0,
            requirement_aggregate_count=0,
            task_aggregate_count=0,
        )
    )
    assert [(error.rule, error.message) for error in gate.errors] == [
        ("minimum_valid_samples", "not enough valid samples")
    ]
    diff = VersionDiffPolicy.diff({"skills": [1], "tasks": []}, {"skills": [2]})
    assert diff["changed_sections"] == ["skills", "tasks"]


def test_publish_gate_rejects_invalid_profile_importance_and_modality():
    gate = evaluate_publish_gate(
        PublishGateFacts(
            build_status="approved",
            valid_sample_count=1,
            minimum_valid_samples=1,
            position_active=True,
            supports=(),
            relations=(
                RelationGateFact(
                    1,
                    "approved",
                    1.0,
                    0.0,
                    invalid_importance_level=True,
                ),
                RelationGateFact(
                    2,
                    "approved",
                    1.0,
                    0.0,
                    invalid_modality=True,
                ),
            ),
            review_tasks=(),
            unresolved_count=0,
            non_exact_evidence_count=0,
            requirement_aggregate_count=0,
            task_aggregate_count=0,
        )
    )
    assert {error.rule for error in gate.errors} == {
        "profile_importance_invalid",
        "profile_modality_invalid",
    }


def test_publish_gate_rejects_approved_unknown_modality():
    gate = evaluate_publish_gate(
        PublishGateFacts(
            build_status="approved",
            valid_sample_count=1,
            minimum_valid_samples=1,
            position_active=True,
            supports=(),
            relations=(RelationGateFact(1, "approved", 1.0, 0.1),),
            review_tasks=(),
            unresolved_count=0,
            non_exact_evidence_count=0,
            requirement_aggregate_count=0,
            task_aggregate_count=0,
        )
    )
    assert {error.rule for error in gate.errors} == {"unknown_modality"}
    assert not gate.allowed


def test_duplicate_cluster_key_encoding_is_unambiguous():
    first = duplicate_cluster_key("a|b", "c")
    second = duplicate_cluster_key("a", "b|c")
    assert first != second
    assert len(first) <= 80
    assert len(second) <= 80
