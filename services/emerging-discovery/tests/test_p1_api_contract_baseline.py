"""Characterization guard for the discovery service OpenAPI contract."""

from __future__ import annotations

from app.main import app


def test_public_openapi_contract_matches_strict_v2_baseline() -> None:
    schema = app.openapi()
    request = schema["components"]["schemas"]["DiscoveryRunRequest"]
    assert request["properties"]["contract_version"]["const"] == "discovery.v2"
    assert "LegacyDiscoveryRequest" not in schema["components"]["schemas"]
    create = schema["paths"]["/api/v1/discovery-runs"]["post"]
    assert create["responses"]["201"]["content"]["application/json"]["schema"]
