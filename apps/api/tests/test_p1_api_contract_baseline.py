"""Semantic guard for the public skill-governance OpenAPI contract."""

from __future__ import annotations

from app.main import app


def test_public_openapi_contract_matches_typed_request_baseline() -> None:
    """Protect the published catalog lifecycle without a content fingerprint."""
    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/skills/normalize-candidates/re-normalize"]
    assert "get" in paths["/api/v1/skills/catalog/downstream"]
    assert "post" in paths["/api/v1/skills/catalog/publish"]
    assert "get" in paths["/api/v1/skills/catalog/versions/{catalog_version}"]
