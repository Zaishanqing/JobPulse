from __future__ import annotations
from app.domain.json_types import FrozenJsonObject

from dataclasses import dataclass
from typing import Callable

from app.domain.accounts import (
    AccountActor,
    AccountRuleViolation,
    ENTERPRISE_READ_ROLES,
    require_account_role,
    require_enterprise_manager,
    require_enterprise_status,
    require_public_registration_role,
)
from app.contexts.access._ports.accounts import (
    AccountRecord,
    AccountUnitOfWork,
    EnterpriseRecord,
    PasswordPort,
    TokenPort,
)
from app.domain.accounts import ACCOUNT_ROLES
from app.domain.permissions import ALL_PERMISSIONS, require_permission
from app.domain.errors import PermissionDenied


UoWFactory = Callable[[], AccountUnitOfWork]
MIN_PASSWORD_LENGTH = 8


@dataclass(frozen=True)
class ChangePasswordResult:
    user_id: str
    password_changed: bool
    access_token: str
    token_type: str = "bearer"


@dataclass(frozen=True)
class AccountRoleChangeCommand:
    account_id: str
    role: str


@dataclass(frozen=True)
class AccountRoleChangeResult:
    user_id: str
    role: str


@dataclass(frozen=True)
class AccountActiveChangeCommand:
    account_id: str
    is_active: bool


@dataclass(frozen=True)
class AccountActiveChangeResult:
    user_id: str
    is_active: bool


@dataclass(frozen=True)
class EnterpriseUpdateCommand:
    enterprise_name: str | None = None
    industry: str | None = None
    scale: str | None = None
    location: str | None = None
    description: str | None = None
    status: str | None = None

    def changes(self) -> FrozenJsonObject:
        return FrozenJsonObject({
            name: value for name, value in vars(self).items() if value is not None
        })


class AccountNotFound(LookupError):
    pass


class AccountConflict(RuntimeError):
    pass


class AccountInputError(ValueError):
    pass


class InvalidAccountChange(AccountConflict):
    pass


class DuplicateAccount(AccountConflict):
    pass


class InvalidCredentials(ValueError):
    pass


class InactiveAccount(InvalidCredentials):
    pass


class EnterpriseNotFound(LookupError):
    pass


@dataclass(frozen=True)
class RegisterAccount:
    uow_factory: UoWFactory
    passwords: PasswordPort
    allow_demo_admin_registration: bool = False

    def execute(
        self,
        *,
        username: str,
        password: str,
        role: str,
        email: str | None,
        phone: str | None,
    ) -> AccountRecord:
        require_public_registration_role(
            role,
            allow_demo_admin_registration=self.allow_demo_admin_registration,
        )
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AccountInputError(
                f"Password must contain at least {MIN_PASSWORD_LENGTH} characters"
            )
        with self.uow_factory() as uow:
            if uow.accounts.get_by_username(username) is not None:
                raise DuplicateAccount("Username already exists")
            record = uow.accounts.add(
                username=username,
                email=email,
                phone=phone,
                password_hash=self.passwords.hash(password),
                role=role,
            )
            uow.commit()
            return record


@dataclass(frozen=True)
class AuthenticateAccount:
    uow_factory: UoWFactory
    passwords: PasswordPort
    tokens: TokenPort

    def execute(self, username: str, password: str) -> tuple[AccountRecord, str]:
        with self.uow_factory() as uow:
            account = uow.accounts.get_by_username(username)
            if account is None or not self.passwords.verify(password, account.password_hash):
                raise InvalidCredentials("Incorrect username or password")
            if not account.is_active:
                raise InactiveAccount("User is inactive")
            return account, self.tokens.issue(account.username, account.token_version)

    def resolve(self, token: str) -> AccountRecord:
        username, token_version = self.tokens.identity(token)
        with self.uow_factory() as uow:
            account = uow.accounts.get_by_username(username)
            if account is None or not account.is_active:
                raise InvalidCredentials("User is inactive or does not exist")
            if token_version != account.token_version:
                raise InvalidCredentials("Token session has been revoked")
            return account

    def refresh(self, account: AccountRecord) -> str:
        return self.tokens.issue(account.username, account.token_version)

    def logout_all(self, account: AccountRecord) -> int:
        with self.uow_factory() as uow:
            token_version = uow.accounts.increment_token_version(account.account_id)
            uow.commit()
            return token_version


