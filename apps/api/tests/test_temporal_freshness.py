from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.contexts.evidence_independence.application import (
    INDEPENDENCE_WEIGHT_RULES_V1,
    build_cluster_freshness,
    build_evidence_aggregation,
    build_summary,
    cluster_weights_v4,
    config_hash,
)
from app.contexts.evidence_independence.contracts import (
    CollectionTimeBasis,
    EvidenceRecord,
    IndependenceRequest,
    MassCappedAggregationRules,
)
from app.contexts.evidence_independence.temporal import (
    TEMPORAL_FRESHNESS_VERSION,
    EvidenceTemporalProfile,
    SourceLagProfile,
    TemporalFreshnessError,
    TemporalFreshnessRules,
    all_clusters_stale,
    build_evidence_temporal_profile,
    build_source_lag_profiles,
    build_temporal_freshness,
    cluster_staleness_state,
    cluster_staleness_states,
    derive_temporal_reasons,
    temporal_rules_hash,
)

REF = date(2026, 8, 1)
TZ = timezone.utc


def _record(
    evidence_id: str,
    *,
    source_id: str = "s1",
    enterprise_id: str | None = "ent-a",
    position_id: str | None = "POS",
    published: date | None = None,
    collected: datetime | None = None,
    basis: str = CollectionTimeBasis.UNKNOWN,
    fingerprint: str | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject_ref="POS",
        source_id=source_id,
        enterprise_id=enterprise_id,
        position_id=position_id,
        published_at=published,
        collected_at=collected,
        collection_time_basis=basis,
        text_fingerprint=fingerprint,
        release_id="rel-1",
    )


def _published(evidence_id: str, published: date, **overrides) -> EvidenceRecord:
    return _record(evidence_id, published=published, **overrides)


def _collected(
    evidence_id: str,
    collected: datetime,
    *,
    basis: str = CollectionTimeBasis.PIPELINE_OBSERVED,
    **overrides,
) -> EvidenceRecord:
    return _record(evidence_id, collected=collected, basis=basis, **overrides)


def _v5_request(**overrides) -> IndependenceRequest:
    values = {
        "subject_ref": "POS",
        "release_id": "rel-1",
        "algorithm_version": "evidence-independence.v3.2",
        "aggregation_version": "robust-evidence-aggregation.v5",
        "observation_reference_date": REF,
        "stale_observation_days": 1000,
        "coverage_status": "covered",
    }
    values.update(overrides)
    return IndependenceRequest(**values)


# ---- decay math ----------------------------------------------------------


def test_decay_is_monotonic_in_market_age() -> None:
    rules = TemporalFreshnessRules()
    older = build_evidence_temporal_profile(
        _published("e-old", date(2026, 5, 1)), REF, rules, {}
    )
    newer = build_evidence_temporal_profile(
        _published("e-new", date(2026, 7, 1)), REF, rules, {}
    )
    assert older.market_age_days > newer.market_age_days
    assert older.age_decay < newer.age_decay
    assert older.freshness_weight < newer.freshness_weight


def test_half_life_math_is_exact() -> None:
    rules = TemporalFreshnessRules(half_life_days=60.0)
    at_half_life = build_evidence_temporal_profile(
        _published("e", date(2026, 6, 1)), REF, rules, {}
    )
    assert at_half_life.market_age_days == 61
    expected = 2.0 ** (-61 / 60)
    assert abs(at_half_life.age_decay - expected) < 1e-6
    fresh = build_evidence_temporal_profile(
        _published("e0", REF), REF, rules, {}
    )
    assert fresh.age_decay == 1.0


# ---- time basis priority ---------------------------------------------------


def test_published_time_is_preferred() -> None:
    record = _record(
        "e1",
        published=date(2026, 7, 1),
        collected=datetime(2026, 7, 5, tzinfo=TZ),
        basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
    )
    profile = build_evidence_temporal_profile(
        record, REF, TemporalFreshnessRules(), {}
    )
    assert profile.time_basis == "published"
    assert profile.time_confidence == 1.0
    assert profile.acquisition_delay_days == 4.0
    assert profile.staleness_state == "fresh"


