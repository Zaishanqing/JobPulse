from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.bootstrap import create_app  # noqa: E402
from app.infrastructure.database import create_database  # noqa: E402
from app.infrastructure.models import Base  # noqa: E402
from app.infrastructure.settings import Settings  # noqa: E402
from app.infrastructure.credibility_store import SqlAlchemyCredibilityStore  # noqa: E402
import app.acquisition.infrastructure.acquisition_models  # noqa: E402, F401

TOKEN = "test-internal-token-strong"


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TREND_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TREND_TEST_POSTGRES_URL is not configured")
    if not value.startswith("postgresql+psycopg://") or not value.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TREND_TEST_POSTGRES_URL must target a disposable PostgreSQL *_test database")
    return value


@pytest.fixture
def database(database_url: str):
    db = create_database(database_url)
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)
    yield db
    Base.metadata.drop_all(db.engine)
    db.engine.dispose()


@pytest.fixture
def client(database_url: str, database):
    settings = Settings(
        DATABASE_URL=database_url,
        INTERNAL_TOKEN=TOKEN,
        MAX_ATTEMPTS=3,
    )
    with TestClient(create_app(settings, database=database)) as test_client:
        yield test_client


@pytest.fixture
def credibility_store(database):
    store = SqlAlchemyCredibilityStore(database.sessions)
    store.ensure_seeded()
    return store


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def payload() -> dict:
    return {
        "contract_version": "trend-analysis.v2",
        "request_id": "request-001",
        "idempotency_key": "idem-001",
        "time_window": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-02-01T00:00:00Z",
        },
        "data_sources": ["papers", "policy"],
        "weights": {"recency": 0.6, "authority": 0.4},
        "algorithm_version": "skeleton-v1",
        "formula_version": "formula-v1",
    }
