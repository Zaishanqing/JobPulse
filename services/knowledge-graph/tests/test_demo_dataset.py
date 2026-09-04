from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import create_database
from app.infrastructure.sqlalchemy.graph_build_repository import (
    SqlAlchemyGraphBuildRepository,
)
from app.models import GraphBuildSample, GraphVersion, JDDocument, PublishedFactImport
from scripts.load_demo_dataset import DEMO_DATASET_ID, DEMO_POSITION_ID, load


ROOT = Path(__file__).parents[1]


def test_demo_dataset_is_explicit_idempotent_and_excluded_from_builds(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    environment = {**os.environ, "DATABASE_URL": database_url}
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert migration.returncode == 0, migration.stderr
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ENVIRONMENT", "development")

    assert load("minimal")["document_count"] == 2
    assert load("minimal")["document_count"] == 2

    database = create_database(Settings.from_env())
    try:
        with database.session_factory() as session:
            documents = session.scalars(
                select(JDDocument).where(JDDocument.source_name == DEMO_DATASET_ID)
            ).all()
            assert len(documents) == 2
            assert all(item.is_synthetic for item in documents)
            assert all(item.fact_authority == "demo" for item in documents)
            assert not session.scalars(select(PublishedFactImport)).all()
            assert not session.scalars(select(GraphBuildSample)).all()
            assert not session.scalars(select(GraphVersion)).all()
            assert not SqlAlchemyGraphBuildRepository(session).load_facts(
                DEMO_POSITION_ID, authoritative_only=True
            ).documents
            assert not SqlAlchemyGraphBuildRepository(session).load_facts(
                DEMO_POSITION_ID, authoritative_only=False
            ).documents
            with pytest.raises(IntegrityError):
                session.execute(
                    update(JDDocument)
                    .where(JDDocument.document_id == documents[0].document_id)
                    .values(fact_authority="authoritative")
                )
                session.commit()
            session.rollback()
    finally:
        database.engine.dispose()


def test_demo_dataset_is_forbidden_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://kg:password@db/knowledge_graph"
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "demo-guard-production-secret-with-at-least-32-characters"
    )
    monkeypatch.setenv(
        "KNOWLEDGE_GRAPH_SERVICE_PASSWORD", "demo-guard-service-password"
    )
    monkeypatch.setenv("CATALOG_WRITES_ENABLED", "false")
    with pytest.raises(RuntimeError, match="forbidden in production"):
        load("minimal")


def test_container_startup_does_not_auto_seed():
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    standalone = (ROOT / "docker-compose.yml").read_text("utf-8")
    root_compose = (
        ROOT.parents[1] / "infra" / "compose" / "docker-compose.candidate.yml"
    ).read_text("utf-8")
    assert "seed_reference_data.py" not in dockerfile
    assert "seed_reference_data.py" not in standalone
    assert "seed_reference_data.py" not in root_compose
