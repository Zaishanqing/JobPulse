"""Run destructive KG scale/concurrency acceptance against a disposable PostgreSQL DB."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.contracts import BuildGraphCommand, RollbackGraphCommand
from app.application.use_cases import BuildGraphUseCase, RollbackGraphVersionUseCase
from app.infrastructure.sqlalchemy.graph_persistence import publish_gate_status
from app.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from app.models import (
    AlgorithmConfig,
    GraphBuildRun,
    GraphVersion,
    JDDocument,
    PositionSkillRelationDraft,
    PublishedFactImport,
    ReviewTask,
    Skill,
    StandardPosition,
    User,
)


ROOT = Path(__file__).resolve().parents[1]


def _seconds(action):
    started = time.perf_counter()
    value = action()
    return round(time.perf_counter() - started, 4), value


def _require_disposable_postgres(database_url: str) -> None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("scale acceptance requires PostgreSQL")
    database = (parsed.database or "").casefold()
    if not any(marker in database for marker in ("test", "acceptance", "benchmark", "perf")):
        raise RuntimeError("database name must identify a disposable test/acceptance/benchmark DB")


def _batch_insert(connection, table, rows, batch_size=2_000):
    for offset in range(0, len(rows), batch_size):
        connection.execute(insert(table), rows[offset : offset + batch_size])


def run(database_url: str, jd_count: int, relation_count: int, positions: int) -> dict:
    _require_disposable_postgres(database_url)
    if jd_count < 100_000 or relation_count < 10_000 or positions < 2:
        raise RuntimeError("acceptance minima are 100000 JD, 10000 relations and 2 positions")
    environment = {**os.environ, "DATABASE_URL": database_url}
    migration_seconds, migration = _seconds(
        lambda: subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
    )
    if migration.returncode:
        raise RuntimeError(migration.stderr)

    engine = create_engine(database_url, isolation_level="READ COMMITTED", pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    position_ids = [f"PERF_{run_id}_POS_{index}" for index in range(positions)]
    skill_ids = [f"PERF_{run_id}_SKILL_{index:05d}" for index in range(relation_count)]
    metrics = {"modality_distribution": {"required": 1.0}}

    with engine.begin() as connection:
        connection.execute(
            insert(User),
            {"username": f"perf-{run_id}", "password_hash": "disabled", "role": "reviewer", "created_at": now},
        )
        user_id = connection.execute(
            select(User.id).where(User.username == f"perf-{run_id}")
        ).scalar_one()
        _batch_insert(
            connection,
            StandardPosition.__table__,
            [
                {
                    "position_id": value,
                    "name": f"Performance position {index}",
                    "category_code": "PERF",
                    "status": "active",
                    "created_at": now,
                }
                for index, value in enumerate(position_ids)
            ],
        )
        if connection.execute(select(text("count(*)")).select_from(AlgorithmConfig)).scalar_one() == 0:
            connection.execute(
                insert(AlgorithmConfig),
                {
                    "version": f"perf-{run_id}",
                    "payload": {},
                    "active": True,
                    "created_at": now,
                },
            )

    def insert_facts(start: int, end: int) -> None:
        documents = []
        imports = []
        for index in range(start, end):
            identifier = f"PERF_{run_id}_JD_{index:06d}"
            fact_id = f"PERF_{run_id}_FACT_{index:06d}"
            digest = f"{index:064x}"[-64:]
            documents.append(
                {
                    "document_id": identifier,
                    "raw_text": "authoritative performance fact",
                    "source_type": "performance_acceptance",
                    "source_name": f"kg-performance:{run_id}",
                    "source_credibility": 1.0,
                    "is_synthetic": False,
                    "source_system": "main-system",
                    "fact_authority": "authoritative",
                    "source_fact_id": fact_id,
                    "source_fact_version": "1",
                    "source_schema_version": "published-jd-fact.v2",
                    "content_hash": digest,
                    "created_at": now,
                }
            )
            imports.append(
                {
                    "source_system": "main-system",
                    "source_fact_id": fact_id,
                    "source_fact_version": "1",
                    "source_schema_version": "published-jd-fact.v2",
                    "content_hash": digest,
                    "document_id": identifier,
                    "published_at": now,
                    "payload": {"performance_acceptance": run_id},
                    "created_at": now,
                }
            )
        with engine.begin() as connection:
            _batch_insert(connection, JDDocument.__table__, documents)
            _batch_insert(connection, PublishedFactImport.__table__, imports)

    fact_10k_seconds, _ = _seconds(lambda: insert_facts(0, 10_000))
    fact_100k_seconds, _ = _seconds(lambda: insert_facts(10_000, jd_count))
    fact_100k_seconds = round(fact_10k_seconds + fact_100k_seconds, 4)

    build_use_case = BuildGraphUseCase(lambda: SqlAlchemyUnitOfWork(factory))

    def build(position_id: str):
        return build_use_case.execute(
            BuildGraphCommand(position_id, None, None, 0.0, 0, False)
        )

    concurrent_build_seconds, build_results = _seconds(
        lambda: list(ThreadPoolExecutor(max_workers=positions).map(build, position_ids))
    )
    relation_build_id = build_results[0].build_run_id

    def insert_relations(start: int, end: int) -> None:
        with engine.begin() as connection:
            _batch_insert(
                connection,
                Skill.__table__,
                [
                    {
                        "skill_id": skill_ids[index],
                        "canonical_name": f"Performance skill {index}",
                        # The scale fixture measures relation persistence, not taxonomy
                        # publication. Leaving this unset avoids claiming an authoritative
                        # taxonomy projection that the fixture does not create.
                        "taxonomy_version": None,
                        "status": "active",
                        "created_at": now,
                    }
                    for index in range(start, end)
                ],
            )
            _batch_insert(
                connection,
                PositionSkillRelationDraft.__table__,
                [
                    {
                        "build_run_id": relation_build_id,
                        "position_id": position_ids[0],
                        "skill_id": skill_ids[index],
                        "revision": 1,
                        "status": "approved",
                        "metrics": metrics,
                        "statistics": {},
                        "explanation": {},
                        "auto_weight": 0.5,
                        "final_weight": 0.5,
                        "auto_confidence": 0.9,
                        "final_confidence": 0.9,
                        "auto_importance_level": "important",
                        "final_importance_level": "important",
                        "created_at": now,
                    }
                    for index in range(start, end)
                ],
            )

    relation_1k_seconds, _ = _seconds(lambda: insert_relations(0, 1_000))
    relation_10k_seconds, _ = _seconds(lambda: insert_relations(1_000, relation_count))
    relation_10k_seconds = round(relation_1k_seconds + relation_10k_seconds, 4)
    def read_relations():
        with engine.connect() as connection:
            return connection.execute(
                select(
                    PositionSkillRelationDraft.id,
                    PositionSkillRelationDraft.skill_id,
                    PositionSkillRelationDraft.final_weight,
                ).where(PositionSkillRelationDraft.build_run_id == relation_build_id)
            ).all()

    relation_read_seconds, relation_rows = _seconds(read_relations)
    relation_id = relation_rows[0].id

    def edit(weight: float) -> int:
        with engine.begin() as connection:
            return connection.execute(
                update(PositionSkillRelationDraft)
                .where(
                    PositionSkillRelationDraft.id == relation_id,
                    PositionSkillRelationDraft.revision == 1,
                )
                .values(revision=2, final_weight=weight)
            ).rowcount

    with ThreadPoolExecutor(max_workers=8) as executor:
        edit_results = list(executor.map(edit, (0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57, 0.58)))

    with factory() as session:
        gate_run = GraphBuildRun(
            position_id=position_ids[1],
            status="succeeded",
            config_snapshot={"minimum_valid_samples": 0},
            summary={"included_samples": 0, "excluded_samples": 0},
        )
        session.add(gate_run)
        session.flush()
        session.add(
            PositionSkillRelationDraft(
                build_run_id=gate_run.id,
                position_id=position_ids[1],
                skill_id=skill_ids[0],
                revision=1,
                status="approved",
                metrics=metrics,
                statistics={},
                explanation={},
                auto_weight=0.5,
                final_weight=0.5,
                auto_confidence=0.9,
                final_confidence=0.9,
                auto_importance_level="important",
                final_importance_level="important",
            )
        )
        session.add(
            ReviewTask(
                object_type="position_skill_relation",
                object_id="performance-gate",
                build_run_id=gate_run.id,
                status="pending",
                payload={"performance_acceptance": run_id},
            )
        )
        session.commit()
        gate = publish_gate_status(session, gate_run)

    with factory() as session:
        version_position = position_ids[-1]
        # Reuse the real concurrent build as the rollback source so its persisted
        # BuildInputWatermark is part of the acceptance path.
        source_run = session.get(GraphBuildRun, build_results[-1].build_run_id)
        source_run.status = "published"
        current_run = GraphBuildRun(position_id=version_position, status="published", config_snapshot={}, summary={})
        session.add(current_run)
        session.flush()
        common = {
            "position_id": version_position,
            "algorithm_version": "perf-v1",
            "normalization_map_version": "perf-v1",
            "published_fact_versions": [],
            "skill_catalog_version": f"perf:{run_id}",
            "mapping_snapshot_version": "perf-v1",
            "normalization_algorithm_version": "perf-v1",
            "build_config_version": "perf-v1",
            "source_time_window": {},
            "published_by": user_id,
        }
        source_version = GraphVersion(
            **common,
            build_run_id=source_run.id,
            version_number=1,
            version_name="v1",
            snapshot={"position": {"position_id": version_position}, "marker": "old"},
            content_hash="1" * 64,
        )
        current_version = GraphVersion(
            **common,
            build_run_id=current_run.id,
            version_number=2,
            version_name="v2",
            snapshot={"position": {"position_id": version_position}, "marker": "current"},
            content_hash="2" * 64,
        )
        session.add_all([source_version, current_version])
        session.flush()
        source_version_id = source_version.id
        session.execute(
            update(StandardPosition)
            .where(StandardPosition.position_id == version_position)
            .values(current_version_id=current_version.id)
        )
        session.commit()

    rollback = RollbackGraphVersionUseCase(lambda: SqlAlchemyUnitOfWork(factory))
    start_reading = threading.Event()
    rollback_done = threading.Event()

    def rollback_writer():
        start_reading.wait()
        try:
            return rollback.execute(
                RollbackGraphCommand(version_position, source_version_id, user_id, f"perf-{run_id}", "performance rollback")
            )
        finally:
            rollback_done.set()

    def old_version_reader():
        observed = []
        start_reading.set()
        while not rollback_done.is_set() or len(observed) < 20:
            with factory() as session:
                row = session.get(GraphVersion, source_version_id)
                observed.append((row.content_hash, row.snapshot["marker"]))
        return observed

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(rollback_writer)
        reader = executor.submit(old_version_reader)
        rollback_result = writer.result()
        observations = reader.result()

    with engine.connect() as connection:
        stored_fact_count = connection.execute(
            select(text("count(*)")).select_from(JDDocument).where(
                JDDocument.source_name == f"kg-performance:{run_id}"
            )
        ).scalar_one()

    engine.dispose()
    assertions = {
        "jd_fact_count_exact": stored_fact_count == jd_count,
        "relation_count_exact": len(relation_rows) == relation_count,
        "concurrent_builds_succeeded": len(build_results) == positions,
        "single_relation_edit_winner": sum(edit_results) == 1,
        "open_review_blocks_publish": not gate["allowed"] and gate["open_review_task_count"] == 1,
        "rollback_created_new_version": rollback_result.rollback_from_version_id == source_version_id,
        "old_version_reads_stable": bool(observations) and set(observations) == {("1" * 64, "old")},
    }
    if not all(assertions.values()):
        raise RuntimeError(f"acceptance assertion failed: {assertions}")
    return {
        "contract": "kg-postgresql-scale-result.v1",
        "run_id": run_id,
        "database_engine": "postgresql",
        "isolation": "READ COMMITTED",
        "counts": {"jd_facts": jd_count, "relations": relation_count, "positions": positions},
        "seconds": {
            "migration": migration_seconds,
            "jd_fact_10000": fact_10k_seconds,
            "jd_fact_100000": fact_100k_seconds,
            "relation_1000": relation_1k_seconds,
            "relation_10000": relation_10k_seconds,
            "relation_10000_read": relation_read_seconds,
            "concurrent_position_builds": concurrent_build_seconds,
        },
        "assertions": assertions,
        "failure_boundaries": {
            "minimum_jd_facts": 100_000,
            "minimum_relations": 10_000,
            "relation_edit_expected_winners": 1,
            "publish_with_open_review": "rejected",
            "old_version_mutation": "forbidden",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("KG_PERF_POSTGRES_URL"))
    parser.add_argument("--jd-facts", type=int, default=100_000)
    parser.add_argument("--relations", type=int, default=10_000)
    parser.add_argument("--positions", type=int, default=4)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or KG_PERF_POSTGRES_URL is required")
    result = run(args.database_url, args.jd_facts, args.relations, args.positions)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
