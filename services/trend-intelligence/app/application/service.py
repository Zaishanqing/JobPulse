from __future__ import annotations

from dataclasses import replace

from app.domain.analysis_run import AnalysisRun, AnalysisRunLog, NewAnalysisRun
from app.ports.credibility import CredibilityStore
from app.ports.repository import AnalysisRunRepository
from app.ports.market import AnalysisDataStore


class AnalysisRunService:
    def __init__(self, repository: AnalysisRunRepository, *, max_attempts: int = 3, data_store: AnalysisDataStore | None = None, credibility_store: CredibilityStore | None = None) -> None:
        self.repository = repository
        self.max_attempts = max_attempts
        self.data_store = data_store
        self.credibility_store = credibility_store

    def create(self, command: NewAnalysisRun) -> AnalysisRun:
        if self.credibility_store:
            versions = self.credibility_store.active_versions()
            payload = dict(command.run_payload or {})
            payload["config_versions"] = versions
            command = replace(command, run_payload=payload)
        return self.repository.create_or_get(command, max_attempts=self.max_attempts)

    def get(self, run_id: str) -> AnalysisRun | None:
        return self.repository.get(run_id)

    def logs(self, run_id: str) -> list[AnalysisRunLog] | None:
        if self.repository.get(run_id) is None:
            return None
        return self.repository.logs(run_id)

    def cancel(self, run_id: str) -> AnalysisRun | None:
        return self.repository.cancel(run_id)

    def source_report(self, run_id: str) -> dict[str, object] | None:
        if self.repository.get(run_id) is None:
            return None
        return self.data_store.source_report(run_id) if self.data_store else {"source_coverage": 0.0, "missing_sources": [], "quality_flags": [], "sources": []}

    def signals(self, run_id: str) -> list[dict[str, object]] | None:
        if self.repository.get(run_id) is None:
            return None
        return self.data_store.signals(run_id) if self.data_store else []

    def predictions(self, run_id: str) -> list[dict[str, object]] | None:
        if self.repository.get(run_id) is None:
            return None
        return self.data_store.predictions(run_id) if self.data_store else []

    def prediction_explanation(self, run_id: str, prediction_id: str) -> dict[str, object] | None:
        if self.repository.get(run_id) is None or self.data_store is None:
            return None
        return self.data_store.prediction_explanation(run_id, prediction_id)

    def position_skill_trend(self, run_id: str) -> dict[str, object] | None:
        run = self.repository.get(run_id)
        if run is None:
            return None
        return self.data_store.position_skill_trend(run_id) if self.data_store else None

    def replay(self, run_id: str, request_id: str, idempotency_key: str | None) -> AnalysisRun | None:
        original = self.repository.get(run_id)
        if original is None:
            return None
        payload = dict(original.run_payload or {})
        payload["replay_of_run_id"] = original.id
        command = NewAnalysisRun(
            contract_version=original.contract_version, request_id=request_id,
            idempotency_key=idempotency_key, window_start=original.window_start,
            window_end=original.window_end, data_sources=original.data_sources,
            weights=original.weights, algorithm_version=original.algorithm_version,
            formula_version=original.formula_version, run_type=original.run_type,
            run_payload=payload,
        )
        return self.repository.create_or_get(command, max_attempts=self.max_attempts)
