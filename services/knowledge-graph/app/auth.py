from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import Settings
from app.database import get_db
from app.models import User

bearer=HTTPBearer(auto_error=False)
password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

VALID_ROLES = {"personal_user", "enterprise_user", "reviewer", "admin", "developer"}
GRAPH_EDITORS = ("admin", "developer")
REVIEWERS = ("reviewer", "admin")
PUBLISHERS = ("admin", "developer")

def hash_password(value: str) -> str:
    return password_context.hash(value)

def verify_password(value: str, hashed: str) -> bool:
    try:
        return password_context.verify(value, hashed)
    except (TypeError, ValueError):
        return False
def create_token(user:User, settings: Settings)->str:
    return create_token_for(user.id, user.role, settings)
def create_token_for(user_id:int, role:str, settings: Settings)->str:
    return jwt.encode({"sub":str(user_id),"role":role,"exp":datetime.now(timezone.utc)+timedelta(hours=8)},settings.jwt_secret_key,algorithm="HS256")
def current_user(request: Request, credentials:HTTPAuthorizationCredentials|None=Depends(bearer),db:Session=Depends(get_db))->User:
    if not credentials: raise HTTPException(401,"missing bearer token")
    try: payload=jwt.decode(credentials.credentials,request.app.state.settings.jwt_secret_key,algorithms=["HS256"]); user=db.get(User,int(payload["sub"]))
    except (jwt.PyJWTError,ValueError,KeyError) as exc: raise HTTPException(401,"invalid token") from exc
    if not user or user.role not in VALID_ROLES: raise HTTPException(401,"user not found")
    return user
def require_roles(*roles):
    def check(user:User=Depends(current_user)):
        if user.role not in roles: raise HTTPException(403,"insufficient permission")
        return user
    return check
