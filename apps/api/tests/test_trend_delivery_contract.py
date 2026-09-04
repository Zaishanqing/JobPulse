from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.main import app
from app.schemas.trend_delivery import TrendDeliveryResource


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "trend-delivery.openapi.yaml"
TYPESCRIPT = ROOT / "docs" / "trend-delivery.types.ts"
SCENARIOS = ROOT / "docs" / "trend-delivery-scenarios.json"

REQUIRED_FIELDS = {
    "schema_version",
    "resource_type",
    "resource_id",
    "status",
    "progress",
    "source_coverage",
    "missing_sources",
    "quality_flags",
    "evidence_references",
    "review_status",
    "review_task_id",
    "publication_gate",
}

REQUIRED_PATHS = {
    "/predicted-positions/tasks",
    "/predicted-positions/tasks/{task_id}",
    "/predicted-positions",
    "/predicted-positions/batch-query",
    "/predicted-positions/{predicted_id}",
    "/trend-runs",
    "/trend-runs/batch-query",
    "/positions/{position_id}/trend-analysis/tasks",
    "/trend-analysis/tasks/{task_id}",
    "/positions/{position_id}/trend-reports",
    "/trend-reports/batch-query",
    "/trend-reports/{report_id}",
    "/trend-reports/{report_id}/publish",
}


def test_checked_in_openapi_matches_runtime_paths_and_common_fields() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    runtime = app.openapi()

    assert contract["info"]["version"] == "trend-delivery.v1"
    assert REQUIRED_PATHS <= set(contract["paths"])
    assert {f"/api/v1{path}" for path in REQUIRED_PATHS} <= set(runtime["paths"])
    properties = contract["components"]["schemas"]["TrendDeliveryResource"]["properties"]
    assert REQUIRED_FIELDS == set(properties)
    detail = contract["components"]["schemas"]["SkillTrendDetail"]
    assert {
        "growth_rate",
        "trend_direction",
        "evidence_references",
        "quality_flags",
        "score_explanation",
        "current_window_signal",
        "historical_window_signal",
    } <= set(detail["properties"])
    required = contract["components"]["schemas"]["TrendDeliveryResource"]["required"]
    assert set(required) == REQUIRED_FIELDS


def test_runtime_openapi_exposes_typed_delivery_envelopes_and_collection_queries() -> None:
    schema = app.openapi()
    detail = schema["paths"]["/api/v1/trend-reports/{report_id}"]["get"]
    response_schema = detail["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/TrendDeliveryEnvelope")

    listing = schema["paths"]["/api/v1/predicted-positions"]["get"]
    parameters = {item["name"] for item in listing["parameters"]}
    assert {"page", "page_size", "status", "quality_flag", "sort_by", "sort_order"} <= parameters
    list_schema = listing["responses"]["200"]["content"]["application/json"]["schema"]
    assert list_schema["$ref"].endswith("/TrendDeliveryCollectionEnvelope")


def test_six_frontend_scenarios_validate_against_shared_resource_schema() -> None:
    fixture = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    scenarios = fixture["scenarios"]
    assert {item["name"] for item in scenarios} == {
        "predicted_position",
        "successful_trend_report",
        "partial_source_failure",
        "complete_failure",
        "pending_review",
        "published",
    }
    for scenario in scenarios:
        TrendDeliveryResource.model_validate({"schema_version": fixture["schema_version"], **scenario})


def test_typescript_example_contains_all_stable_fields() -> None:
    source = TYPESCRIPT.read_text(encoding="utf-8")
    assert "interface TrendDeliveryResource" in source
    assert "interface TrendDeliveryCollection" in source
    assert "interface SkillTrendDetail" in source
    for field in REQUIRED_FIELDS:
        assert field in source