@dataclass(frozen=True)
class ManageEnterprise:
    uow_factory: UoWFactory

    def create(self, actor: AccountActor, **values: str | None) -> EnterpriseRecord:
        require_enterprise_manager(actor)
        with self.uow_factory() as uow:
            record = uow.enterprises.add(owner_user_id=actor.account_id, **values)
            uow.commit()
            return record

    def mine(self, actor: AccountActor) -> EnterpriseRecord | None:
        require_enterprise_manager(actor)
        with self.uow_factory() as uow:
            return uow.enterprises.latest_for_owner(actor.account_id)

    def get(self, actor: AccountActor, enterprise_id: str) -> EnterpriseRecord:
        with self.uow_factory() as uow:
            record = uow.enterprises.get(enterprise_id)
            if record is None:
                raise EnterpriseNotFound("Enterprise not found")
            self._authorize(actor, record, write=False)
            return record

    def update(
        self, actor: AccountActor, enterprise_id: str, command: EnterpriseUpdateCommand
    ) -> EnterpriseRecord:
        with self.uow_factory() as uow:
            record = uow.enterprises.get(enterprise_id)
            if record is None:
                raise EnterpriseNotFound("Enterprise not found")
            self._authorize(actor, record, write=True)
            changes = command.changes()
            if command.status is not None:
                require_enterprise_status(command.status)
            updated = uow.enterprises.update(enterprise_id, changes)
            uow.commit()
            return updated

    @staticmethod
    def _authorize(actor: AccountActor, record: EnterpriseRecord, *, write: bool) -> None:
        if record.owner_user_id == actor.account_id:
            return
        if not write and actor.role in ENTERPRISE_READ_ROLES:
            return
        raise PermissionDenied("No permission for this enterprise")


@dataclass(frozen=True)
class ChangePassword:
    uow_factory: UoWFactory
    passwords: PasswordPort
    tokens: TokenPort

    def execute(self, actor: AccountActor, old_password: str, new_password: str) -> ChangePasswordResult:
        with self.uow_factory() as uow:
            account = uow.accounts.get(actor.account_id)
            if account is None:
                raise AccountNotFound("User not found")
            if not self.passwords.verify(old_password, account.password_hash):
                raise AccountInputError("Old password is incorrect")
            if old_password == new_password:
                raise AccountInputError("New password must differ from old password")
            if len(new_password) < MIN_PASSWORD_LENGTH:
                raise AccountInputError(
                    f"New password must contain at least {MIN_PASSWORD_LENGTH} characters"
                )
            uow.accounts.change_password_hash(
                account.account_id, self.passwords.hash(new_password)
            )
            token_version = uow.accounts.increment_token_version(account.account_id)
            uow.commit()
            access_token = self.tokens.issue(account.username, token_version)
            return ChangePasswordResult(account.account_id, True, access_token)


@dataclass(frozen=True)
class ManageAccount:
    uow_factory: UoWFactory

    # ── read operations ──────────────────────────────────────────────────

    def list_roles(self, actor: AccountActor) -> tuple[str, ...]:
        require_permission(actor.role, "account.manage")
        return tuple(sorted(ACCOUNT_ROLES))

    def list_permissions(self, actor: AccountActor) -> tuple[str, ...]:
        require_permission(actor.role, "account.manage")
        return tuple(sorted(ALL_PERMISSIONS))

    # ── mutating operations ──────────────────────────────────────────────

    def change_role(
        self, actor: AccountActor, command: AccountRoleChangeCommand
    ) -> AccountRoleChangeResult:
        require_permission(actor.role, "account.manage")

        try:
            require_account_role(command.role)
        except AccountRuleViolation as exc:
            raise AccountInputError(str(exc)) from exc

        # 1) self‑protection (outside UoW — no lock needed)
        if actor.account_id == command.account_id:
            raise InvalidAccountChange("Cannot change your own administrative role")

        with self.uow_factory() as uow:
            # 2) acquire serialisation lock BEFORE any read
            uow.acquire_account_administration_lock()

            # 3) read current state *after* lock acquired
            target = self._require_account(uow, command.account_id)

            # 4) last‑active‑admin protection
            if (
                target.role == "admin"
                and target.is_active
                and command.role != "admin"
            ):
                active_admins = uow.accounts.active_account_ids_by_role_for_update("admin")
                if len(active_admins) <= 1:
                    raise InvalidAccountChange(
                        "Cannot demote the last active administrator"
                    )

            uow.accounts.change_role(command.account_id, command.role)
            uow.commit()
            return AccountRoleChangeResult(command.account_id, command.role)

    def change_active(
        self, actor: AccountActor, command: AccountActiveChangeCommand
    ) -> AccountActiveChangeResult:
        require_permission(actor.role, "account.manage")

        # 1) self‑protection (outside UoW — no lock needed)
        if not command.is_active and actor.account_id == command.account_id:
            raise InvalidAccountChange("Cannot disable your own account")

        with self.uow_factory() as uow:
            # 2) acquire serialisation lock BEFORE any read
            uow.acquire_account_administration_lock()

            # 3) read current state *after* lock acquired
            target = self._require_account(uow, command.account_id)

            # 4) last‑active‑admin protection
            if (
                target.role == "admin"
                and target.is_active
                and not command.is_active
            ):
                active_admins = uow.accounts.active_account_ids_by_role_for_update("admin")
                if len(active_admins) <= 1:
                    raise InvalidAccountChange(
                        "Cannot disable the last active administrator"
                    )

            uow.accounts.change_active(command.account_id, command.is_active)
            uow.commit()
            return AccountActiveChangeResult(command.account_id, command.is_active)

    @staticmethod
    def _require_account(uow: AccountUnitOfWork, account_id: str) -> AccountRecord:
        record = uow.accounts.get(account_id)
        if record is None:
            raise AccountNotFound("User not found")
        return record


@dataclass(frozen=True)
class AccountHandlers:
    registration: RegisterAccount
    authentication: AuthenticateAccount
    password: ChangePassword
    management: ManageAccount
    enterprises: ManageEnterprise