def test_acquisition_delay_requires_crawler_provenance() -> None:
    # pipeline-observed / unknown collection timestamps are pipeline
    # bookkeeping and must NOT populate acquisition_delay_days: that field is
    # defined only as crawler_acquired_at - published_at.
    pipeline = _record(
        "e-p",
        published=date(2026, 7, 1),
        collected=datetime(2026, 7, 5, tzinfo=TZ),
        basis=CollectionTimeBasis.PIPELINE_OBSERVED,
    )
    unknown = _record(
        "e-u",
        published=date(2026, 7, 1),
        collected=datetime(2026, 7, 5, tzinfo=TZ),
        basis=CollectionTimeBasis.UNKNOWN,
    )
    rules = TemporalFreshnessRules()
    for record in (pipeline, unknown):
        profile = build_evidence_temporal_profile(record, REF, rules, {})
        assert profile.acquisition_delay_days is None
        assert "collected_before_published" not in profile.anomaly_reasons


# ---- provenance gating on SourceLagProfile --------------------------------


def test_crawler_acquired_timestamp_enters_source_lag_profile() -> None:
    records = [
        _record(
            f"e{i}",
            published=date(2026, 6, 1),
            collected=datetime(2026, 6, 3, tzinfo=TZ),
            basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
        )
        for i in range(5)
    ]
    lag = build_source_lag_profiles(records)["s1"]
    assert lag.valid_crawler_delay_samples == 5
    assert lag.median_delay_days == 2.0
    assert lag.p90_delay_days == 2.0
    assert lag.pipeline_observation_count == 0
    assert lag.unknown_provenance_count == 0


def test_pipeline_observation_never_enters_source_lag_profile() -> None:
    records = [
        _record(
            "e1",
            published=date(2026, 6, 1),
            collected=datetime(2026, 6, 3, tzinfo=TZ),
            basis=CollectionTimeBasis.PIPELINE_OBSERVED,
        )
    ]
    lag = build_source_lag_profiles(records)["s1"]
    assert lag.valid_crawler_delay_samples == 0
    assert lag.pipeline_observation_count == 1
    assert lag.median_delay_days is None
    assert lag.p90_delay_days is None


def test_unknown_provenance_never_enters_source_lag_profile() -> None:
    records = [
        _record(
            "e1",
            published=date(2026, 6, 1),
            collected=datetime(2026, 6, 3, tzinfo=TZ),
            basis=CollectionTimeBasis.UNKNOWN,
        )
    ]
    lag = build_source_lag_profiles(records)["s1"]
    assert lag.valid_crawler_delay_samples == 0
    assert lag.unknown_provenance_count == 1


def test_pipeline_rows_do_not_enable_lag_estimation() -> None:
    # A source with 5+ pipeline-observed rows is NOT sufficient to estimate
    # publish dates: only crawler acquisition delays may train the profile.
    records = [
        _collected(
            f"e{i}",
            datetime(2026, 7, 10, tzinfo=TZ),
            source_id="s9",
            enterprise_id=f"ent-{i}",
        )
        for i in range(5)
    ]
    cert = build_temporal_freshness(records, REF, TemporalFreshnessRules())
    assert cert.source_lag_profiles[0].valid_crawler_delay_samples == 0
    assert cert.source_lag_profiles[0].pipeline_observation_count == 0
    assert cert.source_lag_profiles[0].missing_publish_count == 5
    assert all(
        profile.time_basis == "observed_lower_bound"
        for profile in cert.profiles
    )


def test_negative_crawler_delay_is_invalid_not_mixed() -> None:
    records = [
        _record(
            "e1",
            published=date(2026, 6, 5),
            collected=datetime(2026, 6, 1, tzinfo=TZ),
            basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
        )
    ]
    lag = build_source_lag_profiles(records)["s1"]
    assert lag.valid_crawler_delay_samples == 0
    assert lag.invalid_sample_count == 1
    assert lag.median_delay_days is None


