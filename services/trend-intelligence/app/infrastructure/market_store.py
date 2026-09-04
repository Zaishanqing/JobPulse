from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.market import EXPECTED_MINIMUMS, ExtractedTerm, PredictionResult, SignalObservation, SourceRecord, StoredSnapshot, compute_source_quality_grade
from app.domain.evidence import EventCluster, normalize_url, quality_weight
from app.infrastructure.models import (
    EvidenceModel,
    ExtractedTermModel,
    PredictionResultModel,
    PositionSkillTrendResultModel,
    RunSourceStatusModel,
    SignalObservationModel,
    SourceSnapshotModel,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class SqlAlchemyAnalysisDataStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def initialize_sources(self, run_id: str, sources: Sequence[str]) -> None:
        with self.sessions.begin() as session:
            existing = set(session.scalars(select(RunSourceStatusModel.source).where(RunSourceStatusModel.analysis_run_id == run_id)))
            session.add_all(RunSourceStatusModel(analysis_run_id=run_id, source=source) for source in sources if source not in existing)

    def _source(self, session: Session, run_id: str, source: str) -> RunSourceStatusModel:
        row = session.scalar(select(RunSourceStatusModel).where(RunSourceStatusModel.analysis_run_id == run_id, RunSourceStatusModel.source == source))
        if row is None:
            row = RunSourceStatusModel(analysis_run_id=run_id, source=source)
            session.add(row)
            session.flush()
        return row

    def mark_source_running(self, run_id: str, source: str) -> None:
        with self.sessions.begin() as session:
            row = self._source(session, run_id, source)
            row.status = "running"
            row.started_at = now_utc()
            row.completed_at = None
            row.error_message = None
            row.updated_at = now_utc()

    def mark_source_succeeded(self, run_id: str, source: str, count: int) -> None:
        with self.sessions.begin() as session:
            row = self._source(session, run_id, source)
            row.status = "succeeded"
            row.records_fetched = count
            row.error_message = None
            row.completed_at = now_utc()
            row.updated_at = now_utc()

    def mark_source_failed(self, run_id: str, source: str, error: str) -> None:
        with self.sessions.begin() as session:
            row = self._source(session, run_id, source)
            row.status = "failed"
            row.error_message = error[:4000]
            row.completed_at = now_utc()
            row.updated_at = now_utc()

    def save_snapshots(self, run_id: str, records: Sequence[SourceRecord]) -> list[StoredSnapshot]:
        stored: list[StoredSnapshot] = []
        with self.sessions.begin() as session:
            for record in records:
                identity = (
                    SourceSnapshotModel.source == record.source,
                    SourceSnapshotModel.external_id == record.external_id,
                    SourceSnapshotModel.source_version == record.source_version,
                )
                row = session.scalar(select(SourceSnapshotModel).where(*identity))
                if row is None:
                    try:
                        with session.begin_nested():
                            row = SourceSnapshotModel(
                                first_seen_run_id=run_id,
                                source=record.source,
                                external_id=record.external_id,
                                source_version=record.source_version,
                                title=record.title,
                                content=record.content,
                                url=record.url,
                                normalized_url=normalize_url(record.url),
                                source_type=str(record.metadata.get("source_type") or (
                                    "academic" if record.source in {"arxiv", "cvf", "acl"}
                                    else record.source
                                )),
                                published_at=record.published_at,
                                captured_at=record.captured_at or now_utc(),
                                date_precision=str(record.metadata.get("date_precision") or (
                                    "estimated" if "estimated_publish_date" in record.metadata.get("quality_flags", [])
                                    else "exact"
                                )),
                                content_completeness=str(record.metadata.get("content_completeness") or (
                                    "missing" if not record.content.strip()
                                    else "title_only" if record.content.strip() == record.title.strip()
                                    else "full"
                                )),
                                snapshot_metadata=dict(record.metadata),
                            )
                            session.add(row)
                            session.flush()
                    except IntegrityError:
                        row = session.scalar(select(SourceSnapshotModel).where(*identity))
                if row is None:
                    raise RuntimeError("source snapshot could not be persisted")
                stored.append(StoredSnapshot(id=row.id, record=record))
        return stored

    def save_evidence_clusters(self, run_id: str, clusters: Sequence[EventCluster]) -> None:
        with self.sessions.begin() as session:
            for cluster in clusters:
                for snapshot in cluster.snapshots:
                    weight, flags = quality_weight(snapshot)
                    row = session.get(SourceSnapshotModel, snapshot.id)
                    if row is not None:
                        row.event_cluster_id = cluster.id
                    existing = session.scalar(select(EvidenceModel.id).where(
                        EvidenceModel.analysis_run_id == run_id,
                        EvidenceModel.snapshot_id == snapshot.id,
                    ))
                    if existing is None:
                        session.add(EvidenceModel(
                            analysis_run_id=run_id,
                            snapshot_id=snapshot.id,
                            event_cluster_id=cluster.id,
                            contribution_weight=weight,
                            quality_flags=list(flags),
                        ))

    def save_terms(self, terms: Sequence[ExtractedTerm]) -> None:
        with self.sessions.begin() as session:
            for term in terms:
                exists = session.scalar(select(ExtractedTermModel.id).where(ExtractedTermModel.snapshot_id == term.snapshot_id, ExtractedTermModel.term == term.term, ExtractedTermModel.extractor_version == term.extractor_version))
                if exists is None:
                    session.add(ExtractedTermModel(snapshot_id=term.snapshot_id, term=term.term, score=term.score, week_start=date.fromisoformat(term.week_start), extractor_version=term.extractor_version))

    @staticmethod
    def _chunks(values: Sequence[str], size: int = 500):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    def existing_terms(
        self, snapshot_ids: Sequence[str], extractor_version: str
    ) -> dict[str, list[ExtractedTerm]]:
        """Load cached extraction output in bounded bulk queries."""
        result: dict[str, list[ExtractedTerm]] = {}
        identifiers = list(dict.fromkeys(snapshot_ids))
        if not identifiers:
            return result
        with self.sessions() as session:
            for chunk in self._chunks(identifiers):
                rows = session.scalars(
                    select(ExtractedTermModel).where(
                        ExtractedTermModel.snapshot_id.in_(chunk),
                        ExtractedTermModel.extractor_version == extractor_version,
                    )
                ).all()
                for row in rows:
                    result.setdefault(row.snapshot_id, []).append(ExtractedTerm(
                        snapshot_id=row.snapshot_id,
                        term=row.term,
                        score=row.score,
                        week_start=row.week_start.isoformat(),
                        extractor_version=row.extractor_version,
                    ))
        return result

    def save_signals(self, signals: Sequence[SignalObservation]) -> None:
        with self.sessions.begin() as session:
            for signal in signals:
                exists = session.scalar(select(SignalObservationModel.id).where(SignalObservationModel.analysis_run_id == signal.analysis_run_id, SignalObservationModel.source == signal.source, SignalObservationModel.industry_domain == signal.industry_domain, SignalObservationModel.week_start == date.fromisoformat(signal.week_start)))
                if exists is None:
                    session.add(SignalObservationModel(analysis_run_id=signal.analysis_run_id, source=signal.source, industry_domain=signal.industry_domain, week_start=date.fromisoformat(signal.week_start), signal_strength=signal.signal_strength, raw_value=signal.raw_value, keywords=list(signal.keywords), evidence_snapshot_ids=list(signal.evidence_snapshot_ids)))

    def save_predictions(self, predictions: Sequence[PredictionResult]) -> None:
        with self.sessions.begin() as session:
            for result in predictions:
                exists = session.scalar(select(PredictionResultModel.id).where(PredictionResultModel.analysis_run_id == result.analysis_run_id, PredictionResultModel.job_name == result.job_name, PredictionResultModel.industry_domain == result.industry_domain))
                if exists is None:
                    session.add(PredictionResultModel(analysis_run_id=result.analysis_run_id, job_name=result.job_name, industry_domain=result.industry_domain, emergence_score=result.emergence_score, source_scores=dict(result.source_scores), related_keywords=list(result.related_keywords), evidence_snapshot_ids=list(result.evidence_snapshot_ids), algorithm_version=result.algorithm_version, formula_version=result.formula_version, window_start=result.window_start, window_end=result.window_end, source_coverage=result.source_coverage, missing_sources=list(result.missing_sources), quality_flags=list(result.quality_flags), config_versions=dict(result.config_versions), score_explanation=dict(result.score_explanation)))

    def replace_market_results(
        self,
        run_id: str,
        clusters: Sequence[EventCluster],
        terms: Sequence[ExtractedTerm],
        signals: Sequence[SignalObservation],
        predictions: Sequence[PredictionResult],
    ) -> None:
        """Replace all derived output for one run in a single transaction."""
        with self.sessions.begin() as session:
            session.execute(delete(PredictionResultModel).where(
                PredictionResultModel.analysis_run_id == run_id
            ))
            session.execute(delete(SignalObservationModel).where(
                SignalObservationModel.analysis_run_id == run_id
            ))
            session.execute(delete(EvidenceModel).where(
                EvidenceModel.analysis_run_id == run_id
            ))
            for cluster in clusters:
                for snapshot in cluster.snapshots:
                    weight, flags = quality_weight(snapshot)
                    row = session.get(SourceSnapshotModel, snapshot.id)
                    if row is None:
                        raise RuntimeError(f"source snapshot {snapshot.id} is missing")
                    row.event_cluster_id = cluster.id
                    session.add(EvidenceModel(
                        analysis_run_id=run_id,
                        snapshot_id=snapshot.id,
                        event_cluster_id=cluster.id,
                        contribution_weight=weight,
                        quality_flags=list(flags),
                    ))
            for term in terms:
                exists = session.scalar(select(ExtractedTermModel.id).where(
                    ExtractedTermModel.snapshot_id == term.snapshot_id,
                    ExtractedTermModel.term == term.term,
                    ExtractedTermModel.extractor_version == term.extractor_version,
                ))
                if exists is None:
                    session.add(ExtractedTermModel(
                        snapshot_id=term.snapshot_id,
                        term=term.term,
                        score=term.score,
                        week_start=date.fromisoformat(term.week_start),
                        extractor_version=term.extractor_version,
                    ))
            session.add_all(SignalObservationModel(
                analysis_run_id=signal.analysis_run_id,
                source=signal.source,
                industry_domain=signal.industry_domain,
                week_start=date.fromisoformat(signal.week_start),
                signal_strength=signal.signal_strength,
                raw_value=signal.raw_value,
                keywords=list(signal.keywords),
                evidence_snapshot_ids=list(signal.evidence_snapshot_ids),
            ) for signal in signals)
            session.add_all(PredictionResultModel(
                analysis_run_id=result.analysis_run_id,
                job_name=result.job_name,
                industry_domain=result.industry_domain,
                emergence_score=result.emergence_score,
                source_scores=dict(result.source_scores),
                related_keywords=list(result.related_keywords),
                evidence_snapshot_ids=list(result.evidence_snapshot_ids),
                algorithm_version=result.algorithm_version,
                formula_version=result.formula_version,
                window_start=result.window_start,
                window_end=result.window_end,
                source_coverage=result.source_coverage,
                missing_sources=list(result.missing_sources),
                quality_flags=list(result.quality_flags),
                config_versions=dict(result.config_versions),
                score_explanation=dict(result.score_explanation),
            ) for result in predictions)

    def source_report(self, run_id: str) -> dict[str, object]:
        with self.sessions() as session:
            rows = list(session.scalars(
                select(RunSourceStatusModel)
                .where(RunSourceStatusModel.analysis_run_id == run_id)
                .order_by(RunSourceStatusModel.source)
            ))
            snapshots = list(session.scalars(
                select(SourceSnapshotModel)
                .where(SourceSnapshotModel.first_seen_run_id == run_id)
                .order_by(SourceSnapshotModel.source, SourceSnapshotModel.external_id)
            ))
            succeeded = [row.source for row in rows if row.status == "succeeded"]
            missing = [row.source for row in rows if row.status != "succeeded"]
            coverage = len(succeeded) / len(rows) if rows else 0.0
            flags: list[str] = []
            if missing:
                flags.append("partial_source_coverage")
            if not succeeded:
                flags.append("no_sources_available")
            sources_detail = []
            for row in rows:
                source_snapshots = [s for s in snapshots if s.source == row.source]
                detail = self._source_detail(row, source_snapshots)
                detail_flags = list(detail.get("quality_flags", []))
                flags.extend(f for f in detail_flags if f not in flags)
                sources_detail.append(detail)
            total_snapshots = len(snapshots)
            title_only = sum(item.content_completeness == "title_only" for item in snapshots)
            incomplete = sum(item.content_completeness != "full" for item in snapshots)
            estimated_dates = sum(item.date_precision != "exact" for item in snapshots)
            independent_events = len({item.event_cluster_id or item.id for item in snapshots})
            degraded = bool(missing or title_only or incomplete or estimated_dates)
            evidence_ids = dict(session.execute(
                select(EvidenceModel.snapshot_id, EvidenceModel.id).where(
                    EvidenceModel.analysis_run_id == run_id
                )
            ).all())
            return {
                "source_coverage": round(coverage, 6),
                "analysis_quality_status": "degraded" if degraded else "complete",
                "missing_sources": missing,
                "quality_flags": flags,
                "sources": sources_detail,
                "snapshots": [
                    {
                        "snapshot_id": item.id,
                        "evidence_id": evidence_ids.get(item.id),
                        "event_cluster_id": item.event_cluster_id,
                        "source": item.source,
                        "source_type": item.source_type,
                        "external_id": item.external_id,
                        "source_version": item.source_version,
                        "source_version": item.source_version,
                        "title": item.title,
                        "url": item.url,
                        "normalized_url": item.normalized_url,
                        "published_at": item.published_at,
                        "date_precision": item.date_precision,
                        "captured_at": item.captured_at,
                        "content_completeness": item.content_completeness,
                        "metadata": item.snapshot_metadata,
                    }
                    for item in snapshots
                ],
                "quality_summary": {
                    "record_count": total_snapshots,
                    "independent_event_count": independent_events,
                    "title_only_count": title_only,
                    "title_only_ratio": round(title_only / total_snapshots, 6) if total_snapshots else 0.0,
                    "incomplete_content_count": incomplete,
                    "incomplete_content_ratio": round(incomplete / total_snapshots, 6) if total_snapshots else 0.0,
                    "estimated_date_count": estimated_dates,
                    "estimated_date_ratio": round(estimated_dates / total_snapshots, 6) if total_snapshots else 0.0,
                    "failed_source_count": len(missing),
                    "failed_source_ratio": round(len(missing) / len(rows), 6) if rows else 0.0,
                    "impact": "low-quality records participate with explicit weights; failed sources reduce coverage and score",
                },
            }

    def _source_detail(self, row, source_snapshots) -> dict[str, object]:
        source = row.source
        raw_count = len(source_snapshots)
        deduped_count = len({s.id for s in source_snapshots})
        parse_success = sum(
            1 for s in source_snapshots
            if s.title and s.title.strip() and len(s.title.strip()) >= 3
        )
        request_hours = (
            (row.completed_at - row.started_at).total_seconds() / 3600
            if row.started_at and row.completed_at else None
        )
        published_dates = [s.published_at for s in source_snapshots if s.published_at]
        if published_dates:
            earliest = min(published_dates)
            latest = max(published_dates)
            coverage_days = (latest - earliest).total_seconds() / 86400
            latest_aware = latest.replace(tzinfo=timezone.utc) if latest.tzinfo is None else latest
            freshness_hours = (
                (datetime.now(timezone.utc) - latest_aware).total_seconds() / 3600
            )
        else:
            earliest = latest = None
            coverage_days = 0.0
            freshness_hours = float("inf")
        field_counts = {"title": 0, "abstract": 0, "url": 0, "published_at": 0}
        for s in source_snapshots:
            if s.title and s.title.strip():
                field_counts["title"] += 1
            if s.content and len(s.content) > len(s.title or ""):
                field_counts["abstract"] += 1
            if s.url and s.url.strip():
                field_counts["url"] += 1
            if s.published_at:
                field_counts["published_at"] += 1
        total = max(raw_count, 1)
        field_completeness = sum(v / total for v in field_counts.values()) / len(field_counts) * 100
        parse_rate = (parse_success / total * 100) if raw_count else 0.0
        if published_dates and len(published_dates) >= 2:
            daily_counts: dict[str, int] = {}
            for dt in published_dates:
                key = dt.date().isoformat()
                daily_counts[key] = daily_counts.get(key, 0) + 1
            mean_val = sum(daily_counts.values()) / len(daily_counts)
            variance = sum((v - mean_val) ** 2 for v in daily_counts.values()) / len(daily_counts)
            std_val = variance ** 0.5
            temporal_distribution = max(0.0, (1.0 - min(std_val / mean_val, 1.0)) * 100) if mean_val else 0.0
        else:
            temporal_distribution = 0.0
        dimension_values = {
            "time_window_coverage": min(coverage_days / max(request_hours or 24, 1) * 24 * 100, 100.0),
            "sample_sufficiency": min(deduped_count / EXPECTED_MINIMUMS.get(source, 10.0), 1.0) * 100,
            "data_freshness": max(0.0, (1.0 - min(freshness_hours, 168) / 168) * 100),
            "field_completeness": round(field_completeness, 6),
            "parse_success_rate": round(parse_rate, 6),
            "temporal_distribution": round(temporal_distribution, 6),
            "source_specific_signal": 50.0,
        }
        score, grade, dimension_flags = compute_source_quality_grade(source, dimension_values)
        return {
            "source": source,
            "status": row.status,
            "records_fetched": row.records_fetched,
            "error": row.error_message,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "quality_grade": grade,
            "quality_score": score,
            "dimensions": dimension_values,
            "quality_flags": dimension_flags,
            "raw_count": raw_count,
            "deduped_count": deduped_count,
            "coverage_window": (
                {"earliest": earliest.isoformat(), "latest": latest.isoformat()}
                if earliest and latest else None
            ),
            "freshness_hours": round(freshness_hours, 2) if freshness_hours != float("inf") else None,
        }

    def signals(self, run_id: str) -> list[dict[str, object]]:
        with self.sessions() as session:
            rows = session.scalars(select(SignalObservationModel).where(SignalObservationModel.analysis_run_id == run_id).order_by(SignalObservationModel.source, SignalObservationModel.industry_domain))
            return [{"id": row.id, "analysis_run_id": row.analysis_run_id, "source": row.source, "industry_domain": row.industry_domain, "week_start": row.week_start, "signal_strength": row.signal_strength, "raw_value": row.raw_value, "keywords": row.keywords, "evidence_snapshot_ids": row.evidence_snapshot_ids} for row in rows]

    def predictions(self, run_id: str) -> list[dict[str, object]]:
        with self.sessions() as session:
            rows = session.scalars(select(PredictionResultModel).where(PredictionResultModel.analysis_run_id == run_id).order_by(PredictionResultModel.emergence_score.desc(), PredictionResultModel.job_name))
            return [{"id": row.id, "analysis_run_id": row.analysis_run_id, "job_name": row.job_name, "industry_domain": row.industry_domain, "emergence_score": row.emergence_score, "source_scores": row.source_scores, "related_keywords": row.related_keywords, "evidence_snapshot_ids": row.evidence_snapshot_ids, "algorithm_version": row.algorithm_version, "formula_version": row.formula_version, "time_window": {"start": row.window_start, "end": row.window_end}, "source_coverage": row.source_coverage, "missing_sources": row.missing_sources, "quality_flags": row.quality_flags, "config_versions": row.config_versions, "score_explanation": row.score_explanation} for row in rows]

    def prediction_explanation(self, run_id: str, prediction_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.scalar(select(PredictionResultModel).where(
                PredictionResultModel.analysis_run_id == run_id,
                PredictionResultModel.id == prediction_id,
            ))
            if row is None:
                return None
            return {
                "prediction_id": row.id, "analysis_run_id": row.analysis_run_id,
                "job_name": row.job_name, "emergence_score": row.emergence_score,
                "quality_flags": row.quality_flags, "config_versions": row.config_versions,
                "explanation": row.score_explanation,
            }

    def save_position_skill_trend(self, run_id: str, payload: dict[str, object]) -> None:
        with self.sessions.begin() as session:
            existing = session.scalar(select(PositionSkillTrendResultModel).where(
                PositionSkillTrendResultModel.analysis_run_id == run_id,
                PositionSkillTrendResultModel.position_id == str(payload["position_id"]),
                PositionSkillTrendResultModel.graph_version == str(payload["graph_version"]),
            ))
            if existing is None:
                session.add(PositionSkillTrendResultModel(
                    analysis_run_id=run_id,
                    position_id=str(payload["position_id"]),
                    position_name=str(payload["position_name"]),
                    graph_version=str(payload["graph_version"]),
                    skill_catalog_version=str(payload["skill_catalog_version"]),
                    algorithm_version=str(payload["algorithm_version"]),
                    formula_version=str(payload["formula_version"]),
                    config_version=str(payload["config_version"]),
                    result_payload=payload,
                ))

    def replace_position_skill_result(
        self,
        run_id: str,
        clusters: Sequence[EventCluster],
        terms: Sequence[ExtractedTerm],
        payload: dict[str, object],
    ) -> None:
        """Persist evidence, extracted terms and the formal KG-bound report atomically."""
        with self.sessions.begin() as session:
            session.execute(delete(PositionSkillTrendResultModel).where(
                PositionSkillTrendResultModel.analysis_run_id == run_id
            ))
            session.execute(delete(EvidenceModel).where(
                EvidenceModel.analysis_run_id == run_id
            ))
            snapshots = [
                (cluster, snapshot)
                for cluster in clusters
                for snapshot in cluster.snapshots
            ]
            snapshot_ids = list(dict.fromkeys(snapshot.id for _, snapshot in snapshots))
            snapshot_rows: dict[str, SourceSnapshotModel] = {}
            for chunk in self._chunks(snapshot_ids):
                snapshot_rows.update({
                    row.id: row for row in session.scalars(
                        select(SourceSnapshotModel).where(SourceSnapshotModel.id.in_(chunk))
                    )
                })
            missing = set(snapshot_ids) - set(snapshot_rows)
            if missing:
                raise RuntimeError(
                    f"source snapshots are missing: {', '.join(sorted(missing)[:5])}"
                )
            evidence_rows = []
            for cluster, snapshot in snapshots:
                weight, flags = quality_weight(snapshot)
                snapshot_rows[snapshot.id].event_cluster_id = cluster.id
                evidence_rows.append(EvidenceModel(
                    analysis_run_id=run_id,
                    snapshot_id=snapshot.id,
                    event_cluster_id=cluster.id,
                    contribution_weight=weight,
                    quality_flags=list(flags),
                ))
            session.add_all(evidence_rows)

            existing: set[tuple[str, str, str]] = set()
            term_snapshot_ids = list(dict.fromkeys(term.snapshot_id for term in terms))
            versions = list(dict.fromkeys(term.extractor_version for term in terms))
            for chunk in self._chunks(term_snapshot_ids):
                existing.update(session.execute(
                    select(
                        ExtractedTermModel.snapshot_id,
                        ExtractedTermModel.term,
                        ExtractedTermModel.extractor_version,
                    ).where(
                        ExtractedTermModel.snapshot_id.in_(chunk),
                        ExtractedTermModel.extractor_version.in_(versions),
                    )
                ).all())
            session.add_all(
                ExtractedTermModel(
                    snapshot_id=term.snapshot_id,
                    term=term.term,
                    score=term.score,
                    week_start=date.fromisoformat(term.week_start),
                    extractor_version=term.extractor_version,
                )
                for term in terms
                if (term.snapshot_id, term.term, term.extractor_version) not in existing
            )
            session.add(PositionSkillTrendResultModel(
                analysis_run_id=run_id,
                position_id=str(payload["position_id"]),
                position_name=str(payload["position_name"]),
                graph_version=str(payload["graph_version"]),
                skill_catalog_version=str(payload["skill_catalog_version"]),
                algorithm_version=str(payload["algorithm_version"]),
                formula_version=str(payload["formula_version"]),
                config_version=str(payload["config_version"]),
                result_payload=payload,
            ))

    def position_skill_trend(self, run_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.scalar(select(PositionSkillTrendResultModel).where(
                PositionSkillTrendResultModel.analysis_run_id == run_id
            ))
            return dict(row.result_payload) if row else None
