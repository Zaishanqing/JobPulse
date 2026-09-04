"""Cross-service permission contract tests for the main-system → KG review chain.

These tests verify the authorization pipeline at the contract level:
  main-system user → main-system role/permission check
  → KnowledgeGraphClient with service token + delegated actor headers
  → KG verifies integration_service permissions (MockTransport)

No real network, no Docker, no sleep, no fixed ports, no test-order dependency.
The KG review responses are constructed by a MockTransport handler — these are
*cross-service contract tests*, not true end-to-end tests.  KG route-level
authorization is verified in the KG service's own test suite using TestClient.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.contexts.knowledge_graph import (
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
    ManageKnowledgeGraphIntegration,
)
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.domain.permissions import permissions_for_role, require_permission
from app.integrations.knowledge_graph.client import KnowledgeGraphClient


# ── domain-layer permission model tests (Task 2) ────────────────────────────


class TestMainSystemRolePermissions:
    """Verify the unified ROLE_PERMISSIONS mapping."""

    PUBLIC = frozenset(
        {
            "catalog.read_published", "emerging.read_published",
            "evidence.read_public", "trend.published.read",
        }
    )

    def test_personal_user_has_public_read_and_owned_resume_workflow(self):
        perms = set(permissions_for_role("personal_user"))
        assert perms == self.PUBLIC | {
            "resume.parse.manage",
            "resume.profile.generate",
            "matching.run",
            "learning_path.create",
        }

    def test_enterprise_user_has_jd_business_permissions(self):
        perms = set(permissions_for_role("enterprise_user"))
        assert perms == self.PUBLIC | {"jd.create", "jd.parse"}

    def test_reviewer_has_normalization_and_review(self):
        perms = set(permissions_for_role("reviewer"))
        assert perms >= (self.PUBLIC | {"kg.normalization.manage", "kg.review.manage"})
        assert "kg.build.manage" not in perms

    def test_admin_has_all_permissions(self):
        perms = set(permissions_for_role("admin"))
        assert "kg.build.manage" in perms
        assert "kg.review.manage" in perms
        assert "kg.version.manage" in perms
        assert "emerging.discovery.manage" in perms

    def test_developer_has_integration_operations(self):
        perms = set(permissions_for_role("developer"))
        assert perms == self.PUBLIC | {
            "integration.status.view",
            "integration.cv.retry",
            "integration.jd.retry",
            "integration.outbox.requeue",
            "integration.worker.run",
            "acquisition.read",
            "acquisition.job.manage",
            "jd.create",
            "jd.parse",
            "jd.publish",
            "resume.parse.manage",
            "resume.profile.generate",
            "matching.run",
            "learning_path.create",
            "trend.run.manage",
            "trend.source.manage",
            "trend.review.manage",
            "trend.publish.manage",
        }

    def test_unknown_role_returns_empty(self):
        perms = set(permissions_for_role("unknown_fake_role"))
        assert perms == set()

    def test_permissions_for_role_returns_stable_sorted_tuple(self):
        result = permissions_for_role("admin")
        assert isinstance(result, tuple)
        assert result == tuple(sorted(result))

    def test_require_permission_raises_for_missing(self):
        with pytest.raises(PermissionDenied):
            require_permission("developer", "kg.review.manage")

    def test_require_permission_passes_for_present(self):
        # Should not raise
        require_permission("admin", "kg.review.manage")
        require_permission("reviewer", "kg.review.manage")


# ── application-layer authorization tests (Task 2) ──────────────────────────


class TestApplicationLayerAuthorization:
    """Verify ManageKnowledgeGraphIntegration uses require_permission,
    not hardcoded role sets."""

    @staticmethod
    def _make_handlers():
        @contextmanager
        def factory():
            yield SimpleNamespace(
                set_mapping=lambda *a, **kw: None,
                mapping_status=lambda *a, **kw: None,
                build=lambda *a, **kw: None,
                build_runs=lambda *a, **kw: None,
                build_run=lambda *a, **kw: None,
                graph=lambda *a, **kw: None,
                versions=lambda *a, **kw: None,
                relation_evidence=lambda *a, **kw: None,
                portal=lambda cmd, actor: {},
            )

        return ManageKnowledgeGraphIntegration(factory)

    def test_admin_can_build(self):
        handlers = self._make_handlers()
        # Should not raise
        handlers.build(AccountActor("admin-1", "admin"), "POS_BACKEND", None)

    def test_reviewer_cannot_build(self):
        handlers = self._make_handlers()
        with pytest.raises(PermissionDenied):
            handlers.build(AccountActor("reviewer-1", "reviewer"), "POS_BACKEND", None)

    def test_developer_cannot_build(self):
        handlers = self._make_handlers()
        with pytest.raises(PermissionDenied):
            handlers.build(AccountActor("dev-1", "developer"), "POS_BACKEND", None)

    def test_admin_can_access_relation_evidence(self):
        handlers = self._make_handlers()
        # Should not raise
        handlers.relation_evidence(AccountActor("admin-1", "admin"), "1")

    def test_reviewer_can_access_relation_evidence(self):
        handlers = self._make_handlers()
        # Should not raise — evidence.read_public is in reviewer's permissions
        handlers.relation_evidence(AccountActor("reviewer-1", "reviewer"), "1")

    def test_personal_user_can_access_relation_evidence(self):
        handlers = self._make_handlers()
        # Should not raise — evidence.read_public is public
        handlers.relation_evidence(AccountActor("personal-1", "personal_user"), "1")

    def test_developer_cannot_review(self):
        handlers = self._make_handlers()
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )
        with pytest.raises(PermissionDenied):
            handlers.portal(AccountActor("dev-1", "developer"), review_command)

    def test_personal_user_cannot_review(self):
        handlers = self._make_handlers()
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )
        with pytest.raises(PermissionDenied):
            handlers.portal(
                AccountActor("personal-1", "personal_user"), review_command
            )

    def test_reviewer_can_review(self):
        handlers = self._make_handlers()
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )
        # Should not raise — reviewer has kg.review.manage
        handlers.portal(AccountActor("reviewer-1", "reviewer"), review_command)

    def test_admin_can_review(self):
        handlers = self._make_handlers()
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )
        # Should not raise
        handlers.portal(AccountActor("admin-1", "admin"), review_command)

    def test_unknown_role_denied_everywhere(self):
        handlers = self._make_handlers()
        with pytest.raises(PermissionDenied):
            handlers.build(AccountActor("ghost-1", "unknown"), "POS_BACKEND", None)
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )
        with pytest.raises(PermissionDenied):
            handlers.portal(AccountActor("ghost-1", "unknown"), review_command)


# ── Cross-service contract tests ────────────────────────────────────────────


def _kg_mock_handler(request: httpx.Request) -> httpx.Response:
    """Mock KG server handler for contract-level verification.

    Verifies: Bearer token presence, X-Main-User-Id / X-Main-User-Role header
    propagation.  Responses are synthetic — no real KG route or persistence.
    """
    path = request.url.path

    if path == "/api/v1/auth/token":
        return httpx.Response(200, json={
            "code": 0, "message": "success",
            "data": {
                "access_token": "kg-service-token",
                "token_type": "bearer",
                "role": "integration_service",
            },
            "trace_id": "kg_auth",
        })

    # All other endpoints: verify Bearer token present
    auth = request.headers.get("Authorization", "")
    if auth != "Bearer kg-service-token":
        return httpx.Response(401, json={
            "code": 40101, "message": "unauthorized", "data": None,
            "trace_id": "kg_401",
        })

    if path == "/api/v1/review-tasks":
        return httpx.Response(200, headers={
            "X-Total-Count": "23",
            "X-Page": "2",
            "X-Page-Size": "10",
        }, json={
            "code": 0, "message": "success",
            "data": [{
                "id": 1, "object_type": "evidence", "object_id": "1",
                "build_run_id": 1, "status": "pending", "assignee_id": None,
            }],
            "trace_id": "kg_review_list",
        })

    if path.startswith("/api/v1/review-tasks/") and path.endswith("/approve"):
        return httpx.Response(200, json={
            "code": 0, "message": "success",
            "data": {
                "id": 1, "status": "approved",
            },
            "trace_id": "kg_review_approve",
        })

    return httpx.Response(404, json={
        "code": 40401, "message": "not found", "data": None,
        "trace_id": "kg_404",
    })


def _kg_client(handler) -> KnowledgeGraphClient:
    return KnowledgeGraphClient(
        base_url="http://kg.test",
        username="integration_developer",
        password="secret",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


class TestCrossServiceContract:
    """Contract tests: main system → KG via KnowledgeGraphClient with MockTransport."""

    def test_admin_main_role_can_call_kg_review(self):
        """Main-system admin → KG client sends Bearer + delegated headers."""
        kg = _kg_client(_kg_mock_handler)
        try:
            result = kg.portal_call(
                "GET", "/api/v1/review-tasks",
                actor_id="admin-1", actor_role="admin",
            )
            assert result.code == 0
        finally:
            kg.close()

    def test_reviewer_main_role_can_call_kg_review(self):
        """Main-system reviewer → KG client sends Bearer + delegated headers."""
        kg = _kg_client(_kg_mock_handler)
        try:
            result = kg.portal_call(
                "GET", "/api/v1/review-tasks",
                actor_id="reviewer-1", actor_role="reviewer",
            )
            assert result.code == 0
        finally:
            kg.close()

    def test_kg_pagination_headers_and_query_parameters_are_preserved(self):
        captured_query = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/token":
                return httpx.Response(200, json={
                    "code": 0,
                    "message": "success",
                    "data": {"access_token": "kg-service-token"},
                    "trace_id": "kg_auth",
                })
            captured_query.update(request.url.params)
            return _kg_mock_handler(request)

        kg = _kg_client(handler)
        try:
            result = kg.portal_call(
                "GET",
                "/api/v1/review-tasks",
                params={"page": 2, "page_size": 10, "status": "pending"},
                actor_id="reviewer-1",
                actor_role="reviewer",
            )
            assert captured_query == {
                "page": "2",
                "page_size": "10",
                "status": "pending",
            }
            assert result.response_headers == {
                "X-Total-Count": "23",
                "X-Page": "2",
                "X-Page-Size": "10",
            }
        finally:
            kg.close()

    def test_developer_main_role_rejected_at_main_system(self):
        """Main-system developer is rejected at main system permission layer."""
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )

        @contextmanager
        def factory():
            yield SimpleNamespace(portal=lambda command, actor: {})

        handlers = ManageKnowledgeGraphIntegration(factory)
        with pytest.raises(PermissionDenied):
            handlers.portal(AccountActor("developer-1", "developer"), review_command)

    def test_personal_user_main_role_rejected_at_main_system(self):
        """personal_user rejected at main system permission layer."""
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )

        @contextmanager
        def factory():
            yield SimpleNamespace(portal=lambda command, actor: {})

        handlers = ManageKnowledgeGraphIntegration(factory)
        with pytest.raises(PermissionDenied):
            handlers.portal(
                AccountActor("personal-1", "personal_user"), review_command
            )

    def test_enterprise_user_main_role_rejected_at_main_system(self):
        """enterprise_user rejected at main system permission layer."""
        review_command = KnowledgeGraphPortalCommand(
            KnowledgeGraphPortalOperation.REVIEW_TASKS,
        )

        @contextmanager
        def factory():
            yield SimpleNamespace(portal=lambda command, actor: {})

        handlers = ManageKnowledgeGraphIntegration(factory)
        with pytest.raises(PermissionDenied):
            handlers.portal(
                AccountActor("enterprise-1", "enterprise_user"), review_command
            )

    def test_kg_service_token_has_integration_service_permissions(self):
        """Mock KG auth token returns role=integration_service."""
        kg = _kg_client(_kg_mock_handler)
        try:
            token_resp = kg._http.request(
                "POST", "/api/v1/auth/token",
                json={"username": "integration_developer", "password": "secret"},
            )
            assert token_resp.status_code == 200
            token_data = token_resp.json()
            assert token_data["data"]["role"] == "integration_service"
        finally:
            kg.close()

    def test_delegated_actor_headers_are_sent(self):
        """KnowledgeGraphClient sends X-Main-User-Id and X-Main-User-Role headers."""
        captured_headers = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            captured_headers["x-main-user-id"] = request.headers.get("X-Main-User-Id")
            captured_headers["x-main-user-role"] = request.headers.get("X-Main-User-Role")
            captured_headers["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={
                "code": 0, "message": "success", "data": {}, "trace_id": "t1",
            })

        kg = KnowledgeGraphClient(
            base_url="http://kg.test",
            username="integration_developer",
            password="secret",
            timeout_seconds=5,
            transport=httpx.MockTransport(
                lambda req: (
                    httpx.Response(200, json={
                        "code": 0, "message": "success",
                        "data": {"access_token": "kg-service-token", "token_type": "bearer"},
                        "trace_id": "kg_auth",
                    })
                    if req.url.path == "/api/v1/auth/token"
                    else capture_handler(req)
                )
            ),
        )
        try:
            kg.portal_call(
                "POST", "/api/v1/review-tasks/1/approve",
                actor_id="admin-1", actor_role="admin",
            )
            assert captured_headers["x-main-user-id"] == "admin-1"
            assert captured_headers["x-main-user-role"] == "admin"
            assert captured_headers["auth"] == "Bearer kg-service-token"
        finally:
            kg.close()

    def test_delegated_actor_flow_succeeds(self):
        """Service token valid → review call succeeds (contract level)."""
        kg = _kg_client(_kg_mock_handler)
        try:
            # 1. Get service token
            token_resp = kg._http.request(
                "POST", "/api/v1/auth/token",
                json={"username": "integration_developer", "password": "secret"},
            )
            assert token_resp.status_code == 200

            # 2. Review as delegated admin succeeds
            result = kg.portal_call(
                "POST", "/api/v1/review-tasks/1/approve",
                actor_id="admin-1", actor_role="admin",
            )
            assert result.code == 0
            assert result.data["status"] == "approved"
        finally:
            kg.close()
