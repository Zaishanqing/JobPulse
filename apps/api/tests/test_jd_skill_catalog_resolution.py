from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contexts.governance_feedback import ManageReviews
from app.contexts.jd_lifecycle import Actor, JDApplicationError, JDUseCases
from app.domain.accounts import AccountActor
from app.domain.jd_skill_catalog import (
    CatalogAlias,
    CatalogSkill,
    resolve_catalog_skill,
)
from app.infrastructure.governance import SqlAlchemyGovernanceUnitOfWork
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure.jd_repository import SqlAlchemyJDUoW
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.main import app
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.outbox_message import OutboxMessage
from app.models.review_task import ReviewTask
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from app.models.standard_position import StandardPosition
from tests.runtime_database import reset_database_data, SessionLocal
from tests.test_extraction_draft_import import (
    _bundle,
    _source_and_task,
)


ADMIN = Actor("catalog-admin", "admin")
REVIEWER = AccountActor("catalog-reviewer", "reviewer")
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


class SkillBundleProvider:
    name = "fake-deepseek"
    request_id = "fake-deepseek-skill-bundle-v1"

    def __init__(
        self,
        *,
        source_name: str,
        skill_id: str | None,
        canonical_name: str | None,
    ) -> None:
        self.source_name = source_name
        self.skill_id = skill_id
        self.canonical_name = canonical_name

    def extract(self, envelope):
        bundle = _bundle(envelope)
        extracted_requirement = bundle.extraction_result.requirements[0]
        extracted_item = extracted_requirement.items[0].model_copy(
            update={"name": self.source_name}
        )
        extraction = bundle.extraction_result.model_copy(
            update={
                "requirements": [
                    extracted_requirement.model_copy(update={"items": [extracted_item]})
                ]
            }
        )
        requirement = bundle.normalized_result.normalized_requirements[0]
        skill = requirement.normalized_skills[0].model_copy(
            update={
                "source_name": self.source_name,
                "skill_id": self.skill_id,
                "canonical_name": self.canonical_name,
            }
        )
        normalized = bundle.normalized_result.model_copy(
            update={
                "normalized_requirements": [
                    requirement.model_copy(update={"normalized_skills": [skill]})
                ]
            }
        )
        return bundle.model_copy(
            update={
                "extraction_result": extraction,
                "normalized_result": normalized,
            }
        )


class ExtractionOnlySkillBundleProvider(SkillBundleProvider):
    def extract(self, envelope):
        bundle = super().extract(envelope)
        return bundle.model_copy(
            update={
                "normalized_result": bundle.normalized_result.model_copy(
                    update={"normalized_requirements": []}
                )
            }
        )


def _catalog_skill(skill_id: str, name: str, category: str = "technology"):
    with SessionLocal() as session:
        session.add(Skill(id=skill_id, skill_name=name, category=category))
        session.commit()


def _catalog_alias(skill_id: str, alias: str):
    with SessionLocal() as session:
        session.add(SkillAlias(skill_id=skill_id, alias=alias))
        session.commit()


def _classified_catalog_skill(catalog_code: str, name: str) -> str:
    with SessionLocal() as session:
        skill = Skill(catalog_code=catalog_code, skill_name=name, category=None)
        concept = SkillTaxonomyNode(
            facet="concept_class",
            code="technology",
            name_zh="技术",
            status="active",
        )
        kind = SkillTaxonomyNode(
            facet="technology_kind",
            code="programming_language",
            name_zh="编程语言",
            status="active",
        )
        session.add_all((skill, concept, kind))
        session.flush()
        session.add_all(
            (
                SkillClassification(
                    skill_id=skill.id,
                    taxonomy_node_id=concept.id,
                    facet=concept.facet,
                    is_primary=True,
                ),
                SkillClassification(
                    skill_id=skill.id,
                    taxonomy_node_id=kind.id,
                    facet=kind.facet,
                    is_primary=True,
                ),
            )
        )
        session.commit()
        return skill.id


def _draft(provider: SkillBundleProvider, *, bind_position: bool = True):
    extraction, _, task = _source_and_task(
        provider=provider,
        seed_catalog=False,
    )
    draft = extraction.import_extraction_bundle(task.id)
    if not bind_position:
        return draft
    with SessionLocal() as session:
        position = StandardPosition(
            position_code="BACKEND_ENGINEER",
            position_name="Backend Engineer",
            taxonomy_family_code="SOFTWARE_ENGINEERING",
            taxonomy_family_name="软件研发",
            skill_domain_codes=["software_engineering"],
            core_responsibilities=[],
            required_skills=[],
            bonus_skills=[],
            industry_scenarios=[],
            status="existing",
        )
        session.add(position)
        session.commit()
        position_id = position.id
    _jd_use_cases().map_parse_position_to_catalog(
        ADMIN,
        draft.parse_result_id,
        target_position_id=position_id,
    )
    return draft


