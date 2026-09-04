from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401
from app.bootstrap.container import _build_application_container  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.models.extraction_task import ExtractionTask  # noqa: E402
from app.workers.validation_tasks import (  # noqa: E402
    ValidationWorkerResult,
    build_worker_runtime,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the pending JD Extraction and Data Validation work for one offline bundle."
    )
    parser.add_argument("--bundle-id", required=True)
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.DATA_VALIDATION_MODE == "off":
        raise ValueError("DATA_VALIDATION_MODE must be observe or enforce")
    database = create_database(settings.DATABASE_URL)
    validation_runtime = None
    try:
        container = _build_application_container(settings, database)
        with database.session_factory() as session:
            task_ids = [
                row[0]
                for row in session.query(ExtractionTask.id)
                .join(
                    app.models.OfflineImportItem,
                    app.models.OfflineImportItem.extraction_task_id
                    == ExtractionTask.id,
                )
                .join(
                    app.models.OfflineImportBatch,
                    app.models.OfflineImportBatch.id
                    == app.models.OfflineImportItem.batch_id,
                )
                .filter(
                    app.models.OfflineImportBatch.bundle_id == args.bundle_id,
                    ExtractionTask.status == "pending",
                )
                .order_by(ExtractionTask.created_at.asc(), ExtractionTask.id.asc())
                .all()
            ]
        for task_id in task_ids:
            result = container.extraction_tasks.run_extraction_task(task_id)
            if result.status != "succeeded":
                raise RuntimeError(
                    f"Extraction task did not succeed: {task_id} ({result.status})"
                )

        validation_runtime = build_worker_runtime(settings)
        validation_counts = {"succeeded": 0, "failed": 0}
        while True:
            result = validation_runtime.worker.run_once()
            if result is ValidationWorkerResult.NO_WORK:
                break
            if result is ValidationWorkerResult.RETRYABLE_CONFLICT:
                continue
            if result is ValidationWorkerResult.SUCCEEDED:
                validation_counts["succeeded"] += 1
            elif result is ValidationWorkerResult.FAILED:
                validation_counts["failed"] += 1
            else:
                raise RuntimeError(f"Unexpected validation worker result: {result.value}")

        print(
            json.dumps(
                {
                    "bundle_id": args.bundle_id,
                    "extraction_succeeded": len(task_ids),
                    "validation_succeeded": validation_counts["succeeded"],
                    "validation_failed": validation_counts["failed"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if validation_runtime is not None:
            validation_runtime.close()
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
