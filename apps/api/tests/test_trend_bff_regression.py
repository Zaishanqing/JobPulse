"""A10: Trend BFF / Contract 关键回归测试。

验证主系统代理层没有丢掉：version, window, lineage, filter scope。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.contexts.market_intelligence._ports.trend_intelligence_gateway_v1 import (
    TrendIntelligenceGatewayError,
)
from app.infrastructure.trend_intelligence_gateway import (
    HttpTrendIntelligenceGatewayV1,
    TrendIntelligenceHttpClient,
)


# ── helpers ──────────────────────────────────────────────────────────

def _gateway(handler) -> HttpTrendIntelligenceGatewayV1:
    return HttpTrendIntelligenceGatewayV1(TrendIntelligenceHttpClient(
        base_url="http://trend.test",
        token="secret",
        timeout_seconds=5,
        max_retries=0,
        retry_backoff_seconds=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ))


def _change_point(subject_id: str, window_label: str, trend_state: str,
                  source_record_ids: list[str]) -> dict:
    return {
        "subject_id": subject_id,
        "window_label": window_label,
        "change_type": "sudden_change",
        "trend_state": trend_state,
        "magnitude": 0.75,
        "confidence": 0.85,
        "source_record_ids": source_record_ids,
        "window_start": "2026-06-01",
        "window_end": "2026-06-30",
        "algorithm_version": "trend-change-v2",
        "config_version": "config.v2",
    }


# ── A10.1: version identity ──────────────────────────────────────────

def test_bff_from_history_preserves_version_identity():
    """趋势历史分析结果包含 algorithm_version + config_version 且 BFF 不丢弃。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        return httpx.Response(200, json={"data": {
            "analysis_id": "ca-version-01",
            "status": "succeeded",
            "algorithm_version": body.get("algorithm_version", "trend-change-v2"),
            "config_version": body.get("config_version", "config.v2"),
            "filter": {
                "summary_scope": "global",
                "applied_filters": [],
            },
            "subjects": [
                {
                    "subject_id": "k8s",
                    "canonical_name": "Kubernetes",
                    "trend_state": "accelerating",
                    "window_scores": [
                        {
                            "window_label": "2026-Q1",
                            "score": 0.72,
                            "algorithm_version": "trend-change-v2",
                            "config_version": "config.v2",
                        },
                    ],
                },
            ],
            "change_points": [],
        }})

    gw = _gateway(handler)
    result = gw.create_trend_change_from_history({
        "subject_ids": ["k8s"],
        "algorithm_version": "trend-change-v2",
        "config_version": "config.v2",
    })

    assert result["algorithm_version"] == "trend-change-v2"
    assert result["config_version"] == "config.v2"
    subject = result["subjects"][0]
    ws = subject["window_scores"][0]
    assert ws["algorithm_version"] == "trend-change-v2"
    assert ws["config_version"] == "config.v2"


# ── A10.2: filter summary scope ──────────────────────────────────────

def test_bff_exposes_filter_summary_scope():
    """filter 结果包含 summary_scope 元数据。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {
            "analysis_id": "ca-filter-01",
            "status": "succeeded",
            "algorithm_version": "trend-change-v2",
            "config_version": "config.v2",
            "filter": {
                "summary_scope": "global",
                "applied_filters": ["min_window_count:4", "min_confidence:0.5"],
                "pre_filter_count": 15,
                "post_filter_count": 8,
                "note": "full-series summary; per-subject windows available in subjects[].window_scores",
            },
            "subjects": [],
            "change_points": [],
        }})

    gw = _gateway(handler)
    result = gw.create_trend_change_from_history({
        "subject_ids": ["docker", "k8s", "terraform"],
    })

    assert result["filter"]["summary_scope"] == "global"
    assert "applied_filters" in result["filter"]
    assert result["filter"]["post_filter_count"] <= result["filter"]["pre_filter_count"]


# ── A10.3: version incompatible error ────────────────────────────────

def test_bff_propagates_version_incompatible_error():
    """版本不兼容错误被正确传播，不被静默吞掉。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={
            "detail": (
                "TREND_SERIES_VERSION_INCOMPATIBLE: history windows span "
                "multiple algorithm versions (['algo-v1', 'algo-v2']) "
                "or config versions (['config.v1', 'config.v2'])."
            ),
            "code": "TREND_SERIES_VERSION_INCOMPATIBLE",
            "retryable": False,
        })

    gw = _gateway(handler)
    with pytest.raises(TrendIntelligenceGatewayError) as exc:
        gw.create_trend_change_from_history({
            "subject_ids": ["k8s"],
            "algorithm_version": "algo-v1",
        })
    assert "VERSION_INCOMPATIBLE" in str(exc.value)
    assert exc.value.status_code == 409