def _jd_use_cases() -> JDUseCases:
    return JDUseCases(
        lambda: SqlAlchemyJDUoW(SessionLocal),
        OpenPyxlJDExporter(),
        VersionedJDSchemaAdapter(),
    )


def _review_task(parse_result_id: str) -> ReviewTask:
    with SessionLocal() as session:
        return (
            session.query(ReviewTask)
            .filter(
                ReviewTask.object_type == "jd_parse_result",
                ReviewTask.object_id == parse_result_id,
            )
            .one()
        )


def _approve(parse_result_id: str):
    task = _review_task(parse_result_id)
    reviews = ManageReviews(lambda: SqlAlchemyGovernanceUnitOfWork(SessionLocal))
    reviews.transition(REVIEWER, task.id, "claim")
    return reviews.transition(REVIEWER, task.id, "approve", "Catalog mapping verified")


def test_existing_catalog_id_is_resolved_without_losing_source_fields():
    _catalog_skill("skill-python", "Python", "programming_language")
    draft = _draft(
        SkillBundleProvider(
            source_name="Python",
            skill_id="skill-python",
            canonical_name="Python",
        )
    )

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        assert skill["resolution_status"] == "resolved"
        assert skill["skill_id"] == "skill-python"
        assert skill["source_skill_id"] == "skill-python"
        assert skill["source_canonical_name"] == "Python"
        assert result.extraction_result["requirements"][0]["evidence"]["quote"] == "Python"
        assert not any(
            flag.get("code", "").startswith("skill_catalog_")
            for flag in result.normalized_result["unresolved_items"]
        )


def test_catalog_code_and_classification_snapshot_survive_import_and_review():
    internal_id = _classified_catalog_skill("skill-python", "Python")
    draft = _draft(
        SkillBundleProvider(
            source_name="Python",
            skill_id="skill-python",
            canonical_name="Python",
        )
    )

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        assert skill["resolution_status"] == "resolved"
        assert skill["skill_id"] == internal_id
        assert skill["source_skill_id"] == "skill-python"

    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(
        ADMIN,
        draft.parse_result_id,
    )
    assert publication.parse_result_id == draft.parse_result_id


def test_missing_catalog_id_is_unresolved_and_creates_blocking_flag():
    draft = _draft(
        SkillBundleProvider(
            source_name="Python",
            skill_id="missing-python",
            canonical_name="Python",
        )
    )

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        flags = result.normalized_result["unresolved_items"]
        assert skill["resolution_status"] == "unresolved"
        assert skill["skill_id"] is None
        assert skill["source_skill_id"] == "missing-python"
        assert any(
            flag["code"] == "skill_catalog_unresolved" and flag["severity"] == "blocking"
            for flag in flags
        )
        assert session.query(Skill).count() == 0
        assert _review_task(draft.parse_result_id).status == "pending"