def test_future_crawler_does_not_train_lag_and_is_anomaly_capped() -> None:
    # A crawler acquisition time after the observation reference is a
    # future-leakage anomaly: it must NOT enter SourceLagProfile training, and
    # freshness must be capped by the temporal anomaly rule.
    records = [
        _record(
            "e-future",
            published=date(2026, 7, 1),
            collected=datetime(2026, 12, 1, tzinfo=TZ),
            basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
        )
    ]
    rules = TemporalFreshnessRules(anomaly_confidence_cap=0.4)
    lag = build_source_lag_profiles(records, REF)["s1"]
    assert lag.valid_crawler_delay_samples == 0
    assert lag.invalid_sample_count == 1
    profile = build_evidence_temporal_profile(
        records[0], REF, rules, {"s1": lag}
    )
    assert "crawler_acquired_after_reference" in profile.anomaly_reasons
    assert profile.freshness_weight <= rules.anomaly_confidence_cap


def test_missing_publish_count_is_reported() -> None:
    records = [
        _collected("e1", datetime(2026, 7, 1, tzinfo=TZ), basis=CollectionTimeBasis.CRAWLER_ACQUIRED)
    ]
    lag = build_source_lag_profiles(records)["s1"]
    assert lag.missing_publish_count == 1
    assert lag.valid_crawler_delay_samples == 0


def test_collected_before_published_is_anomaly() -> None:
    record = _record(
        "e1",
        published=date(2026, 7, 5),
        collected=datetime(2026, 7, 1, tzinfo=TZ),
        basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
    )
    profile = build_evidence_temporal_profile(
        record, REF, TemporalFreshnessRules(), {}
    )
    assert "collected_before_published" in profile.anomaly_reasons


def test_source_lag_profile_sufficient_uses_crawler_count() -> None:
    lag = SourceLagProfile(
        source_id="s1",
        valid_crawler_delay_samples=0,
        pipeline_observation_count=10,
        unknown_provenance_count=0,
        missing_publish_count=0,
        invalid_sample_count=0,
        median_delay_days=None,
    )
    assert lag.lag_profile_sufficient(5) is False
    assert lag.non_crawler_observation_count == 10


# ---- anomaly confidence cap ------------------------------------------------


def test_published_after_reference_is_downgraded_not_top_freshness() -> None:
    rules = TemporalFreshnessRules(anomaly_confidence_cap=0.4)
    future = build_evidence_temporal_profile(
        _published("e-future", date(2026, 12, 1)), REF, rules, {}
    )
    assert "published_at_after_reference" in future.anomaly_reasons
    assert future.market_age_days < 0
    assert future.freshness_weight <= rules.anomaly_confidence_cap
    assert future.staleness_state == "stale"


def test_estimated_publish_after_reference_is_downgraded() -> None:
    rules = TemporalFreshnessRules(anomaly_confidence_cap=0.4)
    records = [
        # published rows train crawler lag median 2d
        _record(
            f"e{i}",
            published=date(2026, 7, 1),
            collected=datetime(2026, 7, 3, tzinfo=TZ),
            basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
        )
        for i in range(5)
    ]
    lag = build_source_lag_profiles(records)["s1"]
    # collected base at 2026-12-01 -> est publish 2026-11-29 > REF
    record = _record(
        "obs",
        source_id="s1",
        enterprise_id="ent-obs",
        collected=datetime(2026, 12, 1, tzinfo=TZ),
        basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
    )
    profile = build_evidence_temporal_profile(record, REF, rules, {"s1": lag})
    assert profile.time_basis == "estimated_from_source_lag"
    assert "estimated_publish_at_after_reference" in profile.anomaly_reasons
    assert profile.freshness_weight <= rules.anomaly_confidence_cap
    assert profile.staleness_state == "stale"


def test_anomaly_cap_enters_config_hash() -> None:
    base = temporal_rules_hash(TemporalFreshnessRules(anomaly_confidence_cap=0.4))
    other = temporal_rules_hash(TemporalFreshnessRules(anomaly_confidence_cap=0.25))
    assert base != other


# ---- tri-state staleness -----------------------------------------------------


def test_unknown_staleness_state_is_unknown() -> None:
    cert = build_temporal_freshness(
        [_record("e1", published=None, collected=None)], REF, TemporalFreshnessRules()
    )
    profile = cert.profiles[0]
    assert profile.time_basis == "unknown"
    assert profile.staleness_state == "unknown"
    assert profile.stale is False
    assert profile.freshness_weight == 0.4


