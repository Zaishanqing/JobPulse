from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
from app.contexts.knowledge_graph import ManageKnowledgeGraphIntegration
from app.integrations.knowledge_graph.service import KnowledgeGraphIntegrationService
from app.main import app
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from tests.runtime_database import reset_database_data, SessionLocal
from tests.user_factory import create_internal_user


def envelope(data, *, headers=None):
    return SimpleNamespace(
        code=0,
        message="success",
        data=data,
        details={},
        trace_id="kg-e2e",
        response_headers=headers or {},
    )


class StatefulKnowledgeGraph:
    def __init__(self):
        self.review_status = "pending"
        self.weight = 0.5
        self.calls: list[tuple[str, str]] = []

    def list_positions(self):
        return [{"position_id": "KG_POS", "name": "闭环岗位", "status": "active"}]

    def list_skills(self):
        return [{"skill_id": "KG_PY", "canonical_name": "Python", "status": "active"}]

    def build_graph(self, position_id, payload, **actor):
        assert position_id == "KG_POS"
        self.calls.append(("POST", f"/api/v1/positions/{position_id}/graph/build"))
        return envelope({"build_run_id": 101, "status": "succeeded", "summary": {"included_samples": 1, "relations": 1}})

    def portal_call(self, method, path, *, payload=None, params=None, **actor):
        self.calls.append((method, path))
        relation = {
            "relation_id": 501,
            "skill_id": "KG_PY",
            "canonical_name": "Python",
            "category_code": "LANG",
            "weight": self.weight,
            "confidence": 0.9,
            "importance_level": "core",
            "primary_modality": "required",
            "modality_distribution": {"required": 1},
            "trend_score": None,
            "revision": 2 if self.weight != 0.5 else 1,
            "metrics": {"support_document_count": 1, "support_count": 1, "trusted_evidence_ratio": 1, "unknown_ratio": 0},
        }
        if path == "/api/v1/positions/KG_POS/graph/drafts":
            return envelope({"draft_id": 900, "build_run_id": 101, "position_id": "KG_POS", "base_version_id": 10})
        if path == "/api/v1/graph/build-runs/101/graph":
            return envelope({"position_id": "KG_POS", "position": {"position_id": "KG_POS", "name": "闭环岗位", "category_code": "TECH"}, "skill_relations": [relation], "requirement_profile": [], "responsibilities": [], "company_context": [], "employment_context": [], "sample_stats": {"included_samples": 1}, "view_type": "draft", "draft_id": 900, "build_run_id": 101, "base_version_id": 10})
        if path == "/api/v1/positions/KG_POS/graph":
            return envelope({"position_id": "KG_POS", "position": {"position_id": "KG_POS", "name": "闭环岗位", "category_code": "TECH"}, "skill_relations": [relation], "requirement_profile": [], "responsibilities": [], "company_context": [], "employment_context": [], "sample_stats": {"included_samples": 1}, "view_type": "published", "version_id": 11})
        if path == "/api/v1/position-profiles/KG_POS":
            assert params == {
                "contract_version": "position-profile.v3",
                "view": "published",
            }
            return envelope({
                "position_id": "KG_POS",
                "graph_version": "11",
                "graph_version_id": 11,
                "requirement_inflation": {
                    "algorithm_version": "requirement-strength-calibration.v1",
                    "scope": "required_skills",
                    "summary": {
                        "jd_count": 1,
                        "total_required_requirement_count": 1,
                        "market_supported_count": 0,
                        "enterprise_specific_count": 0,
                        "inflation_risk_count": 1,
                        "jd_risk_level_counts": {"low": 0, "medium": 1, "high": 0},
                    },
                    "jd_diagnostics": [],
                },
            })
        if path == "/api/v1/relations/501/modify":
            assert payload["build_run_id"] == 101
            assert payload["position_id"] == "KG_POS"
            assert payload["reason"] == "E2E expert edit"
            self.weight = payload["weight"]
            return envelope({"relation_id": 501, "draft_id": 900, "build_run_id": 101, "revision": 2})
        if path.startswith("/api/v1/review-tasks/") and len(path.split("/")) == 6:
            action = path.rsplit("/", 1)[-1]
            task_id = int(path.split("/")[-2])
            assert payload["reason"]
            self.review_status = "claimed" if action == "claim" else action + "d"
            return envelope({"id": task_id, "status": self.review_status})
        if path == "/api/v1/review-tasks":
            page = int((params or {}).get("page", 1))
            page_size = int((params or {}).get("page_size", 20))
            tasks = [
                {
                    "contract_version": "review-task.v1",
                    "task_id": str(700 + index),
                    "source_system": "knowledge-graph",
                    "task_kind": "position_skill_relation",
                    "id": 700 + index,
                    "object_type": "position_skill_relation",
                    "object_id": str(500 + index),
                    "build_run_id": 101,
                    "status": "pending" if index == 0 else self.review_status,
                    "assignee_id": None,
                    "payload": {},
                    "original_content": {"weight": 0.5},
                    "changed_content": {"weight": self.weight},
                    "evidence": [{"quote": "熟悉 Python"}],
                    "review_flags": ["manually_modified_relation"],
                    "risk_level": "medium",
                    "impact_scope": {"position_id": "KG_POS"},
                    "history": [],
                    "evidence_context": {
                        "evidence": [{"quote": "熟悉 Python"}],
                        "original_values": {"weight": 0.5},
                        "current_values": {"weight": self.weight},
                        "modified_values": {},
                        "impacted_relations": [],
                        "review_flags": ["manually_modified_relation"],
                        "impact_scope": {"position_id": "KG_POS"},
                        "history": [],
                    },
                    "allowed_actions": ["claim"] if index == 0 else (
                        ["approve", "reject", "modify"] if self.review_status == "claimed" else []
                    ),
                }
                for index in range(150)
            ]
            filtered = [item for item in tasks if item["status"] == (params or {}).get("status", item["status"])]
            start = (page - 1) * page_size
            return envelope(
                filtered[start:start + page_size],
                headers={"X-Total-Count": str(len(filtered))},
            )
        if path.startswith("/api/v1/review-tasks/") and len(path.split("/")) == 5:
            task_id = int(path.split("/")[-2] if path.endswith("/context") else path.rsplit("/", 1)[-1])
            if task_id not in range(700, 850):
                return envelope(None)
            item = {
                "contract_version": "review-task.v1",
                "task_id": str(task_id),
                "source_system": "knowledge-graph",
                "task_kind": "position_skill_relation",
                "id": task_id,
                "object_type": "position_skill_relation",
                "object_id": str(task_id - 200),
                "build_run_id": 101,
                "status": self.review_status,
                "assignee_id": None,
                "payload": {},
                "original_content": {"weight": 0.5},
                "changed_content": {"weight": self.weight},
                "evidence": [{"quote": "熟悉 Python"}],
                "review_flags": ["manually_modified_relation"],
                "risk_level": "medium",
                "impact_scope": {"position_id": "KG_POS"},
                "history": [],
                "evidence_context": {
                    "evidence": [{"quote": "熟悉 Python"}],
                    "original_values": {"weight": 0.5},
                    "current_values": {"weight": self.weight},
                    "modified_values": {},
                    "impacted_relations": [],
                    "review_flags": ["manually_modified_relation"],
                    "impact_scope": {"position_id": "KG_POS"},
                    "history": [],
                },
                "allowed_actions": [],
            }
            return envelope(item)
        if path == "/api/v1/graph/build-runs/101/publish-gate":
            return envelope({"allowed": True, "errors": [], "valid_sample_count": 1, "open_review_task_count": 0, "unresolved_count": 0, "non_exact_evidence_count": 0, "low_confidence_relation_count": 0, "minimum_valid_samples": 1, "minimum_samples_met": True})
        if path == "/api/v1/graph/build-runs/101/publish":
            return envelope({"version_id": 11, "version_number": 2, "rollback_from_version_id": None})
        if path == "/api/v1/positions/KG_POS/graph/versions":
            return envelope([{"id": 10, "version_number": 1, "rollback_from_version_id": None, "created_at": "2026-07-28"}, {"id": 11, "version_number": 2, "rollback_from_version_id": None, "created_at": "2026-07-29"}])
        if path == "/api/v1/positions/KG_POS/graph/versions/diff":
            assert params == {"from_version_id": 10, "to_version_id": 11}
            return envelope({"added": [], "removed": [], "changed": [{"skill_id": "KG_PY", "changed_fields": {"weight": {"before": 0.5, "after": self.weight}}}], "context_changes": {}, "evidence_changes": []})
        if path == "/api/v1/positions/KG_POS/graph/versions/10/rollback":
            assert payload["reason"] == "E2E restore"
            return envelope({"version_id": 12, "version_number": 3, "rollback_from_version_id": 10})
        raise AssertionError(f"unexpected KG call: {method} {path}")


class LargeReviewQueueKnowledgeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _item(task_id: int) -> dict:
        return {
            "contract_version": "review-task.v1",
            "task_id": str(task_id),
            "source_system": "knowledge-graph",
            "task_kind": "position_skill_relation",
            "id": task_id,
            "object_type": "position_skill_relation",
            "object_id": str(2000 + task_id),
            "build_run_id": 101,
            "status": "pending",
            "assignee_id": None,
            "payload": {},
            "original_content": {"weight": 0.5},
            "changed_content": {"weight": 0.5},
            "evidence": [{"quote": "熟悉 Python"}],
            "review_flags": ["manually_modified_relation"],
            "risk_level": "medium",
            "impact_scope": {"position_id": "KG_POS"},
            "history": [],
            "evidence_context": {
                "evidence": [{"quote": "熟悉 Python"}],
                "original_values": {"weight": 0.5},
                "current_values": {"weight": 0.5},
                "modified_values": {},
                "impacted_relations": [],
                "review_flags": ["manually_modified_relation"],
                "impact_scope": {"position_id": "KG_POS"},
                "history": [],
            },
            "allowed_actions": ["claim"],
        }

    def portal_call(self, method, path, *, payload=None, params=None, **actor):
        self.calls.append((method, path))
        if path == "/api/v1/review-tasks":
            page = int((params or {}).get("page", 1))
            page_size = int((params or {}).get("page_size", 20))
            items = [self._item(900 + index) for index in range(150)]
            start = (page - 1) * page_size
            return envelope(
                items[start:start + page_size],
                headers={"X-Total-Count": str(len(items))},
            )
        if path.startswith("/api/v1/review-tasks/") and len(path.split("/")) == 5:
            task_id = int(path.rsplit("/", 1)[-1])
            if not 900 <= task_id <= 1049:
                return envelope(None)
            return envelope(self._item(task_id))
        raise AssertionError(f"unexpected KG call: {method} {path}")


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


