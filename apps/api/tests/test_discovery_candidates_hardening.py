"""D1.1 hardening tests: strict Candidate contract validation and lifecycle gate.

Client tests exercise EmergingDiscoveryClient contract validation with realistic
upstream payloads; governance tests exercise the service-side lifecycle gate that
only allows stable_emerging_role candidates to enter the EmergingPosition chain.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.integrations.emerging_discovery.client import EmergingDiscoveryClient
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError
from app.main import app
from app.models.position_cluster import PositionCluster
from tests.runtime_database import SessionLocal, reset_database_data
from tests.user_factory import create_internal_user


client = TestClient(app)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _envelope(data):
    return {"code": 0, "message": "success", "data": data}


def _candidate(candidate_id: str = "cand-1", *, status: str = "stable_emerging_role", **overrides) -> dict:
    payload = {
        "candidate_id": candidate_id,
        "status": status,
        "first_seen_window_id": "2026-03",
        "last_seen_window_id": "2026-06",
        "age": 4,
        "current_cluster_id": "cluster-4",
        "previous_cluster_ids": ["cluster-1"],
        "canonical_title": "AI Agent Developer",
        "display_title": "Agent Engineer",
        "definition": {"position_name": "Agent Engineer"},
        "support_count": 5,
        "company_coverage": 3,
        "skill_similarity": 0.9,
        "responsibility_similarity": 0.85,
        "title_similarity": 0.8,
        "membership_overlap": 0.7,
        "identity_similarity": 0.92,
        "novelty_score": 0.6,
        "emergence_score": 0.78,
        "evidence": {"sample_count": 5},
        "identity_stability": 4,
        "identity_profile": {
            "titles": ["AI Agent Developer"],
            "skills": ["python", "rag"],
            "responsibilities": ["build agents"],
            "member_jd_ids": ["JD-1"],
            "observed_window_ids": ["2026-03"],
            "semantic_centroid": [0.1, 0.2],
        },
        "created_at": "2026-03-31T00:00:00+00:00",
        "updated_at": "2026-06-30T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _observation(candidate_id: str = "cand-1", **overrides) -> dict:
    payload = {
        "observation_id": "obs-1",
        "candidate_id": candidate_id,
        "run_id": "run-1",
        "cluster_id": "cluster-1",
        "cluster_name": "AI Agent 岗位簇",
        "window_id": "2026-03",
        "title": "AI Agent Developer",
        "status": "weak_signal",
        "emergence_score": 0.42,
        "support_count": 2,
        "company_count": 1,
        "identity_similarity": 1.0,
        "skill_similarity": 0.9,
        "responsibility_similarity": 0.85,
        "title_similarity": 0.8,
        "membership_overlap": 0.7,
        "semantic_similarity": 0.95,
        "evidence": {"sample_count": 2},
        "match_evidence": {
            "matched": True,
            "closest_candidate_id": "cand-1",
            "identity_similarity": 0.92,
            "threshold": 0.6,
            "components": {
                "title_similarity": 0.8,
                "skill_similarity": 0.9,
                "responsibility_similarity": 0.85,
                "membership_overlap": 0.7,
                "semantic_similarity": 0.95,
            },
            "decision_reason": "identity_similarity 0.92 >= threshold 0.6",
            "decision_version": "candidate-identity-v1",
        },
        "created_at": "2026-03-28T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _reset_database():
    reset_database_data()
    yield
    reset_database_data()


# ── Client / Gateway contract validation ──────────────────────────────────────


def test_client_rejects_non_object_json_body(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, []))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_string_json_body(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, "hello"))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_empty_candidate_entry(monkeypatch):
    import httpx

    payload = _envelope({"candidates": [{}], "filters": {}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert captured.value.details["errors"]


def test_client_rejects_candidate_without_candidate_id(monkeypatch):
    import httpx

    bad = _candidate()
    del bad["candidate_id"]
    payload = _envelope({"candidates": [bad], "filters": {}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any("candidate_id" in error for error in captured.value.details["errors"])


@pytest.mark.parametrize("status", ["weird_status", None, "", 42])
def test_client_rejects_unknown_candidate_status(monkeypatch, status):
    import httpx

    payload = _envelope({"candidates": [_candidate(status=status)], "filters": {}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any("status" in error for error in captured.value.details["errors"])


@pytest.mark.parametrize("field", ["age", "support_count", "company_coverage", "identity_stability"])
def test_client_rejects_damaged_integer_fields(monkeypatch, field):
    import httpx

    bad = _candidate(**{field: "4"})
    payload = _envelope({"candidates": [bad], "filters": {}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any(field in error for error in captured.value.details["errors"])


@pytest.mark.parametrize("field", ["identity_similarity", "novelty_score", "emergence_score"])
def test_client_rejects_damaged_numeric_fields(monkeypatch, field):
    import httpx

    bad = _candidate(**{field: "high"})
    payload = _envelope({"candidates": [bad], "filters": {}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_negative_age(monkeypatch):
    import httpx

    payload = _envelope({"candidates": [_candidate(age=-1)], "filters": {}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any("age" in error for error in captured.value.details["errors"])


def test_client_rejects_detail_candidate_id_mismatch(monkeypatch):
    import httpx

    detail = {"candidate": _candidate(candidate_id="cand-other"), "latest_observation": None}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(detail)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_detail_latest_observation_candidate_id_mismatch(monkeypatch):
    import httpx

    detail = {
        "candidate": _candidate(),
        "latest_observation": _observation(candidate_id="cand-other"),
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(detail)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_trajectory_candidate_id_mismatch(monkeypatch):
    import httpx

    payload = {"candidate_id": "cand-other", "trajectory": [_observation()]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(payload)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate_trajectory("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_trajectory_observation_candidate_id_mismatch(monkeypatch):
    import httpx

    payload = {"candidate_id": "cand-1", "trajectory": [_observation(candidate_id="cand-other")]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(payload)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate_trajectory("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"


def test_client_rejects_malformed_match_evidence(monkeypatch):
    import httpx

    malformed = _observation()
    malformed["match_evidence"] = {"matched": "yes", "threshold": 0.6}
    payload = {"candidate_id": "cand-1", "trajectory": [malformed]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(payload)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate_trajectory("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any("matched" in error for error in captured.value.details["errors"])


def test_client_rejects_match_evidence_missing_components(monkeypatch):
    import httpx

    malformed = _observation()
    del malformed["match_evidence"]["components"]
    payload = {"candidate_id": "cand-1", "trajectory": [malformed]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(payload)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate_trajectory("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any("components" in error for error in captured.value.details["errors"])


def test_client_rejects_malformed_observation(monkeypatch):
    import httpx

    malformed = _observation()
    del malformed["evidence"]
    payload = {"candidate_id": "cand-1", "trajectory": [malformed]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, _envelope(payload)))
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").get_candidate_trajectory("cand-1")
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
    assert any("evidence" in error for error in captured.value.details["errors"])


# ── Governance lifecycle gate ─────────────────────────────────────────────────


def _login(username: str = "admin", role: str = "admin") -> str:
    create_internal_user(username, role)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_cluster(cluster_id: str) -> None:
    with SessionLocal() as session:
        session.add(
            PositionCluster(
                id=cluster_id,
                cluster_name="Agent Engineer 岗位簇",
                sample_count=5,
                representative_jd_ids=["JD-1"],
                discovery_run_id="run-1",
                discovery_run_status="succeeded",
                discovery_assessment={"germination_score": 0.78, "score_dimensions": {}},
                generated_definition={
                    "position_name": "Agent Engineer",
                    "core_responsibilities": ["构建智能体应用"],
                    "required_skills": [{"raw_skill": "Python"}, {"raw_skill": "RAG"}],
                    "bonus_skills": [],
                    "industry_scenarios": [],
                    "field_evidence": {},
                },
            )
        )
        session.commit()


@pytest.mark.parametrize(
    "status",
    ["weak_signal", "incubating", "emerging_candidate", "dead", "noise", "official_position"],
)
def test_governance_rejects_non_stable_candidates(monkeypatch, status):
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient as Client

    monkeypatch.setattr(
        Client,
        "get_candidate",
        lambda self, candidate_id: {"candidate": _candidate(status=status), "latest_observation": None},
    )
    token = _login()
    response = client.post(
        f"/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == 409
    assert body["data"]["error_code"] == "candidate_lifecycle_gate_rejected"
    assert body["data"]["candidate_status"] == status


def test_governance_rejects_stable_candidate_without_current_cluster(monkeypatch):
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient as Client

    monkeypatch.setattr(
        Client,
        "get_candidate",
        lambda self, candidate_id: {
            "candidate": _candidate(current_cluster_id=None),
            "latest_observation": None,
        },
    )
    token = _login()
    response = client.post(
        "/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert response.status_code == 409
    assert response.json()["data"]["error_code"] == "candidate_lifecycle_cluster_missing"


def test_governance_rejects_stable_candidate_with_unprojected_cluster(monkeypatch):
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient as Client

    monkeypatch.setattr(
        Client,
        "get_candidate",
        lambda self, candidate_id: {"candidate": _candidate(), "latest_observation": None},
    )
    token = _login()
    response = client.post(
        "/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert response.status_code == 409
    assert response.json()["data"]["error_code"] == "candidate_lifecycle_cluster_not_projected"


def test_governance_creates_emerging_position_for_stable_mapped_candidate(monkeypatch):
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient as Client

    _seed_cluster("cluster-4")
    monkeypatch.setattr(
        Client,
        "get_candidate",
        lambda self, candidate_id: {"candidate": _candidate(), "latest_observation": None},
    )
    token = _login()
    response = client.post(
        "/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "draft"
    assert data["cluster_id"] == "cluster-4"
    assert data["emerging_id"]
    assert data["field_evidence"]["candidate_lifecycle"] == {
        "candidate_id": "cand-1",
        "status": "stable_emerging_role",
        "emergence_score": 0.78,
        "observed_window_ids": ["2026-03"],
        "support_count": 5,
        "company_coverage": 3,
    }

    # 第二次调用保持幂等：返回同一个 EmergingPosition，不重复创建。
    second = client.post(
        "/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert second.status_code == 200
    assert second.json()["data"]["emerging_id"] == data["emerging_id"]


def test_governance_returns_409_when_generated_definition_incomplete(monkeypatch):
    from app.integrations.emerging_discovery.client import EmergingDiscoveryClient as Client

    # Cluster 已投影，但 generated_definition 不完整 -> DiscoveryEvidenceUnavailable -> 409，不得 500。
    with SessionLocal() as session:
        session.add(
            PositionCluster(
                id="cluster-4",
                cluster_name="Agent Engineer 岗位簇",
                sample_count=5,
                representative_jd_ids=["JD-1"],
                discovery_run_id="run-1",
                discovery_run_status="succeeded",
                discovery_assessment={"germination_score": 0.78, "score_dimensions": {}},
                generated_definition={},
            )
        )
        session.commit()
    monkeypatch.setattr(
        Client,
        "get_candidate",
        lambda self, candidate_id: {"candidate": _candidate(), "latest_observation": None},
    )
    token = _login()
    response = client.post(
        "/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error_code"] == "candidate_lifecycle_definition_incomplete"


def test_governance_requires_permissions():
    token = _login("reviewer", role="reviewer")
    response = client.post(
        "/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance",
        headers=_headers(token),
    )
    assert response.status_code == 403


def test_governance_requires_authentication():
    response = client.post("/api/v1/portal/admin/discovery-candidates/cand-1/enter-governance")
    assert response.status_code == 401
