"""Load an explicitly isolated, idempotent demo dataset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, select, text

from app.config import Settings
from app.database import create_database
from app.infrastructure.readiness import EXPECTED_MIGRATION_REVISION
from app.models import JDDocument, PositionCategory, Skill, SkillCategory, StandardPosition


DEMO_DATASET_ID = "kg-demo:minimal:v1"
DEMO_POSITION_ID = "DEMO_POS_BACKEND"
DEMO_SKILL_ID = "DEMO_SKILL_PYTHON"


def _ensure(row, factory, session):
    if session.scalar(row) is None:
        session.add(factory())


def load(dataset: str) -> dict[str, object]:
    if dataset != "minimal":
        raise ValueError(f"unsupported demo dataset: {dataset}")
    settings = Settings.from_env()
    if settings.environment.casefold() == "production":
        raise RuntimeError("demo datasets are forbidden in production")
    database = create_database(settings)
    try:
        with database.session_factory() as session:
            if not inspect(session.connection()).has_table("alembic_version"):
                raise RuntimeError("run 'alembic upgrade head' before loading demo data")
            revision = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            if revision != EXPECTED_MIGRATION_REVISION:
                raise RuntimeError(
                    f"database migration is {revision!r}; expected {EXPECTED_MIGRATION_REVISION!r}"
                )
            _ensure(
                select(PositionCategory).where(PositionCategory.code == "DEMO_TECH"),
                lambda: PositionCategory(code="DEMO_TECH", name="演示技术岗位"),
                session,
            )
            _ensure(
                select(SkillCategory).where(SkillCategory.code == "DEMO_LANG"),
                lambda: SkillCategory(code="DEMO_LANG", name="演示编程语言"),
                session,
            )
            _ensure(
                select(StandardPosition).where(
                    StandardPosition.position_id == DEMO_POSITION_ID
                ),
                lambda: StandardPosition(
                    position_id=DEMO_POSITION_ID,
                    name="演示后端工程师（非正式数据）",
                    category_code="DEMO_TECH",
                    status="active",
                ),
                session,
            )
            _ensure(
                select(Skill).where(Skill.skill_id == DEMO_SKILL_ID),
                lambda: Skill(
                    skill_id=DEMO_SKILL_ID,
                    canonical_name="演示 Python 技能（非正式数据）",
                    category_code="DEMO_LANG",
                    taxonomy_version=DEMO_DATASET_ID,
                    status="active",
                ),
                session,
            )
            for ordinal, raw_text in enumerate(
                (
                    "演示岗位：负责 API 开发，要求 Python。",
                    "演示岗位：负责服务维护，要求测试能力。",
                ),
                start=1,
            ):
                document_id = f"DEMO_JD_MINIMAL_{ordinal}"
                _ensure(
                    select(JDDocument).where(JDDocument.document_id == document_id),
                    lambda document_id=document_id, raw_text=raw_text: JDDocument(
                        document_id=document_id,
                        raw_text=raw_text,
                        source_type="synthetic_demo",
                        source_name=DEMO_DATASET_ID,
                        enterprise_name="DEMO_ONLY_NOT_REAL",
                        source_credibility=0.0,
                        is_synthetic=True,
                        source_system="knowledge-graph-demo",
                        fact_authority="demo",
                        source_schema_version="demo.v1",
                    ),
                    session,
                )
            session.commit()
            return {
                "dataset": dataset,
                "dataset_id": DEMO_DATASET_ID,
                "position_id": DEMO_POSITION_ID,
                "skill_id": DEMO_SKILL_ID,
                "document_count": session.scalar(
                    select(text("count(*)")).select_from(JDDocument).where(
                        JDDocument.source_name == DEMO_DATASET_ID
                    )
                ),
                "fact_authority": "demo",
                "publishable": False,
            }
    finally:
        database.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=("minimal",), required=True)
    args = parser.parse_args()
    print(load(args.dataset))


if __name__ == "__main__":
    main()
