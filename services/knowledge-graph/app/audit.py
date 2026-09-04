from sqlalchemy.orm import Session

from app.models import AuditLog


class AuditService:
    """Single entry point for trustworthy, JWT-attributed audit records."""

    @staticmethod
    def record(
        db: Session,
        *,
        actor_id: int,
        action: str,
        object_type: str,
        object_id: str,
        before_snapshot: dict | None,
        after_snapshot: dict | None,
        reason: str | None,
        trace_id: str,
    ) -> AuditLog:
        entry = AuditLog(
            actor_id=actor_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            reason=reason,
            trace_id=trace_id,
        )
        db.add(entry)
        return entry
