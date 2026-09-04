"""A-DATA-01 / A14: 将离线冻结清单导入系统数据库。

读取 A-DATA-01_frozen_manifest.json，在 knowledge-graph 数据库中创建：
  - GraphBuildRun 记录（每 release 一个）
  - GraphVersion 正式记录（含 catalog/algorithm/config 身份）
  - release_import_batches 记录（Release 身份登记）
  - 更新 standard_positions.current_version_id
  - blocked pair 阻断表记录（graph_version_blocked_pairs）

不创建 ReleaseImportItem / PublishedFactReleaseLink（离线快照
缺少逐条 jd_documents 和 published_fact_imports 外键依赖）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

KG_SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KG_SERVICE))

from app.config import Settings
from app.database import create_database
from app.models import (
    GraphBuildRun,
    GraphVersion,
    ReleaseImportBatch,
    StandardPosition,
)
from app.infrastructure.readiness import EXPECTED_MIGRATION_REVISION
from sqlalchemy import text, select

FROZEN_MANIFEST_PATH = (
    KG_SERVICE / "evaluation" / "reports" / "real-graph-baseline"
    / "A-DATA-01_frozen_manifest.json"
)

PRODUCER = "A-DATA-01-freeze"
RELEASE_SCHEMA_VERSION = "release-manifest-v1"
MANIFEST_VERSION = "A-DATA-01-v1"

# 离线冻结清单的 position_id 与目录 standard_positions.position_id 的映射。
# 离线原型用 POS_JAVA_BACKEND / POS_LLM_ALGO，目录用 BACKEND_ENGINEER / LLM_ALGORITHM_ENGINEER。
CATALOG_POSITION_ID_MAP = {
    "POS_JAVA_BACKEND": "BACKEND_ENGINEER",
    "POS_LLM_ALGO": "LLM_ALGORITHM_ENGINEER",
}


def _catalog_position_id(offline_pid: str) -> str:
    return CATALOG_POSITION_ID_MAP.get(offline_pid, offline_pid)


def _check_migration(session) -> None:
    row = session.execute(
        text("SELECT version_num FROM alembic_version")
    ).fetchone()
    if row is None:
        raise RuntimeError("alembic_version 表为空，请先运行 alembic upgrade head")
    current = row[0]
    if current != EXPECTED_MIGRATION_REVISION:
        raise RuntimeError(
            f"迁移版本不匹配: 数据库={current}, 期望={EXPECTED_MIGRATION_REVISION}。"
            f"请先运行 alembic upgrade head"
        )
    print(f"迁移版本检查通过: {current}")


def _ensure_positions(session, position_ids: set[str]) -> dict[str, StandardPosition]:
    result: dict[str, StandardPosition] = {}
    for pid in sorted(position_ids):
        pos = session.scalar(
            select(StandardPosition).where(StandardPosition.position_id == pid)
        )
        if pos is None:
            raise RuntimeError(
                f"standard_positions 中不存在 {pid}，"
                f"请先导入岗位目录数据或运行 seed 脚本"
            )
        result[pid] = pos
    print(f"岗位检查通过: {len(result)} 个岗位存在")
    return result


def _build_release_manifest(
    release: dict, position_name: str
) -> tuple[dict, str]:
    """将冻结清单中的 release 记录转换为 ReleaseManifestV1 形状并计算 hash。"""
    manifest = {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "release_id": release["release_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "producer": {
            "name": PRODUCER,
            "version": MANIFEST_VERSION,
        },
        "mode": "full",
        "parent_release_id": None,
        "observation_window": {
            "start": release["time_window"]["start"],
            "end": release["time_window"]["end"],
        },
        "artifacts": [
            {
                "artifact_type": "graph_snapshot",
                "position_id": release["position_id"],
                "position_name": position_name,
                "graph_version_id": release["graph_version_id"],
                "version_number": release["graph_version_number"],
                "catalog_version": release.get("catalog_version_id", "CAT-v1-real-jd"),
                "detector_version": release.get("detector_version", ""),
                "config_version": release.get("config_version", ""),
                "sample_count": release.get("sample_count", 0),
                "skill_count": release.get("skill_count", 0),
                "responsibility_count": release.get("responsibility_count", 0),
                "source_platforms": release.get("source_platforms", []),
                "source_count": release.get("source_count", 0),
                "enterprise_count": release.get("enterprise_count", 0),
                "coverage_risks": release.get("coverage_risks", []),
            }
        ],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    return manifest, manifest_hash


def _create_blocked_pairs_table(session) -> None:
    """创建 blocked pair 阻断表（如果不存在）。"""
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "postgresql":
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS graph_version_blocked_pairs (
                id SERIAL PRIMARY KEY,
                pair_id VARCHAR(256) UNIQUE NOT NULL,
                position_id VARCHAR(100) NOT NULL,
                from_graph_version_id INTEGER NOT NULL
                    REFERENCES graph_versions(id),
                to_graph_version_id INTEGER NOT NULL
                    REFERENCES graph_versions(id),
                blocked_reasons JSONB NOT NULL DEFAULT '[]',
                limitations JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_blocked_pair_from FOREIGN KEY (from_graph_version_id)
                    REFERENCES graph_versions(id),
                CONSTRAINT fk_blocked_pair_to FOREIGN KEY (to_graph_version_id)
                    REFERENCES graph_versions(id)
            )
        """))
    else:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS graph_version_blocked_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair_id VARCHAR(256) UNIQUE NOT NULL,
                position_id VARCHAR(100) NOT NULL,
                from_graph_version_id INTEGER NOT NULL
                    REFERENCES graph_versions(id),
                to_graph_version_id INTEGER NOT NULL
                    REFERENCES graph_versions(id),
                blocked_reasons JSON NOT NULL DEFAULT '[]',
                limitations JSON NOT NULL DEFAULT '[]',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
    session.commit()
    print("blocked pair 阻断表已就绪")


