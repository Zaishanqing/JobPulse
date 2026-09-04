"""Backfill JD drafts for succeeded ExtractionTasks that have none.

One-off maintenance helper: tasks that succeeded before automatic draft
import was enabled never produced a JobDescription / JDParseResult / review
task, so they never appear in the JD data center.  Run inside the
main-backend container:

    python scripts/backfill_extraction_drafts.py
"""

from __future__ import annotations

from app.bootstrap.container import _build_runtime
from app.core.config import settings


def main() -> int:
    runtime = _build_runtime(settings)
    try:
        use_cases = runtime.container.extraction_tasks
        imported = 0
        already_had_draft = 0
        page = 1
        while True:
            page_result = use_cases.list_extraction_tasks(
                status="succeeded", page=page, page_size=100
            )
            if not page_result.items:
                break
            for task in page_result.items:
                try:
                    use_cases.get_imported_draft(task.id)
                    already_had_draft += 1
                    continue
                except Exception:
                    pass
                try:
                    use_cases.import_extraction_bundle(task.id)
                    imported += 1
                except Exception as exc:
                    print(
                        f"deferred task={task.id} "
                        f"error={type(exc).__name__}: {exc}"
                    )
            page += 1
        print(f"imported={imported} already_had_draft={already_had_draft}")
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
