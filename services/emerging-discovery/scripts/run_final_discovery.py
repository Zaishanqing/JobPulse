from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local evidence-driven discovery acceptance fixture."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=SERVICE_ROOT / "examples" / "final_discovery_fixture.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SERVICE_ROOT / "artifacts" / "final-discovery-result.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    from fastapi.testclient import TestClient
    from alembic import command
    from alembic.config import Config

    from app.bootstrap.application import create_app
    from app.bootstrap.settings import Settings
    from app.infrastructure.database import create_database

    runtime = Settings(
        ENVIRONMENT="test",
        DATABASE_URL=os.environ.get("DATABASE_URL", Settings().DATABASE_URL),
    )
    alembic_config = Config(str(SERVICE_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
    command.upgrade(alembic_config, "head")
    database = create_database(runtime)
    application = create_app(runtime, database)
    headers = {"Authorization": f"Bearer {runtime.INTERNAL_SERVICE_TOKEN}"}
    created_runs = []
    with TestClient(application) as client:
        for payload in fixture["runs"]:
            response = client.post(
                "/api/v1/discovery-runs", json=payload, headers=headers
            )
            response.raise_for_status()
            created_runs.append(response.json()["data"])
        final_run = created_runs[-1]
        query = client.get(
            f"/api/v1/discovery-runs/{final_run['run_id']}", headers=headers
        )
        query.raise_for_status()
        comparison_payload = deepcopy(fixture["runs"][-1])
        comparison_payload["request_id"] = "final-demo-comparison"
        comparison_payload["comparison_algorithms"] = fixture[
            "comparison_algorithms"
        ]
        comparison = client.post(
            "/api/v1/discovery-comparisons",
            json=comparison_payload,
            headers=headers,
        )
        comparison.raise_for_status()
        result = {
            "fixture": str(args.fixture),
            "created_runs": created_runs,
            "final_query": query.json()["data"],
            "create_query_consistent": query.json()["data"] == final_run,
            "algorithm_comparison": comparison.json()["data"],
        }
    database.engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "run_count": len(created_runs),
                "final_cluster_count": len(result["final_query"]["clusters"]),
                "lineage_count": len(result["final_query"]["lineages"]),
                "create_query_consistent": result["create_query_consistent"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
