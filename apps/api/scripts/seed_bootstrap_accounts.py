from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.infrastructure.accounts import Pbkdf2PasswordAdapter  # noqa: E402
from app.models.user import User  # noqa: E402


BOOTSTRAP_PASSWORD = "password123"
BOOTSTRAP_ACCOUNTS = (
    ("demo_admin", "admin"),
    ("demo_enterprise", "enterprise_user"),
    ("demo_personal", "personal_user"),
)


def seed(database_url: str) -> dict[str, int]:
    """Create only Quickstart login identities; never create business data."""
    database = create_database(database_url)
    created = 0
    try:
        with database.session_factory() as session:
            for username, role in BOOTSTRAP_ACCOUNTS:
                existing = (
                    session.query(User).filter(User.username == username).one_or_none()
                )
                if existing is not None:
                    if existing.role != role:
                        raise ValueError(
                            f"bootstrap account role conflict: {username}"
                        )
                    continue
                session.add(
                    User(
                        username=username,
                        role=role,
                        hashed_password=Pbkdf2PasswordAdapter().hash(
                            BOOTSTRAP_PASSWORD
                        ),
                        is_active=True,
                    )
                )
                created += 1
            session.commit()
    finally:
        database.dispose()
    return {"configured_accounts": len(BOOTSTRAP_ACCOUNTS), "created": created}


if __name__ == "__main__":
    print(json.dumps(seed(settings.DATABASE_URL)))
