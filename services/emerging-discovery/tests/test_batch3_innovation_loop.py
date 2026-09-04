from datetime import date

import pytest

from app.domain.germination import assess_germination
from app.domain.lineage import ClusterLineageSpec, match_cluster_lineage


def _spec(cluster_id, members, *, skills=("python",), centroid=(1.0, 0.0), window="w"):
    return ClusterLineageSpec(
        cluster_id,
        frozenset(members),
        tuple(centroid),
        frozenset(skills),
        window,
    )


def test_all_six_lineage_events_and_explainable_components():
    assert {item.relation_type for item in match_cluster_lineage([], [_spec("n", {"1"})])} == {"birth"}
    assert {item.relation_type for item in match_cluster_lineage([_spec("d", {"1"})], [])} == {"decline"}
    continued = match_cluster_lineage([_spec("p", {"1"})], [_spec("c", {"1"})])
    assert {item.relation_type for item in continued} == {"continue"}
    assert continued[0].score.member_overlap == 1.0
    assert continued[0].score.core_skill_overlap == 1.0
    assert continued[0].score.semantic_center_distance == 0.0

    split = match_cluster_lineage(
        [_spec("p", {"1", "2"})],
        [_spec("a", {"1"}), _spec("b", {"2"})],
    )
    assert {item.relation_type for item in split} == {"split"}

    merged = match_cluster_lineage(
        [_spec("a", {"1"}), _spec("b", {"2"})],
        [_spec("c", {"1", "2"})],
    )
    assert {item.relation_type for item in merged} == {"merge", "absorbed"}


def test_lineage_threshold_boundary_and_determinism():
    previous = [_spec("p", {"1"}, skills=(), centroid=(0.0, 1.0))]
    current = [_spec("c", {"1"}, skills=(), centroid=(1.0, 0.0))]
    first = match_cluster_lineage(previous, current, threshold=0.4)
    second = match_cluster_lineage(previous, current, threshold=0.4)
    assert first == second
    assert first[0].relation_type == "continue"
    assert first[0].similarity_score == 0.4
    assert {item.relation_type for item in match_cluster_lineage(previous, current, threshold=0.400001)} == {"birth", "decline"}


def test_lineage_uses_stable_occupation_identity_across_incompatible_vector_spaces():
    previous = [
        ClusterLineageSpec(
            "previous",
            frozenset({"old-fact"}),
            (1.0, 0.0),
            frozenset({"python"}),
            "w1",
            "rag应用",
        )
    ]
    current = [
        ClusterLineageSpec(
            "current",
            frozenset({"new-fact"}),
            (1.0, 0.0, 0.0),
            frozenset({"python"}),
            "w2",
            "rag应用",
        )
    ]

    relations = match_cluster_lineage(previous, current)

    assert [item.relation_type for item in relations] == ["continue"]
    assert relations[0].score.semantic_center_similarity == 0.0
    assert relations[0].similarity_score < relations[0].threshold
    assert relations[0].decision_reason == (
        "stable occupation identity matched across incompatible vector spaces"
    )


_DEFAULT_EVIDENCE_QUALITY = {
    "evidence_count_score": 1.0,
    "field_coverage": 1.0,
    "source_reliability": 1.0,
    "original_text_locatability": 1.0,
}


def _assessment(
    *,
    sample_count=6,
    effective_sample_count=6,
    enterprises=None,
    config=None,
    evidence_quality=_DEFAULT_EVIDENCE_QUALITY,
):
    windows = ["w1", "w1", "w2", "w2", "w3", "w3"]
    return assess_germination(
        sample_count=sample_count,
        effective_sample_count=effective_sample_count,
        sources=["a", "b", "c", "a", "b", "c"][:sample_count],
        enterprises=(enterprises or ["e1", "e2", "e3", "e1", "e2", "e3"])[:sample_count],
        spread_labels=["i"] * sample_count,
        publish_dates=[date(2026, 1, 1)] * sample_count,
        all_publish_dates=[date(2026, 1, 1)] * 6,
        window_ids=windows[:sample_count],
        all_window_ids=windows,
        candidate_skills={"python", "llm"},
        reference_skill_sets=[{"java"}],
        stability_score=0.8,
        evidence_quality=evidence_quality,
        config=config or {},
    )


def test_seven_dimension_score_weights_determinism_and_duplicate_suppression():
    first = _assessment()
    second = _assessment()
    assert first == second
    breakdown = first.evidence_summary["emergence_index"]["dimensions"]
    assert set(breakdown) == {
        "growth",
        "cross_window_persistence",
        "enterprise_coverage",
        "source_diversity",
        "standard_position_distance",
        "evidence_quality",
        "result_stability",
    }
    assert sum(item["weight"] for item in breakdown.values()) == pytest.approx(1.0)
    assert first.germination_score == pytest.approx(
        sum(item["contribution"] for item in breakdown.values())
    )
    spam = _assessment(
        sample_count=6,
        effective_sample_count=1,
        enterprises=["one-company"] * 6,
    )
    assert spam.germination_score < first.germination_score
    assert spam.qualified_as_emerging is False


def test_invalid_score_weight_config_is_rejected():
    with pytest.raises(ValueError, match="sum to one"):
        _assessment(config={"growth_weight": 0.5})


def test_missing_evidence_quality_blocks_formal_qualification():
    unknown = _assessment(evidence_quality=None)

    assert unknown.evidence_summary["evidence_quality"] == "unknown"
    assert (
        unknown.evidence_summary["emergence_index"]["dimensions"]
        ["evidence_quality"]["raw_value"]["status"]
        == "unknown"
    )
    assert unknown.qualified_as_emerging is False