def test_admin_knowledge_graph_management_closure():
    remote = StatefulKnowledgeGraph()

    @contextmanager
    def adapter_scope():
        with SessionLocal() as session:
            yield KnowledgeGraphIntegrationService(session, remote, enabled=True)
            session.commit()

    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: ManageKnowledgeGraphIntegration(adapter_scope)
    create_internal_user("kg-closure-admin", "admin")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "kg-closure-admin", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    created = client.post("/api/v1/positions", json={"position_name": "闭环岗位", "core_responsibilities": ["服务开发"], "required_skills": [], "bonus_skills": [], "industry_scenarios": []}, headers=headers)
    assert created.status_code == 200
    position_id = created.json()["data"]["position_id"]

    candidates = client.get("/api/v1/portal/admin/knowledge-graph/mapping-candidates", params={"entity_type": "position", "query": "闭环"}, headers=headers)
    assert candidates.json()["data"][0]["knowledge_graph_id"] == "KG_POS"
    mapped = client.put(f"/api/v1/portal/admin/knowledge-graph/mappings/position/{position_id}", json={"knowledge_graph_id": "KG_POS"}, headers=headers)
    assert mapped.json()["data"]["sync_status"] == "confirmed"
    listed = client.get("/api/v1/portal/admin/knowledge-graph/mappings", params={"entity_type": "position"}, headers=headers).json()["data"]
    assert listed[0]["knowledge_graph_id"] == "KG_POS"
    published_graph = client.get(
        f"/api/v1/portal/positions/{position_id}/graph", headers=headers
    ).json()["data"]
    assert published_graph["view_type"] == "published"
    assert published_graph["graph_version"] == "11"
    inflation = client.get(
        f"/api/v1/portal/positions/{position_id}/requirement-inflation",
        headers=headers,
    ).json()["data"]
    assert inflation["position_id"] == position_id
    assert inflation["requirement_inflation"]["summary"]["inflation_risk_count"] == 1
    assert set(inflation) == {
        "position_id",
        "graph_version",
        "graph_version_id",
        "requirement_inflation",
    }
    cancelled = client.delete(f"/api/v1/portal/admin/knowledge-graph/mappings/position/{position_id}", headers=headers)
    assert cancelled.json()["data"]["sync_status"] == "pending"
    client.put(f"/api/v1/portal/admin/knowledge-graph/mappings/position/{position_id}", json={"knowledge_graph_id": "KG_POS"}, headers=headers)
    with SessionLocal() as session:
        row = session.query(KnowledgeGraphEntityMapping).filter_by(entity_type="position", main_system_id=position_id).one()
        row.sync_status = "failed:mapping"
        session.commit()
    retried = client.post(f"/api/v1/portal/admin/knowledge-graph/mappings/position/{position_id}/retry", headers=headers)
    assert retried.json()["data"]["sync_status"] == "confirmed"

    create_internal_user("kg-closure-reader", "personal_user")
    reader_login = client.post("/api/v1/auth/login", json={"username": "kg-closure-reader", "password": "password123"})
    reader_headers = {"Authorization": f"Bearer {reader_login.json()['data']['access_token']}"}
    assert client.get("/api/v1/portal/admin/knowledge-graph/mappings", params={"entity_type": "position"}, headers=reader_headers).status_code == 403

    build = client.post(f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/build", json={}, headers=headers).json()["data"]
    assert build["build_run_id"] == 101
    draft = client.post(f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/graph/drafts", json={}, headers=headers).json()["data"]
    assert draft == {"draft_id": 900, "build_run_id": 101, "position_id": "KG_POS", "base_version_id": 10}
    graph = client.get(f"/api/v1/portal/admin/knowledge-graph/drafts/{draft['build_run_id']}/graph", headers=headers).json()["data"]
    relation = graph["skill_relations"][0]
    modified = client.post(f"/api/v1/portal/admin/knowledge-graph/relations/{relation['relation_id']}/modify", json={"build_run_id": draft["build_run_id"], "position_id": position_id, "expected_revision": relation["revision"], "weight": 0.8, "reason": "E2E expert edit"}, headers=headers)
    assert modified.status_code == 200

    task = next(
        item
        for item in client.get("/api/v1/review-tasks", headers=headers).json()["data"]
        if item.get("source_system") == "knowledge-graph"
    )
    assert client.post(
        f"/api/v1/review-tasks/{task['task_id']}/claim", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/review-tasks/{task['task_id']}/approve",
        json={"review_comment": "E2E approved"},
        headers=headers,
    ).status_code == 200
    gate = client.get(f"/api/v1/portal/admin/knowledge-graph/build-runs/{draft['build_run_id']}/publish-gate", headers=headers).json()["data"]
    assert gate["allowed"] is True
    published = client.post(f"/api/v1/portal/admin/knowledge-graph/build-runs/{draft['build_run_id']}/publish", json={"reason": "E2E publish"}, headers=headers).json()["data"]
    assert published["version_id"] == 11
    versions = client.get(f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/versions", headers=headers).json()["data"]
    diff = client.get(f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/versions/diff", params={"from_version_id": versions[0]["id"], "to_version_id": versions[1]["id"]}, headers=headers).json()["data"]
    assert diff["changed"][0]["changed_fields"]["weight"]["after"] == 0.8
    rolled_back = client.post(f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/versions/{versions[0]['id']}/rollback", json={"reason": "E2E restore"}, headers=headers).json()["data"]
    assert rolled_back["rollback_from_version_id"] == versions[0]["id"]


def test_unified_review_queue_uses_real_total_and_detail_by_id():
    remote = LargeReviewQueueKnowledgeGraph()

    @contextmanager
    def adapter_scope():
        with SessionLocal() as session:
            yield KnowledgeGraphIntegrationService(session, remote, enabled=True)
            session.commit()

    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: ManageKnowledgeGraphIntegration(adapter_scope)
    create_internal_user("kg-queue-admin", "admin")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "kg-queue-admin", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    first_page = client.get(
        "/api/v1/review-tasks",
        params={"page": 1, "page_size": 20},
        headers=headers,
    )
    assert first_page.status_code == 200
    assert first_page.headers["X-Total-Count"] == "150"
    assert len(first_page.json()["data"]) == 20

    second_page = client.get(
        "/api/v1/review-tasks",
        params={"page": 8, "page_size": 20},
        headers=headers,
    )
    assert second_page.status_code == 200
    assert second_page.headers["X-Total-Count"] == "150"
    assert len(second_page.json()["data"]) == 10
    assert second_page.json()["data"][0]["task_id"] == "kg:1040"

    detail = client.get(
        "/api/v1/review-tasks/kg:1010",
        headers=headers,
    )
    if detail.status_code != 200:
        print("DETAIL_BODY", detail.text)
    assert detail.status_code == 200
    assert detail.json()["data"]["task_id"] == "kg:1010"
    assert detail.json()["data"]["object_id"] == "3010"

    context = client.get(
        "/api/v1/review-tasks/kg:1010/context",
        headers=headers,
    )
    assert context.status_code == 200
    assert context.json()["data"]["evidence"][0]["quote"] == "熟悉 Python"

    missing = client.get(
        "/api/v1/review-tasks/kg:99999",
        headers=headers,
    )
    assert missing.status_code == 404
    assert any(path == "/api/v1/review-tasks/1010" for _, path in remote.calls)


def test_unified_review_queue_paginates_combined_local_and_kg_pool():
    remote = LargeReviewQueueKnowledgeGraph()

    @contextmanager
    def adapter_scope():
        with SessionLocal() as session:
            yield KnowledgeGraphIntegrationService(session, remote, enabled=True)
            session.commit()

    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: ManageKnowledgeGraphIntegration(adapter_scope)
    create_internal_user("kg-queue-mixed", "reviewer")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "kg-queue-mixed", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    for index in range(3):
        response = client.post(
            "/api/v1/review-tasks",
            json={
                "object_type": "generic_review_object",
                "object_id": f"local-{index}",
                "priority": "normal",
            },
            headers=headers,
        )
        assert response.status_code == 200

    pages = [
        client.get(
            "/api/v1/review-tasks",
            params={"page": page, "page_size": 10},
            headers=headers,
        )
        for page in (1, 2, 3)
    ]
    for response in pages:
        assert response.status_code == 200
        assert response.headers["X-Total-Count"] == "153"
        assert len(response.json()["data"]) <= 10
    first_ids = {item["task_id"] for item in pages[0].json()["data"]}
    second_ids = {item["task_id"] for item in pages[1].json()["data"]}
    third_ids = {item["task_id"] for item in pages[2].json()["data"]}
    assert first_ids.isdisjoint(second_ids)
    assert second_ids.isdisjoint(third_ids)
    assert {"local-0", "local-1", "local-2"} <= {
        item["object_id"] for item in pages[0].json()["data"]
    }


def test_unified_review_queue_value_sort_returns_global_top_k():
    remote = LargeReviewQueueKnowledgeGraph()

    @contextmanager
    def adapter_scope():
        with SessionLocal() as session:
            yield KnowledgeGraphIntegrationService(session, remote, enabled=True)
            session.commit()

    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: ManageKnowledgeGraphIntegration(adapter_scope)
    create_internal_user("kg-queue-value", "reviewer")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "kg-queue-value", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    for object_id, priority in (("value-quiet", "low"), ("value-top", "urgent")):
        response = client.post(
            "/api/v1/review-tasks",
            json={
                "object_type": "generic_review_object",
                "object_id": object_id,
                "priority": priority,
            },
            headers=headers,
        )
        assert response.status_code == 200

    response = client.get(
        "/api/v1/review-tasks",
        params={"page": 1, "page_size": 1, "sort": "value"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "152"
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["object_id"] == "value-top"
    assert data[0]["value_ranking"]["blocking_state"] is True
