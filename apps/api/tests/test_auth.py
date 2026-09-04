from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.accounts import get_account_handlers_from_request
from tests.runtime_database import reset_database_data, SessionLocal
from app.main import app
from app.core.security import decode_access_token
from app.schemas.auth import PublicRegisterRequest
from app.services.auth_service import register_user
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _register_payload(username: str, role: str = "personal_user") -> dict:
    return {
        "role": role,
        "username": username,
        "password": "password123",
        "email": f"{username}@example.com",
        "phone": "13800000000",
    }


def test_register_personal_user_success():
    response = client.post("/api/v1/auth/register", json=_register_payload("user001"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["message"] == "success"
    assert payload["data"]["user_id"]
    assert payload["data"]["role"] == "personal_user"
    assert payload["data"]["username"] == "user001"


def test_register_enterprise_user_success():
    response = client.post(
        "/api/v1/auth/register",
        json=_register_payload("enterprise001", role="enterprise_user"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["role"] == "enterprise_user"
    assert payload["data"]["username"] == "enterprise001"


def test_register_invalid_role_fails():
    response = client.post(
        "/api/v1/auth/register",
        json=_register_payload("badrole001", role="guest"),
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422


@pytest.mark.parametrize("role", ["admin", "reviewer", "developer"])
def test_anonymous_registration_rejects_internal_roles(role: str):
    response = client.post(
        "/api/v1/auth/register",
        json=_register_payload(f"blocked_{role}", role=role),
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422


def test_demo_admin_registration_opt_in_uses_normal_jwt_and_rbac():
    handlers = app.state.container.accounts
    enabled_handlers = replace(
        handlers,
        registration=replace(
            handlers.registration,
            allow_demo_admin_registration=True,
        ),
    )
    app.dependency_overrides[get_account_handlers_from_request] = (
        lambda: enabled_handlers
    )
    try:
        registered = client.post(
            "/api/v1/auth/register",
            json=_register_payload("demo_registered_admin", role="admin"),
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "demo_registered_admin", "password": "password123"},
        )
        token = login.json()["data"]["access_token"]
        current = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert registered.status_code == 200
        assert registered.json()["data"]["role"] == "admin"
        assert login.status_code == 200
        assert current.status_code == 200
        assert current.json()["data"]["role"] == "admin"
        assert "account.manage" in current.json()["data"]["permissions"]

        for role in ("reviewer", "developer"):
            blocked = client.post(
                "/api/v1/auth/register",
                json=_register_payload(f"still_blocked_{role}", role=role),
            )
            assert blocked.status_code == 422
    finally:
        app.dependency_overrides.pop(get_account_handlers_from_request, None)


def test_registration_service_rejects_admin_when_demo_flag_is_disabled():
    payload = PublicRegisterRequest.model_construct(
        role="admin", username="service_attack", password="password123"
    )
    with SessionLocal() as db, pytest.raises(Exception) as exc_info:
        register_user(db, payload)

    assert getattr(exc_info.value, "status_code", None) == 422


def test_registration_service_rejects_weak_password_when_schema_is_bypassed():
    payload = PublicRegisterRequest.model_construct(
        role="personal_user", username="weak_service_password", password="short7"
    )
    with SessionLocal() as db, pytest.raises(Exception) as exc_info:
        register_user(db, payload)

    assert getattr(exc_info.value, "status_code", None) == 422


def test_only_internal_admin_or_developer_can_assign_roles():
    admin_id = create_internal_user("role_admin", "admin")
    assert admin_id
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "role_admin", "password": "password123"},
    )
    admin_headers = {
        "Authorization": f"Bearer {admin_login.json()['data']['access_token']}"
    }
    personal = client.post(
        "/api/v1/auth/register", json=_register_payload("role_personal")
    ).json()["data"]
    personal_login = client.post(
        "/api/v1/auth/login",
        json={"username": "role_personal", "password": "password123"},
    )
    personal_headers = {
        "Authorization": f"Bearer {personal_login.json()['data']['access_token']}"
    }

    self_escalation = client.put(
        f"/api/v1/users/{personal['user_id']}/role",
        json={"role": "admin"},
        headers=personal_headers,
    )
    admin_grant = client.put(
        f"/api/v1/users/{personal['user_id']}/role",
        json={"role": "reviewer"},
        headers=admin_headers,
    )

    assert self_escalation.status_code == 403
    assert admin_grant.status_code == 200
    assert admin_grant.json()["data"]["role"] == "reviewer"


def test_register_duplicate_username_fails():
    payload = _register_payload("duplicate001")
    first_response = client.post("/api/v1/auth/register", json=payload)
    second_response = client.post("/api/v1/auth/register", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["code"] == 409


def test_login_success_returns_access_token():
    client.post("/api/v1/auth/register", json=_register_payload("login001"))

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "login001", "password": "password123"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"]
    assert data["token_type"] == "bearer"


def test_auth_me_success_with_token():
    client.post("/api/v1/auth/register", json=_register_payload("me001"))
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "me001", "password": "password123"},
    )
    token = login_response.json()["data"]["access_token"]

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["username"] == "me001"
    assert payload["data"]["role"] == "personal_user"


@pytest.mark.parametrize(
    ("role", "can_write_jd"),
    [
        ("enterprise_user", True),
        ("reviewer", False),
        ("admin", True),
        ("developer", True),
        ("personal_user", False),
    ],
)
def test_auth_me_exposes_jd_business_permissions(role: str, can_write_jd: bool):
    username = f"me_jd_{role}"
    if role in {"admin", "reviewer", "developer"}:
        create_internal_user(username, role)
    else:
        response = client.post(
            "/api/v1/auth/register", json=_register_payload(username, role=role)
        )
        assert response.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    token = login.json()["data"]["access_token"]

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    permissions = set(response.json()["data"]["permissions"])
    assert ("jd.create" in permissions) is can_write_jd
    assert ("jd.parse" in permissions) is can_write_jd
    assert ("jd.publish" in permissions) is (role in {"admin", "developer"})
    assert "integration.jd.retry" not in permissions or role in {"admin", "developer"}


def test_login_fails_with_wrong_password():
    client.post("/api/v1/auth/register", json=_register_payload("wrongpwd001"))

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "wrongpwd001", "password": "bad-password"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == 401


def test_change_password_requires_and_verifies_old_password():
    client.post("/api/v1/auth/register", json=_register_payload("password001"))
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "password001", "password": "password123"},
    )
    headers = {
        "Authorization": f"Bearer {login_response.json()['data']['access_token']}"
    }

    missing_old = client.put(
        "/api/v1/auth/password",
        json={"new_password": "new-password123"},
        headers=headers,
    )
    wrong_old = client.put(
        "/api/v1/auth/password",
        json={"old_password": "wrong-password", "new_password": "new-password123"},
        headers=headers,
    )
    changed = client.put(
        "/api/v1/auth/password",
        json={"old_password": "password123", "new_password": "new-password123"},
        headers=headers,
    )

    assert missing_old.status_code == 422
    assert wrong_old.status_code == 422
    assert changed.status_code == 200
    assert changed.json()["data"]["password_changed"] is True
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "password001", "password": "password123"},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "password001", "password": "new-password123"},
    ).status_code == 200


