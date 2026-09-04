"""Focused tests for the read-only cross-release survival analysis."""

from __future__ import annotations

from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_release_survival_certificate import (  # noqa: E402
    analyze_evidence_survival,
    build_distribution_delta,
)


def _row(evidence_id: str, claim: str, fact_version: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "position_code": claim,
        "source_jd_id": "jd-1",
        "source_fact_id": "fact-1",
        "source_fact_version": fact_version,
    }


def test_survival_classifies_same_replaced_added_removed() -> None:
    before = [
        _row("e1", "BACKEND_ENGINEER", "v1"),
        _row("e2", "BACKEND_ENGINEER", "v1"),
        _row("e3", "BACKEND_ENGINEER", "v1"),
    ]
    after = [
        _row("e1", "BACKEND_ENGINEER", "v1"),
        _row("e2", "BACKEND_ENGINEER", "v2"),
        _row("e4", "BACKEND_ENGINEER", "v1"),
    ]
    universe = [
        {"evidence_id": "e3", "membership_verified": False},
        {"evidence_id": "e1", "membership_verified": True},
        {"evidence_id": "e2", "membership_verified": True},
        {"evidence_id": "e4", "membership_verified": True},
    ]
    claims = analyze_evidence_survival(before, after, universe)
    payload = claims["BACKEND_ENGINEER"]
    assert payload["before_evidence_count"] == 3
    assert payload["after_evidence_count"] == 3
    assert payload["surviving_count"] == 2
    assert payload["surviving_same_fact_triple_count"] == 1
    assert payload["surviving_replaced_fact_triple_count"] == 1
    assert payload["added_count"] == 1
    assert payload["removed_count"] == 1
    assert payload["removed_reasons"] == {"release_not_included_unverified": 1}


def test_removed_reason_absent_from_universe() -> None:
    before = [_row("e1", "LLM_ALGORITHM_ENGINEER", "v1")]
    after: list[dict] = []
    claims = analyze_evidence_survival(before, after, [])
    assert claims["LLM_ALGORITHM_ENGINEER"]["removed_reasons"] == {
        "absent_from_after_sample_universe": 1
    }


def test_distribution_delta_counts_are_deterministic() -> None:
    manifest = [
        {
            "evidence_id": "e1",
            "source_platform": "boss_zhipin",
            "enterprise_identity": "enterprise-name:a",
            "template_candidate_cluster_id": "template-candidate:t1",
        },
        {
            "evidence_id": "e2",
            "source_platform": "liepin",
            "enterprise_identity": "enterprise-name:b",
            "template_candidate_cluster_id": "template-candidate:t2",
        },
    ]
    before = [_row("e1", "BACKEND_ENGINEER", "v1")]
    after = [
        _row("e1", "BACKEND_ENGINEER", "v1"),
        _row("e2", "BACKEND_ENGINEER", "v1"),
    ]
    deltas = build_distribution_delta(manifest, before, after)
    assert deltas["source_platform"]["before_counts"] == {
        "boss_zhipin": 1,
        "liepin": 0,
    }
    assert deltas["source_platform"]["after_counts"] == {
        "boss_zhipin": 1,
        "liepin": 1,
    }
    assert deltas["source_platform"]["delta"] == {
        "boss_zhipin": 0,
        "liepin": 1,
    }