def test_cluster_staleness_tri_state_rules() -> None:
    fresh = EvidenceTemporalProfile(
        evidence_id="f",
        reference_date=REF,
        time_basis="published",
        market_age_days=1.0,
        staleness_state="fresh",
    )
    stale = EvidenceTemporalProfile(
        evidence_id="s",
        reference_date=REF,
        time_basis="published",
        market_age_days=1000.0,
        staleness_state="stale",
    )
    unknown = EvidenceTemporalProfile(
        evidence_id="u",
        reference_date=REF,
        time_basis="unknown",
        staleness_state="unknown",
    )
    assert cluster_staleness_state([fresh, stale]) == "fresh"
    assert cluster_staleness_state([stale, stale]) == "stale"
    assert cluster_staleness_state([unknown]) == "unknown"
    # stale + unknown is NOT washed back to fresh; all trusted stale -> stale
    assert cluster_staleness_state([stale, unknown]) == "stale"
    assert cluster_staleness_state([]) == "unknown"


def test_unknown_cluster_does_not_wash_stale_cluster_to_fresh() -> None:
    # one stale cluster + one all-unknown cluster
    records = [
        _published("e1", date(2025, 1, 1), source_id="s1", enterprise_id="ent-1"),
        _published("e2", date(2025, 2, 1), source_id="s2", enterprise_id="ent-2"),
        _record("e3", source_id="s3", enterprise_id="ent-3", published=None, collected=None),
    ]
    rules = TemporalFreshnessRules(stale_after_days=30)
    cert = build_temporal_freshness(records, REF, rules)
    clusters = tuple((record.evidence_id,) for record in records)
    states = cluster_staleness_states(clusters, cert)
    # e1/e2 published 2025 -> stale; e3 unknown
    assert states["e1"] == "stale"
    assert states["e2"] == "stale"
    assert states["e3"] == "unknown"
    assert all_clusters_stale(clusters, cert) is False


def test_unknown_only_cluster_is_not_stale_and_not_fresh() -> None:
    records = [_record("e1", source_id="s3", enterprise_id="ent-3")]
    cert = build_temporal_freshness(records, REF, TemporalFreshnessRules())
    clusters = tuple((record.evidence_id,) for record in records)
    states = cluster_staleness_states(clusters, cert)
    assert states["e1"] == "unknown"
    assert all_clusters_stale(clusters, cert) is False


# ---- stale gate ---------------------------------------------------------------


def test_stale_gate_triggers_when_all_clusters_stale_and_state_ok() -> None:
    records = [
        _record(
            f"e{i}",
            source_id=f"s{i}",
            enterprise_id=f"ent-{i}",
            published=date(2025, 1, i),
        )
        for i in range(1, 4)
    ]
    rules = TemporalFreshnessRules(
        stale_after_days=30, stale_gate_enabled=True
    )
    certificate = build_temporal_freshness(records, REF, rules)
    clusters = tuple((record.evidence_id,) for record in records)
    assert all_clusters_stale(clusters, certificate) is True
    summary = build_summary(
        records,
        _v5_request(min_effective_sample_size=1.0),
        temporal_rules=rules,
    )
    assert summary.uncertainty_state == "stale_observation"
    assert "all_clusters_stale" in summary.uncertainty_reasons


def test_v5_source_concentrated_survives_stale_unknown_mixture() -> None:
    # Base v5 state must NOT be clobbered by temporal post-processing: a
    # source-concentrated result with a stale + unknown cluster mixture stays
    # source_concentrated (never rewritten to ``ok``), and the presence of an
    # unknown cluster adds only temporal_state_indeterminate.
    records = [
        _published("e1", date(2025, 1, 1), source_id="s1", enterprise_id="ent-1"),
        _published("e2", date(2025, 2, 1), source_id="s1", enterprise_id="ent-2"),
        _record("e3", source_id="s1", enterprise_id="ent-3", published=None, collected=None),
    ]
    rules = TemporalFreshnessRules(
        stale_after_days=30, stale_gate_enabled=True
    )
    summary = build_summary(
        records,
        _v5_request(min_effective_sample_size=1.0, min_independent_clusters=1),
        temporal_rules=rules,
    )
    assert summary.uncertainty_state == "source_concentrated"
    assert "source_id_concentrated" in summary.uncertainty_reasons
    assert "temporal_state_indeterminate" in summary.uncertainty_reasons
    assert summary.uncertainty_state != "ok"
    assert summary.uncertainty_state != "stale_observation"


