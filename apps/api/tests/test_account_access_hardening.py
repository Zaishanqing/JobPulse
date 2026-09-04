"""Access control hardening tests — account management RBAC and safety rules.

Coverage:
  - Permission directory: account.manage assignment across all roles
  - Application layer: ManageAccount authorization (allow / deny per role)
  - API layer: 200/403 for every management endpoint x role
  - Self‑protection: admin cannot change their own role or disable themselves
  - Last‑admin protection: cannot demote or disable the last active admin
  - Token invalidation: disabled account token → 401
  - Role refresh: role change reflected in /auth/me without new token
  - Directory consistency: roles / permissions from canonical sources
  - Regression: public registration still blocked for internal roles
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data
from tests.user_factory import create_internal_user
from app.main import app
from app.domain.permissions import permissions_for_role
from app.domain.accounts import ACCOUNT_ROLES
from app.domain.permissions import ALL_PERMISSIONS, ACCOUNT_MANAGE


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


# ── helpers ──────────────────────────────────────────────────────────────────

def _register_payload(username: str, role: str = "personal_user") -> dict:
    return {
        "role": role,
        "username": username,
        "password": "password123",
        "email": f"{username}@example.com",
        "phone": "13800000000",
    }


def _api_register(username: str, role: str = "personal_user") -> dict:
    """Register a user via the public API. Returns response JSON data."""
    resp = client.post("/api/v1/auth/register", json=_register_payload(username, role))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _token(username: str, password: str = "password123") -> str:
    """Login and return an access token."""
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ensure_user(username: str, role: str, password: str = "password123") -> tuple[str, str]:
    """Create (or skip if exists) a user via DB seed + register, then return (user_id, token)."""
    uid = create_internal_user(username, role, password)
    try:
        client.post("/api/v1/auth/register", json=_register_payload(username, role))
    except Exception:
        pass  # may already exist
    tok = _token(username, password)
    return uid, tok


# =============================================================================
# 1. Permission directory
# =============================================================================

class TestPermissionDirectory:
    """account.manage is only granted to admin."""

    def test_admin_has_account_manage(self):
        perms = set(permissions_for_role("admin"))
        assert ACCOUNT_MANAGE in perms

    def test_developer_does_not_have_account_manage(self):
        perms = set(permissions_for_role("developer"))
        assert ACCOUNT_MANAGE not in perms

    def test_reviewer_does_not_have_account_manage(self):
        perms = set(permissions_for_role("reviewer"))
        assert ACCOUNT_MANAGE not in perms

    def test_personal_user_does_not_have_account_manage(self):
        perms = set(permissions_for_role("personal_user"))
        assert ACCOUNT_MANAGE not in perms

    def test_enterprise_user_does_not_have_account_manage(self):
        perms = set(permissions_for_role("enterprise_user"))
        assert ACCOUNT_MANAGE not in perms

    def test_unknown_role_permissions_empty(self):
        perms = set(permissions_for_role("unknown_fake_role"))
        assert perms == set()

    def test_account_manage_in_all_permissions(self):
        assert ACCOUNT_MANAGE in ALL_PERMISSIONS


# =============================================================================
# 2. Application-layer authorization
# =============================================================================

class TestApplicationLayerAuthorization:
    """ManageAccount honours require_permission('account.manage') for every operation."""

    @staticmethod
    def _manage_account():
        from app.contexts.access import ManageAccount

        class FakeAccounts:
            def __init__(self):
                self.role_changes = []
                self.active_changes = []
                self._accounts = {
                    "target-1": type("Rec", (), {
                        "account_id": "target-1",
                        "username": "target",
                        "email": None,
                        "phone": None,
                        "role": "personal_user",
                        "is_active": True,
                        "password_hash": "x",
                    })(),
                }

            def get(self, account_id):
                return self._accounts.get(account_id)

            def get_by_username(self, username):
                return None

            def add(self, **kw):
                return None

            def change_role(self, aid, role):
                self.role_changes.append((aid, role))

            def change_active(self, aid, active):
                self.active_changes.append((aid, active))

            def change_password_hash(self, aid, h):
                pass

            def active_account_ids_by_role_for_update(self, role):
                # return enough admins so last-admin guard is never triggered here
                if role == "admin":
                    return ("admin-1", "admin-2", "admin-3")
                return ()

        class FakeUoW:
            def __init__(self):
                self.accounts = FakeAccounts()
                self._committed = False

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def commit(self):
                self._committed = True

            def rollback(self):
                pass

            def acquire_account_administration_lock(self) -> None:
                pass

        return ManageAccount(lambda: FakeUoW())

    @staticmethod
    def _actor(role: str, aid: str = "actor-1"):
        from app.domain.accounts import AccountActor
        return AccountActor(aid, role)

    @staticmethod
    def _cmd(role: str, target_id: str = "target-1"):
        from app.contexts.access import AccountRoleChangeCommand
        return AccountRoleChangeCommand(target_id, role)

    @staticmethod
    def _active_cmd(active: bool, target_id: str = "target-1"):
        from app.contexts.access import AccountActiveChangeCommand
        return AccountActiveChangeCommand(target_id, active)

    # ── admin can do everything ──────────────────────────────────────────

    def test_admin_can_list_roles(self):
        mgr = self._manage_account()
        roles = mgr.list_roles(self._actor("admin"))
        assert len(roles) == 5
        assert "admin" in roles

    def test_admin_can_list_permissions(self):
        mgr = self._manage_account()
        perms = mgr.list_permissions(self._actor("admin"))
        assert ACCOUNT_MANAGE in perms

    def test_admin_can_change_role(self):
        mgr = self._manage_account()
        result = mgr.change_role(self._actor("admin"), self._cmd("reviewer"))
        assert result.role == "reviewer"

    def test_admin_can_disable(self):
        mgr = self._manage_account()
        result = mgr.change_active(self._actor("admin"), self._active_cmd(False))
        assert result.is_active is False

    def test_admin_can_enable(self):
        mgr = self._manage_account()
        result = mgr.change_active(self._actor("admin"), self._active_cmd(True))
        assert result.is_active is True

    # ── non‑admin roles are denied ────────────────────────────────────────

    @pytest.mark.parametrize("role", ["developer", "reviewer", "personal_user", "enterprise_user"])
    def test_non_admin_cannot_list_roles(self, role):
        mgr = self._manage_account()
        from app.domain.errors import PermissionDenied
        with pytest.raises(PermissionDenied):
            mgr.list_roles(self._actor(role))

    @pytest.mark.parametrize("role", ["developer", "reviewer", "personal_user", "enterprise_user"])
    def test_non_admin_cannot_list_permissions(self, role):
        mgr = self._manage_account()
        from app.domain.errors import PermissionDenied
        with pytest.raises(PermissionDenied):
            mgr.list_permissions(self._actor(role))

    @pytest.mark.parametrize("role", ["developer", "reviewer", "personal_user", "enterprise_user"])
    def test_non_admin_cannot_change_role(self, role):
        mgr = self._manage_account()
        from app.domain.errors import PermissionDenied
        with pytest.raises(PermissionDenied):
            mgr.change_role(self._actor(role), self._cmd("reviewer"))

    @pytest.mark.parametrize("role", ["developer", "reviewer", "personal_user", "enterprise_user"])
    def test_non_admin_cannot_change_active(self, role):
        mgr = self._manage_account()
        from app.domain.errors import PermissionDenied
        with pytest.raises(PermissionDenied):
            mgr.change_active(self._actor(role), self._active_cmd(False))

    def test_unknown_role_denied(self):
        mgr = self._manage_account()
        from app.domain.errors import PermissionDenied
        with pytest.raises(PermissionDenied):
            mgr.list_roles(self._actor("unknown_fake"))


# =============================================================================
# 3. API-layer authorization
# =============================================================================

class TestApiLayerAuthorization:
    """Every account‑management endpoint returns 403 for non‑admin roles."""

    def test_admin_get_roles_ok(self):
        uid, tok = _ensure_user("api_admin", "admin")
        resp = client.get("/api/v1/roles", headers=_headers(tok))
        assert resp.status_code == 200

    def test_admin_get_permissions_ok(self):
        uid, tok = _ensure_user("api_admin2", "admin")
        resp = client.get("/api/v1/permissions", headers=_headers(tok))
        assert resp.status_code == 200

    def test_admin_put_role_ok(self):
        uid, tok = _ensure_user("api_admin3", "admin")
        target = _api_register("api_target_role", "personal_user")
        resp = client.put(
            f"/api/v1/users/{target['user_id']}/role",
            json={"role": "reviewer"},
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "reviewer"

    def test_admin_disable_ok(self):
        uid, tok = _ensure_user("api_admin4", "admin")
        target = _api_register("api_target_disable", "personal_user")
        resp = client.put(
            f"/api/v1/users/{target['user_id']}/disable",
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is False

    def test_admin_enable_ok(self):
        uid, tok = _ensure_user("api_admin5", "admin")
        target = _api_register("api_target_enable", "personal_user")
        # first disable
        client.put(f"/api/v1/users/{target['user_id']}/disable", headers=_headers(tok))
        # then enable
        resp = client.put(
            f"/api/v1/users/{target['user_id']}/enable",
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is True

    @pytest.mark.parametrize("role", ["developer", "reviewer", "personal_user", "enterprise_user"])
    def test_non_admin_roles_forbidden(self, role):
        """All management endpoints return 403 for non‑admin roles."""
        uid, tok = _ensure_user(f"api_{role}", role)
        target = _api_register(f"target_{role}", "personal_user")

        roles_resp = client.get("/api/v1/roles", headers=_headers(tok))
        perms_resp = client.get("/api/v1/permissions", headers=_headers(tok))
        role_resp = client.put(
            f"/api/v1/users/{target['user_id']}/role",
            json={"role": "reviewer"},
            headers=_headers(tok),
        )
        disable_resp = client.put(
            f"/api/v1/users/{target['user_id']}/disable",
            headers=_headers(tok),
        )

        assert roles_resp.status_code == 403
        assert perms_resp.status_code == 403
        assert role_resp.status_code == 403
        assert disable_resp.status_code == 403


# =============================================================================
# 4. Self‑protection: admin cannot change own role or disable self
# =============================================================================

class TestSelfProtection:
    """An admin must not demote or disable themselves."""

    def test_admin_cannot_change_own_role(self):
        uid, tok = _ensure_user("self_role_admin", "admin")
        resp = client.put(
            f"/api/v1/users/{uid}/role",
            json={"role": "personal_user"},
            headers=_headers(tok),
        )
        assert resp.status_code == 409
        assert "own administrative role" in resp.json()["message"].lower()

    def test_admin_cannot_disable_self(self):
        uid, tok = _ensure_user("self_disable_admin", "admin")
        resp = client.put(
            f"/api/v1/users/{uid}/disable",
            headers=_headers(tok),
        )
        assert resp.status_code == 409
        assert "own account" in resp.json()["message"].lower()

    def test_admin_can_still_change_own_password(self):
        uid, tok = _ensure_user("self_pwd_admin", "admin")
        resp = client.put(
            "/api/v1/auth/password",
            json={"old_password": "password123", "new_password": "new-password123"},
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["password_changed"] is True


# =============================================================================
# 5. Last‑active‑admin protection
# =============================================================================

class TestLastAdminProtection:
    """The system must always retain at least one active admin."""

    def test_cannot_disable_last_active_admin(self):
        # Create two admins
        uid1, tok1 = _ensure_user("last_a1", "admin")
        uid2, tok2 = _ensure_user("last_a2", "admin")

        # Disable uid1 via uid2 — leaves uid2 as the only active admin
        resp = client.put(
            f"/api/v1/users/{uid1}/disable",
            headers=_headers(tok2),
        )
        assert resp.status_code == 200

        # Now only uid2 is active. uid2 cannot disable itself (self-protection).
        resp_self = client.put(
            f"/api/v1/users/{uid2}/disable",
            headers=_headers(tok2),
        )
        assert resp_self.status_code == 409
        assert "own account" in resp_self.json()["message"].lower()

    def test_two_admins_can_safely_demote_one(self):
        uid1, tok1 = _ensure_user("multi_a1", "admin")
        uid2, tok2 = _ensure_user("multi_a2", "admin")
        # Demote uid1 via uid2
        resp = client.put(
            f"/api/v1/users/{uid1}/role",
            json={"role": "personal_user"},
            headers=_headers(tok2),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "personal_user"

    def test_two_admins_can_safely_disable_one(self):
        uid1, tok1 = _ensure_user("multi_d1", "admin")
        uid2, tok2 = _ensure_user("multi_d2", "admin")
        resp = client.put(
            f"/api/v1/users/{uid1}/disable",
            headers=_headers(tok2),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is False

    def test_promote_to_admin_always_allowed(self):
        uid1, tok1 = _ensure_user("promote_admin", "admin")
        target = _api_register("promote_target", "personal_user")
        resp = client.put(
            f"/api/v1/users/{target['user_id']}/role",
            json={"role": "admin"},
            headers=_headers(tok1),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "admin"

    def test_enable_disabled_admin_always_allowed(self):
        uid1, tok1 = _ensure_user("reenable_a1", "admin")
        uid2, tok2 = _ensure_user("reenable_a2", "admin")
        # disable uid2 via uid1
        client.put(f"/api/v1/users/{uid2}/disable", headers=_headers(tok1))
        # re-enable uid2 via uid1
        resp = client.put(
            f"/api/v1/users/{uid2}/enable",
            headers=_headers(tok1),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is True


# =============================================================================
# 6. Token state consistency
# =============================================================================

class TestTokenStateConsistency:
    """Token invalidation and role refresh without re‑issuing JWT."""

    def test_disabled_account_token_returns_401(self):
        uid, tok = _ensure_user("tok_dis_user", "personal_user")
        # verify token works
        me1 = client.get("/api/v1/auth/me", headers=_headers(tok))
        assert me1.status_code == 200

        # admin disables this user
        admin_uid, admin_tok = _ensure_user("tok_dis_admin", "admin")
        client.put(f"/api/v1/users/{uid}/disable", headers=_headers(admin_tok))

        # old token should now return 401
        me2 = client.get("/api/v1/auth/me", headers=_headers(tok))
        assert me2.status_code == 401

    def test_role_change_reflected_in_auth_me_with_old_token(self):
        uid, tok = _ensure_user("tok_role_dev", "developer")
        me1 = client.get("/api/v1/auth/me", headers=_headers(tok))
        assert me1.status_code == 200
        assert me1.json()["data"]["role"] == "developer"
        assert ACCOUNT_MANAGE not in me1.json()["data"]["permissions"]

        # admin changes developer to reviewer
        admin_uid, admin_tok = _ensure_user("tok_role_admin", "admin")
        resp = client.put(
            f"/api/v1/users/{uid}/role",
            json={"role": "reviewer"},
            headers=_headers(admin_tok),
        )
        assert resp.status_code == 200

        # same old token now returns new role
        me2 = client.get("/api/v1/auth/me", headers=_headers(tok))
        assert me2.status_code == 200
        assert me2.json()["data"]["role"] == "reviewer"
        # reviewer should have kg.review.manage, but not account.manage
        perms = me2.json()["data"]["permissions"]
        assert ACCOUNT_MANAGE not in perms
        assert "kg.review.manage" in perms

    def test_role_change_to_personal_user_reflected(self):
        uid, tok = _ensure_user("tok_role_dev2", "developer")
        admin_uid, admin_tok = _ensure_user("tok_role_admin2", "admin")
        client.put(
            f"/api/v1/users/{uid}/role",
            json={"role": "personal_user"},
            headers=_headers(admin_tok),
        )
        me = client.get("/api/v1/auth/me", headers=_headers(tok))
        assert me.json()["data"]["role"] == "personal_user"


# =============================================================================
# 7. Directory consistency
# =============================================================================

class TestDirectoryConsistency:
    """Roles and permissions returned by the API come from canonical sources."""

    def test_list_roles_returns_sorted_acount_roles(self):
        uid, tok = _ensure_user("dir_admin", "admin")
        resp = client.get("/api/v1/roles", headers=_headers(tok))
        assert resp.status_code == 200
        assert resp.json()["data"] == list(tuple(sorted(ACCOUNT_ROLES)))

    def test_list_permissions_returns_sorted_all_permissions(self):
        uid, tok = _ensure_user("dir_admin2", "admin")
        resp = client.get("/api/v1/permissions", headers=_headers(tok))
        assert resp.status_code == 200
        assert resp.json()["data"] == list(tuple(sorted(ALL_PERMISSIONS)))


# =============================================================================
# 8. Regression: public registration rules unchanged
# =============================================================================

class TestRegistrationRegression:
    """Public registration still blocks internal roles."""

    @pytest.mark.parametrize("role", ["admin", "reviewer", "developer"])
    def test_public_registration_rejects_internal_role(self, role):
        resp = client.post(
            "/api/v1/auth/register",
            json=_register_payload(f"reg_block_{role}", role=role),
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 422

    def test_public_registration_allows_personal_user(self):
        resp = client.post(
            "/api/v1/auth/register",
            json=_register_payload("reg_ok_personal", role="personal_user"),
        )
        assert resp.status_code == 200

    def test_public_registration_allows_enterprise_user(self):
        resp = client.post(
            "/api/v1/auth/register",
            json=_register_payload("reg_ok_enterprise", role="enterprise_user"),
        )
        assert resp.status_code == 200


# =============================================================================
# 9. Enterprise authorization unchanged (smoke test)
# =============================================================================

class TestLastAdminApplicationLayer:
    """Direct Application-layer tests that hit the last-admin guard branch.

    These tests use a Fake Repository to bypass the API's self-protection
    check: the actor has a different account_id than the target, but the
    repository only contains a *single* active admin (the target).

    The Fake UoW and Fake Repository record every operation in an ``events``
    list so that the lock→get→query→mutate→commit ordering is verified.
    """

    @staticmethod
    def _imports():
        """Lazy imports so module-level caches are warm."""
        from app.contexts.access import (
            AccountActiveChangeCommand,
            AccountRoleChangeCommand,
            InvalidAccountChange,
            ManageAccount,
        )
        from app.domain.accounts import AccountActor
        return {
            "AccountActiveChangeCommand": AccountActiveChangeCommand,
            "AccountRoleChangeCommand": AccountRoleChangeCommand,
            "InvalidAccountChange": InvalidAccountChange,
            "ManageAccount": ManageAccount,
            "AccountActor": AccountActor,
        }

    @staticmethod
    def _manage_account(repo_admin_count: int = 1):
        """Build a ManageAccount whose FakeAccounts returns the given number of
        active admins from ``active_account_ids_by_role_for_update``."""
        mod = TestLastAdminApplicationLayer._imports()

        events: list[str] = []

        class FakeAccounts:
            def __init__(self):
                self.role_changes: list = []
                self.active_changes: list = []
                self._account = type("Rec", (), {
                    "account_id": "target-1",
                    "username": "target",
                    "email": None,
                    "phone": None,
                    "role": "admin",
                    "is_active": True,
                    "password_hash": "x",
                })()
                self._admin_count = repo_admin_count

            def get(self, account_id):
                events.append("get:" + account_id)
                return self._account if account_id == "target-1" else None

            def get_by_username(self, username):
                return None

            def add(self, **kw):
                return None

            def change_role(self, aid, role):
                events.append("change_role:" + aid)
                self.role_changes.append((aid, role))

            def change_active(self, aid, active):
                events.append("change_active:" + aid)
                self.active_changes.append((aid, active))

            def change_password_hash(self, aid, h):
                pass

            def active_account_ids_by_role_for_update(self, role):
                events.append("active_admins_for_update")
                if role == "admin":
                    return tuple(f"admin-{i}" for i in range(1, self._admin_count + 1))
                return ()

        class FakeUoW:
            def __init__(self, accounts):
                self.accounts = accounts
                self._committed = False
                self._rolled_back = False

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def commit(self):
                events.append("commit")
                self._committed = True

            def rollback(self):
                events.append("rollback")
                self._rolled_back = True

            def acquire_account_administration_lock(self) -> None:
                events.append("lock")

        accounts = FakeAccounts()
        return mod["ManageAccount"](lambda: FakeUoW(accounts)), accounts, events

    # ── demotion ─────────────────────────────────────────────────────────

    def test_last_admin_demotion_rejected_at_application_layer(self):
        """actor != target, target is the *only* active admin → rejected."""
        mod = self._imports()

        mgr, accounts, events = self._manage_account(repo_admin_count=1)

        # actor is a *different* admin, so self‑protection is not triggered
        actor = mod["AccountActor"]("actor-2", "admin")
        cmd = mod["AccountRoleChangeCommand"]("target-1", "personal_user")

        with pytest.raises(mod["InvalidAccountChange"]) as exc_info:
            mgr.change_role(actor, cmd)

        assert "demote" in str(exc_info.value).lower()
        assert "last active administrator" in str(exc_info.value).lower()
        # target must NOT have been modified
        assert accounts.role_changes == []

        # Verify call order: lock → get target → active admins query → NO mutation → NO commit
        assert events[0] == "lock", f"expected lock first, got {events}"
        assert "get:target-1" in events[1:3], f"expected get after lock, got {events}"
        idx_lock = events.index("lock")
        idx_get = next(i for i, e in enumerate(events) if e.startswith("get:"))
        assert idx_lock < idx_get, f"lock ({idx_lock}) must precede get ({idx_get})"
        assert "active_admins_for_update" in events
        assert "change_role:" not in str(events)
        assert "commit" not in events

    def test_last_admin_demotion_allowed_when_two_admins(self):
        """Two active admins in repository → demotion succeeds."""
        mod = self._imports()

        mgr, accounts, events = self._manage_account(repo_admin_count=2)

        actor = mod["AccountActor"]("actor-2", "admin")
        cmd = mod["AccountRoleChangeCommand"]("target-1", "personal_user")

        result = mgr.change_role(actor, cmd)
        assert result.role == "personal_user"
        assert accounts.role_changes == [("target-1", "personal_user")]

        # Verify call order: lock → get → active_admins → change_role → commit
        assert events[0] == "lock"
        assert "change_role:target-1" in events
        assert "commit" in events
        idx_lock = events.index("lock")
        idx_get = next(i for i, e in enumerate(events) if e.startswith("get:"))
        idx_mut = events.index("change_role:target-1")
        idx_commit = events.index("commit")
        assert idx_lock < idx_get < idx_mut < idx_commit

    # ── disable ──────────────────────────────────────────────────────────

    def test_last_admin_disable_rejected_at_application_layer(self):
        """actor != target, target is the *only* active admin → disable rejected."""
        mod = self._imports()

        mgr, accounts, events = self._manage_account(repo_admin_count=1)

        actor = mod["AccountActor"]("actor-2", "admin")
        cmd = mod["AccountActiveChangeCommand"]("target-1", False)

        with pytest.raises(mod["InvalidAccountChange"]) as exc_info:
            mgr.change_active(actor, cmd)

        assert "disable" in str(exc_info.value).lower()
        assert "last active administrator" in str(exc_info.value).lower()
        assert accounts.active_changes == []

        # Verify call order: lock → get → active admins → NO mutation → NO commit
        assert events[0] == "lock"
        assert "active_admins_for_update" in events
        assert "change_active:" not in str(events)
        assert "commit" not in events

    def test_last_admin_disable_allowed_when_two_admins(self):
        """Two active admins in repository → disable succeeds."""
        mod = self._imports()

        mgr, accounts, events = self._manage_account(repo_admin_count=2)

        actor = mod["AccountActor"]("actor-2", "admin")
        cmd = mod["AccountActiveChangeCommand"]("target-1", False)

        result = mgr.change_active(actor, cmd)
        assert result.is_active is False
        assert accounts.active_changes == [("target-1", False)]

        # Verify call order
        assert events[0] == "lock"
        assert "commit" in events
        idx_lock = events.index("lock")
        idx_get = next(i for i, e in enumerate(events) if e.startswith("get:"))
        idx_mut = events.index("change_active:target-1")
        idx_commit = events.index("commit")
        assert idx_lock < idx_get < idx_mut < idx_commit

    def test_last_admin_self_protection_is_separate_from_last_admin(self):
        """Self-protection (actor == target) triggers BEFORE UoW, with different
        error message — confirm they are independent code paths."""
        mod = self._imports()

        mgr, accounts, events = self._manage_account(repo_admin_count=1)

        # actor IS the target → self-protection
        actor = mod["AccountActor"]("target-1", "admin")
        cmd = mod["AccountRoleChangeCommand"]("target-1", "personal_user")

        with pytest.raises(mod["InvalidAccountChange"]) as exc_info:
            mgr.change_role(actor, cmd)

        assert "own administrative role" in str(exc_info.value).lower()
        # self-protection triggers before UoW is entered, so lock was never called
        assert "lock" not in events
        assert events == []


class TestAccountAdminLockDialect:
    """Dialect branch tests for acquire_account_administration_lock."""

    @staticmethod
    def _make_session_mock(dialect_name: str):
        """Return (session_mock, execute_log) where session_mock simulates
        the given dialect name and records any ``execute()`` calls."""
        execute_log: list[str] = []

        class FakeDialect:
            name = dialect_name

        class FakeBind:
            dialect = FakeDialect()

        class FakeSession:
            def __init__(self):
                self._bind = FakeBind()

            def get_bind(self):
                return self._bind

            def execute(self, stmt):
                execute_log.append(str(stmt))

        return FakeSession(), execute_log

    def test_sqlite_issues_begin_immediate(self):
        from app.infrastructure.accounts import SqlAlchemyAccountUnitOfWork

        session, log = self._make_session_mock("sqlite")
        uow = SqlAlchemyAccountUnitOfWork.__new__(SqlAlchemyAccountUnitOfWork)
        uow._session_factory = None
        uow._session = session

        uow.acquire_account_administration_lock()

        assert len(log) == 1
        assert "BEGIN IMMEDIATE" in log[0]

    def test_postgresql_does_not_issue_sql(self):
        from app.infrastructure.accounts import SqlAlchemyAccountUnitOfWork

        session, log = self._make_session_mock("postgresql")
        uow = SqlAlchemyAccountUnitOfWork.__new__(SqlAlchemyAccountUnitOfWork)
        uow._session_factory = None
        uow._session = session

        uow.acquire_account_administration_lock()

        assert log == []

    def test_mysql_does_not_issue_sql(self):
        from app.infrastructure.accounts import SqlAlchemyAccountUnitOfWork

        session, log = self._make_session_mock("mysql")
        uow = SqlAlchemyAccountUnitOfWork.__new__(SqlAlchemyAccountUnitOfWork)
        uow._session_factory = None
        uow._session = session

        uow.acquire_account_administration_lock()

        assert log == []

    def test_none_session_raises(self):
        from app.infrastructure.accounts import SqlAlchemyAccountUnitOfWork

        uow = SqlAlchemyAccountUnitOfWork.__new__(SqlAlchemyAccountUnitOfWork)
        uow._session_factory = None
        uow._session = None

        with pytest.raises(RuntimeError, match="unit of work"):
            uow.acquire_account_administration_lock()
    """The three sources of role names must stay in sync."""

    def test_orm_check_constraint_matches_acount_roles(self):
        from app.models.user import USER_ROLES

        orm_roles = set(USER_ROLES)
        domain_roles = set(ACCOUNT_ROLES)
        assert orm_roles == domain_roles, (
            f"ORM USER_ROLES {sorted(orm_roles)} != ACCOUNT_ROLES {sorted(domain_roles)}"
        )

    def test_test_factory_matches_acount_roles(self):
        from tests.user_factory import ALL_ROLES

        factory_roles = ALL_ROLES
        domain_roles = set(ACCOUNT_ROLES)
        assert factory_roles == domain_roles, (
            f"Factory ALL_ROLES {sorted(factory_roles)} != ACCOUNT_ROLES {sorted(domain_roles)}"
        )


class TestEnterpriseAuthorizationUnchanged:
    """Enterprise profile authorization is not affected by account.manage changes."""

    def test_enterprise_user_can_create_enterprise(self):
        uid, tok = _ensure_user("ent_user", "enterprise_user")
        resp = client.post(
            "/api/v1/enterprises",
            json={"enterprise_name": "Test Corp"},
            headers=_headers(tok),
        )
        assert resp.status_code == 200

    def test_admin_can_read_enterprise(self):
        ent_uid, ent_tok = _ensure_user("ent_owner", "enterprise_user")
        create_resp = client.post(
            "/api/v1/enterprises",
            json={"enterprise_name": "ReadCorp"},
            headers=_headers(ent_tok),
        )
        eid = create_resp.json()["data"]["enterprise_id"]

        admin_uid, admin_tok = _ensure_user("ent_admin", "admin")
        resp = client.get(
            f"/api/v1/enterprises/{eid}",
            headers=_headers(admin_tok),
        )
        assert resp.status_code == 200

    def test_personal_user_cannot_read_others_enterprise(self):
        ent_uid, ent_tok = _ensure_user("ent_owner2", "enterprise_user")
        create_resp = client.post(
            "/api/v1/enterprises",
            json={"enterprise_name": "PrivateCorp"},
            headers=_headers(ent_tok),
        )
        eid = create_resp.json()["data"]["enterprise_id"]

        pu_uid, pu_tok = _ensure_user("ent_personal", "personal_user")
        resp = client.get(
            f"/api/v1/enterprises/{eid}",
            headers=_headers(pu_tok),
        )
        assert resp.status_code == 403
