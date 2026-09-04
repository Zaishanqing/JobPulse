from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from time import perf_counter

from app.domain.analysis_run import AnalysisRun
from app.domain.market import ExtractedTerm, StoredSnapshot, aggregate_signals, predict_jobs
from app.ports.market import AnalysisDataStore, KeywordExtractor, SourceAdapter
from app.ports.credibility import CredibilityStore
from app.ports.source_governance import SourceGovernanceStore
from app.acquisition.ports.trend_input import TrendInputAdapter
from app.domain.evidence import cluster_snapshots, quality_weight


class AllSourcesUnavailable(RuntimeError):
    pass


class MarketPrediction:
    def __init__(
        self,
        store: AnalysisDataStore,
        sources: Sequence[SourceAdapter],
        extractor: KeywordExtractor,
        credibility_store: CredibilityStore,
        source_governance: SourceGovernanceStore | None = None,
        trend_input_adapter: TrendInputAdapter | None = None,
    ) -> None:
        self.store = store
        self.sources = {source.name: source for source in sources}
        self.extractor = extractor
        self.credibility_store = credibility_store
        self.source_governance = source_governance
        self.trend_input_adapter = trend_input_adapter

    @staticmethod
    def _expanded_sources(requested: Sequence[str]) -> list[str]:
        expanded = []
        for source in requested:
            names = ("arxiv", "cvf", "acl") if source in {"papers", "academic"} else (source,)
            for name in names:
                if name not in expanded:
                    expanded.append(name)
        return expanded

    def execute(self, run: AnalysisRun) -> dict[str, int]:
        versions = dict((run.run_payload or {}).get("config_versions") or self.credibility_store.active_versions())
        configurations = self.credibility_store.payloads(versions)
        selected = self._expanded_sources(run.data_sources)
        self.store.initialize_sources(run.id, selected)
        snapshots: list[StoredSnapshot] = []
        failed: list[str] = []
        replay_run_id = (run.run_payload or {}).get("replay_of_run_id")
        acquisition_bundle_id = (run.run_payload or {}).get("acquisition_bundle_id")
        staged_run = bool(acquisition_bundle_id)
        failure_threshold = int(configurations["trend_thresholds"].get("source_circuit_failure_threshold", 3))
        open_seconds = int(configurations["trend_thresholds"].get("source_circuit_open_seconds", 300))
        for source_name in selected:
            adapter = self.sources.get(source_name)
            self.store.mark_source_running(run.id, source_name)
            started = perf_counter()
            if self.source_governance and not replay_run_id and not staged_run and not self.source_governance.circuit_allows(source_name, datetime.now(timezone.utc)):
                failed.append(source_name)
                self.store.mark_source_failed(run.id, source_name, "source circuit is open")
                self.source_governance.record_attempt(
                    run_id=run.id, source=source_name, status="circuit_open", duration_ms=0,
                    records=[], error_type="CircuitOpen", window_end=run.window_end,
                    failure_threshold=failure_threshold, open_seconds=open_seconds,
                )
                continue
            if adapter is None and not replay_run_id and not staged_run:
                failed.append(source_name)
                self.store.mark_source_failed(run.id, source_name, "unsupported data source")
                if self.source_governance:
                    self.source_governance.record_attempt(
                        run_id=run.id, source=source_name, status="failed", duration_ms=0,
                        records=[], error_type="UnsupportedSource", window_end=run.window_end,
                        failure_threshold=failure_threshold, open_seconds=open_seconds,
                    )
                continue
            records = []
            try:
                if staged_run:
                    if self.trend_input_adapter is None:
                        raise RuntimeError("Trend input adapter is unavailable for acquisition run")
                    records = self.trend_input_adapter.records_for_run(run.id, source_name)
                elif adapter is not None:
                    adapter.configure(configurations)
                if not staged_run:
                    records = (self.source_governance.replay_records(str(replay_run_id), source_name)
                               if self.source_governance and replay_run_id else
                               adapter.collect(run.window_start, run.window_end))
                if records is None:
                    raise RuntimeError("replay cache is unavailable")
                if not records:
                    raise RuntimeError("source returned no records in the requested window")
                records = [
                    item for item in records
                    if run.window_start <= item.published_at < run.window_end
                ]
                if not records:
                    raise RuntimeError("source records fall outside the bound analysis window")
                if self.source_governance and not replay_run_id and not staged_run:
                    self.source_governance.cache_records(run.id, source_name, records, run.request_id)
                stored = self.store.save_snapshots(run.id, records)
            except Exception as exc:
                failed.append(source_name)
                self.store.mark_source_failed(run.id, source_name, f"{type(exc).__name__}: {exc}")
                if self.source_governance and not staged_run:
                    self.source_governance.record_attempt(
                        run_id=run.id, source=source_name, status="empty" if not records else "failed", duration_ms=(perf_counter() - started) * 1000,
                        records=[], error_type=type(exc).__name__, window_end=run.window_end,
                        failure_threshold=failure_threshold, open_seconds=open_seconds,
                    )
                continue
            snapshots.extend(stored)
            self.store.mark_source_succeeded(run.id, source_name, len(records))
            if self.source_governance and not staged_run:
                self.source_governance.record_attempt(
                    run_id=run.id, source=source_name, status="replayed" if replay_run_id else "succeeded",
                    duration_ms=(perf_counter() - started) * 1000, records=records, error_type=None,
                    window_end=run.window_end, failure_threshold=failure_threshold, open_seconds=open_seconds,
                )

        if not snapshots:
            raise AllSourcesUnavailable("all configured sources were unavailable or returned no data")

        clusters = cluster_snapshots(snapshots)
        independent_snapshots = [
            max(cluster.snapshots, key=lambda item: quality_weight(item)[0])
            for cluster in clusters
        ]
        terms: list[ExtractedTerm] = []
        for snapshot in independent_snapshots:
            terms.extend(self.extractor.extract(snapshot))
        signals = aggregate_signals(
            run.id, independent_snapshots, terms, knowledge_base=configurations["job_knowledge"]
        )
        report = self.store.source_report(run.id)
        quality_flags = list(report["quality_flags"])
        represented = {signal.source for signal in signals}
        if len(represented) < 2:
            quality_flags.append("limited_signal_diversity")
        if not signals:
            quality_flags.append("no_predictive_signals")
        predictions = predict_jobs(
            run.id,
            signals,
            weights=run.weights,
            algorithm_version=run.algorithm_version,
            formula_version=run.formula_version,
            window_start=run.window_start,
            window_end=run.window_end,
            source_coverage=float(report["source_coverage"]),
            missing_sources=list(report["missing_sources"]),
            quality_flags=quality_flags,
            knowledge_base=configurations["job_knowledge"],
            thresholds=configurations["trend_thresholds"],
            config_versions=versions,
            evidence_published_at={item.id: item.record.published_at for item in snapshots},
            configuration_drift=versions != self.credibility_store.active_versions(),
        )
        if not predictions:
            raise RuntimeError("analysis produced no predictions")
        self.store.replace_market_results(run.id, clusters, terms, signals, predictions)
        return {
            "snapshots": len(snapshots),
            "signals": len(signals),
            "predictions": len(predictions),
            "skill_trends": 0,
        }
