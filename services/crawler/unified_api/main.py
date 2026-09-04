"""统一爬虫后端 API 入口。"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from jobgraph_contracts.offline_api_docs import install_offline_api_docs
from .config import (
    CORS_ALLOWED_ORIGINS,
    CORS_ALLOW_CREDENTIALS,
    validate_security_settings,
)
from .database import ensure_schema
from .routers import (
    auth_router,
    boss_router,
    company_router,
    liepin_router,
    task_router,
    internal_router,
)
from patches.scheduler import start_scheduler
from patches.scheduler_router import router as scheduler_router

app = FastAPI(
    title="统一爬虫后端 API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)
install_offline_api_docs(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    validate_security_settings()
    ensure_schema()
    if os.getenv("CRAWLER_EMBEDDED_SCHEDULER_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        start_scheduler()


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 注册路由
app.include_router(auth_router.router)
app.include_router(boss_router.router)
app.include_router(company_router.router)
app.include_router(liepin_router.router)
app.include_router(task_router.router)
app.include_router(internal_router.router)
app.include_router(scheduler_router)
