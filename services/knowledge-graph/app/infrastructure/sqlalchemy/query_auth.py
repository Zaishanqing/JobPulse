from sqlalchemy import select

from app.infrastructure.sqlalchemy.query_base import QuerySession
from app.models import User


class AuthenticationQueryMixin(QuerySession):
    def authenticate(self, username: str) -> dict | None:
        row = self.session.scalar(select(User).where(User.username == username))
        if row is None:
            return None
        return {
            "id": row.id,
            "username": row.username,
            "role": row.role,
            "password_hash": row.password_hash,
        }
