"""Verify pg_dump/pg_restore fidelity using an explicitly disposable restore DB."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from app.infrastructure.readiness import EXPECTED_MIGRATION_REVISION
from app.models import GraphVersion


def _validate(source_url: str, restore_url: str) -> None:
    source = make_url(source_url)
    restore = make_url(restore_url)
    if source.get_backend_name() != "postgresql" or restore.get_backend_name() != "postgresql":
        raise RuntimeError("backup/restore verification requires PostgreSQL URLs")
    if source.render_as_string(hide_password=False) == restore.render_as_string(hide_password=False):
        raise RuntimeError("source and restore databases must be different")
    target_name = (restore.database or "").casefold()
    if not any(marker in target_name for marker in ("restore", "test", "acceptance")):
        raise RuntimeError("restore database name must contain restore/test/acceptance")
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        raise RuntimeError("pg_dump and pg_restore must be installed and on PATH")


def _snapshot(database_url: str) -> dict:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            schema = inspect(connection)
            tables = sorted(schema.get_table_names())
            preparer = connection.dialect.identifier_preparer
            counts = {
                table: connection.execute(
                    text(f"SELECT COUNT(*) FROM {preparer.quote(table)}")
                ).scalar_one()
                for table in tables
                if table != "alembic_version"
            }
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            version_hashes = connection.execute(
                select(GraphVersion.id, GraphVersion.content_hash).order_by(GraphVersion.id)
            ).all()
            return {
                "revision": revision,
                "tables": tables,
                "counts": counts,
                "graph_version_hashes": [list(item) for item in version_hashes],
            }
    finally:
        engine.dispose()


def verify(source_url: str, restore_url: str) -> dict:
    _validate(source_url, restore_url)
    before = _snapshot(source_url)
    if before["revision"] != EXPECTED_MIGRATION_REVISION:
        raise RuntimeError(
            f"source migration is {before['revision']}; expected {EXPECTED_MIGRATION_REVISION}"
        )
    with tempfile.TemporaryDirectory(prefix="kg-pg-backup-") as directory:
        dump_path = Path(directory) / "knowledge-graph.dump"
        started = time.perf_counter()
        dump = subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                f"--file={dump_path}",
                f"--dbname={source_url}",
            ],
            capture_output=True,
            text=True,
        )
        dump_seconds = round(time.perf_counter() - started, 4)
        if dump.returncode:
            raise RuntimeError(dump.stderr)
        started = time.perf_counter()
        restore = subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                f"--dbname={restore_url}",
                str(dump_path),
            ],
            capture_output=True,
            text=True,
        )
        restore_seconds = round(time.perf_counter() - started, 4)
        if restore.returncode:
            raise RuntimeError(restore.stderr)
        dump_bytes = dump_path.stat().st_size
    after = _snapshot(restore_url)
    exact = before == after
    if not exact:
        raise RuntimeError("restored schema/count/version-hash snapshot differs from source")
    return {
        "contract": "kg-postgresql-backup-restore-result.v1",
        "migration_revision": before["revision"],
        "table_count": len(before["tables"]),
        "row_count": sum(before["counts"].values()),
        "graph_version_count": len(before["graph_version_hashes"]),
        "dump_bytes": dump_bytes,
        "dump_seconds": dump_seconds,
        "restore_seconds": restore_seconds,
        "schema_counts_and_version_hashes_equal": exact,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--restore-url", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = verify(args.source_url, args.restore_url)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