def test_v5_insufficient_evidence_not_promoted_to_ok_by_temporal() -> None:
    # Independent-cluster count below the minimum -> base insufficient_evidence.
    # The temporal gate (stale + unknown mixture present) must NOT promote it to
    # ``ok`` or ``stale_observation``; it only escalates the indeterminate
    # reason.
    records = [
        _record(
            "e1",
            source_id="s1",
            enterprise_id="ent-1",
            published=date(2025, 1, 1),
        ),
        _record(
            "e2",
            source_id="s2",
            enterprise_id="ent-2",
            published=None,
            collected=None,
        ),
    ]
    rules = TemporalFreshnessRules(
        stale_after_days=30, stale_gate_enabled=True
    )
    summary = build_summary(
        records,
        _v5_request(min_effective_sample_size=1.0),
        temporal_rules=rules,
    )
    assert summary.uncertainty_state == "insufficient_evidence"
    assert "independent_clusters_below_minimum" in summary.uncertainty_reasons
    assert "temporal_state_indeterminate" in summary.uncertainty_reasons
    assert summary.uncertainty_state != "ok"
    assert summary.uncertainty_state != "stale_observation"


def test_temporal_state_indeterminate_when_no_fresh_but_unknown_exists() -> None:
    records = [
        _published("e1", date(2025, 1, 1), source_id="s1", enterprise_id="ent-1"),
        _record("e2", source_id="s2", enterprise_id="ent-2", published=None, collected=None),
    ]
    rules = TemporalFreshnessRules(stale_after_days=30)
    cert = build_temporal_freshness(records, REF, rules)
    clusters = tuple((record.evidence_id,) for record in records)
    states = cluster_staleness_states(clusters, cert)
    assert states["e1"] == "stale"
    assert states["e2"] == "unknown"
    reasons = derive_temporal_reasons(cert, states, rules)
    assert "temporal_state_indeterminate" in reasons
    assert "all_clusters_stale" not in reasons


# ---- v5 aggregation -----------------------------------------------------------


def test_v5_normalized_weights_sum_to_one() -> None:
    records = [
        _record(f"e{i}", enterprise_id=f"ent-{i}", source_id=("s1" if i <= 3 else "s2"), published=date(2026, 7, i))  # noqa: E501
        for i in range(1, 7)
    ]
    aggregation = build_evidence_aggregation(
        records, _v5_request(), temporal_rules=TemporalFreshnessRules()
    )
    weights = dict(aggregation.weights)
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert aggregation.temporal_algorithm_version == TEMPORAL_FRESHNESS_VERSION
    assert aggregation.temporal_certificate is not None
    assert len(aggregation.cluster_freshness) == len(records)


def test_v5_projection_respects_source_cap() -> None:
    records = [
        _record(f"e{i}", enterprise_id=f"ent-{i}", source_id="boss", published=date(2026, 7, i))  # noqa: E501
        for i in range(1, 5)
    ] + [
        _record("e5", enterprise_id="ent-5", source_id="liepin", published=date(2026, 7, 5))
    ]
    aggregation = build_evidence_aggregation(
        records,
        _v5_request(),
        temporal_rules=TemporalFreshnessRules(),
        mass_capped_rules=MassCappedAggregationRules(
            source_cap=0.6, enterprise_cap=1.0, template_cap=1.0
        ),
    )
    weights = dict(aggregation.weights)
    by_id = {record.evidence_id: record.source_id for record in records}
    source_mass = {"boss": 0.0, "liepin": 0.0}
    for cluster_id, weight in weights.items():
        source_mass[by_id[cluster_id]] += weight
    assert source_mass["boss"] <= 0.6 + 1e-4
    assert aggregation.projection_certificate is not None
    assert aggregation.projection_certificate.converged is True


