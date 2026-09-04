from __future__ import annotations

from datetime import datetime, timezone
from threading import Event

import pytest
from sqlalchemy import func, select

from app.api.schemas import CreateAnalysisRunRequest
from app.application.market_prediction import AllSourcesUnavailable, MarketPrediction
from app.application.position_skill_trend import PositionSkillTrend, _matching_skill, _skill_combo_shifts
from app.domain.market import ExtractedTerm, SourceRecord, week_start
from app.infrastructure.market_store import SqlAlchemyAnalysisDataStore
from app.infrastructure.models import (
    PositionSkillTrendResultModel,
    PredictionResultModel,
    SourceSnapshotModel,
)
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository


class StaticSource:
    def __init__(self, name, records=None, error=None):
        self.name = name
        self.records = records or []
        self.error = error

    def configure(self, _configurations):
        return None

    def collect(self, _start, _end):
        if self.error:
            raise self.error
        return self.records


class StaticExtractor:
    version = "test-extractor.v1"

    def extract(self, snapshot):
        return [ExtractedTerm(snapshot_id=snapshot.id, term="large language model", score=0.9, week_start=week_start(snapshot.record.published_at), extractor_version=self.version)]


def record(source="policy", external_id="one", content="人工智能 大模型", source_version="source.v1"):
    return SourceRecord(source=source, external_id=external_id, source_version=source_version, title="人工智能发展", content=content, url=f"https://example.test/{external_id}", published_at=datetime(2026, 1, 12, tzinfo=timezone.utc), metadata={"industry_domains": ["人工智能"], "keywords": ["大模型"], "signal_value": 1})


def make_run(repository, payload, request_id="request-market", key="idem-market", sources=None):
    value = dict(payload, request_id=request_id, idempotency_key=key, data_sources=sources or ["policy", "arxiv"], weights={"policy": 1.0, "academic": 1.0, "funding": 1.0, "github": 1.0})
    return repository.create_or_get(CreateAnalysisRunRequest.model_validate(value).to_command(), max_attempts=3)


