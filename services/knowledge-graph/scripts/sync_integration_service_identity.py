"""Synchronize the configured main-system integration identity in the KG database."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import hash_password  # noqa: E402
from app.config import Settings  # noqa: E402
from app.database import create_database  # noqa: E402
from app.models import User  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    database = create_database(settings)
    try:
        with database.session_factory() as session:
            identities = list(
                session.scalars(
                    select(User)
                    .where(User.role == "integration_service")
                    .order_by(User.id.asc())
                )
            )
            configured = session.scalar(
                select(User).where(User.username == settings.service_username)
            )
            if configured is not None and configured.role != "integration_service":
                raise ValueError(
                    "Configured KG service username belongs to a non-service account"
                )
            if configured is None:
                if len(identities) > 1:
                    raise ValueError(
                        "Multiple integration service identities exist; select one explicitly"
                    )
                configured = identities[0] if identities else User()
                configured.username = settings.service_username
                configured.role = "integration_service"
                if not identities:
                    session.add(configured)
            configured.password_hash = hash_password(settings.service_password)
            session.commit()
            print(
                json.dumps(
                    {
                        "username": configured.username,
                        "role": configured.role,
                        "created": not identities,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