def test_old_jwt_fails_after_password_change():
    client.post("/api/v1/auth/register", json=_register_payload("password_revoke_old"))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "password_revoke_old", "password": "password123"},
    )
    old_token = login.json()["data"]["access_token"]

    changed = client.put(
        "/api/v1/auth/password",
        json={"old_password": "password123", "new_password": "new-password123"},
        headers={"Authorization": f"Bearer {old_token}"},
    )

    assert changed.status_code == 200
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"}
    ).status_code == 401


def test_new_jwt_succeeds_after_password_change():
    client.post("/api/v1/auth/register", json=_register_payload("password_revoke_new"))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "password_revoke_new", "password": "password123"},
    )
    old_token = login.json()["data"]["access_token"]
    old_claims = decode_access_token(old_token)

    changed = client.put(
        "/api/v1/auth/password",
        json={"old_password": "password123", "new_password": "new-password123"},
        headers={"Authorization": f"Bearer {old_token}"},
    )
    new_token = changed.json()["data"]["access_token"]
    new_claims = decode_access_token(new_token)

    assert type(old_claims["tv"]) is int
    assert new_claims["tv"] == old_claims["tv"] + 1
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"}
    ).status_code == 200
    relogin = client.post(
        "/api/v1/auth/login",
        json={"username": "password_revoke_new", "password": "new-password123"},
    )
    assert relogin.status_code == 200
    assert decode_access_token(relogin.json()["data"]["access_token"])["tv"] == new_claims["tv"]


def test_old_jwt_fails_after_logout_all():
    client.post("/api/v1/auth/register", json=_register_payload("logout_all_user"))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "logout_all_user", "password": "password123"},
    )
    old_token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {old_token}"}

    logged_out = client.post("/api/v1/auth/logout-all", headers=headers)

    assert logged_out.status_code == 200
    assert logged_out.json()["data"]["logged_out_all"] is True
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/refresh", headers=headers).status_code == 401
    relogin = client.post(
        "/api/v1/auth/login",
        json={"username": "logout_all_user", "password": "password123"},
    )
    assert relogin.status_code == 200
    assert client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {relogin.json()['data']['access_token']}"
        },
    ).status_code == 200


