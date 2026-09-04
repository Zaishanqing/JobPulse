from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session, sessionmaker

from app.domain.identity import IdentityActor
from app.models import User
from app.ports.identity import IdentityRecord


class SqlAlchemyIdentityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _record(user: User | None) -> IdentityRecord | None:
        if user is None:
            return None
        return IdentityRecord(user.id, user.username, user.role, user.password_hash)

    def by_id(self, user_id: int) -> IdentityRecord | None:
        with self._session_factory() as session:
            return self._record(session.get(User, user_id))

    def by_username(self, username: str) -> IdentityRecord | None:
        with self._session_factory() as session:
            user = session.query(User).filter(User.username == username).one_or_none()
            return self._record(user)


class SqlAlchemySessionIdentityRepository:
    """Request-session adapter used by the composition root's scoped factory."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def by_id(self, user_id: int) -> IdentityRecord | None:
        return SqlAlchemyIdentityRepository._record(self._session.get(User, user_id))

    def by_username(self, username: str) -> IdentityRecord | None:
        user = self._session.query(User).filter(User.username == username).one_or_none()
        return SqlAlchemyIdentityRepository._record(user)


class PbkdfPasswordVerifier:
    def __init__(self) -> None:
        self._context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def verify(self, plain_text: str, password_hash: str) -> bool:
        try:
            return self._context.verify(plain_text, password_hash)
        except (TypeError, ValueError):
            return False


class JwtTokenCodec:
    def __init__(self, secret: str) -> None:
        self._secret = secret

    def encode(self, actor: IdentityActor) -> str:
        return jwt.encode(
            {
                "sub": str(actor.user_id),
                "role": actor.role,
                "exp": datetime.now(timezone.utc) + timedelta(hours=8),
            },
            self._secret,
            algorithm="HS256",
        )

    def decode_subject(self, token: str) -> int:
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
            return int(payload["sub"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid token") from exc
