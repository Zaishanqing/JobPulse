from tests.runtime_database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password

ALL_ROLES = {"admin", "reviewer", "developer", "personal_user", "enterprise_user"}


def create_internal_user(username: str, role: str, password: str = "password123") -> str | None:
    """Create test identities through the same trusted path as seed/bootstrap."""
    if role not in ALL_ROLES:
        return None
    with SessionLocal() as db:
        existing = db.query(User).filter(User.username == username).first()
        if existing is None:
            existing = User(
                username=username,
                role=role,
                hashed_password=hash_password(password),
                is_active=True,
            )
            db.add(existing)
        else:
            existing.role = role
        db.commit()
        db.refresh(existing)
        return existing.id
