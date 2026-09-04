from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.domain.credibility import quality_flags, ranking_metrics
from app.domain.market import ExtractedTerm, SourceRecord, week_start
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore
from app.infrastructure.models import BacktestRunModel, BacktestSliceResultModel, SourceSnapshotModel


UTC = timezone.utc


def test_configuration_lifecycle_history_comparison_and_analysis_version_recording(client, auth, payload):
    initial = client.get("/internal/v1/configurations", headers=auth).json()["data"]
    assert {item["config_type"] for item in initial} == {
        "job_knowledge", "policy_keywords", "domain_dictionary",
        "github_topics", "trend_thresholds",
    }
    thresholds = next(item for item in initial if item["config_type"] == "trend_thresholds")
    created = client.post(
        "/internal/v1/configurations", headers=auth,
        json={
            "config_type": "trend_thresholds", "version": "trend-thresholds.candidate-v2",
            "payload": {**thresholds["payload"], "low_sample_count": 5},
            "created_by": "offline-evaluator",
        },
    ).json()["data"]
    assert created["status"] == "draft"
    assert client.get("/internal/v1/configurations").status_code == 401
    versions = client.get(
        "/internal/v1/configurations", params={"config_type": "trend_thresholds"}, headers=auth
    ).json()["data"]
    assert {item["version"] for item in versions} == {
        "trend-thresholds.v1", "trend-thresholds.candidate-v2",
    }
    assert client.post(
        f"/internal/v1/configurations/{created['id']}/enable", headers=auth,
        json={"actor": "reviewer-1"},
    ).status_code == 422
    enabled = client.post(
        f"/internal/v1/configurations/{created['id']}/enable", headers=auth,
        json={"actor": "reviewer-1", "review_note": "offline evidence reviewed"},
    ).json()["data"]
    assert enabled["status"] == "active"
    old = client.get(
        f"/internal/v1/configurations/{thresholds['id']}", headers=auth
    ).json()["data"]
    assert old["status"] == "inactive"
    history = client.get(
        f"/internal/v1/configurations/{created['id']}/history", headers=auth
    ).json()["data"]
    assert [item["action"] for item in history] == ["created", "enable"]
    comparison = client.get(
        "/internal/v1/configurations/compare",
        params={"left_id": thresholds["id"], "right_id": created["id"]}, headers=auth,
    ).json()["data"]
    assert comparison["changes"] == [{"key": "low_sample_count", "left": 3, "right": 5}]

    run = client.post(
        "/internal/v1/analysis-runs", headers=auth,
        json={**payload, "request_id": "config-recorded", "idempotency_key": "config-recorded"},
    ).json()["data"]
    assert run["config_versions"]["trend_thresholds"] == "trend-thresholds.candidate-v2"
    assert run["run_payload"]["config_versions"] == run["config_versions"]
    disabled = client.post(
        f"/internal/v1/configurations/{created['id']}/disable", headers=auth,
        json={"actor": "reviewer-1", "review_note": "retired after analysis"},
    ).json()["data"]
    assert disabled["status"] == "inactive"


def _seed_historical_terms(database, run_id: str) -> None:
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    record = SourceRecord(
        source="policy", external_id="historical-ai", source_version="v1",
        title="大模型岗位政策", content="large language model", url="https://example.test/policy",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        metadata={"industry_domains": ["人工智能"]},
    )
    snapshot = store.save_snapshots(run_id, [record])[0]
    store.save_terms([ExtractedTerm(
        snapshot.id, "large language model", 0.1,
        week_start(record.published_at), "backtest-test.v1",
    )])
    with database.sessions.begin() as session:
        session.get(SourceSnapshotModel, snapshot.id).captured_at = datetime(
            2026, 7, 1, tzinfo=UTC
        )
        future = SourceSnapshotModel(
            first_seen_run_id=run_id, source="github", external_id="future-capture",
            source_version="v1", title="future", content="llm",
            url="https://example.test/future", normalized_url="https://example.test/future",
            source_type="opensource", published_at=datetime(2026, 7, 2, tzinfo=UTC),
            date_precision="exact", content_completeness="full",
            captured_at=datetime(2026, 9, 1, tzinfo=UTC), snapshot_metadata={},
        )
        session.add(future)
        session.flush()
        from app.infrastructure.models import ExtractedTermModel
        session.add(ExtractedTermModel(
            snapshot_id=future.id, term="large language model", score=0.1,
            week_start=datetime(2026, 7, 2, tzinfo=UTC).date(), extractor_version="future.v1",
        ))


