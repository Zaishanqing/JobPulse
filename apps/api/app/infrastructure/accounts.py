from __future__ import annotations

from datetime import timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import text

from app.contexts.access import DuplicateAccount, InvalidCredentials
from app.core.config import Settings
from app.models.enterprise import Enterprise
from app.models.user import User
from app.contexts.access import AccountRecord, EnterpriseRecord


class Pbkdf2PasswordAdapter:
    def __init__(self) -> None:
        self._context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

    def verify(self, plain_password: str, password_hash: str) -> bool:
        return self._context.verify(plain_password, password_hash)

    def hash(self, plain_password: str) -> str:
        return self._context.hash(plain_password)


class JwtTokenAdapter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def issue(self, subject: str, token_version: int) -> str:
        from datetime import datetime, timezone

        expires = datetime.now(timezone.utc) + timedelta(
            minutes=self._settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
        return jwt.encode(
            {"sub": subject, "tv": token_version, "exp": expires},
            self._settings.JWT_SECRET_KEY,
            algorithm=self._settings.JWT_ALGORITHM,
        )

    def identity(self, token: str) -> tuple[str, int]:
        try:
            payload = jwt.decode(
                token,
                self._settings.JWT_SECRET_KEY,
                algorithms=[self._settings.JWT_ALGORITHM],
            )
        except JWTError as exc:
            raise InvalidCredentials("Invalid or expired token") from exc
        subject = payload.get("sub")
        if not subject:
            raise InvalidCredentials("Invalid token subject")
        token_version = payload.get("tv")
        if type(token_version) is not int or token_version < 0:
            raise InvalidCredentials("Invalid token version")
        return str(subject), token_version


class SqlAlchemyAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, account_id: str) -> AccountRecord | None:
        row = self._session.get(User, account_id)
        if row is None:
            return None
        return self._record(row)

    def get_by_username(self, username: str) -> AccountRecord | None:
        row = self._session.query(User).filter(User.username == username).first()
        return self._record(row) if row is not None else None

    def add(self, **values: str | None) -> AccountRecord:
        row = User(
            username=values["username"],
            email=values["email"],
            phone=values["phone"],
            hashed_password=values["password_hash"],
            role=values["role"],
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def change_password_hash(self, account_id: str, password_hash: str) -> None:
        self._required(account_id).hashed_password = password_hash

    def increment_token_version(self, account_id: str) -> int:
        row = self._required(account_id)
        self._session.query(User).filter(User.id == account_id).update(
            {User.token_version: User.token_version + 1},
            synchronize_session=False,
        )
        self._session.flush()
        self._session.expire(row, ["token_version"])
        return row.token_version

    def change_role(self, account_id: str, role: str) -> None:
        self._required(account_id).role = role

    def change_active(self, account_id: str, is_active: bool) -> None:
        self._required(account_id).is_active = is_active

    def active_account_ids_by_role_for_update(self, role: str) -> tuple[str, ...]:
        rows = (
            self._session.query(User.id)
            .filter(User.role == role, User.is_active.is_(True))
            .order_by(User.id)
            .with_for_update()
            .all()
        )
        return tuple(row.id for row in rows)

    def _required(self, account_id: str) -> User:
        row = self._session.get(User, account_id)
        if row is None:
            raise LookupError(account_id)
        return row

    @staticmethod
    def _record(row: User) -> AccountRecord:
        return AccountRecord(
            account_id=row.id,
            username=row.username,
            email=row.email,
            phone=row.phone,
            role=row.role,
            is_active=row.is_active,
            password_hash=row.hashed_password,
            token_version=row.token_version,
        )


class SqlAlchemyEnterpriseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, enterprise_id: str) -> EnterpriseRecord | None:
        row = self._session.get(Enterprise, enterprise_id)
        return self._record(row) if row is not None else None

    def latest_for_owner(self, owner_user_id: str) -> EnterpriseRecord | None:
        row = (
            self._session.query(Enterprise)
            .filter(Enterprise.owner_user_id == owner_user_id)
            .order_by(Enterprise.created_at.desc())
            .first()
        )
        return self._record(row) if row is not None else None

    def add(self, **values: str | None) -> EnterpriseRecord:
        row = Enterprise(status="active", **values)
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def update(self, enterprise_id: str, changes: dict[str, object]) -> EnterpriseRecord:
        row = self._session.get(Enterprise, enterprise_id)
        if row is None:
            raise LookupError(enterprise_id)
        for key, value in changes.items():
            setattr(row, key, value)
        self._session.flush()
        return self._record(row)

    @staticmethod
    def _record(row: Enterprise) -> EnterpriseRecord:
        return EnterpriseRecord(
            row.id,
            row.owner_user_id,
            row.enterprise_name,
            row.industry,
            row.scale,
            row.location,
            row.description,
            row.status,
            row.created_at,
            row.updated_at,
        )


class SqlAlchemyAccountUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyAccountUnitOfWork":
        self._session = self._session_factory()
        self.accounts = SqlAlchemyAccountRepository(self._session)
        self.enterprises = SqlAlchemyEnterpriseRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateAccount("User already exists") from exc

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def acquire_account_administration_lock(self) -> None:
        """Acquire a serialisation lock for account role/active mutations.

        SQLite
            Issues ``BEGIN IMMEDIATE`` on the current connection **before**
            any read, so that two concurrent transactions cannot both observe
            ``>=2 active admins`` and then both proceed to demote / disable
            the last one.

        PostgreSQL / MySQL / other dialects
            No SQL-level lock here.  Row-level locking is done inside
            ``active_account_ids_by_role_for_update`` via ``SELECT ... FOR
            UPDATE`` with a stable ``ORDER BY``.
        """
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")

        bind = self._session.get_bind()
        if bind is None:
            raise RuntimeError("session has no bound engine")

        if bind.dialect.name == "sqlite":
            self._session.execute(text("BEGIN IMMEDIATE"))


class ExistingSessionAccountUnitOfWork(SqlAlchemyAccountUnitOfWork):
    """Compatibility UoW for callers that still own an SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._existing_session = session
        self._session = None

    def __enter__(self) -> "ExistingSessionAccountUnitOfWork":
        self._session = self._existing_session
        self.accounts = SqlAlchemyAccountRepository(self._session)
        self.enterprises = SqlAlchemyEnterpriseRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()


class CompatibilityHttpError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def hash_password_compat(password: str) -> str:
    return Pbkdf2PasswordAdapter().hash(password)


def register_user_compat(session: Session, payload) -> AccountRecord:
    """Translate the historical function call into the official registration use case."""

    from app.contexts.access import AccountInputError, RegisterAccount
    from app.domain.accounts import AccountRuleViolation

    use_case = RegisterAccount(
        lambda: ExistingSessionAccountUnitOfWork(session), Pbkdf2PasswordAdapter()
    )
    try:
        return use_case.execute(
            username=payload.username,
            password=payload.password,
            role=payload.role,
            email=getattr(payload, "email", None),
            phone=getattr(payload, "phone", None),
        )
    except AccountRuleViolation as exc:
        raise CompatibilityHttpError(422, str(exc)) from exc
    except AccountInputError as exc:
        raise CompatibilityHttpError(422, str(exc)) from exc
    except DuplicateAccount as exc:
        raise CompatibilityHttpError(400, str(exc)) from exc
