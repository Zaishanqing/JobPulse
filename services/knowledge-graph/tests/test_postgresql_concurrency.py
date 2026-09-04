from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.infrastructure.readiness import DatabaseReadiness
from app.infrastructure.sqlalchemy.build_jobs import SqlAlchemyBuildJobRepository
from app.config import Settings
from app.models import (
    GraphBuildJob,
    GraphBuildRun,
    GraphVersion,
    MappingCandidateRecord,
    MappingReviewDecisionRecord,
    PositionSkillRelationDraft,
    Skill,
    StandardPosition,
    User,
)


POSTGRES_URL = os.getenv("KG_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="KG_TEST_POSTGRES_URL is not configured"
)
ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def postgres_database():
    assert POSTGRES_URL is not None
    parsed = make_url(POSTGRES_URL)
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("KG_TEST_POSTGRES_URL must target a disposable test database")
    engine = create_engine(POSTGRES_URL, isolation_level="READ COMMITTED")
    with engine.begin() as connection:
        for table in reversed(inspect(connection).get_table_names()):
            connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')
    environment = {**os.environ, "DATABASE_URL": POSTGRES_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    yield engine
    engine.dispose()


def _seed(session_factory):
    with session_factory() as session:
        user = User(username="pg-reviewer", password_hash="x", role="reviewer")
        position = StandardPosition(
            position_id="POS_PG", name="PG岗位", category_code="TECH"
        )
        skill = Skill(skill_id="SKILL_PG", canonical_name="PG", taxonomy_version="v1")
        session.add_all([user, position, skill])
        session.flush()
        runs = [
            GraphBuildRun(
                position_id=position.position_id,
                status="succeeded",
                config_snapshot={},
                summary={},
            )
            for _ in range(3)
        ]
        session.add_all(runs)
        session.flush()
        relation = PositionSkillRelationDraft(
            build_run_id=runs[0].id,
            position_id=position.position_id,
            skill_id=skill.skill_id,
            revision=1,
            status="candidate",
            metrics={},
            statistics={},
            explanation={},
            auto_weight=0.5,
            final_weight=0.5,
            auto_confidence=0.8,
            final_confidence=0.8,
            auto_importance_level="important",
            final_importance_level="important",
        )
        candidate = MappingCandidateRecord(
            candidate_id="MAP_PG",
            source_expression="PostgreSQL",
            proposed_skill_id=skill.skill_id,
            signals={},
            priority=0.8,
            model_version="v1",
            index_version="v1",
            mapping_policy_version="v1",
            affected_contexts=[{"source_fact_id": "F1", "requirement_id": "R1"}],
            status="pending",
            revision=1,
        )
        session.add_all([relation, candidate])
        session.commit()
        return user.id, position.position_id, skill.skill_id, [row.id for row in runs], relation.id


def test_postgresql_migration_readiness_and_concurrency(postgres_database):
    factory = sessionmaker(bind=postgres_database, expire_on_commit=False)
    user_id, position_id, _skill_id, run_ids, relation_id = _seed(factory)
    settings = Settings(
        environment="production",
        database_url=POSTGRES_URL,
        catalog_writes_enabled=False,
        jwt_secret_key="postgres-test-secret-with-at-least-32-characters",
        service_password="postgres-test-service-password",
    )
    readiness = DatabaseReadiness(postgres_database, settings).check()
    assert readiness["status"] == "ready"
    assert readiness["transaction_isolation"] == "READ COMMITTED"

    with factory() as session:
        job = SqlAlchemyBuildJobRepository(session).enqueue(
            "pg-job", position_id, {"position_id": position_id}, 3
        )
        session.commit()

    def claim(worker_id):
        with factory() as session:
            claimed = SqlAlchemyBuildJobRepository(session).claim(worker_id, job.job_id)
            session.commit()
            return claimed is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-a", "worker-b")))
    assert sorted(claims) == [False, True]

    def edit_relation(weight):
        with factory() as session:
            changed = session.execute(
                update(PositionSkillRelationDraft)
                .where(
                    PositionSkillRelationDraft.id == relation_id,
                    PositionSkillRelationDraft.revision == 1,
                )
                .values(final_weight=weight, revision=2)
            ).rowcount
            session.commit()
            return changed

    with ThreadPoolExecutor(max_workers=2) as executor:
        edits = list(executor.map(edit_relation, (0.6, 0.7)))
    assert sorted(edits) == [0, 1]

    def publish(build_run_id):
        with factory() as session:
            session.add(
                GraphVersion(
                    position_id=position_id,
                    build_run_id=build_run_id,
                    version_number=1,
                    version_name="v1",
                    snapshot={},
                    content_hash=str(build_run_id % 10) * 64,
                    algorithm_version="v1",
                    normalization_map_version="v1",
                    published_fact_versions=[],
                    skill_catalog_version="v1",
                    mapping_snapshot_version="v1",
                    normalization_algorithm_version="v1",
                    build_config_version="v1",
                    source_time_window={},
                    published_by=user_id,
                )
            )
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        publications = list(executor.map(publish, run_ids[1:3]))
    assert sorted(publications) == [False, True]

    def review_mapping(reason):
        with factory() as session:
            session.add(
                MappingReviewDecisionRecord(
                    candidate_id="MAP_PG",
                    candidate_revision=1,
                    decision="reject",
                    reviewer_id=user_id,
                    reason=reason,
                    policy_version="v1",
                    decided_at="2026-08-01T00:00:00Z",
                    effective_scope="affected_contexts",
                )
            )
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        mapping_reviews = list(executor.map(review_mapping, ("a", "b")))
    assert sorted(mapping_reviews) == [False, True]
