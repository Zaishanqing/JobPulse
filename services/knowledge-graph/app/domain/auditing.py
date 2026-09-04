"""Typed audit write plan shared by application and its persistence port."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.value_types import AuditSnapshot


@dataclass(frozen=True)
class AuditRecord:
    actor_id: int | None
    action: str
    object_type: str
    object_id: str
    before_snapshot: AuditSnapshot | None
    after_snapshot: AuditSnapshot
    reason: str | None
    trace_id: str
