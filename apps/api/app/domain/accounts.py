from __future__ import annotations

from dataclasses import dataclass


PUBLIC_ACCOUNT_ROLES = frozenset({"personal_user", "enterprise_user"})
ACCOUNT_ROLES = frozenset({
    "personal_user",
    "enterprise_user",
    "reviewer",
    "admin",
    "developer",
})
ENTERPRISE_STATUSES = frozenset({"active", "inactive"})

# Roles permitted to read enterprise data without being the owner.
# This is used ONLY for enterprise-related authorization, not for account
# management.  Account management is governed by require_permission("account.manage").
ENTERPRISE_READ_ROLES = frozenset({"admin", "developer"})


class AccountRuleViolation(ValueError):
    """Raised when an account or organization invariant is violated."""


@dataclass(frozen=True)
class AccountActor:
    account_id: str
    role: str


def require_public_registration_role(
    role: str, *, allow_demo_admin_registration: bool = False
) -> None:
    if role not in PUBLIC_ACCOUNT_ROLES and not (
        allow_demo_admin_registration and role == "admin"
    ):
        raise AccountRuleViolation("Public registration cannot create an internal role")


def require_account_role(role: str) -> None:
    if role not in ACCOUNT_ROLES:
        raise AccountRuleViolation("Invalid role")


def require_enterprise_manager(actor: AccountActor) -> None:
    if actor.role != "enterprise_user":
        raise AccountRuleViolation("Only enterprise users can manage enterprise profiles")


def require_enterprise_status(status: str) -> None:
    if status not in ENTERPRISE_STATUSES:
        raise AccountRuleViolation("Invalid enterprise status")