def test_v5_deterministic_output() -> None:
    records = [
        _record(f"e{i}", enterprise_id=f"ent-{i}", source_id="s1", published=date(2026, 7, i), collected=datetime(2026, 7, i + 1, tzinfo=TZ))  # noqa: E501
        for i in range(1, 7)
    ]
    first = build_evidence_aggregation(
        records, _v5_request(), temporal_rules=TemporalFreshnessRules()
    )
    second = build_evidence_aggregation(
        records, _v5_request(), temporal_rules=TemporalFreshnessRules()
    )
    assert first.weights == second.weights
    assert first.temporal_reasons == second.temporal_reasons
    assert first.temporal_certificate.profiles == second.temporal_certificate.profiles
    assert first.temporal_certificate.config_hash == second.temporal_certificate.config_hash


def test_v5_requires_explicit_reference_date() -> None:
    request = IndependenceRequest(
        subject_ref="POS",
        release_id="rel-1",
        algorithm_version="evidence-independence.v3.2",
        aggregation_version="robust-evidence-aggregation.v5",
        observation_reference_date=None,
    )
    with pytest.raises(TemporalFreshnessError):
        build_evidence_aggregation(
            [_record("e1")], request, temporal_rules=TemporalFreshnessRules()
        )


# ---- config hash --------------------------------------------------------------


def test_config_hash_changes_with_temporal_rules() -> None:
    request = _v5_request()
    base = config_hash(request, INDEPENDENCE_WEIGHT_RULES_V1, temporal_rules=TemporalFreshnessRules())
    other = config_hash(
        request,
        INDEPENDENCE_WEIGHT_RULES_V1,
        temporal_rules=TemporalFreshnessRules(half_life_days=30),
    )
    assert base != other
    assert (
        temporal_rules_hash(TemporalFreshnessRules())
        == temporal_rules_hash(TemporalFreshnessRules())
    )


def test_config_hash_carries_reference_date() -> None:
    request = _v5_request()
    request_other = _v5_request(observation_reference_date=date(2026, 9, 1))
    digest = config_hash(
        request, INDEPENDENCE_WEIGHT_RULES_V1, temporal_rules=TemporalFreshnessRules()
    )
    digest_other = config_hash(
        request_other,
        INDEPENDENCE_WEIGHT_RULES_V1,
        temporal_rules=TemporalFreshnessRules(),
    )
    assert digest != digest_other
    assert len(digest) == 64


def test_config_hash_distinguishes_time_provenance_policy_and_keeps_v4() -> None:
    # The v5 temporal payload must carry the time-provenance policy so that
    # time-provenance.v1 vs v2 yield different config identities.  The
    # historical v4 identity must not change.
    from app.contexts.evidence_independence import application as app_module

    request = _v5_request()
    digest_v2 = config_hash(
        request, INDEPENDENCE_WEIGHT_RULES_V1, temporal_rules=TemporalFreshnessRules()
    )
    v4_request = IndependenceRequest(
        subject_ref="POS",
        release_id="rel-1",
        algorithm_version="evidence-independence.v3.2",
        aggregation_version="robust-evidence-aggregation.v4",
        observation_reference_date=REF,
        stale_observation_days=1000,
        coverage_status="covered",
    )
    digest_v4 = config_hash(v4_request, INDEPENDENCE_WEIGHT_RULES_V1)

    original = app_module.TIME_PROVENANCE_POLICY
    try:
        app_module.TIME_PROVENANCE_POLICY = "time-provenance.v1"
        digest_v1 = config_hash(
            request,
            INDEPENDENCE_WEIGHT_RULES_V1,
            temporal_rules=TemporalFreshnessRules(),
        )
        digest_v4_other = config_hash(v4_request, INDEPENDENCE_WEIGHT_RULES_V1)
    finally:
        app_module.TIME_PROVENANCE_POLICY = original
    assert digest_v2 != digest_v1
    assert digest_v4 == digest_v4_other


# ---- v4 backward compatibility --------------------------------------------------


