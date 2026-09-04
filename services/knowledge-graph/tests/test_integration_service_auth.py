"""KG route-level tests verifying integration_service role authorization.

These tests use the real KG TestClient + test database:
  - integration_service can pass require_reviewer
  - developer returns 403 on review endpoints
  - reviewer returns success on review endpoints
  - personal_user returns 403 on review endpoints
  - integration_identity rejects wrong username or wrong role
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.application.identity import AuthorizationDenied, IdentityService
from app.domain.identity import (
    IdentityActor,
    Permission,
    _ROLE_PERMISSIONS,
    has_permission,
)
from app.models import ReviewTask


# ── domain-level permission assertions ──────────────────────────────────────


class TestIntegrationServicePermissions:
    def test_integration_service_has_review(self):
        assert Permission.REVIEW in _ROLE_PERMISSIONS["integration_service"]

    def test_developer_lacks_review(self):
        assert Permission.REVIEW not in _ROLE_PERMISSIONS["developer"]

    @pytest.mark.parametrize("role,has_review", [
        ("personal_user", False),
        ("enterprise_user", False),
        ("reviewer", True),
        ("admin", True),
        ("developer", False),
        ("integration_service", True),
    ])
    def test_has_permission_review_by_role(self, role, has_review):
        actor = IdentityActor(1, role, role)
        assert has_permission(actor, Permission.REVIEW) is has_review


# ── integration_identity checks ─────────────────────────────────────────────


class TestIntegrationIdentity:
    @pytest.fixture
    def _svc(self):
        repo = SimpleNamespace(
            by_username=lambda name: (
                SimpleNamespace(
                    user_id=1, username=name, password_hash="hash",
                    role="integration_service",
                )
                if name == "integration_developer"
                else None
            ),
            by_id=lambda uid: SimpleNamespace(
                user_id=uid, username="integration_developer",
                password_hash="hash", role="integration_service",
            ),
        )
        passwords = SimpleNamespace(verify=lambda pw, hash: True)
        tokens = SimpleNamespace(
            encode=lambda actor: "token",
            decode_subject=lambda token: 1,
        )
        return IdentityService(
            repo, passwords, tokens, service_username="integration_developer"
        )

    def test_correct_username_and_role_accepted(self, _svc):
        actor = SimpleNamespace(
            username="integration_developer", role="integration_service"
        )
        result = _svc.integration_identity(actor, "main-user-1", "admin")
        assert result is not None
        assert result.main_user_id == "main-user-1"
        assert result.main_user_role == "admin"

    def test_correct_username_wrong_role_rejected(self, _svc):
        actor = SimpleNamespace(username="integration_developer", role="developer")
        with pytest.raises(AuthorizationDenied):
            _svc.integration_identity(
                actor, "main-user-1", "admin", required=True
            )

    def test_wrong_username_correct_role_rejected(self, _svc):
        actor = SimpleNamespace(username="someone_else", role="integration_service")
        with pytest.raises(AuthorizationDenied):
            _svc.integration_identity(
                actor, "main-user-1", "admin", required=True
            )

    def test_both_wrong_rejected(self, _svc):
        actor = SimpleNamespace(username="someone_else", role="developer")
        with pytest.raises(AuthorizationDenied):
            _svc.integration_identity(
                actor, "main-user-1", "admin", required=True
            )


# ── real KG route authorization tests ───────────────────────────────────────


class TestKGRouteAuthorization:
    def test_integration_service_can_access_review_endpoint(
        self, client, db, integration_service_user, integration_service_headers
    ):
        task = ReviewTask(object_type="evidence", object_id="1")
        db.add(task); db.commit()
        resp = client.post(
            f"/api/v1/review-tasks/{task.id}/claim",
            json={"reason": "service review"}, headers=integration_service_headers,
        )
        assert resp.status_code == 200

    def test_integration_service_can_access_unresolved(
        self, client, integration_service_headers
    ):
        resp = client.get(
            "/api/v1/normalization/unresolved-items",
            headers=integration_service_headers,
        )
        assert resp.status_code == 200

    def test_developer_returns_403_on_review(
        self, client, db, auth_headers
    ):
        task = ReviewTask(object_type="evidence", object_id="1")
        db.add(task); db.commit()
        resp = client.post(
            f"/api/v1/review-tasks/{task.id}/claim",
            json={"reason": "unauthorized"}, headers=auth_headers("developer"),
        )
        assert resp.status_code == 403

    def test_personal_user_returns_403_on_review(
        self, client, db, auth_headers
    ):
        task = ReviewTask(object_type="evidence", object_id="1")
        db.add(task); db.commit()
        resp = client.post(
            f"/api/v1/review-tasks/{task.id}/claim",
            json={"reason": "unauthorized"}, headers=auth_headers("personal_user"),
        )
        assert resp.status_code == 403

    def test_reviewer_returns_200_on_review(
        self, client, db, auth_headers
    ):
        task = ReviewTask(object_type="evidence", object_id="1")
        db.add(task); db.commit()
        resp = client.post(
            f"/api/v1/review-tasks/{task.id}/claim",
            json={"reason": "take task"}, headers=auth_headers("reviewer"),
        )
        assert resp.status_code == 200
