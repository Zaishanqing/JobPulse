from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.models import (
    AnalysisRunModel,
    PositionSkillTrendResultModel,
    PredictionResultModel,
)


class SqlAlchemyTrendHistoryStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def formal_windows(
        self,
        subject_id: str,
        subject_type: str,
        *,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[dict[str, object]]:
        if subject_type == "job":
            rows = self._market_rows(subject_id, from_time, to_time)
        elif subject_type == "skill":
            rows = self._skill_rows(subject_id, from_time, to_time)
        else:
            return []
        return rows

    def _market_rows(
        self,
        subject_id: str,
        from_time: datetime | None,
        to_time: datetime | None,
    ) -> list[dict[str, object]]:
        statement = (
            select(
                AnalysisRunModel.id.label("run_id"),
                PredictionResultModel.id.label("report_id"),
                AnalysisRunModel.window_start,
                AnalysisRunModel.window_end,
                AnalysisRunModel.completed_at,
                PredictionResultModel.emergence_score.label("score"),
                PredictionResultModel.source_scores,
                PredictionResultModel.evidence_snapshot_ids.label("source_record_ids"),
                AnalysisRunModel.algorithm_version,
                AnalysisRunModel.config_version,
            )
            .join(
                PredictionResultModel,
                PredictionResultModel.analysis_run_id == AnalysisRunModel.id,
            )
            .where(
                AnalysisRunModel.status == "succeeded",
                AnalysisRunModel.completed_at.is_not(None),
                AnalysisRunModel.run_type == "market_prediction",
                PredictionResultModel.job_name == subject_id,
            )
            .order_by(
                AnalysisRunModel.completed_at.desc(),
                AnalysisRunModel.id.desc(),
            )
        )
        statement = self._time_bounds(statement, from_time, to_time)
        with self.sessions() as session:
            rows = session.execute(statement).mappings().all()
        return [self._market_row(row) for row in rows]

    @staticmethod
    def _market_row(row) -> dict[str, object]:
        source_scores = dict(row["source_scores"] or {})
        record_ids = [str(value) for value in (row["source_record_ids"] or [])]
        return {
            "run_id": str(row["run_id"]),
            "report_id": str(row["report_id"]),
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "completed_at": row["completed_at"],
            "score": float(row["score"]),
            "source_scores": source_scores,
            "source_record_ids": record_ids,
            "algorithm_version": str(row["algorithm_version"] or ""),
            "config_version": str(row["config_version"] or ""),
        }

    def _skill_rows(
        self,
        subject_id: str,
        from_time: datetime | None,
        to_time: datetime | None,
    ) -> list[dict[str, object]]:
        statement = (
            select(AnalysisRunModel, PositionSkillTrendResultModel)
            .join(
                PositionSkillTrendResultModel,
                PositionSkillTrendResultModel.analysis_run_id == AnalysisRunModel.id,
            )
            .where(
                AnalysisRunModel.status == "succeeded",
                AnalysisRunModel.completed_at.is_not(None),
                AnalysisRunModel.run_type == "position_skill_trend",
            )
            .order_by(
                AnalysisRunModel.completed_at.desc(),
                AnalysisRunModel.id.desc(),
            )
        )
        statement = self._time_bounds(statement, from_time, to_time)
        with self.sessions() as session:
            pairs = session.execute(statement).all()
        rows = []
        for run, result in pairs:
            skill_trends = (result.result_payload or {}).get("skill_trends") or []
            for skill in skill_trends:
                if str(skill.get("skill_id")) != subject_id:
                    continue
                explanation = skill.get("score_explanation") or {}
                source_scores = explanation.get("source_contributions") or {}
                record_ids = [str(value) for value in (skill.get("evidence_references") or [])]
                rows.append(
                    {
                        "run_id": run.id,
                        "report_id": result.id,
                        "window_start": run.window_start,
                        "window_end": run.window_end,
                        "completed_at": run.completed_at,
                        "score": float(skill.get("trend_score", 0.0)),
                        "source_scores": source_scores,
                        "source_record_ids": record_ids,
                        "algorithm_version": str(run.algorithm_version or ""),
                        "config_version": str(run.config_version or ""),
                    }
                )
        return rows

    @staticmethod
    def _time_bounds(statement, from_time: datetime | None, to_time: datetime | None):
        if from_time is not None:
            statement = statement.where(AnalysisRunModel.window_start >= from_time)
        if to_time is not None:
            statement = statement.where(AnalysisRunModel.window_start <= to_time)
        return statement

    def list_subjects(self, subject_type: str) -> list[str]:
        from app.infrastructure.models import AnalysisRunModel, TrendInputRecordModel
        from sqlalchemy import select, union
        with self.sessions() as session:
            if subject_type == "job":
                rows = session.scalars(
                    select(AnalysisRunModel.request_id)
                    .where(AnalysisRunModel.status == "succeeded", AnalysisRunModel.run_type.is_(None))
                    .distinct()
                )
            else:
                rows = session.scalars(
                    select(AnalysisRunModel.request_id)
                    .where(AnalysisRunModel.status == "succeeded", AnalysisRunModel.run_type == "position_skill_trend")
                    .distinct()
                )
            return [str(row) for row in rows if row]