# ── A10.4: change point source lineage ───────────────────────────────

def test_bff_change_points_keep_source_lineage():
    """Change point 结果保留 source_record_ids 用于回溯。"""

    source_ids = [
        "arxiv-2026-06-15-001",
        "github-2026-06-20-042",
        "cvf-2026-06-22-018",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {
            "analysis_id": "ca-cp-01",
            "status": "completed",
            "algorithm_version": "trend-change-v2",
            "config_version": "config.v2",
            "filter": {"summary_scope": "per_subject"},
            "subjects": [
                {
                    "subject_id": "k8s",
                    "canonical_name": "Kubernetes",
                    "trend_state": "accelerating",
                    "window_scores": [],
                },
            ],
            "change_points": [
                {
                    "subject_id": "k8s",
                    "window_label": "2026-Q2",
                    "change_type": "sudden_change",
                    "trend_state": "accelerating",
                    "magnitude": 0.82,
                    "confidence": 0.88,
                    "algorithm_version": "trend-change-v2",
                    "config_version": "config.v2",
                    "source_record_ids": source_ids,
                    "window_start": "2026-06-01",
                    "window_end": "2026-06-30",
                },
            ],
        }})

    gw = _gateway(handler)
    result = gw.get_trend_change_points("ca-cp-01")

    cp = result["change_points"][0]
    assert cp["source_record_ids"] == source_ids
    assert cp["algorithm_version"] == "trend-change-v2"
    assert cp["config_version"] == "config.v2"
    assert cp["window_start"] == "2026-06-01"
    assert cp["window_end"] == "2026-06-30"


# ── 补充：get_trend_change_analysis 也保留 lineage ──────────────────

def test_bff_analysis_detail_keeps_window_lineage():
    """get_trend_change_analysis 保留 window 级别 lineage 元数据。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {
            "analysis_id": "ca-lineage-01",
            "status": "succeeded",
            "algorithm_version": "trend-change-v2",
            "config_version": "config.v2",
            "filter": {"summary_scope": "global"},
            "subjects": [
                {
                    "subject_id": "react",
                    "canonical_name": "React",
                    "trend_state": "stable",
                    "window_scores": [
                        {
                            "window_label": "2026-01",
                            "score": 0.65,
                            "window_start": "2026-01-01",
                            "window_end": "2026-01-31",
                            "algorithm_version": "trend-change-v2",
                            "config_version": "config.v2",
                            "source_coverage": 0.9,
                            "source_count": 4,
                        },
                        {
                            "window_label": "2026-02",
                            "score": 0.68,
                            "window_start": "2026-02-01",
                            "window_end": "2026-02-28",
                            "algorithm_version": "trend-change-v2",
                            "config_version": "config.v2",
                            "source_coverage": 0.85,
                            "source_count": 4,
                        },
                    ],
                },
            ],
            "change_points": [],
        }})

    gw = _gateway(handler)
    result = gw.get_trend_change_analysis("ca-lineage-01")

    subject = result["subjects"][0]
    assert len(subject["window_scores"]) == 2
    for ws in subject["window_scores"]:
        assert ws["algorithm_version"] == "trend-change-v2"
        assert ws["config_version"] == "config.v2"
        assert "window_start" in ws
        assert "window_end" in ws
        assert "source_coverage" in ws
