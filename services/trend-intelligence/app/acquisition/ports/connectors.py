from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol

from app.acquisition.domain.acquisition import RawRecord


class Connector(Protocol):
    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]: ...


class ConnectorResolver(Protocol):
    def resolve(self, source_type: str) -> Connector | None: ...
