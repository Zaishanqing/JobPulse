from __future__ import annotations

from datetime import datetime
from typing import Protocol


class TrendHistoryStore(Protocol):
    def formal_windows(
        self,
        subject_id: str,
        subject_type: str,
        *,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[dict[str, object]]: ...
