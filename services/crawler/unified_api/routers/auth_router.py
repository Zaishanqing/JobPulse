"""认证路由。"""
from fastapi import APIRouter, Depends, HTTPException
from ..database import get_conn
from ..auth import hash_password, verify_password, create_access_token, get_current_user
from ..schemas.auth import RegisterRequest, LoginRequest, LoginResponse, UserResponse
from ..config import JWT_EXPIRES_DAYS

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/register")
def register(body: RegisterRequest):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (body.username,))
    if cur.fetchone():
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail="用户名已存在")

    pw_hash = hash_password(body.password)
    cur.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
        (body.username, pw_hash),
    )
    conn.commit()
    user_id = cur.lastrowid
    cur.close()
    conn.close()
    return {"id": user_id, "username": body.username, "message": "注册成功"}


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, password_hash FROM users WHERE username = %s",
        (body.username,),
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user or not verify_password(body.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user['id'])
    return LoginResponse(
        access_token=token,
        expires_in=JWT_EXPIRES_DAYS * 86400,
    )


@router.get("/profile", response_model=UserResponse)
def profile(user: dict = Depends(get_current_user)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM crawl_tasks WHERE user_id = %s", (user['id'],))
    total = cur.fetchone()['cnt']
    cur.close()
    conn.close()
    return UserResponse(
        id=user['id'],
        username=user['username'],
        created_at=str(user['created_at']),
        total_tasks=total,
    )
