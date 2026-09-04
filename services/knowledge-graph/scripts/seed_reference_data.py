"""Idempotently create service accounts and the reference catalog."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.auth import hash_password
from app.config import Settings
from app.database import create_database
from app.domain.policies import RELATION_ALGORITHM_CONFIG
from app.models import AlgorithmConfig, PositionCategory, SkillCategory, StandardPosition, User


POSITIONS = [
    ("AI_ALGORITHM", "人工智能与算法", "AI"),
    ("AI_AGENT_ENGINEERING", "AI Agent与智能体工程", "AI"),
    ("SE_BACKEND", "后端开发", "TECH"),
    ("SOFTWARE_ARCHITECTURE", "软件架构与技术专家", "TECH"),
    ("SOFTWARE_ENGINEERING_GENERAL", "通用软件研发", "TECH"),
    ("PRODUCT", "产品", "PRODUCT"),
    ("POS_TAXONOMY_OPERATION", "运营", "OPERATION"),
    ("POS_TAXONOMY_PROJECT_MANAGEMENT", "项目管理", "MANAGEMENT"),
    ("POS_TAXONOMY_SE_FULLSTACK", "全栈开发", "TECH"),
    ("POS_TAXONOMY_SOFTWARE_TESTING", "软件测试与质量保障", "TECH"),
]


def seed() -> None:
    runtime_settings = Settings.from_env()
    database = create_database(runtime_settings)
    db = database.session_factory()
    try:
        service_username = runtime_settings.service_username
        service_password = runtime_settings.service_password
        accounts = ((service_username, "integration_service", service_password),)
        for username, role, password in accounts:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                db.add(User(username=username, password_hash=hash_password(password), role=role))
            elif username == service_username or not user.password_hash.startswith("$2"):
                user.password_hash = hash_password(password)
                user.role = role

        for code, name in (
            ("TECH", "技术"), ("DATA", "数据"), ("AI", "人工智能"),
            ("PRODUCT", "产品"), ("OPERATION", "运营"), ("MANAGEMENT", "管理"),
        ):
            if db.scalar(select(PositionCategory).where(PositionCategory.code == code)) is None:
                db.add(PositionCategory(code=code, name=name))

        for code, name in (
            ("LANG", "编程语言"), ("DATA", "数据"), ("AI", "人工智能"),
            ("DEVOPS", "工程化"), ("WEB", "Web"), ("GENERAL", "通用"),
        ):
            if db.scalar(select(SkillCategory).where(SkillCategory.code == code)) is None:
                db.add(SkillCategory(code=code, name=name))

        for position_id, name, category_code in POSITIONS:
            if db.scalar(select(StandardPosition).where(StandardPosition.position_id == position_id)) is None:
                db.add(StandardPosition(position_id=position_id, name=name, category_code=category_code))

        if db.scalar(select(AlgorithmConfig).where(AlgorithmConfig.version == "weighted-v1")) is None:
            db.add(AlgorithmConfig(
                version="weighted-v1",
                payload={
                    "sample_quality": {
                        "duplicate_factor": 0.6,
                        "copy_factor": 0.4,
                        "inflation_factor": 0.5,
                    },
                    **RELATION_ALGORITHM_CONFIG,
                },
                active=True,
            ))
        db.commit()
        print({"positions": db.query(StandardPosition).count(), "users": db.query(User).count()})
    finally:
        db.close()
        database.engine.dispose()


if __name__ == "__main__":
    seed()
