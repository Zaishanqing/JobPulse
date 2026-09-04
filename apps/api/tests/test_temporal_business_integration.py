"""TEMP-LAG-01 business-chain integration tests.

Covers the wiring that moves the temporal-freshness experiment onto the real
business path: governance provenance mapping, ``build_evidence_context`` v5
opt-in (no global auto->v5), and ``assemble_insight_card`` filling
``temporal_evidence`` from the SAME aggregation (no second run).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone

import pytest

from app.contexts.evidence_independence.application import build_summary
from app.contexts.evidence_independence.contracts import (
    CollectionTimeBasis,
    EvidenceRecord,
    IndependenceRequest,
    ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
)
from app.contexts.evidence_independence.temporal import (
    TEMPORAL_FRESHNESS_VERSION,
    TemporalFreshnessRules,
)
from app.contexts.governance_feedback import (
    EvidenceRecord as GovernanceEvidenceRecord,
)
from app.contexts.insight_cards import (
    EvidenceRef,
    InsightCardSource,
    assemble_insight_card,
)
from app.contexts.insight_cards.business import (
    build_evidence_context,
    governance_evidence_to_independence,
)

REF = date(2026, 8, 12)
TZ = timezone.utc


def _governance(
    evidence_id: str,
    *,
    created_at: datetime | None,
    publish_date: date | None,
    source_platform: str = "boss_zhipin",
    enterprise_id: str | None = None,
) -> GovernanceEvidenceRecord:
    return GovernanceEvidenceRecord(
        evidence_id=evidence_id,
        source_type="source_jd",
        source_name=None,
        title="JD 数据时间滞后分析",
        url=None,
        raw_text="真实业务证据文本",
        publish_date=publish_date,
        credibility_score=0.9,
        related_object_type="jd",
        related_object_id=evidence_id,
        created_at=created_at,
        updated_at=created_at,
        source_platform=source_platform,
        enterprise_id=enterprise_id,
    )


def _evidence(
    evidence_id: str,
    *,
    basis: str = CollectionTimeBasis.UNKNOWN,
    published: date | None = date(2026, 7, 1),
    collected: datetime | None = None,
    source_id: str = "boss_zhipin",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject_ref="BACKEND_ENGINEER",
        source_id=source_id,
        enterprise_id=f"ent-{evidence_id}",
        template_cluster_id=f"tpl-{evidence_id}",
        position_id="BACKEND_ENGINEER",
        published_at=published,
        collected_at=collected,
        collection_time_basis=basis,
        text_fingerprint=f"fp-{evidence_id}",
        release_id="rel-1",
    )


# ---- governance provenance mapping -------------------------------------


def test_governance_created_at_maps_to_pipeline_observed() -> None:
    created = datetime(2026, 7, 10, tzinfo=TZ)
    (mapped,) = governance_evidence_to_independence(
        [
            _governance(
                "g-1",
                created_at=created,
                publish_date=date(2026, 7, 5),
            )
        ],
        "BACKEND_ENGINEER",
        "rel-1",
    )
    assert mapped.collected_at == created
    assert mapped.collection_time_basis == "pipeline_observed"


def test_governance_crawl_time_maps_to_crawler_acquired() -> None:
    created = datetime(2026, 7, 10, tzinfo=TZ)
    crawl = datetime(2026, 7, 9, tzinfo=TZ)
    (mapped,) = governance_evidence_to_independence(
        [
            _governance(
                "g-2",
                created_at=created,
                publish_date=date(2026, 7, 5),
            )
        ],
        "BACKEND_ENGINEER",
        "rel-1",
        crawl_times={"g-2": crawl},
    )
    assert mapped.collected_at == crawl
    assert mapped.collection_time_basis == "crawler_acquired"


def test_governance_without_time_maps_to_unknown() -> None:
    (mapped,) = governance_evidence_to_independence(
        [_governance("g-3", created_at=None, publish_date=None)],
        "BACKEND_ENGINEER",
        "rel-1",
    )
    assert mapped.collected_at is None
    assert mapped.collection_time_basis == "unknown"


# ---- business context: legacy auto unchanged -----------------------------


def test_legacy_build_evidence_context_has_no_temporal_certificate() -> None:
    records = [
        _evidence("e-1", source_id="s1"),
        _evidence("e-2", source_id="s2"),
        _evidence("e-3", source_id="s3"),
    ]
    summary, certificate = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=REF,
        conclusion=None,
    )
    assert summary is not None
    assert certificate is not None
    assert summary.algorithm_version == "evidence-independence.v2"
    assert summary.temporal_certificate is None
    assert summary.temporal_algorithm_version is None


# ---- business context: explicit v5 opt-in --------------------------------


def test_business_v5_path_enables_temporal_certificate() -> None:
    records = [
        _evidence("e-1", source_id="s1", collected=datetime(2026, 7, 5, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
        _evidence("e-2", source_id="s2", collected=datetime(2026, 7, 6, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
        _evidence("e-3", source_id="s3", collected=datetime(2026, 7, 7, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
        _evidence("e-4", source_id="s4", collected=datetime(2026, 7, 8, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
        _evidence("e-5", source_id="s5", collected=datetime(2026, 7, 9, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
    ]
    summary, certificate = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=REF,
        conclusion=None,
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(half_life_days=60.0),
    )
    assert summary is not None
    assert certificate is not None
    assert summary.algorithm_version == "evidence-independence.v3.2"
    assert summary.temporal_algorithm_version == TEMPORAL_FRESHNESS_VERSION
    assert summary.temporal_certificate is not None
    # certificate baseline uses the same temporal rules/config as the summary
    assert certificate.config_hash == summary.config_hash
    # honest provenance: pipeline rows never train source lag, so every source
    # reports sufficient=false and the certificate reflects observed lower bounds
    assert all(
        profile.valid_crawler_delay_samples == 0
        for profile in summary.temporal_certificate.source_lag_profiles
    )
    assert summary.temporal_certificate.reference_date == REF


def test_business_v5_stale_gate_respects_unknown_clusters() -> None:
    # all clusters have age > stale horizon except explicit unknowns -> the
    # pillar may be deep-stale but NOT "all clusters stale".
    records = [
        _evidence("e-1", source_id="s1", published=date(2025, 1, 1)),
        _evidence("e-2", source_id="s2", published=date(2025, 2, 1)),
        _evidence("e-3", source_id="s3", published=None, collected=None, basis=CollectionTimeBasis.UNKNOWN),
    ]
    summary, _ = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=REF,
        conclusion=None,
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(
            stale_after_days=30, stale_gate_enabled=True
        ),
    )
    assert summary is not None
    assert "temporal_state_indeterminate" in summary.uncertainty_reasons
    assert summary.uncertainty_state != "stale_observation"
    assert summary.cluster_staleness


def test_business_v5_config_hash_carries_reference_date_and_rules() -> None:
    records = [
        _evidence("e-1", source_id="s1"),
        _evidence("e-2", source_id="s2"),
        _evidence("e-3", source_id="s3"),
    ]
    summary_a, _ = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=REF,
        conclusion=None,
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(half_life_days=60.0),
    )
    summary_b, _ = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=REF,
        conclusion=None,
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(half_life_days=30.0),
    )
    summary_c, _ = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=date(2026, 9, 1),
        conclusion=None,
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(half_life_days=60.0),
    )
    assert summary_a is not None and summary_b is not None and summary_c is not None
    assert summary_a.config_hash != summary_b.config_hash
    assert summary_a.config_hash != summary_c.config_hash
    # deterministic rerun
    summary_a2, _ = build_evidence_context(
        records,
        "BACKEND_ENGINEER",
        "rel-1",
        observation_reference_date=REF,
        conclusion=None,
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(half_life_days=60.0),
    )
    assert summary_a2 is not None
    assert summary_a2.config_hash == summary_a.config_hash


# ---- InsightCard temporal_evidence propagation ----------------------------


def _card_source(summary) -> InsightCardSource:
    evidence_ids = sorted(summary.evidence_ids)
    return InsightCardSource(
        insight_id="insight-1",
        claim_type="emerging_position",
        subject_ref="BACKEND_ENGINEER",
        claim="真实业务声明",
        evidence_subject_ref="BACKEND_ENGINEER",
        algorithm_version="alg.v1",
        algorithm_config_version="config.v1",
        algorithm_config_hash="hash-abc",
        authority_state="authoritative",
        evidence_refs=tuple(
            EvidenceRef(
                evidence_id=evidence_id,
                source_object_type="source_jd",
                source_object_id=evidence_id,
                source_document_id=evidence_id,
                source_version="v1",
                used=True,
            )
            for evidence_id in evidence_ids
        ),
        used_evidence_ids=tuple(evidence_ids),
        evidence_summary=summary,
        evidence_algorithm_version=summary.algorithm_version,
        evidence_config_hash=summary.config_hash,
        raw_evidence_count=summary.raw_evidence_count,
        effective_sample_size=summary.effective_sample_size,
        uncertainty_state="ok",
        uncertainty_reasons=summary.uncertainty_reasons,
        release_refs=(summary.release_id,),
        graph_version_refs=(),
        catalog_refs=(),
        data_refs=(),
    )


def test_assemble_insight_card_fills_temporal_evidence_from_summary() -> None:
    records = [
        _evidence("e-1", source_id="s1", collected=datetime(2026, 7, 5, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
        _evidence("e-2", source_id="s2", collected=datetime(2026, 7, 6, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
        _evidence("e-3", source_id="s3", collected=datetime(2026, 7, 7, tzinfo=TZ), basis=CollectionTimeBasis.PIPELINE_OBSERVED),
    ]
    request = IndependenceRequest(
        subject_ref="BACKEND_ENGINEER",
        release_id="rel-1",
        algorithm_version="evidence-independence.v3.2",
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        observation_reference_date=REF,
        coverage_status="covered",
    )
    summary = build_summary(
        records,
        request,
        temporal_rules=TemporalFreshnessRules(half_life_days=60.0),
    )
    assert summary.temporal_certificate is not None

    card = assemble_insight_card(_card_source(summary))

    temporal = card.temporal_evidence
    assert temporal is not None
    assert temporal.reference_date == "2026-08-12"
    assert temporal.temporal_algorithm_version == TEMPORAL_FRESHNESS_VERSION
    assert temporal.freshness_adjusted_neff == pytest.approx(
        summary.effective_sample_size
    )
    # every source surfaced with honest zero-crawler counts
    assert len(temporal.source_lag_summary) == 3
    assert all(
        row.valid_sample_count == 0 and row.unknown_provenance_count == 0
        for row in temporal.source_lag_summary
    )
    assert temporal.time_provenance_policy == "time-provenance.v2"

    # no second aggregation: the summary was constructed once and carried.
    plain = asdict(card)
    assert "temporal_evidence" in plain
    assert plain["temporal_evidence"]["reference_date"] == "2026-08-12"


def test_assemble_insight_card_legacy_summary_keeps_temporal_evidence_none() -> None:
    request = IndependenceRequest(
        subject_ref="BACKEND_ENGINEER",
        release_id="rel-1",
        coverage_status="covered",
    )
    records = [
        _evidence("e-1", source_id="s1"),
        _evidence("e-2", source_id="s2"),
        _evidence("e-3", source_id="s3"),
    ]
    summary = build_summary(records, request)
    card = assemble_insight_card(_card_source(summary))
    assert card.temporal_evidence is None
