from __future__ import annotations

from typing import Protocol


class TrendChangeStore(Protocol):
    def create(self, payload: dict[str, object]) -> dict[str, object]: ...

    def get(self, analysis_id: str) -> dict[str, object] | None: ...
