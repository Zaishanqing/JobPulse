from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.workers.outbox import build_worker_runtime  # noqa: E402


def main() -> int:
    settings = Settings()
    if not settings.KNOWLEDGE_GRAPH_ENABLED:
        raise ValueError("KNOWLEDGE_GRAPH_ENABLED must be true")
    runtime = build_worker_runtime(settings)
    delivered = 0
    failed = 0
    try:
        while True:
            result = runtime.dispatcher.dispatch_one(
                "phase2-local-dispatch", datetime.now(timezone.utc)
            )
            if result is None:
                break
            if result.delivered:
                delivered += 1
            else:
                failed += 1
        print(json.dumps({"delivered": delivered, "failed": failed}, indent=2))
        return 0 if failed == 0 else 2
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