def test_unique_catalog_alias_resolves_but_multiple_alias_targets_conflict():
    _catalog_skill("skill-postgres", "PostgreSQL")
    _catalog_alias("skill-postgres", "Postgres")
    unique = resolve_catalog_skill(
        source_name="Postgres",
        claimed_skill_id="external-postgres",
        claimed_canonical_name=None,
        skills=(CatalogSkill("skill-postgres", "PostgreSQL", "technology"),),
        aliases=(CatalogAlias("skill-postgres", "Postgres"),),
    )
    assert unique.status == "resolved"
    assert unique.skill_id == "skill-postgres"

    _catalog_skill("skill-postgres-alt", "Postgres Database")
    _catalog_alias("skill-postgres-alt", "Postgres")
    conflict = _draft(
        SkillBundleProvider(
            source_name="Postgres",
            skill_id="external-postgres",
            canonical_name=None,
        )
    )
    with SessionLocal() as session:
        result = session.get(JDParseResult, conflict.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        assert skill["resolution_status"] == "conflict"
        assert skill["skill_id"] is None
        assert any(
            flag["code"] == "skill_catalog_conflict" and flag["severity"] == "blocking"
            for flag in result.normalized_result["unresolved_items"]
        )


def test_unresolved_skill_is_retained_without_blocking_review_or_publication():
    draft = _draft(
        SkillBundleProvider(
            source_name="Unknown Skill",
            skill_id="external-skill",
            canonical_name=None,
        )
    )

    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(
        ADMIN,
        draft.parse_result_id,
    )

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        assert skill["resolution_status"] == "unresolved"
        assert publication.parse_result_id == draft.parse_result_id
        assert session.query(JDPublication).count() == 1
        assert session.query(OutboxMessage).count() == 1


def test_unresolved_skill_with_blocking_flag_cannot_publish_before_review():
    draft = _draft(
        SkillBundleProvider(
            source_name="Python",
            skill_id="missing-python",
            canonical_name="Python",
        )
    )

    with pytest.raises(JDApplicationError, match="review|Blocking review flags"):
        _jd_use_cases().publish_parse_result_by_id(
            ADMIN,
            draft.parse_result_id,
        )

    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 0
        assert session.query(OutboxMessage).count() == 0


def test_conflicting_skill_is_retained_without_blocking_review_or_publication():
    source_name = "Unknown Skill"
    _catalog_skill("skill-one", "First Skill")
    _catalog_alias("skill-one", source_name)
    _catalog_skill("skill-two", "Second Skill")
    _catalog_alias("skill-two", source_name)
    draft = _draft(
        SkillBundleProvider(
            source_name=source_name,
            skill_id="external-skill",
            canonical_name=None,
        )
    )
    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(
        ADMIN,
        draft.parse_result_id,
    )
    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        assert skill["resolution_status"] == "conflict"
        assert publication.parse_result_id == draft.parse_result_id
        assert session.query(JDPublication).count() == 1
        assert session.query(OutboxMessage).count() == 1


def test_conflicting_catalog_binding_requires_review_before_publication():
    source_name = "Unknown Skill"
    _catalog_skill("skill-one", "First Skill")
    _catalog_alias("skill-one", source_name)
    _catalog_skill("skill-two", "Second Skill")
    _catalog_alias("skill-two", source_name)
    draft = _draft(
        SkillBundleProvider(
            source_name=source_name,
            skill_id="external-skill",
            canonical_name=None,
        )
    )

    with pytest.raises(JDApplicationError, match="review|conflict"):
        _jd_use_cases().publish_parse_result_by_id(
            ADMIN,
            draft.parse_result_id,
        )

    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 0
        assert session.query(OutboxMessage).count() == 0


def test_manual_mapping_closes_only_catalog_flag_then_allows_review_and_publish():
    draft = _draft(
        SkillBundleProvider(
            source_name="Py",
            skill_id="external-py",
            canonical_name=None,
        )
    )
    target_skill_id = _classified_catalog_skill("skill-python", "Python")

    mapping = _jd_use_cases().map_parse_skill_to_catalog(
        ADMIN,
        draft.parse_result_id,
        source_name="Py",
        requirement_id="skill-1",
        target_skill_id=target_skill_id,
    )
    assert mapping.resolution_status == "resolved"
    assert mapping.skill_id == target_skill_id
    assert mapping.closed_blocking_flags == 1

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        skill = result.normalized_result["normalized_requirements"][0]
        assert skill["skill_id"] == target_skill_id
        assert skill["canonical_name"] == "Python"
        assert skill["source_skill_id"] == "external-py"
        assert skill["source_name"] == "Py"
        assert not any(
            flag.get("code", "").startswith("skill_catalog_")
            for flag in result.normalized_result["unresolved_items"]
        )
        assert result.extraction_result["requirements"][0]["evidence"]["quote"] == "Python"
        assert session.query(Skill).count() == 1

    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(ADMIN, draft.parse_result_id)
    with SessionLocal() as session:
        assert publication.parse_result_id == draft.parse_result_id
        assert session.query(JDPublication).count() == 1
        assert session.query(OutboxMessage).count() == 1


def test_manual_position_mapping_clears_position_blocker_and_allows_publication():
    draft = _draft(
        SkillBundleProvider(
            source_name="Unknown Skill",
            skill_id="external-skill",
            canonical_name=None,
        ),
        bind_position=False,
    )
    with SessionLocal() as session:
        position = StandardPosition(
            position_code="BACKEND_ENGINEER",
            position_name="后端开发",
            taxonomy_family_code="SOFTWARE_ENGINEERING",
            taxonomy_family_name="软件研发",
            skill_domain_codes=["software_engineering"],
            core_responsibilities=[],
            required_skills=[],
            bonus_skills=[],
            industry_scenarios=[],
            status="existing",
        )
        session.add(position)
        session.commit()
        position_id = position.id

    _jd_use_cases().map_parse_position_to_catalog(
        ADMIN,
        draft.parse_result_id,
        target_position_id=position_id,
    )

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        classification = result.normalized_result["job_classification"]
        assert classification["position_id"] == position_id
        assert classification["position_code"] == "BACKEND_ENGINEER"
        assert classification["position_name"] == "后端开发"
        assert classification["family_code"] == "SOFTWARE_ENGINEERING"
        assert classification["observed_skill_domain_codes"] == []
        assert "canonical_name" not in classification
        assert classification["classification_status"] == "manually_confirmed"
        assert not any(
            flag["item_type"] in {"position", "job_title", "job_classification"}
            for flag in result.normalized_result["unresolved_items"]
        )

    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(
        ADMIN, draft.parse_result_id
    )
    assert publication.parse_result_id == draft.parse_result_id


def test_manual_position_mapping_keeps_extraction_only_skill_as_unresolved():
    draft = _draft(
        ExtractionOnlySkillBundleProvider(
            source_name="企业级信息系统运作模式",
            skill_id=None,
            canonical_name=None,
        ),
        bind_position=False,
    )
    with SessionLocal() as session:
        position = StandardPosition(
            position_code="IT_MANAGER",
            position_name="信息技术管理",
            taxonomy_family_code="IT_MANAGEMENT_ANALYSIS",
            taxonomy_family_name="信息技术管理",
            skill_domain_codes=["digital_governance"],
            core_responsibilities=[],
            required_skills=[],
            bonus_skills=[],
            industry_scenarios=[],
            status="existing",
        )
        session.add(position)
        session.commit()
        position_id = position.id

    result = _jd_use_cases().map_parse_position_to_catalog(
        ADMIN,
        draft.parse_result_id,
        target_position_id=position_id,
    )

    assert result.normalized_result["job_classification"]["position_id"] == position_id
    assert result.required_skills[0].raw_skill == "企业级信息系统运作模式"
    assert result.required_skills[0].normalized_skill_id is None
    assert result.required_skills[0].resolution_status == "unresolved"
    context = ManageReviews(
        lambda: SqlAlchemyGovernanceUnitOfWork(SessionLocal)
    ).context(REVIEWER, _review_task(draft.parse_result_id).id)
    extraction_only = next(
        item
        for item in context["skills"]
        if item["source_name"] == "企业级信息系统运作模式"
    )
    assert extraction_only["resolution_status"] == "unresolved"
    assert extraction_only["resolution_source"] == "not_normalized"


def test_invalid_manual_mapping_keeps_flag_and_published_snapshot_is_immutable():
    draft = _draft(
        SkillBundleProvider(
            source_name="Py",
            skill_id="external-py",
            canonical_name=None,
        )
    )
    with pytest.raises(JDApplicationError, match="skill_catalog_snapshot_missing"):
        _jd_use_cases().map_parse_skill_to_catalog(
            ADMIN,
            draft.parse_result_id,
            source_name="Py",
            target_skill_id="missing-target",
        )
    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        assert any(
            flag["code"] == "skill_catalog_unresolved"
            for flag in result.normalized_result["unresolved_items"]
        )

    _catalog_skill("skill-python", "Python", "programming_language")
    _jd_use_cases().map_parse_skill_to_catalog(
        ADMIN,
        draft.parse_result_id,
        source_name="Py",
        target_skill_id="skill-python",
    )
    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(ADMIN, draft.parse_result_id)
    with SessionLocal() as session:
        original_snapshot = session.get(JDPublication, publication.id).snapshot_payload

    with pytest.raises(JDApplicationError, match="immutable"):
        _jd_use_cases().map_parse_skill_to_catalog(
            ADMIN,
            draft.parse_result_id,
            source_name="Py",
            target_skill_id="skill-python",
        )
    with SessionLocal() as session:
        assert session.get(JDPublication, publication.id).snapshot_payload == original_snapshot


def test_manual_mapping_api_rejects_unauthenticated_request():
    response = client.post(
        "/api/v1/jd-parse-results/missing/skill-catalog-mappings",
        json={
            "source_name": "Python",
            "target_skill_id": "skill-python",
        },
    )
    assert response.status_code == 401
