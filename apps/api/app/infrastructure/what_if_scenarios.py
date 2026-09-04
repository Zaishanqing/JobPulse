from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.json_types import freeze_json_object, thaw_json_object
from app.contexts.insight_cards.matching_scenarios import (
    WhatIfScenarioDraft,
    WhatIfScenarioRecord,
    WhatIfScenarioRepository,
)
from app.models.what_if_scenario import WhatIfScenario


class SqlAlchemyWhatIfScenarioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, scenario_id: str) -> WhatIfScenarioRecord | None:
        row = self._session.get(WhatIfScenario, scenario_id)
        return self._record(row) if row is not None else None

    def save(self, draft: WhatIfScenarioDraft) -> WhatIfScenarioRecord:
        existing = self._session.get(WhatIfScenario, draft.scenario_id)
        if existing is not None:
            return self._record(existing)
        row = WhatIfScenario(
            scenario_id=draft.scenario_id,
            evaluation_id=draft.evaluation_id,
            actions_payload=thaw_json_object(draft.actions_payload),
            result_payload=thaw_json_object(draft.result_payload),
            release_id=draft.release_id,
            graph_version=draft.graph_version,
            algorithm_version=draft.algorithm_version,
            config_version=draft.config_version,
            created_by=draft.created_by,
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    @staticmethod
    def _record(row: WhatIfScenario) -> WhatIfScenarioRecord:
        return WhatIfScenarioRecord(
            scenario_id=row.scenario_id,
            evaluation_id=row.evaluation_id,
            actions_payload=freeze_json_object(row.actions_payload),
            result_payload=freeze_json_object(row.result_payload),
            release_id=row.release_id,
            graph_version=row.graph_version,
            algorithm_version=row.algorithm_version,
            config_version=row.config_version,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SqlAlchemyWhatIfScenarioUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = session_factory()
        self.scenarios: WhatIfScenarioRepository = (
            SqlAlchemyWhatIfScenarioRepository(self._session)
        )

    def __enter__(self) -> "SqlAlchemyWhatIfScenarioUnitOfWork":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._session.close()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


__all__ = [
    "SqlAlchemyWhatIfScenarioRepository",
    "SqlAlchemyWhatIfScenarioUnitOfWork",
]
