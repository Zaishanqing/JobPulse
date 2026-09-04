from __future__ import annotations

from dataclasses import replace

from app.contexts.evidence_independence.real_data import (
    RealJDCandidate,
    build_inventory,
    freeze_target_samples,
)


def _candidate(**changes) -> RealJDCandidate:
    base = RealJDCandidate(
        asset_pool="bundle",
        record_identity="source:boss:1:sha256:a",
        document_id="doc-1",
        position_code="BACKEND_ENGINEER",
        classification_status="resolved",
        source_platform="boss",
        source_record_id="1",
        source_version="1",
        content_hash="sha256:a",
        source_fact_id=None,
        source_jd_id=None,
        enterprise_name="Example Co",
        enterprise_id=None,
        published_at=None,
        observed_at="2026-08-01T00:00:00+00:00",
        time_basis="observed",
        title="Backend Engineer",
        responsibilities=("Build APIs",),
        skills=("Python",),
        text_excerpt="Backend Engineer\nBuild APIs\nPython",
        text_fingerprint="fingerprint-a",
        release_ids=(),
        identity_kind="source_identity",
        input_ref="fixture",
    )
    return replace(base, **changes)


def test_inventory_does_not_fuzzy_merge_cross_pool_records() -> None:
    left = _candidate()
    right = _candidate(
        asset_pool="run",
        record_identity="run:r1:row:1:document:doc-1",
        identity_kind="run_row_identity",
    )
    inventory, overlaps, _ = build_inventory((left, right))
    assert inventory["asset_record_count"] == 2
    assert inventory["identity_overlap_count"] == 0
    assert overlaps == []


def test_freeze_is_deterministic_and_gold_stays_blank() -> None:
    candidates = (
        _candidate(),
        _candidate(
            record_identity="source:liepin:2:sha256:b",
            document_id="doc-2",
            source_platform="liepin",
            source_record_id="2",
            content_hash="sha256:b",
            position_code="LLM_ALGORITHM_ENGINEER",
            title="LLM Algorithm Engineer",
            text_fingerprint="fingerprint-b",
        ),
    )
    first = freeze_target_samples(candidates, config={"threshold": 0.6})
    second = freeze_target_samples(tuple(reversed(candidates)), config={"threshold": 0.6})
    assert first[0]["dataset_version"] == second[0]["dataset_version"]
    assert first[0]["research_status"] == "incomplete"
    assert all(row["human_gold"]["same_hiring_event_cluster"] is None for row in first[4])


def test_freeze_excludes_non_resolved_positions() -> None:
    selected = _candidate()
    unresolved = _candidate(
        record_identity="source:boss:2:sha256:b",
        document_id="doc-2",
        classification_status="ambiguous",
    )
    manifest, samples, *_ = freeze_target_samples(
        (selected, unresolved), config={"threshold": 0.6}
    )
    assert manifest["sample_count"] == 1
    assert len(samples) == 1
