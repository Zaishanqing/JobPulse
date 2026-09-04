from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
import re
from time import perf_counter
from app.application.market_prediction import AllSourcesUnavailable
from app.domain.analysis_run import AnalysisRun
from app.domain.market import ExtractedTerm, StoredSnapshot
from app.ports.market import AnalysisDataStore, KeywordExtractor, SourceAdapter
from app.ports.credibility import CredibilityStore
from app.domain.credibility import quality_flags as credibility_quality_flags
from app.domain.evidence import cluster_snapshots, quality_weight
from app.ports.source_governance import SourceGovernanceStore


LOGGER = logging.getLogger(__name__)


def _normalize_term(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _alias_matches_term(alias: str, term: str) -> bool:
    """Match a graph skill alias without letting short tokens such as C/Go match arbitrary text."""
    if not alias or not term:
        return False
    if alias == term:
        return True
    if any("\u4e00" <= char <= "\u9fff" for char in alias):
        return len(alias) >= 2 and alias in term
    if len(alias) < 3:
        return False
    if alias.isalnum():
        return re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", term, re.IGNORECASE) is not None
    return alias in term


def _matching_skill(term: str, aliases: dict[str, dict]) -> dict | None:
    exact = aliases.get(term)
    if exact is not None:
        return exact
    for alias in sorted(aliases, key=len, reverse=True):
        if _alias_matches_term(alias, term):
            return aliases[alias]
    return None


def _skill_combo_shifts(historical_combo: list[str], current_combo: list[str]) -> list[dict[str, list[str]]]:
    if not historical_combo or not current_combo or current_combo == historical_combo:
        return []
    return [{"from_skill_ids": historical_combo, "to_skill_ids": current_combo}]


class PositionSkillTrend:
    def __init__(self, store: AnalysisDataStore, sources: Sequence[SourceAdapter], extractor: KeywordExtractor,
                 credibility_store: CredibilityStore, source_governance: SourceGovernanceStore | None = None,
                 source_workers: int = 6) -> None:
        self.store = store
        self.sources = {source.name: source for source in sources}
        self.extractor = extractor
        self.credibility_store = credibility_store
        self.source_governance = source_governance
        self.source_workers = source_workers

    @staticmethod
    def _expanded_sources(requested: Sequence[str]) -> list[str]:
        expanded: list[str] = []
        for source in requested:
            names = ("arxiv", "cvf", "acl") if source in {"papers", "academic"} else (source,)
            for name in names:
                if name not in expanded:
                    expanded.append(name)
        return expanded

    def execute(self, run: AnalysisRun) -> dict[str, int]:
        payload = dict(run.run_payload)
        versions = dict(payload.get("config_versions") or self.credibility_store.active_versions())
        configurations = self.credibility_store.payloads(versions)
        thresholds = configurations["trend_thresholds"]
        skills = list(payload.get("standard_skills") or [])
        duration = run.window_end - run.window_start
        history_start = run.window_start - duration
        selected = self._expanded_sources(run.data_sources)
        self.store.initialize_sources(run.id, selected)
        snapshots: list[StoredSnapshot] = []
        replay_run_id = payload.get("replay_of_run_id")
        failure_threshold = int(thresholds.get("source_circuit_failure_threshold", 3))
        open_seconds = int(thresholds.get("source_circuit_open_seconds", 300))
        futures = {}
        started_by_source: dict[str, float] = {}
        worker_count = max(1, min(self.source_workers, len(selected)))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="trend-source") as pool:
            for source_name in selected:
                adapter = self.sources.get(source_name)
                self.store.mark_source_running(run.id, source_name)
                started_by_source[source_name] = perf_counter()
                if self.source_governance and not replay_run_id and not self.source_governance.circuit_allows(source_name, datetime.now(timezone.utc)):
                    self.store.mark_source_failed(run.id, source_name, "source circuit is open")
                    self.source_governance.record_attempt(
                        run_id=run.id, source=source_name, status="circuit_open", duration_ms=0,
                        records=[], error_type="CircuitOpen", window_end=run.window_end,
                        failure_threshold=failure_threshold, open_seconds=open_seconds,
                    )
                    continue
                if adapter is None and not replay_run_id:
                    self.store.mark_source_failed(run.id, source_name, "unsupported data source")
                    if self.source_governance:
                        self.source_governance.record_attempt(
                            run_id=run.id, source=source_name, status="failed", duration_ms=0,
                            records=[], error_type="UnsupportedSource", window_end=run.window_end,
                            failure_threshold=failure_threshold, open_seconds=open_seconds,
                        )
                    continue
                if adapter is not None:
                    adapter.configure(configurations)
                future = (
                    pool.submit(self.source_governance.replay_records, str(replay_run_id), source_name)
                    if self.source_governance and replay_run_id
                    else pool.submit(adapter.collect, history_start, run.window_end)
                )
                futures[future] = source_name

            for future in as_completed(futures):
                source_name = futures[future]
                started = started_by_source[source_name]
                records = []
                try:
                    records = future.result()
                    if records is None:
                        raise RuntimeError("replay cache is unavailable")
                    if not records:
                        raise RuntimeError("source returned no records in current and historical windows")
                    records = [
                        item for item in records
                        if history_start <= item.published_at <= run.window_end
                    ]
                    if not records:
                        raise RuntimeError("source records fall outside the bound analysis windows")
                    if self.source_governance and not replay_run_id:
                        self.source_governance.cache_records(run.id, source_name, records, run.request_id)
                    stored = self.store.save_snapshots(run.id, records)
                except Exception as exc:
                    self.store.mark_source_failed(run.id, source_name, f"{type(exc).__name__}: {exc}")
                    if self.source_governance:
                        self.source_governance.record_attempt(
                            run_id=run.id, source=source_name, status="empty" if not records else "failed", duration_ms=(perf_counter() - started) * 1000,
                            records=[], error_type=type(exc).__name__, window_end=run.window_end,
                            failure_threshold=failure_threshold, open_seconds=open_seconds,
                        )
                    continue
                snapshots.extend(stored)
                self.store.mark_source_succeeded(run.id, source_name, len(records))
                if self.source_governance:
                    self.source_governance.record_attempt(
                        run_id=run.id, source=source_name, status="replayed" if replay_run_id else "succeeded",
                        duration_ms=(perf_counter() - started) * 1000, records=records, error_type=None,
                        window_end=run.window_end, failure_threshold=failure_threshold, open_seconds=open_seconds,
                    )
        if not snapshots:
            raise AllSourcesUnavailable("all configured sources were unavailable or returned no data")

        processing_started = perf_counter()
        clusters = cluster_snapshots(snapshots)
        clustering_ms = round((perf_counter() - processing_started) * 1000)

        cached = self.store.existing_terms(
            [snapshot.id for snapshot in snapshots], self.extractor.version
        )
        uncached = [snapshot for snapshot in snapshots if snapshot.id not in cached]
        terms = [term for snapshot in snapshots for term in cached.get(snapshot.id, ())]
        extraction_started = perf_counter()
        if uncached:
            extraction_workers = max(1, min(self.source_workers, len(uncached)))
            with ThreadPoolExecutor(
                max_workers=extraction_workers, thread_name_prefix="trend-keyword"
            ) as pool:
                futures = {
                    pool.submit(self.extractor.extract, snapshot): snapshot.id
                    for snapshot in uncached
                }
                for future in as_completed(futures):
                    terms.extend(future.result())
        terms.sort(key=lambda item: (item.snapshot_id, item.term, item.extractor_version))
        extraction_ms = round((perf_counter() - extraction_started) * 1000)
        snapshot_by_id = {item.id: item for item in snapshots}
        aliases: dict[str, dict] = {}
        for skill in skills:
            for value in [skill.get("skill_name"), *(skill.get("aliases") or [])]:
                if value:
                    aliases[_normalize_term(value)] = skill
        current: dict[str, float] = defaultdict(float)
        historical: dict[str, float] = defaultdict(float)
        evidence: dict[str, set[str]] = defaultdict(set)
        source_current: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        source_historical: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        unresolved: dict[str, set[str]] = defaultdict(set)
        skill_clusters: dict[str, set[str]] = defaultdict(set)
        skill_quality_flags: dict[str, set[str]] = defaultdict(set)
        cluster_by_snapshot = {
            snapshot.id: cluster for cluster in clusters for snapshot in cluster.snapshots
        }
        cluster_values: dict[tuple[str, str, str], float] = defaultdict(float)
        for term in terms:
            normalized = _normalize_term(term.term)
            match = _matching_skill(normalized, aliases)
            snapshot = snapshot_by_id.get(term.snapshot_id)
            if snapshot is None:
                continue
            if match is None:
                unresolved[term.term].add(term.snapshot_id)
                continue
            skill_id = str(match["skill_id"])
            cluster = cluster_by_snapshot[term.snapshot_id]
            window = "current" if snapshot.record.published_at >= run.window_start else "historical"
            weight, flags = quality_weight(snapshot)
            contribution = max(0.0, 1.0 - term.score) * weight
            key = (skill_id, cluster.id, window)
            cluster_values[key] = max(cluster_values[key], contribution)
            skill_clusters[skill_id].add(cluster.id)
            skill_quality_flags[skill_id].update(flags)
            # Evidence lineage must point only to documents that actually emitted the matched term.
            # The cluster is used for independent-event weighting, not for expanding provenance.
            evidence[skill_id].add(term.snapshot_id)

        clusters_by_id = {item.id: item for item in clusters}
        for (skill_id, cluster_id, window), contribution in cluster_values.items():
            cluster = clusters_by_id[cluster_id]
            bucket = current if window == "current" else historical
            source_bucket = source_current if window == "current" else source_historical
            bucket[skill_id] += contribution
            sources = sorted({item.record.source for item in cluster.snapshots})
            share = contribution / len(sources)
            for source in sources:
                source_bucket[skill_id][source] += share

        report = self.store.source_report(run.id)
        coverage = float(report["source_coverage"])
        quality_flags = list(report["quality_flags"])
        results = []
        by_id = {str(skill["skill_id"]): skill for skill in skills}
        for skill_id, skill in by_id.items():
            now_value = round(current[skill_id], 6)
            old_value = round(historical[skill_id], 6)
            if old_value > 0:
                growth = round((now_value - old_value) / old_value, 6)
                direction = "rising" if growth >= float(thresholds["rising_growth"]) else "declining" if growth <= float(thresholds["declining_growth"]) else "stable"
            elif now_value > 0:
                # 0 -> positive：不构造固定 +100%，trend_direction 明确标记为 new（新出现）
                growth = None
                direction = "new"
            else:
                # 0 -> 0：无历史可比基线，也不显示伪 +0% 基线
                growth = None
                direction = "stable"
            count = len(skill_clusters[skill_id])
            coverage_part = coverage * float(thresholds["confidence_coverage_weight"])
            evidence_part = min(count / int(thresholds["confidence_evidence_cap"]), 1 - float(thresholds["confidence_coverage_weight"]))
            confidence = round(min(1.0, coverage_part + evidence_part), 6)
            source_contributions = {
                source: round(
                    source_current[skill_id][source] - source_historical[skill_id][source], 6
                )
                for source in set(source_current[skill_id]) | set(source_historical[skill_id])
            }
            latest_evidence = max(
                (snapshot_by_id[item].record.published_at for item in evidence[skill_id]),
                default=None,
            )
            evidence_age = (
                (run.window_end - latest_evidence).total_seconds() / 86400
                if latest_evidence else None
            )
            extra_flags = credibility_quality_flags(
                evidence_count=count, source_contributions=source_contributions,
                evidence_age_days=evidence_age, growth_rate=growth,
                thresholds=thresholds,
            )
            trend_score = round(now_value / (now_value + old_value), 6) if now_value + old_value else 0.0
            evidence_weights = [
                quality_weight(snapshot_by_id[item])[0]
                for item in evidence[skill_id]
                if item in snapshot_by_id
            ]
            quality_penalty = round(
                1.0 - sum(evidence_weights) / len(evidence_weights), 6
            ) if evidence_weights else 1.0
            results.append({
                "skill_id": skill_id,
                "skill_name": skill["skill_name"],
                "current_window_signal": now_value,
                "historical_window_signal": old_value,
                "trend_score": trend_score,
                "growth_rate": growth,
                "trend_direction": direction,
                "evidence_count": count,
                "independent_event_count": count,
                "source_coverage": coverage,
                "confidence": confidence,
                "evidence_references": sorted(evidence[skill_id]),
                "quality_warnings": sorted(skill_quality_flags[skill_id]),
                "quality_flags": list(dict.fromkeys([
                    *quality_flags, *sorted(skill_quality_flags[skill_id]), *extra_flags
                ])),
                "score_explanation": {
                    "definition": "heuristic composite index; not a probability",
                    "trend_score": {"current_window": now_value, "historical_window": old_value},
                    "confidence": {"source_coverage": round(coverage_part, 6), "evidence": round(evidence_part, 6)},
                    "source_contributions": source_contributions,
                    "current_source_contributions": {
                        key: round(value, 6)
                        for key, value in source_current[skill_id].items()
                    },
                    "historical_source_contributions": {
                        key: round(value, 6)
                        for key, value in source_historical[skill_id].items()
                    },
                    "keyword_contributions": {skill["skill_name"]: trend_score},
                    "time_change_contribution": growth,
                    "quality_penalty": quality_penalty,
                    "formula": "quality-weighted independent-event current signal / (current + historical)",
                    "rule_contributions": {"direction_threshold": direction},
                    "configuration_versions": versions,
                },
            })
        declining = [item for item in results if item["trend_direction"] == "declining"]
        current_combo = sorted(item["skill_id"] for item in results if item["current_window_signal"] > 0)
        historical_combo = sorted(item["skill_id"] for item in results if item["historical_window_signal"] > 0)
        result = {
            "position_id": payload["position_id"],
            "position_name": payload["position_name"],
            "graph_version": payload["graph_version"],
            "skill_catalog_version": payload["skill_catalog_version"],
            "algorithm_version": run.algorithm_version,
            "formula_version": run.formula_version,
            "config_version": payload["config_version"],
            "config_versions": versions,
            "time_window": {"start": run.window_start.isoformat(), "end": run.window_end.isoformat()},
            "historical_window": {"start": history_start.isoformat(), "end": run.window_start.isoformat()},
            "skill_trends": results,
            "new_skills": [item for item in results if item["trend_direction"] == "new"],
            "rising_skills": [item for item in results if item["trend_direction"] == "rising"],
            "declining_skills": declining,
            "skill_combo_shifts": _skill_combo_shifts(historical_combo, current_combo),
            "unresolved_terms": [
                {"term": term, "evidence_references": sorted(refs)}
                for term, refs in sorted(unresolved.items())
            ],
            "source_coverage": coverage,
            "analysis_quality_status": report["analysis_quality_status"],
            "missing_sources": report["missing_sources"],
            "quality_flags": quality_flags,
            "quality_summary": report["quality_summary"],
            "evidence_references": sorted({ref for refs in evidence.values() for ref in refs}),
        }
        persistence_started = perf_counter()
        self.store.replace_position_skill_result(run.id, clusters, terms, result)
        persistence_ms = round((perf_counter() - persistence_started) * 1000)
        LOGGER.info(
            "position_skill_trend_phases",
            extra={
                "run_id": run.id,
                "snapshots": len(snapshots),
                "clusters": len(clusters),
                "terms": len(terms),
                "cached_snapshots": len(cached),
                "clustering_ms": clustering_ms,
                "extraction_ms": extraction_ms,
                "persistence_ms": persistence_ms,
            },
        )
        return {
            "snapshots": len(snapshots),
            "signals": 0,
            "predictions": 0,
            "skill_trends": len(results),
        }
