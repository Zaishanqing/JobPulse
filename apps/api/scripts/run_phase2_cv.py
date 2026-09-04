from __future__ import annotations

import argparse
import json
import secrets
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap.container import _build_application_container  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.domain.accounts import AccountActor  # noqa: E402
from app.infrastructure.accounts import Pbkdf2PasswordAdapter  # noqa: E402
from app.models.user import User  # noqa: E402


OWNER_ID = "phase2-cv-owner"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import one exact precomputed CV, validate it, and create its Resume."
    )
    parser.add_argument("--precomputed-index", required=True, type=Path)
    args = parser.parse_args(argv)

    record = json.loads(
        next(
            line
            for line in args.precomputed_index.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    )
    settings = Settings()
    if not settings.CV_EXTRACTION_ENABLED:
        raise ValueError("CV_EXTRACTION_ENABLED must be true")
    database = create_database(settings.DATABASE_URL)
    try:
        with database.session_factory() as session:
            owner = session.get(User, OWNER_ID)
            if owner is None:
                session.add(
                    User(
                        id=OWNER_ID,
                        username=OWNER_ID,
                        hashed_password=Pbkdf2PasswordAdapter().hash(
                            secrets.token_urlsafe(32)
                        ),
                        role="personal_user",
                    )
                )
                session.commit()
            elif owner.role != "personal_user":
                raise ValueError("Phase-two CV owner has an incompatible role")
        container = _build_application_container(settings, database)
        actor = AccountActor(OWNER_ID, "personal_user")
        imported = container.cv_ingestion.import_and_schedule(
            actor,
            source_record_id="phase2-real-cv-20260727-002",
            raw_text=record["raw_text"],
            source_platform="local_real_batch",
        )
        completed = container.cv_ingestion.run(actor, imported.cv_extraction_task_id)
        if completed.resume_id is None:
            raise RuntimeError("CV ingestion completed without a Resume")
        parse_result = container.resumes.get_parse_result(actor, completed.resume_id)
        if parse_result.need_review:
            container.resumes.confirm(actor, completed.resume_id)
        skill_profile = container.resumes.generate_skill_profile(actor, completed.resume_id)
        print(
            json.dumps(
                {
                    "import": asdict(imported),
                    "task": asdict(completed),
                    "resume_skill_count": len(skill_profile),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