def test_partial_source_failure_produces_honest_predictions_and_api_data(database, client, auth, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    run = make_run(repository, payload)
    pipeline = MarketPrediction(store, [StaticSource("policy", [record()]), StaticSource("arxiv", error=RuntimeError("upstream down"))], StaticExtractor(), credibility_store)
    pipeline.execute(run)
    report = store.source_report(run.id)
    assert report["source_coverage"] == 0.5
    assert report["missing_sources"] == ["arxiv"]
    assert report["sources"][0]["error"] == "RuntimeError: upstream down"
    predictions = store.predictions(run.id)
    assert predictions and "emergence_probability" not in predictions[0]
    assert {
        "partial_source_coverage", "limited_signal_diversity",
        "low_sample", "single_source_dominance",
    } <= set(predictions[0]["quality_flags"])
    assert client.get(f"/internal/v1/analysis-runs/{run.id}/sources", headers=auth).status_code == 200
    assert client.get(f"/internal/v1/analysis-runs/{run.id}/signals", headers=auth).json()["data"]
    assert client.get(f"/internal/v1/analysis-runs/{run.id}/predictions", headers=auth).json()["data"]
    explanation = client.get(
        f"/internal/v1/analysis-runs/{run.id}/predictions/{predictions[0]['id']}/explanation",
        headers=auth,
    ).json()["data"]
    assert explanation["explanation"]["source_contributions"]
    assert explanation["config_versions"]


def test_all_sources_failed_is_a_task_failure_condition(database, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    run = make_run(repository, payload)
    pipeline = MarketPrediction(store, [StaticSource("policy", error=RuntimeError("no policy")), StaticSource("arxiv", error=RuntimeError("no papers"))], StaticExtractor(), credibility_store)
    with pytest.raises(AllSourcesUnavailable):
        pipeline.execute(run)
    report = store.source_report(run.id)
    assert report["source_coverage"] == 0
    assert "no_sources_available" in report["quality_flags"]


def test_snapshot_and_result_writes_are_idempotent_and_history_is_preserved(database, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    pipeline = MarketPrediction(store, [StaticSource("policy", [record()])], StaticExtractor(), credibility_store)
    first = make_run(repository, payload, sources=["policy"])
    pipeline.execute(first)
    pipeline.execute(first)
    second = make_run(repository, payload, request_id="request-market-2", key="idem-market-2", sources=["policy"])
    pipeline.execute(second)
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceSnapshotModel)) == 1
        assert session.scalar(select(func.count()).select_from(PredictionResultModel).where(PredictionResultModel.analysis_run_id == first.id)) > 0
        assert session.scalar(select(func.count()).select_from(PredictionResultModel).where(PredictionResultModel.analysis_run_id == second.id)) > 0


def test_changed_content_creates_new_snapshot_and_prediction_evidence_exists(database, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    run = make_run(repository, payload, sources=["policy"])
    snapshots = store.save_snapshots(run.id, [record(), record(content="人工智能 大模型 新版本", source_version="source.v2")])
    assert len({item.id for item in snapshots}) == 2
    pipeline = MarketPrediction(store, [StaticSource("policy", [record()])], StaticExtractor(), credibility_store)
    pipeline.execute(run)
    evidence = {item for prediction in store.predictions(run.id) for item in prediction["evidence_snapshot_ids"]}
    with database.sessions() as session:
        existing = set(session.scalars(select(SourceSnapshotModel.id).where(SourceSnapshotModel.id.in_(evidence))))
    assert evidence == existing


class SkillTrendExtractor:
    version = "test-skill-extractor.v1"

    def extract(self, snapshot):
        values = ["JVM language", "unmapped orchestration"]
        return [
            ExtractedTerm(
                snapshot.id, value, 0.1,
                week_start(snapshot.record.published_at), self.version,
            )
            for value in values
        ]


def test_position_skill_matching_keeps_short_aliases_exact_and_long_aliases_bounded():
    c_skill = {"skill_id": "skill-c", "skill_name": "C"}
    java_skill = {"skill_id": "skill-java", "skill_name": "Java"}
    aliases = {"c": c_skill, "java": java_skill}

    assert _matching_skill("c", aliases) == c_skill
    assert _matching_skill("cvpr", aliases) is None
    assert _matching_skill("java backend engineering", aliases) == java_skill
    assert _matching_skill("javascript", aliases) is None


def test_skill_combo_shift_requires_non_empty_history_and_current_sides():
    assert _skill_combo_shifts([], ["skill-transformer"]) == []
    assert _skill_combo_shifts(["skill-cuda"], []) == []
    assert _skill_combo_shifts(["skill-cuda"], ["skill-cuda"]) == []
    assert _skill_combo_shifts(["skill-cuda"], ["skill-transformer"]) == [{
        "from_skill_ids": ["skill-cuda"],
        "to_skill_ids": ["skill-transformer"],
    }]


def test_position_skill_trend_maps_alias_to_main_skill_id_and_persists_unresolved(database, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    request = dict(
        payload,
        request_id="request-skill-trend",
        idempotency_key="idem-skill-trend",
        run_type="position_skill_trend",
        position_id="position-1",
        position_name="Java engineer",
        graph_version="graph-1",
        standard_skills=[{"skill_id": "skill-java", "skill_name": "Java", "aliases": ["JVM language"]}],
        skill_catalog_version="catalog-v1",
        config_version="config-v1",
        data_sources=["policy"],
        weights={"policy": 1.0},
    )
    run = repository.create_or_get(
        CreateAnalysisRunRequest.model_validate(request).to_command(), max_attempts=3
    )
    records = [
        SourceRecord(
            source="policy", external_id="history", source_version="v1",
            title="historical", content="old", url="https://example.test/history",
            published_at=datetime(2025, 12, 15, tzinfo=timezone.utc),
        ),
        SourceRecord(
            source="policy", external_id="current", source_version="v1",
            title="current", content="new", url="https://example.test/current",
            published_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        ),
    ]
    pipeline = PositionSkillTrend(
        store, [StaticSource("policy", records)], SkillTrendExtractor(), credibility_store
    )
    pipeline.execute(run)
    pipeline.execute(run)
    result = store.position_skill_trend(run.id)
    assert result is not None
    assert result["skill_trends"][0]["skill_id"] == "skill-java"
    assert result["skill_trends"][0]["evidence_count"] == 2
    assert result["unresolved_terms"][0]["term"] == "unmapped orchestration"
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PositionSkillTrendResultModel)) == 1


def test_position_skill_trend_does_not_emit_combo_shift_with_empty_history(database, payload, credibility_store):
    class CurrentOnlyExtractor:
        version = "current-only.v1"

        def extract(self, snapshot):
            term = "JVM language" if snapshot.record.external_id == "current" else "unmapped history"
            return [ExtractedTerm(snapshot.id, term, 0.9, week_start(snapshot.record.published_at), self.version)]

    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    request = dict(
        payload,
        request_id="request-empty-history-combo",
        idempotency_key="idem-empty-history-combo",
        run_type="position_skill_trend",
        position_id="position-1",
        position_name="Java engineer",
        graph_version="graph-1",
        standard_skills=[{"skill_id": "skill-java", "skill_name": "Java", "aliases": ["JVM language"]}],
        skill_catalog_version="catalog-v1",
        config_version="config-v1",
        data_sources=["policy"],
        weights={"policy": 1.0},
    )
    run = repository.create_or_get(CreateAnalysisRunRequest.model_validate(request).to_command(), max_attempts=3)
    records = [
        SourceRecord("policy", "history", "v1", "history", "old", "https://example.test/history", datetime(2025, 12, 15, tzinfo=timezone.utc)),
        SourceRecord("policy", "current", "v1", "current", "new", "https://example.test/current", datetime(2026, 1, 15, tzinfo=timezone.utc)),
    ]
    PositionSkillTrend(store, [StaticSource("policy", records)], CurrentOnlyExtractor(), credibility_store).execute(run)
    result = store.position_skill_trend(run.id)
    assert result is not None
    assert result["skill_trends"][0]["historical_window_signal"] == 0
    assert result["skill_trends"][0]["current_window_signal"] > 0
    assert result["skill_combo_shifts"] == []


def test_position_skill_trend_collects_sources_concurrently_and_keeps_end_boundary(database, payload, credibility_store):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    store = SqlAlchemyAnalysisDataStore(database.sessions)
    policy_ready = Event()
    funding_ready = Event()

    class CoordinatedSource(StaticSource):
        def __init__(self, name, own_ready, other_ready):
            super().__init__(name)
            self.own_ready = own_ready
            self.other_ready = other_ready

        def collect(self, _start, end):
            self.own_ready.set()
            if not self.other_ready.wait(1):
                raise RuntimeError("sources were collected serially")
            return [SourceRecord(
                source=self.name, external_id=f"{self.name}-end", source_version="source.v1",
                title="JVM language", content="JVM language", url=f"https://example.test/{self.name}",
                published_at=end,
            )]

    request = dict(
        payload,
        request_id="request-concurrent-skill-trend",
        idempotency_key="idem-concurrent-skill-trend",
        run_type="position_skill_trend",
        position_id="position-concurrent",
        position_name="Java engineer",
        graph_version="graph-concurrent",
        standard_skills=[{"skill_id": "skill-java", "skill_name": "Java", "aliases": ["JVM language"]}],
        skill_catalog_version="catalog-v1",
        config_version="config-v1",
        data_sources=["policy", "funding"],
        weights={"policy": 1.0, "funding": 1.0},
    )
    run = repository.create_or_get(
        CreateAnalysisRunRequest.model_validate(request).to_command(), max_attempts=3
    )
    pipeline = PositionSkillTrend(
        store,
        [
            CoordinatedSource("policy", policy_ready, funding_ready),
            CoordinatedSource("funding", funding_ready, policy_ready),
        ],
        SkillTrendExtractor(), credibility_store, source_workers=2,
    )

    pipeline.execute(run)

    result = store.position_skill_trend(run.id)
    assert result is not None
    assert result["source_coverage"] == 1
    assert len(result["skill_trends"][0]["evidence_references"]) == 2