def _create_trigger_blocked_pairs_immutable(session) -> None:
    """为 blocked pair 表添加不可变触发器。"""
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "postgresql":
        session.execute(text("""
            CREATE OR REPLACE FUNCTION raise_immutable_table_error()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'graph_version_blocked_pairs is immutable';
            END;
            $$ LANGUAGE plpgsql;
        """))
        session.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_blocked_pairs_no_update'
                ) THEN
                    CREATE TRIGGER trg_blocked_pairs_no_update
                        BEFORE UPDATE OR DELETE ON graph_version_blocked_pairs
                        FOR EACH ROW EXECUTE FUNCTION
                        raise_immutable_table_error();
                END IF;
            END $$;
        """))
    # SQLite 不支持存储过程触发器，依赖应用层约束
    session.commit()


def import_frozen_releases(
    manifest_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    manifest_file = manifest_path or FROZEN_MANIFEST_PATH
    if not manifest_file.exists():
        raise FileNotFoundError(f"冻结清单不存在: {manifest_file}")

    frozen = json.loads(manifest_file.read_text(encoding="utf-8"))
    summary = frozen["summary"]
    releases = frozen["releases"]
    pairs = frozen.get("version_pairs", [])

    print(f"读取冻结清单: {manifest_file}")
    print(f"  Manifest 版本: {summary['manifest_version']}")
    print(f"  Release 数: {len(releases)}")
    print(f"  版本对数: {len(pairs)}")
    print(f"  Detector: {summary['detector_version']}")
    print(f"  Config: {summary['config_version']}")
    print(f"  Catalog: {summary['catalog_version']}")
    print()

    if dry_run:
        print("=== DRY RUN: 不会写入数据库 ===\n")

    settings = Settings.from_env()
    if settings.environment.casefold() == "production":
        raise RuntimeError("禁止在生产环境执行此脚本")

    database = create_database(settings)

    try:
        with database.session_factory() as session:
            _check_migration(session)

            position_ids = {_catalog_position_id(r["position_id"]) for r in releases}
            positions = _ensure_positions(session, position_ids)

            _create_blocked_pairs_table(session)
            _create_trigger_blocked_pairs_immutable(session)

            # —— 按 position 收集版本号，用于命名 ——
            pos_version_counters: dict[str, int] = {}
            for pid in sorted(position_ids):
                existing = session.execute(
                    text(
                        "SELECT MAX(version_number) FROM graph_versions "
                        "WHERE position_id = :pid"
                    ),
                    {"pid": pid},
                ).scalar()
                existing = existing or 0
                pos_version_counters[pid] = existing
                print(f"{pid}: 当前最大 version_number = {existing}")

            # —— 1. 为每个 release 创建 GraphBuildRun + GraphVersion ——
            created_versions: dict[str, int] = {}  # graph_version_id -> db id
            created_build_runs: list[int] = []
            releases_to_import: list[dict] = []

            for rel in releases:
                existing = session.scalar(
                    select(ReleaseImportBatch).where(
                        ReleaseImportBatch.release_id == rel["release_id"]
                    )
                )
                if existing is not None:
                    print(f"  跳过已存在的 Release: {rel['release_id']}")
                    continue

                releases_to_import.append(rel)
                offline_pid = rel["position_id"]
                pid = _catalog_position_id(offline_pid)
                pos_version_counters[pid] += 1
                vnum = pos_version_counters[pid]
                vname = f"v{vnum}-{rel['time_window']['start']}"

                gv_id_str = rel["graph_version_id"]

                if dry_run:
                    print(
                        f"  [DRY RUN] 将创建 {pid} GraphBuildRun + "
                        f"GraphVersion({vname}, number={vnum})"
                    )
                    created_versions[gv_id_str] = vnum  # 用 version number 占位
                    continue

                # 创建 GraphBuildRun
                build_run = GraphBuildRun(
                    position_id=pid,
                    release_id=rel["release_id"],
                    status="published",
                    window_start=rel["time_window"]["start"],
                    window_end=rel["time_window"]["end"],
                    config_snapshot={
                        "detector_version": rel.get("detector_version", ""),
                        "config_version": rel.get("config_version", ""),
                        "catalog_version_id": rel.get("catalog_version_id", ""),
                        "manifest_version": MANIFEST_VERSION,
                    },
                    summary={
                        "sample_count": rel.get("sample_count", 0),
                        "skill_count": rel.get("skill_count", 0),
                        "source_count": rel.get("source_count", 0),
                        "enterprise_count": rel.get("enterprise_count", 0),
                    },
                )
                session.add(build_run)
                session.flush()
                created_build_runs.append(build_run.id)

                # 创建 GraphVersion
                gv = GraphVersion(
                    position_id=pid,
                    build_run_id=build_run.id,
                    release_id=rel["release_id"],
                    version_number=vnum,
                    version_name=vname,
                    snapshot={
                        "position_id": pid,
                        "position_name": rel.get("position_name", ""),
                        "time_window": rel["time_window"],
                        "sample_count": rel.get("sample_count", 0),
                        "skill_count": rel.get("skill_count", 0),
                        "responsibility_count": rel.get("responsibility_count", 0),
                        "source_platforms": rel.get("source_platforms", []),
                        "source_record_ids_sample": rel.get("source_record_ids_sample", []),
                        "crawl_time_range": rel.get("crawl_time_range", {}),
                        "coverage_risks": rel.get("coverage_risks", []),
                        "evidence_available": rel.get("evidence_available", False),
                    },
                    source_version=rel.get("config_version", "evolution-defaults-v1"),
                    algorithm_version=rel.get("detector_version", "position-evolution-events-v1"),
                    normalization_map_version=(
                        summary.get("config_version", "evolution-defaults-v1")
                    ),
                    skill_catalog_version=rel.get("catalog_version_id", "CAT-v1-real-jd"),
                    mapping_snapshot_version=rel.get("catalog_version_id", "CAT-v1-real-jd"),
                    normalization_algorithm_version=(
                        rel.get("detector_version", "position-evolution-events-v1")
                    ),
                    build_config_version=summary.get("config_version", "evolution-defaults-v1"),
                    source_time_window={
                        "start": rel["time_window"]["start"],
                        "end": rel["time_window"]["end"],
                        "sample_count": rel.get("sample_count", 0),
                    },
                    published_fact_versions=[],
                )
                session.add(gv)
                session.flush()
                created_versions[gv_id_str] = gv.id

                # 更新 StandardPosition.current_version_id
                pos = positions[pid]
                pos.current_version_id = gv.id

                # 更新 build_run 的 base_version_id（首个版本为 None，后续指向前一版本）
                # 这里用同一 position 下已创建的版本数来判断
                pos_versions = [
                    v for k, v in created_versions.items()
                    if k.startswith(f"GV-{offline_pid}-")
                ]
                if len(pos_versions) > 1:
                    build_run.base_version_id = pos_versions[-2]

                print(
                    f"  创建: {pid} {vname} "
                    f"(build_run={build_run.id}, gv={gv.id}, "
                    f"samples={rel.get('sample_count', 0)})"
                )

            # —— 2. 创建 release_import_batches ——
            for rel in releases_to_import:
                position_name = rel.get("position_name", rel["position_id"])
                manifest, manifest_hash = _build_release_manifest(rel, position_name)

                if dry_run:
                    print(
                        f"  [DRY RUN] 将创建 ReleaseImportBatch "
                        f"{rel['release_id']} (hash={manifest_hash[:12]}...)"
                    )
                    continue

                batch = ReleaseImportBatch(
                    release_id=rel["release_id"],
                    manifest_hash=manifest_hash,
                    manifest=manifest,
                    record_count=rel.get("sample_count", 0),
                )
                session.add(batch)
                print(f"  创建 ReleaseImportBatch: {rel['release_id']}")

            # —— 3. 写入 blocked pair 阻断记录 ——
            blocked_count = 0
            for pair in pairs:
                if not pair.get("is_blocked") and not pair.get("limitations"):
                    continue

                from_gv_id = created_versions.get(pair["from_graph_version_id"])
                to_gv_id = created_versions.get(pair["to_graph_version_id"])

                if from_gv_id is None or to_gv_id is None:
                    print(
                        f"  警告: 跳过 {pair['pair_id']} — "
                        f"from={pair['from_graph_version_id']}->{from_gv_id}, "
                        f"to={pair['to_graph_version_id']}->{to_gv_id}"
                    )
                    continue

                if dry_run:
                    print(
                        f"  [DRY RUN] 将写入 blocked pair: {pair['pair_id']} "
                        f"({pair['comparability_status']})"
                    )
                    blocked_count += 1
                    continue

                session.execute(
                    text(
                        "INSERT OR IGNORE INTO graph_version_blocked_pairs "
                        "(pair_id, position_id, from_graph_version_id, "
                        "to_graph_version_id, blocked_reasons, limitations) "
                        "VALUES (:pair_id, :pid, :from_gv, :to_gv, :reasons, :limitations)"
                        if session.bind.dialect.name != "postgresql"
                        else "INSERT INTO graph_version_blocked_pairs "
                        "(pair_id, position_id, from_graph_version_id, "
                        "to_graph_version_id, blocked_reasons, limitations) "
                        "VALUES (:pair_id, :pid, :from_gv, :to_gv, "
                        "CAST(:reasons AS jsonb), CAST(:limitations AS jsonb)) "
                        "ON CONFLICT (pair_id) DO NOTHING"
                    ),
                    {
                        "pair_id": pair["pair_id"],
                        "pid": _catalog_position_id(pair["position_id"]),
                        "from_gv": from_gv_id,
                        "to_gv": to_gv_id,
                        "reasons": json.dumps(pair.get("blocked_reasons", [])),
                        "limitations": json.dumps(pair.get("limitations", [])),
                    },
                )
                blocked_count += 1
                print(
                    f"  写入 blocked pair: {pair['pair_id']} "
                    f"({pair['comparability_status']})"
                )

            if dry_run:
                print("\n[DRY RUN] 将创建:")
                print(f"  - {len(releases_to_import)} 个 GraphBuildRun")
                print(f"  - {len(releases_to_import)} 个 GraphVersion")
                print(f"  - {len(releases_to_import)} 个 ReleaseImportBatch")
                print(f"  - {blocked_count} 个 blocked pair 记录")
                print(f"  - 更新 {len(positions)} 个 StandardPosition.current_version_id")
                session.rollback()
            else:
                session.commit()
                print("\n数据库提交成功:")
                print(f"  - {len(created_build_runs)} 个 GraphBuildRun")
                print(f"  - {len(created_versions)} 个 GraphVersion")
                print(f"  - {len(releases_to_import)} 个 ReleaseImportBatch")
                print(f"  - {blocked_count} 个 blocked pair 记录")
                print(f"  - 更新了 {len(positions)} 个 StandardPosition.current_version_id")

            return {
                "graph_versions_created": len(created_versions),
                "build_runs_created": len(created_build_runs),
                "release_batches_created": len(releases_to_import),
                "blocked_pairs_written": blocked_count,
            }

    finally:
        database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="A-DATA-01/A14: 将离线冻结清单导入系统数据库"
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"冻结清单路径（默认: {FROZEN_MANIFEST_PATH}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅校验和预览，不写入数据库",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest) if args.manifest else None
    result = import_frozen_releases(
        manifest_path=manifest_path,
        dry_run=args.dry_run,
    )
    print(f"\n完成: {result}")


if __name__ == "__main__":
    main()
