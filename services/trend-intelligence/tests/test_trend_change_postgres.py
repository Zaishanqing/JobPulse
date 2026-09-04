from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.application.trend_change import TrendChangeService
from app.application.trend_history import BuildTrendHistoricalSequence
from app.infrastructure.models import (
    AnalysisRunModel,
    PositionSkillTrendResultModel,
    PredictionResultModel,
    TrendChangeAnalysisModel,
)
from app.infrastructure.trend_change_store import SqlAlchemyTrendChangeStore
from app.infrastructure.trend_history_store import SqlAlchemyTrendHistoryStore


UTC = timezone.utc


def seed_run(
    database,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    status: str = "succeeded",
    run_type: str = "market_prediction",
    completed_at: datetime | None = None,
) -> None:
    with database.sessions.begin() as session:
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
                formula_version="formula.v1",
                run_type=run_type,
                run_payload={},
                completed_at=completed_at,
            )
        )


def seed_prediction(
    database,
    run_id: str,
    job_name: str,
    score: float,
    *,
    report_id: str | None = None,
    source_scores: dict[str, float] | None = None,
    record_ids: list[str] | None = None,
) -> None:
    with database.sessions() as session:
        run = session.get(AnalysisRunModel, run_id)
    if run is None:
        raise AssertionError(f"run {run_id} must be seeded first")
    with database.sessions.begin() as session:
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
                algorithm_version="alg.v1",
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
    database,
    run_id: str,
    skill_id: str,
    score: float,
    *,
    record_ids: list[str] | None = None,
) -> None:
    with database.sessions.begin() as session:
        session.add(
            PositionSkillTrendResultModel(
                id=f"report-{run_id}",
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
                                "source_contributions": {"policy": 0.8, "github": 0.2}
                            },
                        }
                    ]
                },
            )
        )


def seed_rising_history(database, job_name: str = "AI大模型训练师") -> None:
    for index, score in enumerate([0.20, 0.21, 0.19, 0.45, 0.60, 0.68], start=1):
        run_id = f"run-{index}"
        seed_run(
            database,
            run_id,
            datetime(2026, index, 1, tzinfo=UTC),
            datetime(2026, index, 8, tzinfo=UTC),
            completed_at=datetime(2026, index, 9, tzinfo=UTC),
        )
        seed_prediction(database, run_id, job_name, score)


def service(database) -> TrendChangeService:
    return TrendChangeService(
        SqlAlchemyTrendChangeStore(database.sessions),
        history_builder=BuildTrendHistoricalSequence(
            SqlAlchemyTrendHistoryStore(database.sessions)
        ),
    )


def test_trend_change_analysis_write_read_and_change_points(database):
    seed_rising_history(database)

    result = service(database).analyze_from_history(
        {"subject_id": "AI大模型训练师", "subject_type": "job"}
    )
    analysis_id = str(result["analysis_id"])

    fetched = SqlAlchemyTrendChangeStore(database.sessions).get(analysis_id)
    assert fetched is not None
    subject = fetched["subjects"][0]
    assert subject["sequence_source"] == "persisted_trend_history"
    assert subject["window_count"] == 6
    assert subject["trend_state"] == "rising"
    assert len(subject["change_points"]) == 1

    points = service(database).change_points(analysis_id)
    assert [point["change_point_window"] for point in points] == [
        "2026-04-01T00:00:00+00:00"
    ]


def test_from_history_api_assembles_sequence_and_supports_filters(database, client, auth):
    seed_rising_history(database)

    created = client.post(
        "/internal/v1/trend-change/analyses/from-history",
        headers=auth,
        json={
            "request_id": "from-history-1",
            "subject_id": "AI大模型训练师",
            "subject_type": "job",
        },
    )
    assert created.status_code == 201, created.text
    analysis_id = created.json()["data"]["analysis_id"]

    detail = client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}",
        headers=auth,
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["subjects"][0]["trend_state"] == "rising"

    points = client.get(
        f"/internal/v1/trend-change/analyses/{analysis_id}/change-points",
        headers=auth,
    )
    assert points.status_code == 200
    assert len(points.json()["data"]) == 1


