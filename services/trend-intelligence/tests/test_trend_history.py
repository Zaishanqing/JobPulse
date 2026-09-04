from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.trend_change import TrendChangeService
from app.application.trend_history import BuildTrendHistoricalSequence
from app.infrastructure.models import (
    AnalysisRunModel,
    Base,
    PositionSkillTrendResultModel,
    PredictionResultModel,
)
from app.infrastructure.trend_history_store import SqlAlchemyTrendHistoryStore


@pytest.fixture
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def seed_run(
    sessions,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    status: str = "succeeded",
    run_type: str = "market_prediction",
    completed_at: datetime | None = None,
) -> None:
    with sessions.begin() as session:
        session.add(
            AnalysisRunModel(
                id=run_id,
                contract_version="trend-analysis.v2",
                request_id=f"request-{run_id}",
                status=status,
                window_start=window_start,
                window_end=window_end,
                data_sources=["policy"],
                weights={"policy": 1.0},
                algorithm_version="alg.v1",
                config_version="config.v1",
                formula_version="formula.v1",
                run_type=run_type,
                run_payload={},
                completed_at=completed_at,
            )
        )


def seed_prediction(
    sessions,
    run_id: str,
    job_name: str,
    score: float,
    *,
    report_id: str | None = None,
    source_scores: dict[str, float] | None = None,
    record_ids: list[str] | None = None,
    algorithm_version: str = "alg.v1",
) -> None:
    run = None
    with sessions() as session:
        run = session.get(AnalysisRunModel, run_id)
    if run is None:
        raise AssertionError(f"run {run_id} must be seeded first")
    with sessions.begin() as session:
        session.add(
            PredictionResultModel(
                id=report_id or f"report-{run_id}",
                analysis_run_id=run_id,
                job_name=job_name,
                industry_domain="人工智能/互联网",
                emergence_score=score,
                source_scores=source_scores or {"policy": 1.0},
                related_keywords=[],
                evidence_snapshot_ids=record_ids or [],
                algorithm_version=algorithm_version,
                formula_version="formula.v1",
                window_start=run.window_start,
                window_end=run.window_end,
                source_coverage=1.0,
                missing_sources=[],
                quality_flags=[],
                config_versions={},
                score_explanation={},
            )
        )


def seed_skill_result(
    sessions,
    run_id: str,
    skill_id: str,
    score: float,
    *,
    report_id: str | None = None,
    source_contributions: dict[str, float] | None = None,
    record_ids: list[str] | None = None,
) -> None:
    run = None
    with sessions() as session:
        run = session.get(AnalysisRunModel, run_id)
    if run is None:
        raise AssertionError(f"run {run_id} must be seeded first")
    with sessions.begin() as session:
        session.add(
            PositionSkillTrendResultModel(
                id=report_id or f"report-{run_id}",
                analysis_run_id=run_id,
                position_id="position-1",
                position_name="Java engineer",
                graph_version="graph.v1",
                skill_catalog_version="catalog.v1",
                algorithm_version="alg.v1",
                formula_version="formula.v1",
                config_version="config.v1",
                result_payload={
                    "skill_trends": [
                        {
                            "skill_id": skill_id,
                            "trend_score": score,
                            "evidence_references": record_ids or [],
                            "score_explanation": {
                                "source_contributions": source_contributions or {}
                            },
                        }
                    ]
                },
            )
        )


def store(sessions) -> SqlAlchemyTrendHistoryStore:
    return SqlAlchemyTrendHistoryStore(sessions)


def builder(sessions) -> BuildTrendHistoricalSequence:
    return BuildTrendHistoricalSequence(store(sessions))


class FakeTrendChangeStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        analysis_id = f"analysis-{len(self.records) + 1}"
        record = {"analysis_id": analysis_id, **payload}
        self.records[analysis_id] = record
        return record

    def get(self, analysis_id: str) -> dict[str, object] | None:
        return self.records.get(analysis_id)


