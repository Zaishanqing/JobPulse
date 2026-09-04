from __future__ import annotations

from datetime import datetime, timezone

from app.domain.trend_change import TrendWindowScore
from app.ports.trend_history import TrendHistoryStore


class BuildTrendHistoricalSequence:
    def __init__(self, store: TrendHistoryStore) -> None:
        self.store = store

    def build(
        self,
        subject_id: str,
        subject_type: str,
        *,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int | None = None,
    ) -> list[TrendWindowScore]:
        rows = list(
            self.store.formal_windows(
                subject_id,
                subject_type,
                from_time=from_time,
                to_time=to_time,
            )
        )
        selected = self._deterministic_rows(rows)
        if limit is not None and limit > 0:
            selected = selected[-limit:]
        return [self._to_window_score(subject_id, subject_type, row) for row in selected]

    @staticmethod
    def _deterministic_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        def completed_at(row: dict[str, object]) -> datetime:
            value = row.get("completed_at")
            if value is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        ordered = sorted(
            rows,
            key=lambda row: (completed_at(row), str(row.get("run_id") or "")),
            reverse=True,
        )
        seen: set[tuple[datetime, datetime, str, str]] = set()
        selected: list[dict[str, object]] = []
        for row in ordered:
            window_start = row["window_start"]
            window_end = row["window_end"]
            algorithm_version = str(row.get("algorithm_version") or "")
            config_version = str(row.get("config_version") or "")
            if isinstance(window_start, str):
                start_key = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            else:
                start_key = window_start
            if isinstance(window_end, str):
                end_key = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
            else:
                end_key = window_end
            window_key = (start_key, end_key, algorithm_version, config_version)
            if window_key in seen:
                continue
            seen.add(window_key)
            selected.append(row)
        selected.sort(key=lambda row: row["window_start"])
        return selected

    @staticmethod
    def _to_window_score(
        subject_id: str,
        subject_type: str,
        row: dict[str, object],
    ) -> TrendWindowScore:
        window_start = row["window_start"]
        window_end = row["window_end"]
        if isinstance(window_start, str):
            start_value = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
        else:
            start_value = window_start
        if isinstance(window_end, str):
            end_value = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
        else:
            end_value = window_end
        duration_days = max((end_value - start_value).days, 1)
        source_scores = {
            str(key): float(value)
            for key, value in (row.get("source_scores") or {}).items()
        }
        record_ids = [str(value) for value in (row.get("source_record_ids") or [])]
        return TrendWindowScore(
            subject_id=subject_id,
            subject_type=subject_type,
            window=start_value.isoformat(),
            score=round(float(row["score"]), 6),
            duration_days=float(duration_days),
            source_diversity=sum(1 for value in source_scores.values() if value > 0),
            source_scores=source_scores,
            source_records=tuple(record_ids),
            evidence_ids=tuple(record_ids),
            trend_report_id=str(row["report_id"]),
            analysis_run_id=str(row["run_id"]),
            source_count=len(record_ids),
            algorithm_version=str(row.get("algorithm_version") or ""),
            config_version=str(row.get("config_version") or ""),
            window_start=start_value.isoformat(),
            window_end=end_value.isoformat(),
        )
