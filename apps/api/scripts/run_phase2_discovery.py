from __future__ import annotations

import argparse
import json
import secrets
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap.container import _build_application_container  # noqa: E402
from app.contexts.discovery import Actor, RunDiscoveryCommand  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.domain.values import thaw  # noqa: E402
from app.domain.errors import ExternalGatewayError  # noqa: E402
from app.infrastructure.accounts import Pbkdf2PasswordAdapter  # noqa: E402
from app.models.user import User  # noqa: E402


ACTOR_ID = "phase2-discovery-admin"


def _ensure_local_actor(database) -> None:
    with database.session_factory() as session:
        actor = session.get(User, ACTOR_ID)
        if actor is None:
            session.add(
                User(
                    id=ACTOR_ID,
                    username=ACTOR_ID,
                    hashed_password=Pbkdf2PasswordAdapter().hash(
                        secrets.token_urlsafe(32)
                    ),
                    role="admin",
                )
            )
            session.commit()
        elif actor.role != "admin":
            raise ValueError("Phase-two discovery actor exists without admin role")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run position discovery against the real two-window phase-two JD set."
    )
    parser.add_argument("--window-start", required=True, type=date.fromisoformat)
    parser.add_argument("--window-end", required=True, type=date.fromisoformat)
    args = parser.parse_args(argv)

    settings = Settings()
    database = create_database(settings.DATABASE_URL)
    try:
        _ensure_local_actor(database)
        container = _build_application_container(settings, database)
        try:
            task = container.discovery.start.execute(
                RunDiscoveryCommand(
                    time_window_start=args.window_start,
                    time_window_end=args.window_end,
                    algorithm="default",
                ),
                Actor(ACTOR_ID, "admin"),
            )
        except ExternalGatewayError as exc:
            print(
                json.dumps(
                    {
                        "error_code": exc.error_code,
                        "message": str(exc),
                        "details": exc.details,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        payload = asdict(task)
        payload["input_payload"] = thaw(task.input_payload.values)
        payload["result_payload"] = thaw(task.result_payload.values)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