def test_refresh_renews_current_access_session_without_revoking_source_token():
    client.post("/api/v1/auth/register", json=_register_payload("refresh_session_user"))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "refresh_session_user", "password": "password123"},
    )
    source_token = login.json()["data"]["access_token"]
    source_headers = {"Authorization": f"Bearer {source_token}"}

    refreshed = client.post("/api/v1/auth/refresh", headers=source_headers)
    renewed_token = refreshed.json()["data"]["access_token"]

    assert refreshed.status_code == 200
    assert decode_access_token(renewed_token)["tv"] == decode_access_token(source_token)["tv"]
    assert client.get("/api/v1/auth/me", headers=source_headers).status_code == 200
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {renewed_token}"}
    ).status_code == 200


def test_registration_and_password_change_require_eight_characters():
    too_short_registration = _register_payload("short_password_user")
    too_short_registration["password"] = "short7"
    assert client.post(
        "/api/v1/auth/register", json=too_short_registration
    ).status_code == 422

    client.post("/api/v1/auth/register", json=_register_payload("password_length_user"))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "password_length_user", "password": "password123"},
    )
    assert client.put(
        "/api/v1/auth/password",
        json={"old_password": "password123", "new_password": "short7"},
        headers={
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        },
    ).status_code == 422


def test_account_error_boundaries_and_missing_authentication():
    admin_id = create_internal_user("boundary_admin", "admin")
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "boundary_admin", "password": "password123"},
    )
    headers = {
        "Authorization": f"Bearer {login.json()['data']['access_token']}"
    }

    unauthenticated = client.get("/api/v1/roles")
    missing = client.put(
        "/api/v1/users/missing-account/disable",
        headers=headers,
    )
    invalid_role = client.put(
        f"/api/v1/users/{admin_id}/role",
        json={"role": "not-a-role"},
        headers=headers,
    )

    assert unauthenticated.status_code == 401
    assert missing.status_code == 404
    assert invalid_role.status_code == 422


def test_disabled_account_cannot_login_or_refresh_existing_token():
    admin_id = create_internal_user("disable_login_admin", "admin")
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "disable_login_admin", "password": "password123"},
    )
    admin_headers = {
        "Authorization": f"Bearer {admin_login.json()['data']['access_token']}"
    }
    user = client.post(
        "/api/v1/auth/register",
        json=_register_payload("disabled_login_user"),
    ).json()["data"]
    user_login = client.post(
        "/api/v1/auth/login",
        json={"username": "disabled_login_user", "password": "password123"},
    )
    user_headers = {
        "Authorization": f"Bearer {user_login.json()['data']['access_token']}"
    }

    disabled = client.put(
        f"/api/v1/users/{user['user_id']}/disable",
        headers=admin_headers,
    )
    login_again = client.post(
        "/api/v1/auth/login",
        json={"username": "disabled_login_user", "password": "password123"},
    )
    refresh = client.post("/api/v1/auth/refresh", headers=user_headers)

    assert admin_id
    assert disabled.status_code == 200
    assert login_again.status_code == 401
    assert refresh.status_code == 401


def test_user_cannot_modify_another_account():
    first = client.post(
        "/api/v1/auth/register", json=_register_payload("rbac_first_user")
    ).json()["data"]
    second = client.post(
        "/api/v1/auth/register", json=_register_payload("rbac_second_user")
    ).json()["data"]
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "rbac_first_user", "password": "password123"},
    )
    headers = {
        "Authorization": f"Bearer {login.json()['data']['access_token']}"
    }

    role_change = client.put(
        f"/api/v1/users/{second['user_id']}/role",
        json={"role": "reviewer"},
        headers=headers,
    )
    disable = client.put(
        f"/api/v1/users/{second['user_id']}/disable",
        headers=headers,
    )

    assert first["user_id"] != second["user_id"]
    assert role_change.status_code == 403
    assert disable.status_code == 403


def test_role_change_updates_permissions_for_existing_token():
    create_internal_user("role_effect_admin", "admin")
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "role_effect_admin", "password": "password123"},
    )
    admin_headers = {
        "Authorization": f"Bearer {admin_login.json()['data']['access_token']}"
    }
    user = client.post(
        "/api/v1/auth/register", json=_register_payload("role_effect_user")
    ).json()["data"]
    user_login = client.post(
        "/api/v1/auth/login",
        json={"username": "role_effect_user", "password": "password123"},
    )
    user_headers = {
        "Authorization": f"Bearer {user_login.json()['data']['access_token']}"
    }

    before = client.get("/api/v1/roles", headers=user_headers)
    promoted = client.put(
        f"/api/v1/users/{user['user_id']}/role",
        json={"role": "admin"},
        headers=admin_headers,
    )
    after_promotion = client.get("/api/v1/roles", headers=user_headers)
    demoted = client.put(
        f"/api/v1/users/{user['user_id']}/role",
        json={"role": "personal_user"},
        headers=admin_headers,
    )
    after_demotion = client.get("/api/v1/roles", headers=user_headers)

    assert before.status_code == 403
    assert promoted.status_code == 200
    assert after_promotion.status_code == 200
    assert demoted.status_code == 200
    assert after_demotion.status_code == 403
