from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.models import TrendChangeAnalysisModel


class SqlAlchemyTrendChangeStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        with self.sessions.begin() as session:
            model = TrendChangeAnalysisModel(
                algorithm_version=str(payload["algorithm_version"]),
                config_version=str(payload["config_version"]),
                result_payload=payload,
            )
            session.add(model)
            session.flush()
            return self._record(model.id, model.created_at, payload)

    def get(self, analysis_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            model = session.get(TrendChangeAnalysisModel, analysis_id)
            if model is None:
                return None
            return self._record(model.id, model.created_at, dict(model.result_payload))

    @staticmethod
    def _record(
        analysis_id: str,
        created_at,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return {"analysis_id": analysis_id, "created_at": created_at, **payload}
