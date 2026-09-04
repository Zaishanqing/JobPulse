from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.market import SourceRecord


@dataclass(frozen=True)
class TrendInputImportResult:
    bundle_id: str
    analysis_run_id: str
    imported_count: int
    duplicate_count: int
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "analysis_run_id": self.analysis_run_id,
            "imported_count": self.imported_count,
            "duplicate_count": self.duplicate_count,
            "status": self.status,
        }


class TrendInputAdapter(Protocol):
    def import_bundle(self, bundle_id: str) -> TrendInputImportResult: ...

    def records_for_run(self, run_id: str, source: str) -> list[SourceRecord]: ...
