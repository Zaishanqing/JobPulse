from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application.discovery_identity import normalize_algorithm
from app.bootstrap.application import create_app


HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def test_unknown_algorithms_are_rejected_without_fallback():
    with pytest.raises(ValueError, match="unsupported algorithm"):
        normalize_algorithm("typo-a")
    with pytest.raises(ValueError, match="unsupported algorithm"):
        normalize_algorithm("multi_view")
    selection = normalize_algorithm("emerge_v3_2")
    assert selection.canonical_name == "emerge_v3_2"
    assert selection.similarity_threshold.value == 0.72


def test_openapi_declares_strict_v2_and_complete_201_response():
    schema = create_app().openapi()
    request_schema = schema["components"]["schemas"]["DiscoveryRunRequest"]
    assert "contract_version" in request_schema["required"]
    assert request_schema["properties"]["contract_version"]["const"] == "discovery.v2"
    response = schema["paths"]["/api/v1/discovery-runs"]["post"]["responses"]["201"]
    assert response["content"]["application/json"]["schema"]
    result_schema = schema["components"]["schemas"]["DiscoveryRunSummary"]
    assert {"contract_version", "request_id", "run_id", "clusters"} <= set(
        result_schema["required"]
    )


def test_legacy_and_v2_missing_fact_identity_are_rejected():
    client = TestClient(create_app())
    legacy = {
        "snapshots": [
            {
                "jd_id": f"legacy-{index}",
                "schema_version": "v2",
                "review_status": "approved",
                "title": "legacy",
                "source_name": "source",
                "publish_date": f"2026-0{index}-01",
                "structured_data": {
                    "responsibilities": [],
                    "required_skills": [{"raw_skill": "X"}],
                    "bonus_skills": [],
                    "business_scenarios": [],
                },
            }
            for index in (1, 2)
        ],
        "position_references": [{"position_id": "POS-1", "required_skills": [{"raw_skill": "X"}]}],
    }
    response = client.post("/api/v1/discovery-runs", json=legacy, headers=HEADERS)
    assert response.status_code == 422

    v2 = {
        **legacy,
        "contract_version": "discovery.v2",
        "request_id": "bad-v2",
        "time_window_start": "2026-01-01",
        "time_window_end": "2026-02-28",
    }
    assert client.post("/api/v1/discovery-runs", json=v2, headers=HEADERS).status_code == 422
