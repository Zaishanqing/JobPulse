from __future__ import annotations

from datetime import datetime, timezone

from app.api.schemas import CreateAnalysisRunRequest
from app.application.market_prediction import MarketPrediction
from app.domain.market import ExtractedTerm, SourceRecord, week_start
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository
from app.infrastructure.source_governance import SqlAlchemySourceGovernanceStore


class StaticSource:
    def __init__(self, name, records):
        self.name = name
        self.records = records

    def configure(self, _configurations):
        return None

    def collect(self, _start, _end):
        return self.records


class StaticExtractor:
    version = "governance-test.v1"

    def extract(self, snapshot):
        return [ExtractedTerm(snapshot.id, "large language model", 0.1,
                              week_start(snapshot.record.published_at), self.version)]


def record():
    return SourceRecord(
        source="policy", external_id="governed", source_version="v1", title="AI policy",
        content="large language model", url="https://example.test/policy",
        published_at=datetime(2026, 1, 12, tzinfo=timezone.utc),
        metadata={"industry_domains": ["人工智能"]},
    )


def make_run(repository, payload, *, request_id, key, sources):
    value = {**payload, "request_id": request_id, "idempotency_key": key,
             "data_sources": sources, "weights": {source: 1.0 for source in sources}}
    return repository.create_or_get(CreateAnalysisRunRequest.model_validate(value).to_command(), max_attempts=3)


def _create_sample(client, auth, *, observed_at="2026-10-01T00:00:00Z"):
    dataset = client.post("/internal/v1/evaluation-datasets", headers=auth, json={
        "name": "governed-outcomes", "version": "v1", "description": "Observed outcomes",
        "created_by": "curator",
    }).json()["data"]
    sample = client.post(
        f"/internal/v1/evaluation-datasets/{dataset['id']}/samples/generate", headers=auth,
        json={"source_type": "published_position_graph", "actor": "curator", "records": [{
            "entity_type": "skill", "entity_id": "skill-llm", "entity_name": "LLM",
            "prediction_cutoff": "2026-08-01T00:00:00Z",
            "label_window_start": "2026-08-02T00:00:00Z",
            "label_window_end": "2026-11-01T00:00:00Z",
            "source_reference": "graph://published/v1", "source_dedup_key": "graph-skill-llm-v1",
            "evidence": [{"observed_at": observed_at, "content_hash": "a" * 64}],
        }]},
    ).json()["data"][0]
    return dataset, sample


def test_dataset_two_person_review_conflict_history_revision_and_immutability(client, auth):
    dataset, sample = _create_sample(client, auth)
    first = client.post(f"/internal/v1/evaluation-samples/{sample['id']}/labels", headers=auth, json={
        "label_type": "skill_change", "direction": "rising", "observed_value": 0.4,
        "evidence": [{"observed_at": "2026-10-10T00:00:00Z"}],
        "confidence_level": "high", "annotator_id": "annotator-a",
    }).json()["data"]
    second = client.post(f"/internal/v1/evaluation-samples/{sample['id']}/labels", headers=auth, json={
        "label_type": "skill_change", "direction": "declining", "observed_value": -0.2,
        "evidence": [{"observed_at": "2026-10-11T00:00:00Z"}],
        "confidence_level": "medium", "annotator_id": "annotator-b",
    }).json()["data"]
    assert second["status"] == "conflict"
    assert client.post(f"/internal/v1/evaluation-labels/{first['id']}/review", headers=auth, json={
        "decision": "approve", "reviewer_id": "annotator-a",
    }).status_code == 409
    approved = client.post(f"/internal/v1/evaluation-labels/{first['id']}/review", headers=auth, json={
        "decision": "approve", "reviewer_id": "reviewer-c", "review_note": "source checked",
    })
    assert approved.status_code == 200
    published = client.post(f"/internal/v1/evaluation-datasets/{dataset['id']}/publish", headers=auth,
                            json={"actor": "publisher"}).json()["data"]
    assert published["status"] == "published"
    immutable = client.post(
        f"/internal/v1/evaluation-datasets/{dataset['id']}/samples/generate", headers=auth,
        json={"source_type": "historical_hiring", "actor": "curator", "records": [{
            "entity_type": "skill", "entity_id": "new", "entity_name": "new",
            "prediction_cutoff": "2026-08-01T00:00:00Z", "label_window_start": "2026-08-02T00:00:00Z",
            "label_window_end": "2026-11-01T00:00:00Z", "source_reference": "hiring://new",
        }]},
    )
    assert immutable.status_code == 409
    revision = client.post(f"/internal/v1/evaluation-datasets/{dataset['id']}/revisions", headers=auth,
                           json={"version": "v2", "actor": "curator-2"}).json()["data"]
    assert revision["parent_dataset_id"] == dataset["id"] and revision["status"] == "draft"
    history = client.get(f"/internal/v1/evaluation-datasets/{dataset['id']}/history", headers=auth).json()["data"]
    assert {item["action"] for item in history} >= {
        "dataset_created", "sample_generated", "label_conflict_detected",
        "label_conflict_resolved", "dataset_published",
    }


