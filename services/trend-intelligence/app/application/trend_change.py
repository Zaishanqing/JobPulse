from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from app.domain.trend_change import (
    DEFAULT_ALGORITHM_VERSION,
    DEFAULT_CONFIG_VERSION,
    DEFAULT_TREND_CHANGE_CONFIG,
    TrendWindowScore,
    analyze_trend_series,
)
from app.ports.trend_change import TrendChangeStore


class TrendChangeService:
    def __init__(
        self,
        store: TrendChangeStore,
        history_builder: object | None = None,
    ) -> None:
        self.store = store
        self.history_builder = history_builder

    def analyze(self, request: dict[str, object]) -> dict[str, object]:
        subjects = []
        for subject in request["subjects"]:
            windows = [
                TrendWindowScore(
                    subject_id=str(subject["subject_id"]),
                    subject_type=str(subject["subject_type"]),
                    window=str(item["window"]),
                    score=float(item["score"]),
                    duration_days=float(item.get("duration_days", 1.0)),
                    source_diversity=int(item.get("source_diversity", 0)),
                    source_records=tuple(
                        str(value) for value in item.get("source_records", [])
                    ),
                    evidence_ids=tuple(
                        str(value) for value in item.get("evidence_ids", [])
                    ),
                    trend_report_id=item.get("trend_report_id"),
                    analysis_run_id=item.get("analysis_run_id"),
                    source_count=int(item.get("source_count", 0)),
                    source_scores={
                        str(k): float(v)
                        for k, v in item.get("source_scores", {}).items()
                    },
                    algorithm_version=item.get("algorithm_version"),
                    config_version=item.get("config_version"),
                    window_start=item.get("window_start"),
                    window_end=item.get("window_end"),
                )
                for item in subject["windows"]
            ]
            subjects.append(
                asdict(
                    analyze_trend_series(
                        str(subject["subject_id"]),
                        str(subject["subject_type"]),
                        windows,
                        algorithm_version=DEFAULT_ALGORITHM_VERSION,
                        config_version=DEFAULT_CONFIG_VERSION,
                        config=DEFAULT_TREND_CHANGE_CONFIG,
                    )
                )
            )
        return self.store.create(self._payload(subjects))

    def analyze_from_history(self, request: dict[str, object]) -> dict[str, object]:
        if self.history_builder is None:
            raise ValueError(
                "HISTORY_BUILDER_UNAVAILABLE: trend history store is not configured"
            )
        subject_id = str(request["subject_id"])
        subject_type = str(request["subject_type"])
        windows = self.history_builder.build(
            subject_id,
            subject_type,
            from_time=request.get("from_time"),
            to_time=request.get("to_time"),
            limit=request.get("limit"),
        )
        if not windows:
            raise ValueError(
                f"HISTORY_NOT_FOUND: no formal trend windows for "
                f"{subject_type}:{subject_id}"
            )
        if len(windows) < 2:
            raise ValueError(
                f"INSUFFICIENT_HISTORY: only {len(windows)} formal window for "
                f"{subject_type}:{subject_id}; at least 2 required"
            )
        algorithm_versions = {item.algorithm_version for item in windows}
        config_versions = {item.config_version for item in windows}
        if len(algorithm_versions) > 1 or len(config_versions) > 1:
            raise ValueError(
                "TREND_SERIES_VERSION_INCOMPATIBLE: history windows span "
                f"multiple algorithm versions ({sorted(algorithm_versions)}) "
                f"or config versions ({sorted(config_versions)}). "
                "Recompute with a consistent version or explicitly re-align the series."
            )
        effective_algorithm = next(iter(algorithm_versions)) or DEFAULT_ALGORITHM_VERSION
        effective_config = next(iter(config_versions)) or DEFAULT_CONFIG_VERSION
        analysis = analyze_trend_series(
            subject_id,
            subject_type,
            windows,
            algorithm_version=effective_algorithm,
            config_version=effective_config,
            config=DEFAULT_TREND_CHANGE_CONFIG,
        )
        subject = asdict(analysis)
        subject["sequence_source"] = "persisted_trend_history"
        subject["included_run_ids"] = sorted(
            {item.analysis_run_id for item in windows if item.analysis_run_id}
        )
        subject["included_report_ids"] = sorted(
            {item.trend_report_id for item in windows if item.trend_report_id}
        )
        subject["window_count"] = len(windows)
        return self.store.create(self._payload([subject]))

    @staticmethod
    def _payload(subjects: list[dict[str, object]]) -> dict[str, object]:
        return {
            "algorithm_version": DEFAULT_ALGORITHM_VERSION,
            "config_version": DEFAULT_CONFIG_VERSION,
            "config": DEFAULT_TREND_CHANGE_CONFIG,
            "subjects": subjects,
        }

    def get(
        self,
        analysis_id: str,
        *,
        subject_id: str | None = None,
        window: str | None = None,
        trend_state: str | None = None,
    ) -> dict[str, object] | None:
        payload = self.store.get(analysis_id)
        if payload is None:
            return None
        return self._apply_filters(payload, subject_id, window, trend_state)

    def change_points(
        self,
        analysis_id: str,
        *,
        subject_id: str | None = None,
        window: str | None = None,
        trend_state: str | None = None,
    ) -> list[dict[str, object]] | None:
        payload = self.store.get(analysis_id)
        if payload is None:
            return None
        filtered = self._apply_filters(payload, subject_id, None, trend_state)
        points = [
            point
            for subject in filtered["subjects"]
            for point in subject["change_points"]
        ]
        if window:
            points = [
                point for point in points if point["change_point_window"] == window
            ]
        return points

    @staticmethod
    def _apply_filters(
        payload: dict[str, object],
        subject_id: str | None,
        window: str | None,
        trend_state: str | None,
    ) -> dict[str, object]:
        result = deepcopy(payload)
        subjects = []
        for subject in result["subjects"]:
            if subject_id and subject["subject_id"] != subject_id:
                continue
            if trend_state and subject["trend_state"] != trend_state:
                continue
            subjects.append(subject)
        filters_applied = []
        if window:
            filters_applied.append("window")
            for subject in subjects:
                subject["windows"] = [
                    item for item in subject["windows"] if item["window"] == window
                ]
                subject["change_points"] = [
                    point
                    for point in subject["change_points"]
                    if point["change_point_window"] == window
                ]
        result["subjects"] = subjects
        if filters_applied:
            result["filter"] = {
                "applied": filters_applied,
                "summary_scope": "global",
                "note": (
                    "trend_state, growth_rate, acceleration, state_confidence, "
                    "change_point_confidence, and compatibility confidence "
                    "reflect the full (unfiltered) series"
                ),
            }
        return result
