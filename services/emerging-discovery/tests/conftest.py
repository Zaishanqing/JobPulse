import os
import gc
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.getenv(
    "EMERGING_DISCOVERY_TEST_DATABASE_URL",
    "postgresql+psycopg://emerging_discovery:"
    "jobgraph-emerging-local-password@localhost:5434/"
    "emerging_discovery_test?connect_timeout=3",
)
test_url = make_url(TEST_DATABASE_URL)
if test_url.drivername != "postgresql+psycopg" or test_url.database != "emerging_discovery_test":
    raise RuntimeError("tests require the isolated PostgreSQL emerging_discovery_test database")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"

from tests.runtime_database import engine  # noqa: E402

os.environ.pop("DATABASE_URL", None)
os.environ.pop("ENVIRONMENT", None)


@pytest.fixture(autouse=True)
def local_formal_emergence_policy(monkeypatch):
    """Keep the isolated module suite independent from the external KG service."""
    from app.infrastructure.emergence_v32 import KnowledgeGraphEmergenceV32Client

    def evaluate(_client, *, dataset_id, clusters):
        del dataset_id
        result = {}
        for cluster in clusters:
            members = cluster.get("members") or []
            dates = {item.get("observation_date") for item in members}
            platforms = {item.get("source_platform") for item in members}
            state = "emerging" if len(dates) >= 2 and len(platforms) >= 2 else "watchlist"
            result[str(cluster["cluster_id"])] = {
                "state": state,
                "reason": "isolated test policy",
            }
        return result

    monkeypatch.setattr(KnowledgeGraphEmergenceV32Client, "evaluate", evaluate)


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    for candidate in gc.get_objects():
        if isinstance(candidate, TestClient):
            candidate.close()
    for candidate in gc.get_objects():
        if isinstance(candidate, Engine):
            candidate.dispose()
    gc.collect()
    command.downgrade(config, "base")


@pytest.fixture(autouse=True)
def clean_database(isolated_test_database):
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE discovery_maintenance_audits, "
                "identity_resolution_audits, "
                "candidate_lineage_reviews, candidate_lineage_relations, "
                "candidate_status_transitions, candidate_cluster_observations, candidates, "
                "germination_assessments, cluster_lineages, cluster_memberships, "
                "clusters, algorithm_config_snapshots, input_snapshots, discovery_runs CASCADE"
            )
        )
    yield
