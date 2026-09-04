from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bootstrap.application import create_app  # noqa: E402
from app.bootstrap.settings import Settings  # noqa: E402
from app.infrastructure.models import (  # noqa: E402
    AlgorithmConfigSnapshot,
    DiscoveryRun,
    InputSnapshot,
)


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * ratio))]


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL competition-scale benchmark")
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--reads", type=int, default=40)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "postgresql-benchmark.json")
    args = parser.parse_args()
    if args.rows < 10_000 or args.concurrency < 1 or args.reads < args.concurrency:
        parser.error("rows must be >= 10000 and reads must be >= concurrency")

    settings = Settings()
    database_url = str(settings.DATABASE_URL)
    engine = create_engine(database_url, pool_pre_ping=True)
    alembic = Config(str(ROOT / "alembic.ini"))
    alembic.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic, "head")

    run_id = str(uuid4())
    now = datetime.now(timezone.utc)
    snapshots = [
        {
            "id": str(uuid4()),
            "run_id": run_id,
            "source_jd_id": f"benchmark-jd-{index:05d}",
            "window_id": f"benchmark-window-{index % 3 + 1}",
            "input_version": "benchmark-v1",
            "schema_version": "input-snapshot.v1",
            "payload": {"title": "benchmark JD", "source_name": f"source-{index % 8}"},
            "created_at": now,
        }
        for index in range(args.rows)
    ]
    write_started = perf_counter()
    with engine.begin() as connection:
        connection.execute(
            insert(DiscoveryRun),
            [{
                "id": run_id,
                "request_id": f"benchmark-{run_id}",
                "status": "succeeded",
                "algorithm_version": "benchmark-write-v1",
                "formula_version": "emergence-index-v4-seven-dimensions",
                "time_window_start": date(2026, 1, 1),
                "time_window_end": date(2026, 3, 31),
                "created_at": now,
                "completed_at": now,
            }],
        )
        connection.execute(
            insert(AlgorithmConfigSnapshot),
            [{
                "id": str(uuid4()),
                "run_id": run_id,
                "algorithm_version": "benchmark-write-v1",
                "formula_version": "emergence-index-v4-seven-dimensions",
                "config": {"run_context": {"benchmark": True}, "input_quality_report": {}},
                "created_at": now,
            }],
        )
        for offset in range(0, len(snapshots), 1_000):
            connection.execute(insert(InputSnapshot), snapshots[offset : offset + 1_000])
    write_seconds = perf_counter() - write_started

    app = create_app(settings)
    headers = {"Authorization": f"Bearer {settings.INTERNAL_SERVICE_TOKEN}"}

    def read_once(_: int) -> tuple[int, float]:
        started = perf_counter()
        with TestClient(app) as client:
            response = client.get(f"/api/v1/discovery-runs/{run_id}", headers=headers)
        return response.status_code, (perf_counter() - started) * 1_000

    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            reads = list(executor.map(read_once, range(args.reads)))
        latencies = [latency for status, latency in reads if status == 200]
        result = {
            "scope": "competition-demo-only; not a production capacity claim",
            "database": database_url.split("@")[-1],
            "rows_written": args.rows,
            "write_seconds": round(write_seconds, 4),
            "write_rows_per_second": round(args.rows / write_seconds, 2),
            "concurrent_read_workers": args.concurrency,
            "read_requests": args.reads,
            "read_successes": len(latencies),
            "read_latency_ms_p50": round(median(latencies), 3),
            "read_latency_ms_p95": round(percentile(latencies, 0.95), 3),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL jobgraph.allow_discovery_cleanup = 'on'"))
            connection.execute(text("DELETE FROM algorithm_config_snapshots WHERE run_id=:run_id"), {"run_id": run_id})
            connection.execute(text("DELETE FROM input_snapshots WHERE run_id=:run_id"), {"run_id": run_id})
            connection.execute(text("DELETE FROM discovery_runs WHERE id=:run_id"), {"run_id": run_id})
        engine.dispose()


if __name__ == "__main__":
    os.environ.setdefault("ENVIRONMENT", "test")
    main()
