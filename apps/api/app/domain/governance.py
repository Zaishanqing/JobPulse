from __future__ import annotations

from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied


GOVERNANCE_ROLES = frozenset({"admin", "reviewer", "developer"})


def require_governance_role(actor: AccountActor) -> None:
    if actor.role not in GOVERNANCE_ROLES:
        raise PermissionDenied("No permission to manage evidence sources")
