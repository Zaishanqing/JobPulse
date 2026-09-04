from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.core.database import Base, create_database
from app.models import *  # noqa: F403
from app.models.skill import Skill
from app.models.skill_catalog_version import SkillCatalogVersion
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from app.models.user import User
from app.infrastructure.data_validation import (
    FrozenSkillCatalogResolutionAdapter,
    SqlAlchemyValidationPortFactory,
    load_catalog_entries,
)
from app.infrastructure.knowledge_graph_repositories import (
    SqlAlchemyKnowledgeGraphSourceRepository,
)
from scripts.sync_skill_taxonomy_catalog import DEFAULT_CATALOG, sync


def test_reviewed_catalog_sync_is_atomic_and_idempotent(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'catalog.db'}"
    database = create_database(database_url)
    Base.metadata.create_all(database.engine)
    database.dispose()
    catalog = {
        "schema": "skill-taxonomy-snapshot.v1",
        "catalog_version": "skill-taxonomy-catalog.v1",
        "nodes": [
            {"facet": "concept_class", "code": "technology", "name_zh": "技术实体", "name_en": "Technology", "status": "active"},
            {"facet": "technology_kind", "code": "language", "name_zh": "编程语言", "name_en": "Language", "status": "active"},
            {"facet": "domain", "code": "software_engineering", "name_zh": "软件工程", "name_en": "Software engineering", "status": "active"},
        ],
        "skills": {
            "LANG_PYTHON": {
                "canonical_name": "Python",
                "review": {"status": "approved", "review_basis": "canonical_identity", "domain_decision": "classified"},
                "classifications": [
                    {"facet": "concept_class", "code": "technology", "is_primary": True},
                    {"facet": "technology_kind", "code": "language", "is_primary": True},
                    {"facet": "domain", "code": "software_engineering", "is_primary": True},
                ],
            }
        },
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    first = sync(path, database_url, check=False)
    second = sync(path, database_url, check=False)

    assert first["created_skills"] == 1
    assert second["created_skills"] == 0
    database = create_database(database_url)
    with database.session_factory() as session:
        skill = session.scalar(select(Skill).where(Skill.catalog_code == "LANG_PYTHON"))
        assert skill is not None and skill.skill_name == "Python"
        assert len(session.scalars(select(SkillTaxonomyNode)).all()) == 3
        assert len(session.scalars(select(SkillClassification)).all()) == 3
    database.dispose()


def test_full_catalog_reports_explicit_version_labels_across_main_and_kg(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'full-catalog.db'}"
    database = create_database(database_url)
    Base.metadata.create_all(database.engine)
    database.dispose()

    result = sync(DEFAULT_CATALOG, database_url, check=False)
    assert result["catalog_skills"] == 1122

    database = create_database(database_url)
    with database.session_factory() as session:
        skills, aliases = load_catalog_entries(session)
        validation_version = FrozenSkillCatalogResolutionAdapter(
            skills,
            aliases,
            taxonomy_version="skill-taxonomy-snapshot.v1",
        ).taxonomy_version
        kg_version = SqlAlchemyKnowledgeGraphSourceRepository(
            session
        )._taxonomy_version()
    database.dispose()

    assert validation_version == "skill-taxonomy-snapshot.v1"
    assert kg_version == "skill-taxonomy-snapshot.v1"


def test_fresh_deployment_catalog_sync(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'fresh-deployment.db'}"
    database = create_database(database_url)
    Base.metadata.create_all(database.engine)
    database.dispose()
    result = sync(DEFAULT_CATALOG, database_url, check=False)

    assert result["catalog_skills"] == 1122
    database = create_database(database_url)
    with database.session_factory() as session:
        python_skill = session.scalar(
            select(Skill).where(Skill.catalog_code == "LANG_PYTHON")
        )
        assert python_skill is not None
        assert python_skill.category is None
        assert len(
            session.scalars(
                select(SkillClassification).where(
                    SkillClassification.skill_id == python_skill.id
                )
            ).all()
        ) >= 2
    database.dispose()


def test_sync_seeds_catalog_snapshot_when_user_exists(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'snapshot.db'}"
    database = create_database(database_url)
    Base.metadata.create_all(database.engine)
    with database.session_factory() as session:
        session.add(
            User(username="seed-admin", hashed_password="x", role="admin")
        )
        session.commit()
    database.dispose()

    first = sync(DEFAULT_CATALOG, database_url, check=False)
    second = sync(DEFAULT_CATALOG, database_url, check=False)

    assert first["snapshot_seeded"] == 1
    assert second["snapshot_seeded"] == 0
    database = create_database(database_url)
    with database.session_factory() as session:
        assert session.query(SkillCatalogVersion).count() == 1
        skills, _ = load_catalog_entries(session)
        assert len(skills) == 1122
    database.dispose()


def test_validation_catalog_uses_configured_taxonomy_version(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'configured-version.db'}"
    database = create_database(database_url)
    Base.metadata.create_all(database.engine)

    catalog = SqlAlchemyValidationPortFactory(
        database.session_factory,
        taxonomy_version="configured-taxonomy-version",
    ).current_catalog()

    assert catalog.taxonomy_version == "configured-taxonomy-version"
    database.dispose()
