from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy.exc import IntegrityError

from app.api.schemas import CreateAnalysisRunRequest
from app.infrastructure.database import create_database
from app.infrastructure.models import AnalysisRunModel, Base
from app.infrastructure.repository import SqlAlchemyAnalysisRunRepository

SERVICE_ROOT = Path(__file__).resolve().parents[1]
JOBPULSE_ROOT = SERVICE_ROOT.parents[1]


def test_transaction_rollback_preserves_previous_data(database, payload):
    repository = SqlAlchemyAnalysisRunRepository(database.sessions)
    command = CreateAnalysisRunRequest.model_validate(payload).to_command()
    first = repository.create_or_get(command, max_attempts=3)
    with pytest.raises(IntegrityError):
        with database.sessions.begin() as session:
            session.add(
                AnalysisRunModel(
                    id="duplicate",
                    contract_version="trend-analysis.v2",
                    request_id=payload["request_id"],
                    status="pending",
                    window_start=command.window_start,
                    window_end=command.window_end,
                    data_sources=["papers"],
                    weights={"recency": 1.0},
                    algorithm_version="v1",
                    formula_version="v1",
                )
            )
    assert repository.get(first.id).request_id == payload["request_id"]


def test_service_restart_uses_persisted_database(database_url, payload):
    first_db = create_database(database_url)
    Base.metadata.create_all(first_db.engine)
    first_repo = SqlAlchemyAnalysisRunRepository(first_db.sessions)
    command = CreateAnalysisRunRequest.model_validate(payload).to_command()
    run = first_repo.create_or_get(command, max_attempts=3)
    first_db.engine.dispose()

    restarted = create_database(database_url)
    assert SqlAlchemyAnalysisRunRepository(restarted.sessions).get(run.id).id == run.id
    restarted.engine.dispose()


def test_alembic_upgrade_creates_both_tables(database_url):
    env = os.environ.copy()
    env["TREND_INTELLIGENCE_DATABASE_URL"] = database_url
    env["TREND_INTELLIGENCE_INTERNAL_TOKEN"] = "migration-test-token-strong"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=SERVICE_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    tables = set(sa.inspect(sa.create_engine(database_url)).get_table_names())
    assert {
        "analysis_runs",
        "analysis_run_logs",
        "source_snapshots",
        "extracted_terms",
        "signal_observations",
        "prediction_results",
        "run_source_status",
        "evaluation_datasets",
        "evaluation_samples",
        "evaluation_labels",
        "evaluation_dataset_events",
        "source_fetch_attempts",
        "source_replay_cache",
        "source_circuit_states",
        "backtest_runs",
        "trend_change_analyses",
        "alembic_version",
    } <= tables


def test_root_compose_defines_api_worker_and_shared_postgresql_database():
    compose_path = JOBPULSE_ROOT / "infra" / "compose" / "docker-compose.candidate.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    api = compose["services"]["trend-intelligence"]
    worker = compose["services"]["trend-intelligence-worker"]
    expected = "postgresql+psycopg://trend_intelligence:"
    assert api["environment"]["TREND_INTELLIGENCE_DATABASE_URL"].startswith(expected)
    assert worker["environment"]["TREND_INTELLIGENCE_DATABASE_URL"].startswith(expected)
    assert "volumes" not in api
    assert "volumes" not in worker


def test_application_and_domain_have_no_framework_or_orm_imports():
    forbidden = ("fastapi", "sqlalchemy")
    for layer in ("application", "domain"):
        for path in (SERVICE_ROOT / "app" / layer).glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not any(name in source for name in forbidden), path
