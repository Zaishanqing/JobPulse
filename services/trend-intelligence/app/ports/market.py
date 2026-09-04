from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.domain.market import ExtractedTerm, PredictionResult, SignalObservation, SourceRecord, StoredSnapshot


class SourceAdapter(Protocol):
    name: str

    def configure(self, configurations: dict[str, dict]) -> None: ...

    def collect(self, window_start: datetime, window_end: datetime) -> list[SourceRecord]: ...


class KeywordExtractor(Protocol):
    version: str

    def extract(self, snapshot: StoredSnapshot) -> list[ExtractedTerm]: ...


class AnalysisDataStore(Protocol):
    def initialize_sources(self, run_id: str, sources: Sequence[str]) -> None: ...

    def mark_source_running(self, run_id: str, source: str) -> None: ...

    def mark_source_succeeded(self, run_id: str, source: str, count: int) -> None: ...

    def mark_source_failed(self, run_id: str, source: str, error: str) -> None: ...

    def save_snapshots(self, run_id: str, records: Sequence[SourceRecord]) -> list[StoredSnapshot]: ...

    def save_evidence_clusters(self, run_id: str, clusters: Sequence[object]) -> None: ...

    def save_terms(self, terms: Sequence[ExtractedTerm]) -> None: ...

    def existing_terms(
        self, snapshot_ids: Sequence[str], extractor_version: str
    ) -> dict[str, list[ExtractedTerm]]: ...

    def save_signals(self, signals: Sequence[SignalObservation]) -> None: ...

    def save_predictions(self, predictions: Sequence[PredictionResult]) -> None: ...

    def replace_market_results(
        self,
        run_id: str,
        clusters: Sequence[object],
        terms: Sequence[ExtractedTerm],
        signals: Sequence[SignalObservation],
        predictions: Sequence[PredictionResult],
    ) -> None: ...

    def source_report(self, run_id: str) -> dict[str, object]: ...

    def signals(self, run_id: str) -> list[dict[str, object]]: ...

    def predictions(self, run_id: str) -> list[dict[str, object]]: ...

    def prediction_explanation(self, run_id: str, prediction_id: str) -> dict[str, object] | None: ...

    def save_position_skill_trend(self, run_id: str, payload: dict[str, object]) -> None: ...

    def replace_position_skill_result(
        self,
        run_id: str,
        clusters: Sequence[object],
        terms: Sequence[ExtractedTerm],
        payload: dict[str, object],
    ) -> None: ...

    def position_skill_trend(self, run_id: str) -> dict[str, object] | None: ...
