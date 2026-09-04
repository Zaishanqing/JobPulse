from datetime import date

from app.domain.germination import assess_germination


def test_germination_formula_uses_observed_evidence_and_bounds():
    result = assess_germination(
        sample_count=3,
        effective_sample_count=3,
        sources=["a", "a", "a"],
        spread_labels=["AI"],
        publish_dates=[date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
        all_publish_dates=[date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
        candidate_skills={"RAG", "Python"},
        reference_skill_sets=[{"Java"}],
        stability_score=0.85,
        config={},
    )
    assert 0 <= result.germination_score <= 1
    assert result.dimensions.single_platform_noise_penalty == -0.08
    assert result.formula_version == "emergence-index-v4-seven-dimensions"
    emergence = result.evidence_summary["emergence_index"]
    assert emergence["total_score"] == result.germination_score
    assert set(emergence["dimensions"]) == {
        "growth",
        "cross_window_persistence",
        "enterprise_coverage",
        "source_diversity",
        "standard_position_distance",
        "evidence_quality",
        "result_stability",
    }
    for dimension in emergence["dimensions"].values():
        assert {
            "raw_value",
            "normalized_value",
            "weight",
            "contribution",
            "business_meaning",
        } == set(dimension)
    assert result.evidence_summary["growth"]["method"] == "three_window_cluster_share_growth_v2"


def test_score_components_are_authoritative_and_diagnostics_not_scored():
    result = assess_germination(
        sample_count=3,
        effective_sample_count=3,
        sources=["a", "b", "c"],
        spread_labels=["AI", "Agent"],
        publish_dates=[date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
        all_publish_dates=[date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
        candidate_skills={"RAG", "Python", "Agent"},
        reference_skill_sets=[{"Java", "Python"}],
        stability_score=0.85,
        config={},
    )
    emergence = result.evidence_summary["emergence_index"]
    components = result.evidence_summary["score_components"]

    # score_components 精确等于正式七维加权维度
    assert [item["name"] for item in components] == list(emergence["dimensions"].keys())
    assert set(emergence["dimensions"].keys()) == {
        "growth",
        "cross_window_persistence",
        "enterprise_coverage",
        "source_diversity",
        "standard_position_distance",
        "evidence_quality",
        "result_stability",
    }
    assert sum(item["contribution"] for item in components) == round(
        result.germination_score, 6
    )

    # 诊断特征全部 not_scored，且不把同一距离拆成多个评分维度
    diagnostics = result.evidence_summary["diagnostic_features"]
    assert diagnostics["standard_position_distance"]["scored"] is False
    assert diagnostics["skill_novelty_diagnostic"]["scored"] is False
    assert diagnostics["legacy_dimension_inputs"]["scored"] is False
    assert diagnostics["penalty_diagnostics"]["scored"] is False
    component_names = [item["name"] for item in components]
    assert "skill_combo_novelty" not in component_names
    assert "distance_from_existing_positions" not in component_names
    assert "standard_position_distance" in component_names