def test_snapshot_replay_health_metrics_and_circuit_breaker(database, client, auth, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    governance = SqlAlchemySourceGovernanceStore(database.sessions)
    original = make_run(repository, payload, request_id="cache-original", key="cache-original", sources=["policy"])
    MarketPrediction(
        store, [StaticSource("policy", [record()])], StaticExtractor(), credibility_store, governance,
    ).execute(original)
    replay = client.post(f"/internal/v1/analysis-runs/{original.id}/replay", headers=auth, json={
        "request_id": "cache-replay", "idempotency_key": "cache-replay",
    }).json()["data"]
    replay_run = repository.get(replay["id"])
    MarketPrediction(store, [], StaticExtractor(), credibility_store, governance).execute(replay_run)
    assert store.predictions(replay_run.id)
    health = client.get("/internal/v1/source-health", headers=auth,
                        params={"source": "policy"}).json()["data"][0]
    assert health["attempts"] == 2
    assert health["success_rate"] == 1.0
    assert health["field_completeness"] == 1.0

    failed_run = make_run(repository, payload, request_id="circuit-run", key="circuit-run", sources=["github"])
    now = datetime.now(timezone.utc)
    for _ in range(3):
        governance.record_attempt(
            run_id=failed_run.id, source="github", status="failed", duration_ms=10,
            records=[], error_type="TimeoutError", window_end=now,
            failure_threshold=3, open_seconds=300,
        )
    assert governance.circuit_allows("github", now) is False
    github = governance.source_health("github")[0]
    assert github["circuit_state"] == "open"
    assert github["error_types"] == ["TimeoutError"]


def test_backtest_rejects_pre_cutoff_label_evidence(client, auth):
    dataset, sample = _create_sample(client, auth, observed_at="2026-07-01T00:00:00Z")
    label = client.post(f"/internal/v1/evaluation-samples/{sample['id']}/labels", headers=auth, json={
        "label_type": "skill_change", "direction": "rising",
        "evidence": [{"observed_at": "2026-07-15T00:00:00Z"}],
        "confidence_level": "medium", "annotator_id": "annotator",
    }).json()["data"]
    client.post(f"/internal/v1/evaluation-labels/{label['id']}/review", headers=auth,
                json={"decision": "approve", "reviewer_id": "reviewer"})
    client.post(f"/internal/v1/evaluation-datasets/{dataset['id']}/publish", headers=auth,
                json={"actor": "publisher"})
    result = client.post("/internal/v1/backtests", headers=auth, json={
        "request_id": "leakage", "dataset_id": dataset["id"], "dataset_version": "v1", "k": 1,
        "time_slices": [{"slice_key": "leak", "observation_cutoff": "2026-08-01T00:00:00Z",
                         "validation_end": "2026-12-01T00:00:00Z"}],
    })
    assert result.status_code == 409
    assert "leak" in result.json()["detail"]
