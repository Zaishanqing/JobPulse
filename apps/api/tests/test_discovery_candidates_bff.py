"""BFF tests for the emerging-discovery Candidate Lifecycle integration.

These tests exercise the main-system portal admin endpoints that proxy the
read-only candidate APIs of emerging-discovery. The upstream client is stubbed
with realistic payloads shaped exactly like the real candidate.v1 contract; the
BFF itself performs no mocking, no silent fallback and no fabricated data.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.contexts.discovery import QueryPositionDiscovery, RecentPositionSignal
from app.main import app
from app.integrations.emerging_discovery.client import EmergingDiscoveryClient
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError
from tests.runtime_database import reset_database_data
from tests.user_factory import create_internal_user


client = TestClient(app)


def _candidate_payload(candidate_id: str = "cand-1", *, status: str = "stable_emerging_role") -> dict:
    return {
        "candidate_id": candidate_id,
        "status": status,
        "first_seen_window_id": "2026-03",
        "last_seen_window_id": "2026-06",
        "age": 4,
        "current_cluster_id": "cluster-4",
        "previous_cluster_ids": ["cluster-1", "cluster-2", "cluster-3"],
        "canonical_title": "AI Agent Developer",
        "display_title": "Agent Engineer",
        "definition": {
            "position_name": "Agent Engineer",
            "core_responsibilities": ["构建智能体应用"],
            "required_skills": [{"raw_skill": "Python"}, {"raw_skill": "RAG"}],
        },
        "support_count": 5,
        "company_coverage": 3,
        "skill_similarity": 0.9,
        "responsibility_similarity": 0.85,
        "title_similarity": 0.8,
        "membership_overlap": 0.7,
        "identity_similarity": 0.92,
        "novelty_score": 0.6,
        "emergence_score": 0.78,
        "evidence": {"jd_ids": ["JD-1", "JD-2"]},
        "identity_stability": 4,
        "identity_profile": {
            "titles": ["AI Agent Developer", "Agent Engineer"],
            "skills": ["Python", "RAG", "Agent"],
            "responsibilities": ["构建智能体应用"],
            "member_jd_ids": ["JD-1", "JD-2", "JD-3"],
            "observed_window_ids": ["2026-03", "2026-04", "2026-05", "2026-06"],
            "semantic_centroid": [0.1, 0.2],
        },
        "created_at": "2026-03-31T00:00:00+00:00",
        "updated_at": "2026-06-30T00:00:00+00:00",
    }


def _observation_payload(
    *,
    observation_id: str = "obs-1",
    run_id: str = "run-1",
    cluster_id: str = "cluster-1",
    window_id: str = "2026-03",
    status: str = "weak_signal",
    title: str = "AI Agent Developer",
    emergence_score: float = 0.42,
) -> dict:
    return {
        "observation_id": observation_id,
        "candidate_id": "cand-1",
        "run_id": run_id,
        "cluster_id": cluster_id,
        "cluster_name": f"{title} 岗位簇",
        "window_id": window_id,
        "title": title,
        "status": status,
        "emergence_score": emergence_score,
        "support_count": 2,
        "company_count": 1,
        "identity_similarity": 1.0,
        "skill_similarity": 0.9,
        "responsibility_similarity": 0.85,
        "title_similarity": 0.8,
        "membership_overlap": 0.7,
        "semantic_similarity": 0.95,
        "evidence": {"jd_ids": [f"JD-{window_id}"]},
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
        "created_at": f"{window_id}-28T00:00:00+00:00",
    }


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


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


def _upstream_error(status_code: int, *, error_code: str, message: str, details: dict) -> EmergingDiscoveryError:
    return EmergingDiscoveryError(
        message,
        status_code=status_code,
        error_code=error_code,
        details=details,
    )


def test_public_recent_signals_return_only_source_backed_projection(monkeypatch):
    monkeypatch.setattr(
        QueryPositionDiscovery,
        "recent_signals",
        lambda self, actor: (
            RecentPositionSignal(
                signal_id="data-agent",
                position_name="Data Agent 研发工程师",
                representative_title="AI Agent 研发工程师（Data Agent·数据平台）",
                skills=("Multi-Agent", "RAG", "知识图谱"),
                observed_at=date(2026, 8, 1),
                source_jd_ids=("row:2262:2262",),
                source_count=1,
            ),
        ),
    )
    token = _login("reader", role="personal_user")

    response = client.get(
        "/api/v1/portal/emerging-position-signals",
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["observed_from"] == "2026-08-01"
    assert data["observed_to"] == "2026-08-01"
    assert data["source_contract"] == "published-jd-fact.v2"
    assert data["signals"][0]["source_jd_ids"] == ["row:2262:2262"]
    assert data["signals"][0]["skills"] == ["Multi-Agent", "RAG", "知识图谱"]


def test_public_recent_signals_require_login():
    response = client.get("/api/v1/portal/emerging-position-signals")
    assert response.status_code == 401


def test_candidate_list_proxies_upstream_and_echoes_filters(monkeypatch):
    captured: dict = {}

    def fake_list(self, **kwargs):
        captured.update(kwargs)
        return {"candidates": [_candidate_payload()], "filters": dict(kwargs)}

    monkeypatch.setattr(EmergingDiscoveryClient, "list_candidates", fake_list)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates",
        params={"status": "stable_emerging_role", "window_id": "2026-06"},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0
    assert captured["status"] == "stable_emerging_role"
    assert captured["window_id"] == "2026-06"
    assert captured["candidate_id"] is None
    candidates = body["data"]["candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["candidate_id"] == "cand-1"
    assert candidate["status"] == "stable_emerging_role"
    assert candidate["first_seen_window_id"] == "2026-03"
    assert candidate["last_seen_window_id"] == "2026-06"
    assert candidate["age"] == 4
    assert candidate["identity_profile"]["titles"] == ["AI Agent Developer", "Agent Engineer"]
    assert candidate["identity_profile"]["skills"] == ["Python", "RAG", "Agent"]
    assert candidate["identity_profile"]["member_jd_ids"] == ["JD-1", "JD-2", "JD-3"]
    assert body["data"]["filters"]["status"] == "stable_emerging_role"
    assert body["data"]["filters"]["window_id"] == "2026-06"


def test_candidate_list_candidate_id_filter_is_passed_through(monkeypatch):
    captured: dict = {}

    def fake_list(self, **kwargs):
        captured.update(kwargs)
        return {"candidates": [_candidate_payload()], "filters": dict(kwargs)}

    monkeypatch.setattr(EmergingDiscoveryClient, "list_candidates", fake_list)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates",
        params={"candidate_id": "cand-1"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert captured["candidate_id"] == "cand-1"
    assert captured["status"] is None
    assert captured["window_id"] is None


def test_candidate_detail_proxies_upstream(monkeypatch):
    monkeypatch.setattr(
        EmergingDiscoveryClient,
        "get_candidate",
        lambda self, candidate_id: {
            "candidate": _candidate_payload(),
            "latest_observation": _observation_payload(),
        },
    )
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates/cand-1",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["candidate"]["candidate_id"] == "cand-1"
    latest = body["data"]["latest_observation"]
    assert latest["run_id"] == "run-1"
    assert latest["cluster_id"] == "cluster-1"
    assert latest["window_id"] == "2026-03"
    assert latest["status"] == "weak_signal"


def test_candidate_trajectory_proxies_multi_window_chain(monkeypatch):
    trajectory = {
        "candidate_id": "cand-1",
        "trajectory": [
            _observation_payload(
                observation_id="obs-1",
                run_id="run-1",
                cluster_id="cluster-1",
                window_id="2026-03",
                status="weak_signal",
                emergence_score=0.42,
            ),
            _observation_payload(
                observation_id="obs-2",
                run_id="run-2",
                cluster_id="cluster-2",
                window_id="2026-04",
                status="incubating",
                emergence_score=0.55,
                title="AI Agent 开发工程师",
            ),
            _observation_payload(
                observation_id="obs-3",
                run_id="run-3",
                cluster_id="cluster-3",
                window_id="2026-05",
                status="emerging_candidate",
                emergence_score=0.66,
                title="智能体应用工程师",
            ),
            _observation_payload(
                observation_id="obs-4",
                run_id="run-4",
                cluster_id="cluster-4",
                window_id="2026-06",
                status="stable_emerging_role",
                emergence_score=0.78,
                title="Agent Engineer",
            ),
        ],
    }
    monkeypatch.setattr(
        EmergingDiscoveryClient,
        "get_candidate_trajectory",
        lambda self, candidate_id: trajectory,
    )
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates/cand-1/trajectory",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    points = body["data"]["trajectory"]
    assert [item["status"] for item in points] == [
        "weak_signal",
        "incubating",
        "emerging_candidate",
        "stable_emerging_role",
    ]
    assert [item["window_id"] for item in points] == [
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
    ]
    assert [item["run_id"] for item in points] == ["run-1", "run-2", "run-3", "run-4"]
    assert points[-1]["cluster_id"] == "cluster-4"
    assert points[0]["evidence"]["jd_ids"] == ["JD-2026-03"]


def test_candidate_diffusion_graph_is_readonly_passthrough(monkeypatch):
    graph = {
        "schema_version": "candidate-diffusion-graph.v1",
        "readonly": True,
        "scope": "single_candidate_observation_diffusion",
        "candidate_id": "cand-1",
        "nodes": [{"node_id": "candidate:cand-1", "node_type": "candidate"}],
        "edges": [],
        "boundaries": {
            "market_trend": False,
            "industry_evolution": False,
            "causal_diffusion": False,
            "emerging_market_conclusion": False,
        },
    }
    monkeypatch.setattr(
        EmergingDiscoveryClient,
        "get_candidate_diffusion",
        lambda self, candidate_id: graph,
    )
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates/cand-1/diffusion-graph",
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["data"] == graph


def test_candidate_detail_upstream_404_becomes_main_404(monkeypatch):
    def fake_get(self, candidate_id):
        raise _upstream_error(
            404,
            error_code="emerging_discovery_not_found",
            message="Discovery candidate not found",
            details={"upstream_status": 404},
        )

    monkeypatch.setattr(EmergingDiscoveryClient, "get_candidate", fake_get)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates/missing",
        headers=_headers(token),
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 404
    assert body["details"]["error_code"] == "emerging_discovery_not_found"


def test_candidate_trajectory_upstream_404_becomes_main_404(monkeypatch):
    def fake_trajectory(self, candidate_id):
        raise _upstream_error(
            404,
            error_code="emerging_discovery_not_found",
            message="Discovery candidate not found",
            details={"upstream_status": 404},
        )

    monkeypatch.setattr(EmergingDiscoveryClient, "get_candidate_trajectory", fake_trajectory)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates/missing/trajectory",
        headers=_headers(token),
    )
    assert response.status_code == 404
    assert response.json()["details"]["error_code"] == "emerging_discovery_not_found"


def test_candidate_list_client_translates_timeout_to_503(monkeypatch):
    import httpx

    class TimeoutResponse:
        pass

    def fake_get(*args, **kwargs):
        raise httpx.ConnectTimeout("upstream unreachable")

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").list_candidates()
    assert captured.value.status_code == 503
    assert captured.value.error_code == "emerging_discovery_unavailable"
    assert captured.value.details["reason"] == "ConnectTimeout"


def test_candidate_list_upstream_timeout_becomes_main_503(monkeypatch):
    def fake_list(self, **kwargs):
        raise _upstream_error(
            503,
            error_code="emerging_discovery_unavailable",
            message="Emerging discovery service is unavailable",
            details={"reason": "ConnectTimeout"},
        )

    monkeypatch.setattr(EmergingDiscoveryClient, "list_candidates", fake_list)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates",
        headers=_headers(token),
    )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == 503
    assert body["details"]["error_code"] == "emerging_discovery_unavailable"


@pytest.mark.parametrize("upstream_status", [500, 502, 503])
def test_candidate_list_upstream_5xx_is_preserved(monkeypatch, upstream_status):
    def fake_list(self, **kwargs):
        raise _upstream_error(
            upstream_status,
            error_code="emerging_discovery_upstream_error",
            message="Emerging discovery candidates failed",
            details={"upstream_status": upstream_status},
        )

    monkeypatch.setattr(EmergingDiscoveryClient, "list_candidates", fake_list)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates",
        headers=_headers(token),
    )
    assert response.status_code == upstream_status
    assert response.json()["details"]["error_code"] == "emerging_discovery_upstream_error"


def test_candidate_list_upstream_invalid_json_becomes_502(monkeypatch):
    def fake_list(self, **kwargs):
        raise _upstream_error(
            502,
            error_code="emerging_discovery_invalid_json",
            message="Emerging discovery returned invalid JSON",
            details={},
        )

    monkeypatch.setattr(EmergingDiscoveryClient, "list_candidates", fake_list)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates",
        headers=_headers(token),
    )
    assert response.status_code == 502
    assert response.json()["details"]["error_code"] == "emerging_discovery_invalid_json"


def test_candidate_list_upstream_invalid_contract_becomes_502(monkeypatch):
    def fake_list(self, **kwargs):
        raise _upstream_error(
            502,
            error_code="emerging_discovery_contract_error",
            message="Emerging discovery returned an invalid candidate list contract",
            details={"contract": "candidates.v1"},
        )

    monkeypatch.setattr(EmergingDiscoveryClient, "list_candidates", fake_list)
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-candidates",
        headers=_headers(token),
    )
    assert response.status_code == 502
    assert response.json()["details"]["error_code"] == "emerging_discovery_contract_error"


def test_candidate_endpoints_require_discovery_permission():
    token = _login("reviewer", role="reviewer")
    for path in (
        "/api/v1/portal/admin/discovery-candidates",
        "/api/v1/portal/admin/discovery-candidates/cand-1",
        "/api/v1/portal/admin/discovery-candidates/cand-1/trajectory",
    ):
        response = client.get(path, headers=_headers(token))
        assert response.status_code == 403, path


def test_candidate_endpoints_require_authentication():
    response = client.get("/api/v1/portal/admin/discovery-candidates")
    assert response.status_code == 401


def test_existing_discovery_runs_endpoint_is_not_affected():
    token = _login()
    response = client.get(
        "/api/v1/portal/admin/discovery-runs",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


def test_client_run_contract_validation_is_unchanged(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "run_id": "run-1",
                    "status": "succeeded",
                    "algorithm_version": "algorithm-v1",
                    "clusters": [],
                },
            }

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: Response())
    with pytest.raises(EmergingDiscoveryError) as captured:
        EmergingDiscoveryClient("http://discovery.invalid").create_run({})
    assert captured.value.status_code == 502
    assert captured.value.error_code == "emerging_discovery_contract_error"
