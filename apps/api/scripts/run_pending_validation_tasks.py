"""Drain the current finite set of pending Data Validation tasks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.workers.validation_tasks import (  # noqa: E402
    ValidationWorkerResult,
    build_worker_runtime,
)


def run_pending(*, settings: Settings, max_tasks: int) -> dict[str, int]:
    if settings.DATA_VALIDATION_MODE != "enforce":
        raise ValueError("Data Validation must be in enforce mode")
    if max_tasks < 1:
        raise ValueError("max_tasks must be positive")
    runtime = build_worker_runtime(settings)
    succeeded = failed = conflicts = 0
    try:
        for _ in range(max_tasks):
            result = runtime.worker.run_once()
            if result is ValidationWorkerResult.NO_WORK:
                return {
                    "succeeded": succeeded,
                    "failed": failed,
                    "retryable_conflicts": conflicts,
                }
            if result is ValidationWorkerResult.SUCCEEDED:
                succeeded += 1
            elif result is ValidationWorkerResult.FAILED:
                failed += 1
            elif result is ValidationWorkerResult.RETRYABLE_CONFLICT:
                conflicts += 1
            else:
                raise RuntimeError(f"unexpected validation worker result: {result}")
        raise RuntimeError("validation task drain exceeded max_tasks")
    finally:
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tasks", type=int, required=True)
    args = parser.parse_args()
    result = run_pending(settings=Settings(), max_tasks=args.max_tasks)
    print(json.dumps(result, ensure_ascii=False))
    if result["failed"]:
        raise RuntimeError("one or more Data Validation tasks failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