def test_sql_store_assembles_job_sequence_oldest_to_newest(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_run(sessions, "run-2", datetime(2026, 2, 1), datetime(2026, 2, 8), completed_at=datetime(2026, 2, 9))
    seed_run(sessions, "run-3", datetime(2026, 3, 1), datetime(2026, 3, 8), completed_at=datetime(2026, 3, 9))
    seed_prediction(sessions, "run-1", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-2", "AI大模型训练师", 0.45)
    seed_prediction(sessions, "run-3", "AI大模型训练师", 0.6)

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert [window.window for window in windows] == [
        "2026-01-01T00:00:00",
        "2026-02-01T00:00:00",
        "2026-03-01T00:00:00",
    ]
    assert [window.score for window in windows] == [0.2, 0.45, 0.6]


def test_same_window_dedup_chooses_latest_completed_and_run_id_tie_break(sessions):
    seed_run(
        sessions,
        "run-old",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        completed_at=datetime(2026, 1, 9),
    )
    seed_run(
        sessions,
        "run-new",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        completed_at=datetime(2026, 1, 10),
    )
    seed_prediction(sessions, "run-old", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-new", "AI大模型训练师", 0.45)

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert len(windows) == 1
    assert windows[0].analysis_run_id == "run-new"
    assert windows[0].score == 0.45


def test_same_completed_at_uses_run_id_descending_tie_break(sessions):
    seed_run(
        sessions,
        "run-a",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        completed_at=datetime(2026, 1, 9),
    )
    seed_run(
        sessions,
        "run-b",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        completed_at=datetime(2026, 1, 9),
    )
    seed_prediction(sessions, "run-a", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-b", "AI大模型训练师", 0.45)

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert windows[0].analysis_run_id == "run-b"


def test_failed_runs_are_excluded_from_sequence(sessions):
    seed_run(
        sessions,
        "run-ok",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        completed_at=datetime(2026, 1, 9),
    )
    seed_run(
        sessions,
        "run-failed",
        datetime(2026, 2, 1),
        datetime(2026, 2, 8),
        status="failed",
        completed_at=datetime(2026, 2, 9),
    )
    seed_prediction(sessions, "run-ok", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-failed", "AI大模型训练师", 0.9)

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert [window.analysis_run_id for window in windows] == ["run-ok"]


def test_wrong_subject_does_not_mix_into_sequence(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_run(sessions, "run-2", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_prediction(sessions, "run-1", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-2", "量子算法工程师", 0.9)

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert [window.subject_id for window in windows] == ["AI大模型训练师"]
    assert len(windows) == 1


def test_time_range_filters_sequence(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_run(sessions, "run-2", datetime(2026, 2, 1), datetime(2026, 2, 8), completed_at=datetime(2026, 2, 9))
    seed_run(sessions, "run-3", datetime(2026, 3, 1), datetime(2026, 3, 8), completed_at=datetime(2026, 3, 9))
    for run_id, score in (("run-1", 0.2), ("run-2", 0.45), ("run-3", 0.6)):
        seed_prediction(sessions, run_id, "AI大模型训练师", score)

    windows = builder(sessions).build(
        "AI大模型训练师",
        "job",
        from_time=datetime(2026, 2, 1),
        to_time=datetime(2026, 3, 1),
    )

    assert [window.window for window in windows] == [
        "2026-02-01T00:00:00",
        "2026-03-01T00:00:00",
    ]


def test_missing_window_is_not_filled_with_zero(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_run(sessions, "run-3", datetime(2026, 3, 1), datetime(2026, 3, 8), completed_at=datetime(2026, 3, 9))
    seed_prediction(sessions, "run-1", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-3", "AI大模型训练师", 0.6)

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert len(windows) == 2
    assert windows[0].window == "2026-01-01T00:00:00"
    assert windows[1].window == "2026-03-01T00:00:00"


def test_skill_sequence_is_assembled_from_position_skill_trend_results(sessions):
    seed_run(
        sessions,
        "run-1",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        run_type="position_skill_trend",
        completed_at=datetime(2026, 1, 9),
    )
    seed_run(
        sessions,
        "run-2",
        datetime(2026, 2, 1),
        datetime(2026, 2, 8),
        run_type="position_skill_trend",
        completed_at=datetime(2026, 2, 9),
    )
    seed_skill_result(sessions, "run-1", "skill-java", 0.3)
    seed_skill_result(sessions, "run-2", "skill-java", 0.6)

    windows = builder(sessions).build("skill-java", "skill")

    assert [window.score for window in windows] == [0.3, 0.6]
    assert [window.analysis_run_id for window in windows] == ["run-1", "run-2"]


def test_lineage_fields_are_preserved_on_window_scores(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_prediction(
        sessions,
        "run-1",
        "AI大模型训练师",
        0.42,
        report_id="report-1",
        source_scores={"policy": 0.4, "github": 0.2},
        record_ids=["snapshot-a", "snapshot-b"],
    )

    windows = builder(sessions).build("AI大模型训练师", "job")

    window = windows[0]
    assert window.analysis_run_id == "run-1"
    assert window.trend_report_id == "report-1"
    assert window.algorithm_version == "alg.v1"
    assert window.config_version == "config.v1"
    assert window.source_records == ("snapshot-a", "snapshot-b")
    assert window.evidence_ids == ("snapshot-a", "snapshot-b")
    assert window.source_count == 2
    assert window.source_diversity == 2
    assert window.duration_days == 7.0


def test_history_service_raises_insufficient_and_not_found(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_prediction(sessions, "run-1", "AI大模型训练师", 0.2)
    service = TrendChangeService(
        FakeTrendChangeStore(),
        history_builder=builder(sessions),
    )

    with pytest.raises(ValueError, match="INSUFFICIENT_HISTORY"):
        service.analyze_from_history(
            {"subject_id": "AI大模型训练师", "subject_type": "job"}
        )
    with pytest.raises(ValueError, match="HISTORY_NOT_FOUND"):
        service.analyze_from_history(
            {"subject_id": "不存在岗位", "subject_type": "job"}
        )


def test_sequence_to_change_analysis_full_call(sessions):
    for index, score in enumerate([0.20, 0.21, 0.19, 0.45, 0.60, 0.68], start=1):
        month = index
        window_start = datetime(2026, month, 1)
        window_end = datetime(2026, month, 8)
        run_id = f"run-{index}"
        seed_run(
            sessions,
            run_id,
            window_start,
            window_end,
            completed_at=datetime(2026, month, 9),
        )
        seed_prediction(sessions, run_id, "AI大模型训练师", score)
    service = TrendChangeService(
        FakeTrendChangeStore(),
        history_builder=builder(sessions),
    )

    result = service.analyze_from_history(
        {"subject_id": "AI大模型训练师", "subject_type": "job"}
    )

    subject = result["subjects"][0]
    assert subject["sequence_source"] == "persisted_trend_history"
    assert subject["window_count"] == 6
    assert len(subject["included_run_ids"]) == 6
    assert len(subject["included_report_ids"]) == 6
    assert subject["trend_state"] == "rising"
    assert len(subject["change_points"]) == 1
    assert subject["change_points"][0]["change_point_window"] == "2026-04-01T00:00:00"


def test_different_algorithm_versions_preserve_both_windows(sessions):
    seed_run(
        sessions,
        "run-v1",
        datetime(2026, 1, 1),
        datetime(2026, 1, 8),
        completed_at=datetime(2026, 1, 9),
    )
    run_v2_id = "run-v2"
    with sessions.begin() as s:
        s.add(AnalysisRunModel(
            id=run_v2_id,
            contract_version="trend-analysis.v2",
            request_id="request-run-v2",
            status="succeeded",
            window_start=datetime(2026, 1, 1),
            window_end=datetime(2026, 1, 14),
            data_sources=["policy"],
            weights={"policy": 1.0},
            algorithm_version="alg.v2",
            config_version="config.v2",
            formula_version="formula.v1",
            run_type="market_prediction",
            run_payload={},
            completed_at=datetime(2026, 1, 10),
        ))
    seed_prediction(sessions, "run-v1", "AI大模型训练师", 0.2)
    seed_prediction(sessions, run_v2_id, "AI大模型训练师", 0.45, algorithm_version="alg.v2")

    windows = builder(sessions).build("AI大模型训练师", "job")

    assert len(windows) == 2
    versions = {w.algorithm_version for w in windows}
    assert versions == {"alg.v1", "alg.v2"}
    config_versions = {w.config_version for w in windows}
    assert config_versions == {"config.v1", "config.v2"}


def test_analyze_from_history_rejects_incompatible_versions(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    with sessions.begin() as s:
        s.add(AnalysisRunModel(
            id="run-2",
            contract_version="trend-analysis.v2",
            request_id="request-run-2",
            status="succeeded",
            window_start=datetime(2026, 2, 1),
            window_end=datetime(2026, 2, 8),
            data_sources=["policy"],
            weights={"policy": 1.0},
            algorithm_version="alg.v2",
            config_version="config.v1",
            formula_version="formula.v1",
            run_type="market_prediction",
            run_payload={},
            completed_at=datetime(2026, 2, 9),
        ))
    seed_prediction(sessions, "run-1", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-2", "AI大模型训练师", 0.45, algorithm_version="alg.v2")
    service = TrendChangeService(FakeTrendChangeStore(), history_builder=builder(sessions))

    with pytest.raises(ValueError, match="TREND_SERIES_VERSION_INCOMPATIBLE"):
        service.analyze_from_history(
            {"subject_id": "AI大模型训练师", "subject_type": "job"}
        )


def test_filter_result_includes_global_summary_metadata():
    service = TrendChangeService(FakeTrendChangeStore())
    request = {
        "request_id": "r1",
        "subjects": [
            {
                "subject_id": "s1",
                "subject_type": "market_signal",
                "windows": [
                    {"window": f"w{i}", "score": s}
                    for i, s in enumerate([0.20, 0.21, 0.45, 0.60], start=1)
                ],
            }
        ],
    }
    created = service.analyze(request)
    analysis_id = str(created["analysis_id"])

    full = service.get(analysis_id)
    assert "filter" not in full

    filtered = service.get(analysis_id, window="w3")
    assert "filter" in filtered
    assert filtered["filter"]["summary_scope"] == "global"
    assert len(filtered["subjects"][0]["windows"]) == 1
    assert isinstance(filtered["subjects"][0]["trend_state"], str)


def test_same_database_input_is_deterministic(sessions):
    seed_run(sessions, "run-1", datetime(2026, 1, 1), datetime(2026, 1, 8), completed_at=datetime(2026, 1, 9))
    seed_run(sessions, "run-2", datetime(2026, 2, 1), datetime(2026, 2, 8), completed_at=datetime(2026, 2, 9))
    seed_prediction(sessions, "run-1", "AI大模型训练师", 0.2)
    seed_prediction(sessions, "run-2", "AI大模型训练师", 0.45)
    sequence_builder = builder(sessions)

    first = sequence_builder.build("AI大模型训练师", "job")
    second = sequence_builder.build("AI大模型训练师", "job")

    assert [window.window for window in first] == [window.window for window in second]
    assert [window.score for window in first] == [window.score for window in second]
    assert [window.analysis_run_id for window in first] == [
        window.analysis_run_id for window in second
    ]


def test_limit_keeps_newest_windows(sessions):
    for month, score in ((1, 0.2), (2, 0.45), (3, 0.6)):
        run_id = f"run-{month}"
        seed_run(
            sessions,
            run_id,
            datetime(2026, month, 1),
            datetime(2026, month, 8),
            completed_at=datetime(2026, month, 9),
        )
        seed_prediction(sessions, run_id, "AI大模型训练师", score)

    windows = builder(sessions).build("AI大模型训练师", "job", limit=2)

    assert [window.window for window in windows] == [
        "2026-02-01T00:00:00",
        "2026-03-01T00:00:00",
    ]
