from __future__ import annotations

import httpx
import pytest

from app.integrations.emerging_discovery.client import EmergingDiscoveryClient
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError


def test_invalid_json_from_discovery_is_a_structured_502(monkeypatch):
    class InvalidJsonResponse:
        status_code = 200

        def json(self):
            raise ValueError("invalid upstream json")

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: InvalidJsonResponse())

    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient(
            "http://discovery.invalid",
            token="test-internal-service-token",
        ).create_run({})

    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_invalid_json"
    assert isinstance(captured.value.__cause__, ValueError)


@pytest.mark.parametrize("version", [None, "discovery.v1", "discovery.v999"])
def test_discovery_client_rejects_missing_unknown_and_incompatible_response_versions(
    monkeypatch, version
):
    class Response:
        status_code = 200

        def json(self):
            data = {
                "run_id": "run-1",
                "status": "succeeded",
                "algorithm_version": "algorithm-v1",
                "clusters": [],
            }
            if version is not None:
                data["contract_version"] = version
            return {"code": 0, "message": "success", "data": data}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").create_run({})
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


@pytest.mark.parametrize("status_code", [401, 404, 422, 502, 503, 500])
def test_discovery_client_preserves_upstream_http_status(monkeypatch, status_code):
    class Response:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return {"detail": f"upstream {status_code}"}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())

    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").create_run({})

    assert captured.value.status_code == status_code
    assert captured.value.details["upstream_status"] == status_code
