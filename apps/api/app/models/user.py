from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String

from app.core.database import Base


USER_ROLES = (
    "personal_user",
    "enterprise_user",
    "admin",
    "reviewer",
    "developer",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"role in {USER_ROLES}",
            name="ck_users_role_allowed",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    phone = Column(String(32), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
