from __future__ import annotations

from datetime import date, datetime, timezone

from app.contexts.governance_feedback import (
    EvidenceRecord as GovernanceEvidenceRecord,
)
from app.contexts.insight_cards.business import (
    build_evidence_context,
    governance_evidence_to_independence,
)
from app.contexts.evidence_independence.adapters import (
    EvidenceSupportScoreConclusion,
)


def _governance_record(evidence_id: str, **overrides) -> GovernanceEvidenceRecord:
    values = {
        "evidence_id": evidence_id,
        "source_type": "boss",
        "source_name": "Boss直聘",
        "source_platform": overrides.get("source_type", "boss"),
        "title": f"JD {evidence_id}",
        "url": f"https://example.com/jobs/{evidence_id}",
        "raw_text": f"requirements for {evidence_id}",
        "publish_date": date(2026, 7, 10),
        "credibility_score": 0.9,
        "related_object_type": "position",
        "related_object_id": "pos-1",
        "created_at": datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return GovernanceEvidenceRecord(**values)


def test_governance_evidence_maps_to_independence_input() -> None:
    rows = (
        _governance_record("ev-1", source_type="boss"),
        _governance_record("ev-2", source_type="liepin"),
        _governance_record("ev-3", source_type="lagou"),
    )
    records = governance_evidence_to_independence(
        rows, "pos-1", "release-1"
    )
    assert len(records) == 3
    assert all(record.subject_ref == "pos-1" for record in records)
    assert all(record.release_id == "release-1" for record in records)
    assert {record.source_id for record in records} == {
        "boss",
        "liepin",
        "lagou",
    }
    assert all(record.text_fingerprint for record in records)
    assert all(record.quality_score == 0.9 for record in records)


def test_build_evidence_context_returns_summary_and_certificate() -> None:
    rows = (
        _governance_record("ev-1", source_type="boss"),
        _governance_record("ev-2", source_type="liepin"),
        _governance_record("ev-3", source_type="lagou"),
    )
    records = governance_evidence_to_independence(
        rows, "pos-1", "release-1"
    )
    summary, certificate = build_evidence_context(
        records,
        "pos-1",
        "release-1",
        conclusion=EvidenceSupportScoreConclusion(),
    )
    assert summary is not None
    assert summary.subject_ref == "pos-1"
    assert summary.release_id == "release-1"
    assert summary.coverage_status == "unknown"
    assert set(summary.evidence_ids) == {"ev-1", "ev-2", "ev-3"}
    assert certificate is not None
    assert certificate.conclusion_provider == "evidence-support-score.v2"
    assert len(certificate.ablations) == 4


def test_build_evidence_context_without_release_is_pending() -> None:
    rows = (_governance_record("ev-1"),)
    records = governance_evidence_to_independence(rows, "pos-1", "release-1")
    summary, certificate = build_evidence_context(
        records, "pos-1", "", conclusion=None
    )
    assert summary is None
    assert certificate is None


def test_build_evidence_context_detects_stale_with_reference_date() -> None:
    rows = (
        _governance_record("ev-1", publish_date=date(2020, 1, 1)),
        _governance_record("ev-2", publish_date=date(2020, 2, 1)),
    )
    records = governance_evidence_to_independence(
        rows, "pos-1", "release-1"
    )
    summary, _ = build_evidence_context(
        records,
        "pos-1",
        "release-1",
        observation_reference_date=date(2026, 7, 1),
        conclusion=None,
    )
    assert summary is not None
    assert summary.uncertainty_state == "stale_observation"
