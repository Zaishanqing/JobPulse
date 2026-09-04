"""JWT 认证工具。"""
import secrets
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRES_DAYS, INTERNAL_SERVICE_TOKEN
from .database import get_conn

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
internal_security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(days=JWT_EXPIRES_DAYS),
        'iat': datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='登录已过期，请重新登录')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='无效的认证令牌')


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    payload = decode_token(credentials.credentials)
    user_id = payload.get('user_id')
    if not user_id:
        raise HTTPException(status_code=401, detail='令牌无效')
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, created_at FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail='用户不存在')
    return user


def require_internal_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(internal_security),
) -> None:
    token = credentials.credentials if credentials is not None else ""
    if not token or not secrets.compare_digest(token, INTERNAL_SERVICE_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service token",
        )
