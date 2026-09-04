"""Read-only calibration metrics for the review governance policy.

The report is intentionally diagnostic: it never changes review tasks,
drafts, or published snapshots. Use it to decide which review reasons can be
moved to policy auto-accept, sampling, or alert-only in a future policy
version.
"""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import func, select

from app.config import Settings
from app.database import create_database
from app.domain.review_tasks import (
    requires_human_review,
    review_task_reasons,
)
from app.models import (
    PositionRequirementAggregateDraft,
    PositionTaskAggregateDraft,
    ReviewTask,
    ReviewTaskEvent,
)


def main() -> int:
    settings = Settings.from_env()
    database = create_database(settings)
    with database.session_factory() as session:
        tasks = session.scalars(select(ReviewTask)).all()
        by_status = Counter(task.status for task in tasks)
        by_object_type = Counter(task.object_type for task in tasks)
        by_reason: Counter[str] = Counter()
        for task in tasks:
            for reason in review_task_reasons(task.payload):
                by_reason[reason] += 1
        auto_acceptable = [
            task for task in tasks if not requires_human_review(task.payload)
        ]
        events = session.scalars(select(ReviewTaskEvent)).all()
        by_action = Counter(event.action for event in events)
        requirement_ids = {
            task.object_id
            for task in tasks
            if task.object_type == "position_requirement"
            and task.object_id.isdigit()
        }
        task_ids = {
            task.object_id
            for task in tasks
            if task.object_type == "position_task"
            and task.object_id.isdigit()
        }
        singleton_requirements = 0
        if requirement_ids:
            singleton_requirements = session.scalar(
                select(func.count())
                .select_from(PositionRequirementAggregateDraft)
                .where(
                    PositionRequirementAggregateDraft.id.in_(requirement_ids),
                    PositionRequirementAggregateDraft.payload[
                        "support_document_count"
                    ].as_integer()
                    == 1,
                )
            ) or 0
        singleton_tasks = 0
        if task_ids:
            singleton_tasks = session.scalar(
                select(func.count())
                .select_from(PositionTaskAggregateDraft)
                .where(
                    PositionTaskAggregateDraft.id.in_(task_ids),
                    PositionTaskAggregateDraft.payload[
                        "support_document_count"
                    ].as_integer()
                    == 1,
                )
            ) or 0
        report = {
            "task_count": len(tasks),
            "by_status": dict(sorted(by_status.items())),
            "by_object_type": dict(sorted(by_object_type.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "auto_acceptable_count": len(auto_acceptable),
            "requires_human_count": len(tasks) - len(auto_acceptable),
            "event_action_counts": dict(sorted(by_action.items())),
            "singleton_requirement_count": singleton_requirements,
            "singleton_task_count": singleton_tasks,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    database.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
