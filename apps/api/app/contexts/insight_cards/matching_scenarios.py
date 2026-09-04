from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from app.domain.accounts import AccountActor
from app.domain.json_types import FrozenJsonObject


@dataclass(frozen=True)
class WhatIfScenarioDraft:
    scenario_id: str
    evaluation_id: str
    actions_payload: FrozenJsonObject
    result_payload: FrozenJsonObject
    release_id: str | None
    graph_version: str | None
    algorithm_version: str | None
    config_version: str | None
    created_by: str


@dataclass(frozen=True)
class WhatIfScenarioRecord(WhatIfScenarioDraft):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WhatIfScenarioRepository(Protocol):
    def get(self, scenario_id: str) -> WhatIfScenarioRecord | None: ...
    def save(self, draft: WhatIfScenarioDraft) -> WhatIfScenarioRecord: ...


class WhatIfScenarioUnitOfWork(Protocol):
    scenarios: WhatIfScenarioRepository

    def __enter__(self) -> "WhatIfScenarioUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class WhatIfScenarioNotFound(LookupError):
    pass


class ManageMatchingScenarios:
    def __init__(self, uow_factory: Callable[[], WhatIfScenarioUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def save(self, draft: WhatIfScenarioDraft) -> WhatIfScenarioRecord:
        with self._uow_factory() as uow:
            record = uow.scenarios.save(draft)
            uow.commit()
            return record

    def get(
        self, actor: AccountActor, scenario_id: str
    ) -> WhatIfScenarioRecord:
        with self._uow_factory() as uow:
            record = uow.scenarios.get(scenario_id)
        if record is None:
            raise WhatIfScenarioNotFound(scenario_id)
        if (
            record.created_by != actor.account_id
            and actor.role not in {"admin", "developer", "reviewer"}
        ):
            raise WhatIfScenarioNotFound(scenario_id)
        return record


__all__ = [
    "ManageMatchingScenarios",
    "WhatIfScenarioDraft",
    "WhatIfScenarioNotFound",
    "WhatIfScenarioRecord",
    "WhatIfScenarioRepository",
    "WhatIfScenarioUnitOfWork",
]