def test_v4_aggregation_unchanged_and_temporal_fields_default() -> None:
    records = [
        _record(f"e{i}", enterprise_id=f"ent-{i}", source_id="boss", published=date(2026, 7, i))  # noqa: E501
        for i in range(1, 5)
    ] + [
        _record("e5", enterprise_id="ent-5", source_id="liepin", published=date(2026, 7, 5))
    ]
    request = IndependenceRequest(
        subject_ref="POS",
        release_id="rel-1",
        algorithm_version="evidence-independence.v3.2",
        aggregation_version="robust-evidence-aggregation.v4",
        observation_reference_date=REF,
        stale_observation_days=1000,
        coverage_status="covered",
    )
    aggregation = build_evidence_aggregation(records, request)
    assert aggregation.temporal_certificate is None
    assert aggregation.cluster_freshness == ()
    assert aggregation.temporal_algorithm_version is None
    assert aggregation.temporal_reasons == ()
    clusters = aggregation.clusters
    v4_weights, _certificate = cluster_weights_v4(records, clusters)
    assert dict(aggregation.weights) == {
        k: round(v, 8) for k, v in v4_weights.items()
    }


def test_v4_summary_has_no_temporal_reasons() -> None:
    records = [
        _record(
            f"e{i}",
            source_id=f"s{i}",
            enterprise_id=f"ent-{i}",
            published=date(2026, 7, i),
        )
        for i in range(1, 6)
    ]
    request = IndependenceRequest(
        subject_ref="POS",
        release_id="rel-1",
        algorithm_version="evidence-independence.v3.2",
        aggregation_version="robust-evidence-aggregation.v4",
        observation_reference_date=REF,
        stale_observation_days=1000,
        coverage_status="covered",
    )
    summary = build_summary(records, request)
    assert "temporal_coverage_low" not in summary.uncertainty_reasons
    assert summary.temporal_certificate is None
    assert summary.temporal_algorithm_version is None
    assert summary.uncertainty_state == "ok"


def test_legacy_auto_behavior_unchanged() -> None:
    # The default "auto" aggregation must not gain temporal behavior.
    records = [
        _record(f"e{i}", source_id=f"s{i}", enterprise_id=f"ent-{i}", published=date(2026, 7, i))
        for i in range(1, 6)
    ]
    request = IndependenceRequest(
        subject_ref="POS",
        release_id="rel-1",
        coverage_status="covered",
    )
    summary = build_summary(records, request)
    assert summary.temporal_certificate is None
    assert summary.temporal_algorithm_version is None
    assert summary.cluster_staleness == ()
    assert not any(
        reason.startswith("temporal_") or reason in {"all_clusters_stale"}
        for reason in summary.uncertainty_reasons
    )


# ---- derived reasons ------------------------------------------------------------


def test_derive_temporal_reasons_flags_all_five() -> None:
    records = [
        _record(
            "e1",
            source_id="s1",
            published=date(2025, 1, 1),
            collected=datetime(2024, 12, 31, tzinfo=TZ),
            basis=CollectionTimeBasis.CRAWLER_ACQUIRED,
        ),
        _record(
            "e2",
            source_id="s2",
            published=date(2025, 2, 1),
            collected=datetime(2025, 2, 2, tzinfo=TZ),
        ),
        _collected("e3", datetime(2026, 6, 1, tzinfo=TZ), source_id="s3", enterprise_id="ent-3"),
        _collected("e4", datetime(2026, 6, 2, tzinfo=TZ), source_id="s4", enterprise_id="ent-4"),
        _record("e5", source_id="s5", enterprise_id="ent-5", published=None, collected=None),
    ]
    rules = TemporalFreshnessRules(stale_after_days=30)
    certificate = build_temporal_freshness(records, REF, rules)
    assert certificate.publish_time_coverage < 0.5
    # 4 stale + 1 unknown -> still above the high-stale-ratio gate
    assert certificate.stale_evidence_ratio > 0.75
    clusters = tuple((record.evidence_id,) for record in records)
    cluster_freshness = build_cluster_freshness(clusters, certificate)
    assert len(cluster_freshness) == len(records)
    states = cluster_staleness_states(clusters, certificate)
    reasons = derive_temporal_reasons(certificate, states, rules)
    assert "temporal_coverage_low" in reasons
    assert "source_lag_profile_insufficient" in reasons
    assert "high_stale_evidence_ratio" in reasons
    assert "temporal_anomaly_detected" in reasons
    assert "temporal_state_indeterminate" in reasons