def test_backtest_is_idempotent_leakage_safe_and_persists_honest_metrics(database, client, auth, payload):
    analysis = client.post(
        "/internal/v1/analysis-runs", headers=auth,
        json={**payload, "request_id": "backtest-source", "idempotency_key": "backtest-source"},
    ).json()["data"]
    _seed_historical_terms(database, analysis["id"])
    dataset = client.post("/internal/v1/evaluation-datasets", headers=auth, json={
        "name": "real-outcomes", "version": "2026-q3.v1", "created_by": "curator",
    }).json()["data"]
    sample = client.post(
        f"/internal/v1/evaluation-datasets/{dataset['id']}/samples/generate", headers=auth,
        json={"source_type": "historical_hiring", "actor": "curator", "records": [{
            "entity_type": "position", "entity_id": "position-ai-trainer",
            "entity_name": "AI大模型训练师", "prediction_cutoff": "2026-08-01T00:00:00Z",
            "label_window_start": "2026-08-02T00:00:00Z", "label_window_end": "2026-11-01T00:00:00Z",
            "source_reference": "hiring://2026-q3", "evidence": [{
                "observed_at": "2026-10-01T00:00:00Z", "content_hash": "e" * 64,
            }],
        }]},
    ).json()["data"][0]
    label = client.post(f"/internal/v1/evaluation-samples/{sample['id']}/labels", headers=auth, json={
        "label_type": "position_change", "direction": "rising", "observed_value": 0.3,
        "evidence": [{"observed_at": "2026-10-15T00:00:00Z", "content_hash": "d" * 64}],
        "confidence_level": "high", "annotator_id": "annotator-1",
    }).json()["data"]
    assert client.post(f"/internal/v1/evaluation-labels/{label['id']}/review", headers=auth, json={
        "decision": "approve", "reviewer_id": "reviewer-1", "review_note": "verified",
    }).status_code == 200
    assert client.post(f"/internal/v1/evaluation-datasets/{dataset['id']}/publish", headers=auth,
                       json={"actor": "publisher-1"}).status_code == 200
    request = {
        "request_id": "backtest-001", "idempotency_key": "backtest-idem-001", "k": 1,
        "dataset_id": dataset["id"], "dataset_version": dataset["version"],
            "time_slices": [{
                "slice_key": "2026-q3", "observation_cutoff": "2026-08-01T00:00:00Z",
                "validation_end": "2026-12-01T00:00:00Z",
                "weights": {"policy": 1.0, "github": 1.0},
                "weight_variants": [{"policy": 0.5, "github": 1.0}],
            }, {
                "slice_key": "2026-q2-late", "observation_cutoff": "2026-07-15T00:00:00Z",
                "validation_end": "2026-12-01T00:00:00Z",
                "weights": {"policy": 1.0, "github": 1.0},
                "weight_variants": [{"policy": 0.5, "github": 1.0}],
            }],
    }
    first = client.post("/internal/v1/backtests", headers=auth, json=request)
    second = client.post("/internal/v1/backtests", headers=auth, json=request)
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert first.json()["data"]["status"] == "succeeded"
    metrics = client.get(
        f"/internal/v1/backtests/{first.json()['data']['id']}/metrics", headers=auth
    ).json()["data"]["slices"][0]
    # With fixed role priors removed, this slice is an explicit failure case:
    # domain evidence alone cannot rank the labelled role first.
    assert metrics["metrics"]["precision_at_1"] == 0.0
    assert metrics["metrics"]["recall_at_1"] == 0.0
    assert "policy" in metrics["source_ablation"]
    assert "weights_1" in metrics["stability"]
    top = metrics["predictions"][0]
    assert top["source_contributions"].get("policy") == 1.0
    assert "github" not in top["source_contributions"]  # captured after cutoff: excluded
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(BacktestRunModel)) == 1
        assert session.scalar(select(func.count()).select_from(BacktestSliceResultModel)) == 1

    changed = {**request, "k": 2}
    changed_response = client.post("/internal/v1/backtests", headers=auth, json=changed)
    assert changed_response.status_code == 202
    assert changed_response.json()["data"]["id"] == first.json()["data"]["id"]


def test_metrics_quality_flags_and_backtest_transaction_rollback(database, credibility_store):
    metrics = ranking_metrics(
        [{"candidate_key": "a", "direction": "rising"}, {"candidate_key": "b", "direction": "stable"}],
        [{"candidate_key": "a", "direction": "rising"}, {"candidate_key": "b", "direction": "declining"}],
        k=2,
    )
    assert metrics == {
        "precision_at_2": 1.0, "recall_at_2": 1.0,
        "ranking_correlation": 1.0, "direction_accuracy": 0.5,
    }
    flags = quality_flags(
        evidence_count=1, source_contributions={"policy": 1, "github": -0.1},
        evidence_age_days=365, growth_rate=9,
        thresholds=credibility_store.payloads(credibility_store.active_versions())["trend_thresholds"],
    )
    assert {"low_sample", "single_source_dominance", "stale_evidence", "source_conflict"} <= set(flags)

    request = {"request_id": "rollback", "idempotency_key": "rollback", "dataset_id": "dataset",
               "dataset_version": "v1", "k": 1, "time_slices": []}
    run, _ = credibility_store.create_backtest(request, credibility_store.active_versions())
    duplicate = {
        "slice_key": "same", "observation_cutoff": datetime(2026, 1, 1, tzinfo=UTC),
        "validation_end": datetime(2026, 2, 1, tzinfo=UTC), "predictions": [],
        "ground_truth": [], "metrics": {}, "ablation_results": {},
        "stability_results": {}, "quality_flags": [],
    }
    with pytest.raises(IntegrityError):
        credibility_store.save_backtest_results(str(run["id"]), [duplicate, duplicate])
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(BacktestSliceResultModel)) == 0
        assert session.get(BacktestRunModel, run["id"]).status == "pending"
