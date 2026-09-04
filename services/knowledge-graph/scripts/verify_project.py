"""Execute the staged acceptance suite; every failed stage exits non-zero."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
VERIFY = ROOT / ".verification"
VERIFY.mkdir(parents=True, exist_ok=True)


class VerificationError(RuntimeError):
    pass


@dataclass
class StageResult:
    name: str
    status: str
    detail: str


STAGE_RESULTS: list[StageResult] = []


def run_command(cmd, cwd=ROOT, env=None):
    shown = " ".join(str(part) for part in cmd)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise VerificationError(f"command exited {result.returncode}: {shown}")
    return result


def clean_project_temp(name: str) -> Path:
    return Path(".test-artifacts") / f"{name}-{uuid.uuid4().hex}"


def pytest_command(*args: str, basetemp: str) -> list[str]:
    temp_path = clean_project_temp(basetemp)
    return [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        f"--basetemp={temp_path}",
        *args,
    ]


def stage(name: str, action: Callable[[], object]):
    print(f"[stage:start] {name}", flush=True)
    try:
        result = action()
    except Exception as exc:
        STAGE_RESULTS.append(StageResult(name, "FAIL", str(exc)))
        print(f"[stage:fail] {name}: {exc}", flush=True)
        raise
    detail = "completed" if result is None else str(result)
    STAGE_RESULTS.append(StageResult(name, "PASS", detail))
    print(f"[stage:pass] {name}: {detail}", flush=True)
    return result


def database_environment(database: Path, *, builds_inline: bool = True) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env.setdefault("JWT_SECRET_KEY", "verify-project-secret-with-at-least-32-characters")
    env.setdefault(
        "KNOWLEDGE_GRAPH_SERVICE_PASSWORD",
        "verify-project-service-password-with-at-least-32-characters",
    )
    env.setdefault("KNOWLEDGE_GRAPH_POSTGRES_PASSWORD", "verify-compose-password")
    if builds_inline:
        env["BUILD_JOBS_INLINE"] = "true"
    temporary = VERIFY / "process-tmp" / uuid.uuid4().hex
    temporary.mkdir(parents=True, exist_ok=True)
    env["PROJECT_VERIFY_TMP"] = str(temporary.resolve())
    return env


def run_clean_database_pipeline(database: Path, runner=run_command):
    if database.exists():
        database.unlink()
    env = database_environment(database)
    stage(
        "database migration",
        lambda: runner([sys.executable, "-m", "alembic", "upgrade", "head"], env=env),
    )
    stage(
        "reference seed (idempotent x2)",
        lambda: [
            runner([sys.executable, "scripts/seed_reference_data.py"], env=env)
            for _ in range(2)
        ],
    )
    stage(
        "authoritative fact import and graph publish",
        lambda: runner([sys.executable, "scripts/run_authoritative_flow.py"], env=env),
    )
    return env


def check_database(database: Path):
    from app.models import GraphVersion, JDDocument, PublishedFactImport, Skill, StandardPosition

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    db = sessionmaker(bind=engine)()
    try:
        tables = inspect(engine).get_table_names()
        if len(tables) < 30:
            raise VerificationError(f"only {len(tables)} migrated tables")
        documents = db.scalars(select(JDDocument)).all()
        imports = db.scalars(select(PublishedFactImport)).all()
        active_skills = db.scalars(select(Skill).where(Skill.status == "active")).all()
        active_positions = db.scalars(
            select(StandardPosition).where(StandardPosition.status == "active")
        ).all()
        if not documents or not all(
            item.fact_authority == "authoritative" for item in documents
        ):
            raise VerificationError("authoritative published fact invariant failed")
        if len(imports) != 1:
            raise VerificationError("expected one published fact import")
        if not active_skills or not active_positions:
            raise VerificationError("authoritative catalog projection invariant failed")
        versions = db.scalars(select(GraphVersion)).all()
        if len(versions) != 1:
            raise VerificationError("authoritative flow did not create one graph version")
        return {
            "tables": len(tables),
            "documents": len(documents),
            "published_fact_imports": len(imports),
            "versions": len(versions),
        }
    finally:
        db.close()
        engine.dispose()


def main():
    STAGE_RESULTS.clear()
    database = VERIFY / "acceptance-clean.db"
    try:
        if os.getenv("KG_FINAL_ACCEPTANCE") == "1":
            required = (
                "KG_TEST_POSTGRES_URL",
                "KG_PERF_POSTGRES_URL",
                "KG_RESTORE_POSTGRES_URL",
            )
            missing = [name for name in required if not os.getenv(name)]
            if missing:
                raise VerificationError(
                    "final PostgreSQL acceptance requires: " + ", ".join(missing)
                )
        env = run_clean_database_pipeline(database)
        stage(
            "pytest with branch coverage",
            lambda: run_command(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    "--cov-report=json:coverage.json",
                ],
                env=database_environment(VERIFY / "pytest-coverage.db", builds_inline=False),
            ),
        )
        if os.getenv("KG_FINAL_ACCEPTANCE") == "1":
            stage(
                "PostgreSQL migration, concurrency, scale and restore",
                lambda: run_command(
                    pytest_command(
                        "-q",
                        "--no-cov",
                        "tests/test_postgresql_concurrency.py",
                        "tests/test_postgresql_scale.py",
                        basetemp="postgresql-final",
                    ),
                    env=os.environ.copy(),
                ),
            )
        stage("ruff", lambda: run_command([sys.executable, "-m", "ruff", "check", "app", "tests", "scripts"]))
        stage("clean database invariants", lambda: check_database(database))
        stage("docker compose config", lambda: run_command(["docker", "compose", "config", "--quiet"], env=env))
    except Exception:
        print(json.dumps({"status": "FAILED", "stages": [asdict(item) for item in STAGE_RESULTS]}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"status": "COMPLETED", "stages": [asdict(item) for item in STAGE_RESULTS]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
