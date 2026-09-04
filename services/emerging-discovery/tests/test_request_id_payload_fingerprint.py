from __future__ import annotations

from fastapi.testclient import TestClient

from app.infrastructure.models import DiscoveryRun
from app.main import app
from tests.runtime_database import SessionLocal
from tests.test_api import HEADERS, _payload


client = TestClient(app)


def _post(payload: dict):
    return client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)


def test_same_request_same_payload_returns_original_run_with_fingerprint():
    payload = _payload("fp-same")
    first = _post(payload)
    assert first.status_code == 201, first.text
    first_data = first.json()["data"]
    assert first_data["payload_fingerprint"].startswith("sha256:")

    repeated = _post(_payload("fp-same"))
    assert repeated.status_code == 201, repeated.text
    repeated_data = repeated.json()["data"]
    assert repeated_data["run_id"] == first_data["run_id"]
    assert repeated_data["payload_fingerprint"] == first_data["payload_fingerprint"]


def test_same_request_different_payload_returns_409_without_second_run():
    payload = _payload("fp-conflict")
    first = _post(payload)
    assert first.status_code == 201, first.text

    payload["config"]["conflict_marker"] = "changed"
    response = _post(payload)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == 409
    assert "different payload" in response.json()["message"]

    with SessionLocal() as session:
        assert session.query(DiscoveryRun).count() == 1


def test_equivalent_payload_snapshot_order_returns_original_run():
    payload = _payload("fp-order")
    first = _post(payload)
    assert first.status_code == 201, first.text
    first_data = first.json()["data"]

    reordered = _payload("fp-order")
    reordered["snapshots"] = list(reversed(reordered["snapshots"]))
    repeated = _post(reordered)
    assert repeated.status_code == 201, repeated.text
    repeated_data = repeated.json()["data"]
    assert repeated_data["run_id"] == first_data["run_id"]
    assert repeated_data["payload_fingerprint"] == first_data["payload_fingerprint"]
