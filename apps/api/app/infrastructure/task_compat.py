from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.task_record import TaskRecord
from app.models.user import User


def create_task_compat(
    session: Session,
    actor: User,
    task_type: str,
    *,
    input_payload: dict | None = None,
    task_id: str | None = None,
    commit: bool = True,
) -> TaskRecord:
    """Seed a pending task for historical Python callers and test fixtures."""

    now = datetime.now(timezone.utc)
    row = TaskRecord(
        id=task_id or f"{task_type}_{uuid4()}",
        task_type=task_type,
        status="pending",
        progress=0.0,
        input_payload=input_payload or {},
        result_payload={},
        created_by=actor.id,
        log_entries=[{"status": "pending", "at": now.isoformat()}],
    )
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    else:
        session.flush()
    return row
