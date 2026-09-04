"""Main-system counterexamples for the independent emerging-discovery re-audit."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import Base, SessionLocal, engine
from app.integrations.emerging_discovery.client import EmergingDiscoveryClient
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError
from app.main import app
from app.models.emerging_position import EmergingPosition
from app.models.position_cluster import PositionCluster
from app.models.standard_position import StandardPosition
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _admin_headers(name: str) -> dict[str, str]:
    create_internal_user(name, "admin")
    client.post(
        "/api/v1/auth/register",
        json={
            "role": "admin",
            "username": name,
            "password": "password123",
            "email": f"{name}@example.com",
            "phone": "13800000000",
        },
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": name, "password": "password123"},
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _candidate(headers: dict[str, str]) -> str:
    with SessionLocal() as db:
        cluster = PositionCluster(
            cluster_name="审计反例岗位簇",
            algorithm="audit",
            sample_count=1,
            core_skills=[{"raw_skill": "RAG"}],
            representative_titles=["反例"],
            representative_jd_ids=["jd-audit"],
            stability_score=0.1,
            growth_score=0.9,
            distance_from_existing_positions=0.9,
            status="active",
        )
        db.add(cluster)
        db.commit()
        cluster_id = cluster.id
    response = client.post(
        f"/api/v1/emerging-positions/from-cluster/{cluster_id}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "candidate"
    return response.json()["data"]["emerging_id"]


def test_unreviewed_candidate_can_be_published_directly():
    headers = _admin_headers("audit_publish_admin")
    emerging_id = _candidate(headers)
    response = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/publish", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "published"


def test_unpublished_candidate_can_be_promoted_to_standard_position():
    headers = _admin_headers("audit_promote_admin")
    emerging_id = _candidate(headers)
    response = client.post(
        f"/api/v1/emerging-positions/{emerging_id}/promote-to-position", headers=headers
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        candidate = db.get(EmergingPosition, emerging_id)
        standard = (
            db.query(StandardPosition)
            .filter(StandardPosition.source_emerging_position_id == emerging_id)
            .one()
        )
        assert candidate.status == "verified"
        assert standard.status == "existing"


def test_timeout_and_connection_refusal_are_structured_503(monkeypatch):
    for error in (
        httpx.ReadTimeout("timeout"),
        httpx.ConnectError("refused"),
    ):
        monkeypatch.setattr(httpx, "post", lambda *args, _error=error, **kwargs: (_ for _ in ()).throw(_error))
        with pytest.raises(EmergingDiscoveryError) as captured:
            EmergingDiscoveryClient("http://audit.invalid").create_run({})
        assert captured.value.status_code == 503
        assert captured.value.error_code == "emerging_discovery_unavailable"


def test_illegal_json_success_response_is_a_structured_gateway_error(monkeypatch):
    class IllegalJsonResponse:
        status_code = 200

        def json(self):
            raise ValueError("illegal json")

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: IllegalJsonResponse())
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://audit.invalid").create_run({})
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_invalid_json"


@pytest.mark.parametrize(
    "body",
    [
        {"code": 0},
        {"code": 0, "data": []},
        {"message": "success", "data": {}},
    ],
)
def test_partial_success_response_is_a_structured_502(monkeypatch, body):
    class PartialResponse:
        status_code = 200

        def json(self):
            return body

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: PartialResponse())
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://audit.invalid").create_run({})
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
