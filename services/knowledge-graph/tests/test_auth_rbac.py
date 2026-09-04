from sqlalchemy import select

from app.auth import hash_password, verify_password
from app.models import AuditLog
from tests.factories import valid_build

def test_password_hash_is_salted_bcrypt():
    first=hash_password("secret"); second=hash_password("secret")
    assert first.startswith("$2") and first!=second
    assert verify_password("secret",first) and not verify_password("wrong",first)

def test_role_fixtures_reuse_one_valid_password_hash(users):
    password_hashes = {user.password_hash for user in users.values()}
    assert len(password_hashes) == 1
    assert verify_password("secret", password_hashes.pop())

def test_core_rbac_and_draft_visibility(client,db,auth_headers):
    assert client.post("/api/v1/positions/POS_BACKEND/graph/build",json={}).status_code==401
    assert client.post("/api/v1/graph/build-runs/1/publish",json={}).status_code==401
    assert client.post("/api/v1/positions/POS_BACKEND/graph/build",json={},headers=auth_headers("personal_user")).status_code==403
    assert client.get("/api/v1/positions/POS_BACKEND/graph/build-runs",headers=auth_headers("personal_user")).status_code==403
    assert client.post("/api/v1/graph/build-runs/1/publish",json={},headers=auth_headers("reviewer")).status_code==403

def test_invalid_login_uses_unified_error_contract(client, users):
    response=client.post("/api/v1/auth/token",json={"username":"admin","password":"wrong"})
    assert response.status_code==401
    assert response.json()["code"]==40101
    assert response.json()["trace_id"]

def test_forged_actor_is_ignored(client,db,auth_headers,users):
    headers=auth_headers("admin"); build=valid_build(client,db,headers)
    response=client.post(f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',json={"actor_id":999999,"reason":"trusted"},headers=headers)
    assert response.status_code==200
    audit=db.scalar(select(AuditLog).where(AuditLog.action=="publish_graph"))
    assert audit.actor_id==users["admin"].id and audit.actor_id!=999999