def test_same_window_multiple_runs_choose_latest_completed(database):
    seed_run(
        database,
        "run-old",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
        completed_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    seed_run(
        database,
        "run-new",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
        completed_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    seed_run(
        database,
        "run-2",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 8, tzinfo=UTC),
        completed_at=datetime(2026, 2, 9, tzinfo=UTC),
    )
    seed_prediction(database, "run-old", "AI大模型训练师", 0.2)
    seed_prediction(database, "run-new", "AI大模型训练师", 0.45)
    seed_prediction(database, "run-2", "AI大模型训练师", 0.6)

    windows = BuildTrendHistoricalSequence(
        SqlAlchemyTrendHistoryStore(database.sessions)
    ).build("AI大模型训练师", "job")

    assert [window.analysis_run_id for window in windows] == ["run-new", "run-2"]
    assert [window.score for window in windows] == [0.45, 0.6]


def test_failed_and_invalid_runs_are_excluded(database):
    seed_run(
        database,
        "run-ok",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
        completed_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    seed_run(
        database,
        "run-failed",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 8, tzinfo=UTC),
        status="failed",
        completed_at=datetime(2026, 2, 9, tzinfo=UTC),
    )
    seed_prediction(database, "run-ok", "AI大模型训练师", 0.2)
    seed_prediction(database, "run-failed", "AI大模型训练师", 0.9)

    windows = BuildTrendHistoricalSequence(
        SqlAlchemyTrendHistoryStore(database.sessions)
    ).build("AI大模型训练师", "job")

    assert [window.analysis_run_id for window in windows] == ["run-ok"]


def test_skill_sequence_lineage_from_postgres(database):
    seed_run(
        database,
        "run-1",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
        run_type="position_skill_trend",
        completed_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    seed_run(
        database,
        "run-2",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 8, tzinfo=UTC),
        run_type="position_skill_trend",
        completed_at=datetime(2026, 2, 9, tzinfo=UTC),
    )
    seed_skill_result(database, "run-1", "skill-java", 0.3, record_ids=["snapshot-a"])
    seed_skill_result(database, "run-2", "skill-java", 0.6, record_ids=["snapshot-b"])

    windows = BuildTrendHistoricalSequence(
        SqlAlchemyTrendHistoryStore(database.sessions)
    ).build("skill-java", "skill")

    assert [window.score for window in windows] == [0.3, 0.6]
    assert [window.analysis_run_id for window in windows] == ["run-1", "run-2"]
    assert windows[0].source_records == ("snapshot-a",)
    assert windows[0].source_diversity == 2


def test_insufficient_history_writes_no_partial_analysis(database, client, auth):
    seed_run(
        database,
        "run-1",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
        completed_at=datetime(2026, 1, 9, tzinfo=UTC),
    )
    seed_prediction(database, "run-1", "AI大模型训练师", 0.2)

    response = client.post(
        "/internal/v1/trend-change/analyses/from-history",
        headers=auth,
        json={
            "request_id": "from-history-insufficient",
            "subject_id": "AI大模型训练师",
            "subject_type": "job",
        },
    )
    assert response.status_code == 422
    assert "INSUFFICIENT_HISTORY" in response.json()["detail"]
    with database.sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(TrendChangeAnalysisModel)
        ) == 0


def test_unknown_subject_returns_history_not_found(database):
    with database.sessions.begin() as session:
        session.add(
            AnalysisRunModel(
                id="run-unrelated",
                contract_version="trend-analysis.v2",
                request_id="request-unrelated",
                status="succeeded",
                window_start=datetime(2026, 1, 1, tzinfo=UTC),
                window_end=datetime(2026, 1, 8, tzinfo=UTC),
                data_sources=["policy"],
                weights={"policy": 1.0},
                algorithm_version="alg.v1",
                formula_version="formula.v1",
                completed_at=datetime(2026, 1, 9, tzinfo=UTC),
            )
        )

    try:
        service(database).analyze_from_history(
            {"subject_id": "不存在岗位", "subject_type": "job"}
        )
    except ValueError as exc:
        assert "HISTORY_NOT_FOUND" in str(exc)
    else:
        raise AssertionError("expected HISTORY_NOT_FOUND")
