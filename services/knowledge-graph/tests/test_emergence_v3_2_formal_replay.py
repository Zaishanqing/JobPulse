from app.emergence.formal_replay import run_formal_replay


def test_formal_stage2_replay_matches_all_frozen_decisions():
    result = run_formal_replay()

    assert result["status"] == "passed"
    assert result["cluster_counts"]["total_clusters"] == 2811
    assert result["cluster_counts"]["clusters_eligible_for_stage2"] == 2021
    assert result["stage2_distribution_over_eligible"] == {
        "insufficient_evidence": 1310,
        "not_emerging": 562,
        "emerging": 10,
        "weak_emerging_signal": 139,
    }
    assert len(result["emerging_clusters"]) == 10
    assert all(
        isinstance(cluster["canonical_title"], str)
        and cluster["canonical_title"].strip()
        for cluster in result["emerging_clusters"]
    )
    assert result["passed_checks"] == result["total_checks"] == 7
    assert result["mismatches"] == []
