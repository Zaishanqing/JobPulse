from __future__ import annotations
from app.domain.json_types import FrozenJsonObject

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    username: str
    email: str | None
    phone: str | None
    role: str
    is_active: bool
    password_hash: str
    token_version: int = 0


@dataclass(frozen=True)
class EnterpriseRecord:
    enterprise_id: str
    owner_user_id: str
    enterprise_name: str
    industry: str | None
    scale: str | None
    location: str | None
    description: str | None
    status: str
    created_at: datetime | None
    updated_at: datetime | None


class PasswordPort(Protocol):
    def verify(self, plain_password: str, password_hash: str) -> bool: ...
    def hash(self, plain_password: str) -> str: ...


class TokenPort(Protocol):
    def issue(self, subject: str, token_version: int) -> str: ...
    def identity(self, token: str) -> tuple[str, int]: ...


class AccountRepository(Protocol):
    def get(self, account_id: str) -> AccountRecord | None: ...
    def get_by_username(self, username: str) -> AccountRecord | None: ...
    def add(
        self,
        *,
        username: str,
        email: str | None,
        phone: str | None,
        password_hash: str,
        role: str,
    ) -> AccountRecord: ...
    def change_password_hash(self, account_id: str, password_hash: str) -> None: ...
    def increment_token_version(self, account_id: str) -> int: ...
    def change_role(self, account_id: str, role: str) -> None: ...
    def change_active(self, account_id: str, is_active: bool) -> None: ...

    def active_account_ids_by_role_for_update(
        self,
        role: str,
    ) -> tuple[str, ...]: ...


class EnterpriseRepository(Protocol):
    def get(self, enterprise_id: str) -> EnterpriseRecord | None: ...
    def latest_for_owner(self, owner_user_id: str) -> EnterpriseRecord | None: ...
    def add(
        self,
        *,
        owner_user_id: str,
        enterprise_name: str,
        industry: str | None,
        scale: str | None,
        location: str | None,
        description: str | None,
    ) -> EnterpriseRecord: ...
    def update(self, enterprise_id: str, changes: FrozenJsonObject) -> EnterpriseRecord: ...


class AccountUnitOfWork(Protocol):
    accounts: AccountRepository
    enterprises: EnterpriseRepository

    def __enter__(self) -> "AccountUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...

    def acquire_account_administration_lock(self) -> None:
        """Acquire a serialisation lock for account role/active mutations.

        On SQLite this executes ``BEGIN IMMEDIATE`` on the current connection
        so that the critical section is serialised before any read.  On
        dialects that support row-level locking the implementation is a no-op;
        ``active_account_ids_by_role_for_update`` provides the transactional
        guarantee via ``SELECT ... FOR UPDATE`` with a stable ORDER BY.
        """
        ...
