import jwt
import pytest
from fastapi import HTTPException
from app.auth import create_token, current_user, hash_password, require_roles, verify_password
from app.config import Settings
from app.models import User
from app.schemas.extraction import Evidence, JDExtractionResult
from app.domain.policies import align_quote
from app.infrastructure.providers.normalization import Normalizer

def test_jwt_login_me_and_failures(client,db):
    user=User(username="admin",password_hash=hash_password("secret"),role="admin"); db.add(user); db.commit()
    assert verify_password("secret",user.password_hash)
    bad=client.post("/api/v1/auth/token",json={"username":"admin","password":"bad"}); assert bad.status_code==401
    login=client.post("/api/v1/auth/token",json={"username":"admin","password":"secret"}).json()["data"]
    me=client.get("/api/v1/auth/me",headers={"Authorization":f'Bearer {login["access_token"]}'}); assert me.json()["data"]["role"]=="admin"
    assert client.get("/api/v1/auth/me").status_code==401
    assert client.get("/api/v1/auth/me",headers={"Authorization":"Bearer broken"}).status_code==401

def test_auth_unknown_user_and_roles(db):
    ghost=User(id=999,username="ghost",password_hash="x",role="viewer")
    runtime_settings = Settings()
    token=create_token(ghost, runtime_settings)
    class Credentials: credentials=token
    class App: state=type("State", (), {"settings": runtime_settings})()
    class Request: app=App()
    with pytest.raises(HTTPException) as exc: current_user(Request(),Credentials(),db)
    assert exc.value.status_code==401
    admin=User(id=1,username="a",password_hash="x",role="admin")
    assert require_roles("admin")(admin) is admin
    with pytest.raises(HTTPException) as denied: require_roles("reviewer")(admin)
    assert denied.value.status_code==403

def test_contract_edge_branches():
    ev=Evidence(source_id="x",quote="abc",start=0,end=3,alignment="exact",occurrence_index=0)
    assert ev.is_exact_for("abc") and not ev.is_exact_for("xyz")
    with pytest.raises(ValueError): align_quote("abc","abc",99)
    with pytest.raises(ValueError): align_quote("abc","abc",-1)
    result=JDExtractionResult(document_id="x")
    normalized=Normalizer().normalize(result); assert normalized.job_classification.classification_status=="catalog_gap"
    assert normalized.unresolved_items==[]
