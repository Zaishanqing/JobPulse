from types import SimpleNamespace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
from app.application.knowledge_graph import (
    KnowledgeGraphIntegrationRuleViolation,
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
    ManageKnowledgeGraphIntegration,
)

from app.core.config import settings
from tests.runtime_database import SessionLocal, reset_database_data
from app.core.request_context import reset_trace_id, set_trace_id
from app.integrations.knowledge_graph.client import KnowledgeGraphClient
from app.integrations.knowledge_graph.exceptions import (
    KnowledgeGraphError,
    KnowledgeGraphUnavailable,
)
from app.integrations.knowledge_graph.mappings import (
    extraction_to_kg,
    normalization_to_kg,
)
from app.integrations.knowledge_graph.service import KnowledgeGraphIntegrationService
from app.main import app
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.user import User
from app.models.outbox_message import OutboxMessage
from app.models.skill import Skill
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from app.infrastructure.knowledge_graph import KnowledgeGraphAdapterFactory
from app.infrastructure.jd_repository import SqlAlchemyJDPublicationRepository
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.integration_events import OutboxStatus
from sqlalchemy import event
from app.services.auth_service import hash_password


def envelope(data=None, trace_id="kg_trace"):
    return SimpleNamespace(code=0, message="success", data=data, details={}, trace_id=trace_id)


def client_for(handler) -> KnowledgeGraphClient:
    return KnowledgeGraphClient(
        base_url="http://kg.test",
        username="integration_developer",
        password="secret",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )


def test_unified_portal_permissions_allow_public_reads_and_restrict_management_to_admin():
    adapter = SimpleNamespace(portal=lambda command, actor: command.operation)

    @contextmanager
    def factory():
        yield adapter

    handlers = ManageKnowledgeGraphIntegration(factory)
    public_command = KnowledgeGraphPortalCommand(KnowledgeGraphPortalOperation.LIST_POSITIONS)
    explanation_command = KnowledgeGraphPortalCommand(
        KnowledgeGraphPortalOperation.RELATION_EXPLANATION,
        resource_id="1",
    )
    review_command = KnowledgeGraphPortalCommand(KnowledgeGraphPortalOperation.REVIEW_TASKS)
    publish_gate_command = KnowledgeGraphPortalCommand(KnowledgeGraphPortalOperation.PUBLISH_GATE)

    # personal_user can read public data
    assert (
        handlers.portal(AccountActor("personal-1", "personal_user"), public_command)
        == KnowledgeGraphPortalOperation.LIST_POSITIONS
    )
    assert (
        handlers.portal(
            AccountActor("personal-1", "personal_user"), explanation_command
        )
        == KnowledgeGraphPortalOperation.RELATION_EXPLANATION
    )
    # developer cannot review (no kg.review.manage)
    with pytest.raises(PermissionDenied):
        handlers.portal(AccountActor("developer-1", "developer"), review_command)
    # reviewer CAN review (has kg.review.manage)
    assert (
        handlers.portal(AccountActor("reviewer-1", "reviewer"), review_command)
        == KnowledgeGraphPortalOperation.REVIEW_TASKS
    )
    # reviewer cannot build (no kg.build.manage)
    with pytest.raises(PermissionDenied):
        handlers.portal(AccountActor("reviewer-1", "reviewer"), publish_gate_command)
    # admin can do everything
    assert (
        handlers.portal(AccountActor("admin-1", "admin"), review_command)
        == KnowledgeGraphPortalOperation.REVIEW_TASKS
    )


def test_client_caches_token_refreshes_once_and_propagates_trace_id():
    calls = {"token": 0, "skills": 0}
    seen_trace_ids = []

    def handler(request: httpx.Request):
        seen_trace_ids.append(request.headers.get("x-trace-id"))
        if request.url.path == "/api/v1/auth/token":
            calls["token"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {"access_token": f"token-{calls['token']}"},
                    "trace_id": "kg_auth",
                },
            )
        calls["skills"] += 1
        if calls["skills"] == 1:
            return httpx.Response(
                401,
                json={
                    "code": 40101,
                    "message": "expired",
                    "data": None,
                    "details": {},
                    "trace_id": "kg_401",
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": [{"skill_id": "S1"}],
                "trace_id": "kg_ok",
            },
        )

    token = set_trace_id("req_main_trace")
    try:
        assert client_for(handler).list_skills() == [{"skill_id": "S1"}]
    finally:
        reset_trace_id(token)
    assert calls == {"token": 2, "skills": 2}
    assert set(seen_trace_ids) == {"req_main_trace"}


def test_client_timeout_is_converted_to_unavailable_503():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(KnowledgeGraphUnavailable) as captured:
        client_for(handler).readiness()
    assert captured.value.status_code == 503
    assert captured.value.error_code == "knowledge_graph_unavailable"


def test_position_profile_client_explicitly_requests_published_v3(monkeypatch):
    monkeypatch.undo()
    captured = {}

    def handler(request: httpx.Request):
        if request.url.path == "/api/v1/auth/token":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {"access_token": "token"},
                    "trace_id": "auth",
                },
            )
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {"profile_state": "published", "graph_version_id": 7},
                "trace_id": "profile",
            },
        )

    client = client_for(handler)
    try:
        client.position_profile("POS_BACKEND")
    finally:
        client.close()

    assert captured == {
        "contract_version": "position-profile.v3",
        "view": "published",
    }


@pytest.mark.parametrize("status_code", [403, 404, 409, 422])
def test_client_preserves_expected_upstream_http_semantics(status_code):
    def handler(request: httpx.Request):
        if request.url.path == "/api/v1/auth/token":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {"access_token": "token"},
                    "trace_id": "auth",
                },
            )
        return httpx.Response(
            status_code,
            json={
                "code": status_code * 100 + 1,
                "message": "rejected",
                "data": None,
                "details": {"field": "value"},
                "trace_id": "kg_error",
            },
        )

    with pytest.raises(KnowledgeGraphError) as captured:
        client_for(handler).list_skills()
    assert captured.value.status_code == status_code
    assert captured.value.trace_id == "kg_error"


def test_client_preserves_stable_upstream_error_code():
    def handler(request: httpx.Request):
        if request.url.path == "/api/v1/auth/token":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {"access_token": "token"},
                    "trace_id": "auth",
                },
            )
        return httpx.Response(
            422,
            json={
                "code": 42202,
                "message": "private validation message",
                "data": None,
                "details": {"error_code": "PUBLISHED_FACT_HASH_MISMATCH"},
                "trace_id": "kg_error",
            },
        )

    with pytest.raises(KnowledgeGraphError) as captured:
        client_for(handler).list_skills()
    assert captured.value.error_code == "PUBLISHED_FACT_HASH_MISMATCH"


def _main_payloads():
    raw_text = "岗位名称：Python工程师\n负责Python开发"
    title = "Python工程师"
    title_start = raw_text.index(title)
    skill_start = raw_text.rindex("Python")
    extraction = {
        "schema_version": "v2",
        "document_id": "JD1",
        "job_title": {
            "value": title,
            "evidence": {
                "source_id": "JD1",
                "quote": title,
                "start": title_start,
                "end": title_start + len(title),
                "alignment": "exact",
                "occurrence_index": 0,
            },
        },
        "responsibilities": [],
        "requirements": [
            {
                "requirement_id": "REQ1",
                "kind": "skill",
                "modality": "required",
                "items": [{"name": "Python", "item_type": "programming_language"}],
                "proficiency": None,
                "evidence": {
                    "source_id": "JD1",
                    "quote": "Python",
                    "start": skill_start,
                    "end": skill_start + 6,
                    "alignment": "exact",
                    "occurrence_index": 1,
                },
            }
        ],
        "company_facts": [],
        "employment_facts": [],
    }
    normalized = {
        "schema_version": "v2",
        "document_id": "JD1",
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": title,
            "position_id": "main-position",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "Python工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件研发",
            "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.91}],
            "career_level": "senior",
            "leadership_scope": "none",
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.91,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": ["REQ1"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": [
            {
                "source_name": "Python",
                "requirement_id": "REQ1",
                "requirement_kind": "skill",
                "skill_id": "main-python",
                "canonical_name": "Python",
                "category_code": "TECH",
                "subcategory_code": None,
                "resolution_status": "resolved",
            }
        ],
        "salary": None,
        "unresolved_items": [],
    }
    return raw_text, extraction, normalized


def test_v2_mapper_keeps_exact_evidence_and_uses_explicit_kg_ids():
    _, extraction, normalized = _main_payloads()
    kg_extraction = extraction_to_kg(extraction)
    kg_normalized, skills, position = normalization_to_kg(
        normalized,
        extraction,
        kg_skills=[{"skill_id": "KG_PY", "canonical_name": "Python", "category_code": "TECH"}],
        kg_positions=[
            {
                "position_id": "KG_POS",
                "position_code": "BACKEND_ENGINEER",
                "name": "Python工程师",
            }
        ],
        explicit_skill_mappings={"main-python": "KG_PY"},
    )
    evidence = kg_extraction["requirements"][0]["evidence"]
    assert evidence["quote"] == "Python"
    assert evidence["alignment"] == "exact"
    assert (
        kg_normalized["normalized_requirements"][0]["normalized_skills"][0]["skill_id"] == "KG_PY"
    )
    assert skills["main-python"]["skill_id"] == "KG_PY"
    assert position["position_id"] == "KG_POS"


def test_v2_mapper_preserves_structured_non_skill_requirements():
    _, extraction, _ = _main_payloads()
    evidence = {
        "source_id": "JD1",
        "quote": "结构化要求",
        "start": None,
        "end": None,
        "alignment": "unresolved",
        "occurrence_index": None,
    }
    extraction["requirements"].extend(
        [
            {
                "requirement_id": "EDU1",
                "kind": "education",
                "modality": "required",
                "evidence": evidence,
                "minimum_degree": "bachelor",
                "majors": ["计算机相关专业"],
                "school_constraints": [],
                "admission_type": None,
                "graduation_year": None,
                "student_cohort": None,
            },
            {
                "requirement_id": "EXP1",
                "kind": "experience",
                "modality": "required",
                "evidence": evidence,
                "minimum_years": 3,
                "maximum_years": 5,
                "domain": "软件开发",
                "role": "Python 后端",
                "duration_text": "3-5 年",
                "experience_unlimited": False,
            },
            {
                "requirement_id": "CERT1",
                "kind": "certificate",
                "modality": "preferred",
                "evidence": evidence,
                "certificates": ["软考证书"],
            },
            {
                "requirement_id": "SOFT1",
                "kind": "soft_skill",
                "modality": "required",
                "evidence": evidence,
                "skills": ["沟通", "协作"],
            },
        ]
    )

    mapped = extraction_to_kg(extraction)
    requirements = {item["kind"]: item for item in mapped["requirements"]}

    assert requirements["education"]["minimum_degree"] == "bachelor"
    assert requirements["education"]["majors"] == ["计算机相关专业"]
    assert requirements["experience"]["minimum_years"] == 3
    assert requirements["experience"]["maximum_years"] == 5
    assert requirements["certificate"]["certificates"] == ["软考证书"]
    assert requirements["soft_skill"]["skills"] == ["沟通", "协作"]


def test_extraction_mapping_is_deterministic_and_keeps_source_identity():
    _, extraction, _ = _main_payloads()
    first = extraction_to_kg(extraction)
    second = extraction_to_kg(extraction)
    assert first == second
    changed = dict(extraction)
    changed["document_id"] = "doc-changed"
    assert extraction_to_kg(changed) != first


class FakeKnowledgeGraphClient:
    def __init__(self):
        self.calls = []
        self.published_facts = []
        self.skill_snapshots = {}

    def readiness(self):
        return envelope({"status": "ready"})

    def list_positions(self):
        return [
            {
                "position_id": "BACKEND_ENGINEER",
                "position_code": "BACKEND_ENGINEER",
                "name": "Python工程师",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "sample_support_status": "sufficient",
                "status": "active",
            }
        ]

    def list_skills(self):
        return [{"skill_id": "KG_PY", "canonical_name": "Python", "category_code": "TECH"}]

    def upsert_skill_snapshot(self, skill_id, payload, **actor):
        self.skill_snapshots[skill_id] = payload
        return envelope(payload, "kg_skill_snapshot")

    def import_document(self, payload, **actor):
        self.calls.append("document")
        return envelope({"document_id": payload["document_id"]}, "kg_doc")

    def import_extraction(self, document_id, payload, **actor):
        self.calls.append("extraction")
        return envelope({"record_id": 1}, "kg_extract")

    def align_extraction(self, document_id, **actor):
        self.calls.append("align")
        return envelope({}, "kg_align")

    def import_normalization(self, document_id, payload, **actor):
        self.calls.append("normalization")
        return envelope({"record_id": 2}, "kg_normalized")

    def assess_quality(self, document_id, **actor):
        self.calls.append("quality")
        return envelope({"effective_sample_weight": 1}, "kg_quality")

    def import_published_fact(self, payload, **actor):
        self.calls.append("published_fact")
        self.published_facts.append(payload)
        return envelope(
            {
                "contract_version": payload["contract_version"],
                "document_id": payload["source_jd_id"],
                "source_fact_version": payload["source_fact_version"],
            },
            "kg_published_fact",
        )

    def import_published_fact_v3(self, payload, **actor):
        return self.import_published_fact(payload, **actor)


class MappingKnowledgeGraphClient(FakeKnowledgeGraphClient):
    def __init__(self, skills):
        super().__init__()
        self._skills = skills
        self.normalizations = []

    def list_skills(self):
        return self._skills

    def upsert_skill_snapshot(self, skill_id, payload, **actor):
        self._skills = [item for item in self._skills if item.get("skill_id") != skill_id]
        self._skills.append(payload)
        return super().upsert_skill_snapshot(skill_id, payload, **actor)

    def import_published_fact(self, payload, **actor):
        self.normalizations.append(payload["normalized_fact"])
        return super().import_published_fact(payload, **actor)


def _add_classified_skill(db, *, skill_id: str, canonical_name: str) -> Skill:
    skill = Skill(
        id=skill_id,
        catalog_code=skill_id,
        skill_name=canonical_name,
        category="TECH",
    )
    concept = SkillTaxonomyNode(
        facet="concept_class",
        code="technology",
        name_zh="技术实体",
        name_en="Technology",
    )
    kind = SkillTaxonomyNode(
        facet="technology_kind",
        code="language",
        name_zh="编程语言",
        name_en="Language",
    )
    domain = SkillTaxonomyNode(
        facet="domain",
        code="software_engineering",
        name_zh="软件工程",
        name_en="Software engineering",
    )
    db.add_all([skill, concept, kind, domain])
    db.flush()
    db.add_all(
        [
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
            SkillClassification(
                skill_id=skill.id,
                taxonomy_node_id=domain.id,
                facet=domain.facet,
                is_primary=True,
            ),
        ]
    )
    return skill


def _seed_sync_records(db, *, skill_id="main_skill_001", canonical_name="Python Web 开发"):
    raw_text, extraction, normalized = _main_payloads()
    normalized["normalized_requirements"][0].update(
        {
            "skill_id": skill_id,
            "canonical_name": canonical_name,
            "source_name": "Python",
        }
    )
    user = User(
        username=f"user-{skill_id}", role="admin", hashed_password=hash_password("password123")
    )
    jd = JobDescription(id="JD1", source_type="manual", title="Python工程师", raw_text=raw_text)
    parsed = JDParseResult(
        jd_id="JD1",
        position_title="Python工程师",
        responsibilities=[],
        required_skills=[],
        bonus_skills=[],
        tools=[],
        business_scenarios=[],
        extraction_result=extraction,
        normalized_result=normalized,
        schema_version="v2",
        normalization_schema_version="v2",
        workflow_status="published",
        need_review=False,
    )
    db.add_all([user, jd, parsed])
    _add_classified_skill(db, skill_id=skill_id, canonical_name=canonical_name)
    db.flush()
    SqlAlchemyJDPublicationRepository(db).add(
        parsed.id,
        published_by=user.id,
        published_by_role=user.role,
        validation_lineage={
            "state": "absent",
            "absent_reason": "validation_not_enforced",
        },
    )
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def integration_database(monkeypatch):
    reset_database_data()
    monkeypatch.setattr(settings, "KNOWLEDGE_GRAPH_ENABLED", True)
    yield
    app.dependency_overrides.clear()
    reset_database_data()


def test_real_sync_service_persists_mapping_and_is_idempotent(integration_database):
    raw_text, extraction, normalized = _main_payloads()
    with SessionLocal() as db:
        user = User(username="kg-admin", role="admin", hashed_password=hash_password("password123"))
        jd = JobDescription(id="JD1", source_type="manual", title="Python工程师", raw_text=raw_text)
        parsed = JDParseResult(
            jd_id="JD1",
            position_title="Python工程师",
            responsibilities=[],
            required_skills=[],
            bonus_skills=[],
            tools=[],
            business_scenarios=[],
            extraction_result=extraction,
            normalized_result=normalized,
            schema_version="v2",
            normalization_schema_version="v2",
            workflow_status="published",
            need_review=False,
        )
        db.add_all([user, jd, parsed])
        _add_classified_skill(db, skill_id="main-python", canonical_name="Python")
        db.flush()
        SqlAlchemyJDPublicationRepository(db).add(
            parsed.id,
            published_by=user.id,
            published_by_role=user.role,
            validation_lineage={
                "state": "absent",
                "absent_reason": "validation_not_enforced",
            },
        )
        db.commit()
        db.refresh(user)
        fake = FakeKnowledgeGraphClient()
        service = KnowledgeGraphIntegrationService(db, fake)
        first = service.sync_jd("JD1", user)
        second = service.sync_jd("JD1", user)
        assert first.sync_status == "synced"
        assert second.idempotent is True
        assert fake.calls == ["published_fact"]
        contract = fake.published_facts[0]
        assert contract["contract_version"] == "published-jd-fact.v3"
        assert contract["schema_version"] == "v2"
        assert contract["review_status"] == "published"
        assert "raw_text" not in contract
        mapping = service.mapping("document", "JD1")
        assert mapping.knowledge_graph_id == "JD1"
        assert mapping.sync_version == first.sync_version


def test_transactional_outbox_delivers_published_fact_and_records_stable_key(
    integration_database,
):
    with SessionLocal() as db:
        _seed_sync_records(db)
    fake = FakeKnowledgeGraphClient()
    coordinator = KnowledgeGraphAdapterFactory(SessionLocal, fake, enabled=True)
    actor = AccountActor("integration-admin", "admin")

    first = coordinator.sync_jd("JD1", actor)
    second = coordinator.sync_jd("JD1", actor)

    assert first.sync_status == "synced"
    assert second.idempotent is True
    assert fake.calls == ["published_fact"]
    with SessionLocal() as db:
        message = db.query(OutboxMessage).one()
        assert message.status == OutboxStatus.DELIVERED.value
        assert message.attempts == 1
        assert message.idempotency_key.startswith("jd-publication:")


def test_remote_success_then_local_commit_failure_reuses_same_fact_identity(
    integration_database,
):
    with SessionLocal() as db:
        _seed_sync_records(db)
    fake = FakeKnowledgeGraphClient()
    coordinator = KnowledgeGraphAdapterFactory(SessionLocal, fake, enabled=True)
    actor = AccountActor("integration-admin", "admin")
    commits = 0

    def fail_delivery_commit(session):
        nonlocal commits
        commits += 1
        if commits == 3:
            raise RuntimeError("injected delivery commit failure")

    event.listen(SessionLocal.class_, "before_commit", fail_delivery_commit)
    try:
        with pytest.raises(RuntimeError, match="injected delivery commit failure"):
            coordinator.sync_jd("JD1", actor)
    finally:
        event.remove(SessionLocal.class_, "before_commit", fail_delivery_commit)

    with SessionLocal() as db:
        message = db.query(OutboxMessage).one()
        assert message.status == OutboxStatus.CLAIMED.value
        message.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    result = coordinator.sync_jd("JD1", actor)
    assert result.sync_status == "synced"
    assert len(fake.published_facts) >= 1
    assert {
        (
            item["source_fact_id"],
            item["source_fact_version"],
            item["source_fact_version"],
        )
        for item in fake.published_facts
    } == {
        (
            fake.published_facts[0]["source_fact_id"],
            fake.published_facts[0]["source_fact_version"],
            fake.published_facts[0]["source_fact_version"],
        )
    }
    with SessionLocal() as db:
        assert db.query(OutboxMessage).one().status == OutboxStatus.DELIVERED.value


def test_sync_uses_immutable_publication_when_live_parse_state_changes(
    integration_database,
):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        parsed = db.query(JDParseResult).filter(JDParseResult.jd_id == "JD1").one()
        parsed.workflow_status = "reviewed"
        db.commit()
        fake = FakeKnowledgeGraphClient()
        result = KnowledgeGraphIntegrationService(db, fake).sync_jd("JD1", user)
        assert result.sync_status == "synced"
        assert fake.calls == ["published_fact"]


def test_explicit_skill_mapping_overrides_name_matching():
    _, extraction, normalized = _main_payloads()
    result, _, _ = normalization_to_kg(
        normalized,
        extraction,
        kg_skills=[
            {"skill_id": "KG_PY", "canonical_name": "Python"},
            {"skill_id": "KG_FASTAPI", "canonical_name": "FastAPI"},
        ],
        kg_positions=[],
        explicit_skill_mappings={"main-python": "KG_FASTAPI"},
    )
    mapped = result["normalized_requirements"][0]["normalized_skills"][0]
    assert mapped["skill_id"] == "KG_FASTAPI"
    assert mapped["resolution_source"] == "explicit_mapping"


def test_explicit_mapping_works_when_names_differ(integration_database):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        client = MappingKnowledgeGraphClient(
            [{"skill_id": "kg_skill_fastapi", "canonical_name": "FastAPI", "status": "active"}]
        )
        service = KnowledgeGraphIntegrationService(db, client)
        service.set_mapping("skill", "main_skill_001", "kg_skill_fastapi")
        service.sync_jd("JD1", user)
        mapped = client.normalizations[-1]["normalized_requirements"][0]["normalized_skills"][0]
        assert mapped["skill_id"] == "kg_skill_fastapi"


def test_sync_restores_authoritative_skill_after_remote_catalog_rotation(
    integration_database,
):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        client = MappingKnowledgeGraphClient([])
        service = KnowledgeGraphIntegrationService(db, client)

        service.sync_jd("JD1", user)
        assert {item["skill_id"] for item in client._skills} == {"main_skill_001"}

        client._skills.clear()
        client.skill_snapshots.clear()
        service.sync_jd("JD1", user)

        assert {item["skill_id"] for item in client._skills} == {"main_skill_001"}
        assert set(client.skill_snapshots) == {"main_skill_001"}
        mapped = client.normalizations[-1]["normalized_requirements"][0]["normalized_skills"][0]
        assert mapped["skill_id"] == "main_skill_001"
        assert mapped["resolution_source"] == "explicit_mapping"


def test_explicit_mapping_resolves_ambiguous_same_name():
    _, extraction, normalized = _main_payloads()
    skills = [
        {"skill_id": "KG_ONE", "canonical_name": "Python"},
        {"skill_id": "KG_TWO", "canonical_name": "Python"},
    ]
    result, _, _ = normalization_to_kg(
        normalized,
        extraction,
        kg_skills=skills,
        kg_positions=[],
        explicit_skill_mappings={"main-python": "KG_TWO"},
    )
    mapped = result["normalized_requirements"][0]["normalized_skills"][0]
    assert mapped["skill_id"] == "KG_TWO"
    assert mapped["resolution_source"] == "explicit_mapping"


def test_invalid_target_skill_mapping_is_rejected(integration_database):
    with SessionLocal() as db:
        service = KnowledgeGraphIntegrationService(db, MappingKnowledgeGraphClient([]))
        with pytest.raises(
            KnowledgeGraphIntegrationRuleViolation, match="target ID does not exist"
        ):
            service.set_mapping("skill", "main_skill_001", "missing")


def test_disabled_target_skill_mapping_is_not_used(integration_database):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        client = MappingKnowledgeGraphClient(
            [{"skill_id": "KG_DISABLED", "canonical_name": "FastAPI", "status": "disabled"}]
        )
        service = KnowledgeGraphIntegrationService(db, client)
        with pytest.raises(KnowledgeGraphIntegrationRuleViolation, match="inactive"):
            service.set_mapping("skill", "main_skill_001", "KG_DISABLED")
        row = service._mapping("skill", "main_skill_001")
        row.knowledge_graph_id = "KG_DISABLED"
        row.sync_status = "confirmed"
        db.commit()
        service.sync_jd("JD1", user)
        mapped = client.normalizations[-1]["normalized_requirements"][0]["normalized_skills"][0]
        assert mapped["skill_id"] is None
        assert mapped["resolution_source"] == "unresolved"
        db.refresh(row)
        assert row.last_error_code == "invalid_skill_mapping_target"


def test_sync_records_explicit_mapping_resolution_source(integration_database):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        client = MappingKnowledgeGraphClient(
            [{"skill_id": "kg_skill_fastapi", "canonical_name": "FastAPI"}]
        )
        service = KnowledgeGraphIntegrationService(db, client)
        service.set_mapping("skill", "main_skill_001", "kg_skill_fastapi")
        service.sync_jd("JD1", user)
        mapped = client.normalizations[-1]["normalized_requirements"][0]["normalized_skills"][0]
        assert mapped["resolution_source"] == "explicit_mapping"


def test_mapping_change_updates_sync_result(integration_database):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        client = MappingKnowledgeGraphClient(
            [
                {"skill_id": "KG_A", "canonical_name": "FastAPI"},
                {"skill_id": "KG_B", "canonical_name": "Starlette"},
            ]
        )
        service = KnowledgeGraphIntegrationService(db, client)
        service.set_mapping("skill", "main_skill_001", "KG_A")
        first = service.sync_jd("JD1", user)
        first_version = client.published_facts[-1]["source_fact_version"]
        service.set_mapping("skill", "main_skill_001", "KG_B")
        second = service.sync_jd("JD1", user)
        second_version = client.published_facts[-1]["source_fact_version"]
        resolved_ids = [
            payload["normalized_requirements"][0]["normalized_skills"][0]["skill_id"]
            for payload in client.normalizations
        ]
        assert resolved_ids == ["KG_A", "KG_B"]
        assert first.sync_version != second.sync_version
        assert second_version > first_version


def test_confirming_the_same_mapping_does_not_advance_its_revision(
    integration_database,
):
    with SessionLocal() as db:
        user = _seed_sync_records(db)
        client = MappingKnowledgeGraphClient(
            [
                {"skill_id": "KG_A", "canonical_name": "FastAPI"},
            ]
        )
        service = KnowledgeGraphIntegrationService(db, client)
        first = service.set_mapping("skill", "main_skill_001", "KG_A")
        second = service.set_mapping("skill", "main_skill_001", "KG_A")

        assert first.synced_at == second.synced_at
        assert service.sync_jd("JD1", user).sync_status == "synced"


def test_route_auth_and_status_do_not_bypass_main_rbac(integration_database):
    fake = FakeKnowledgeGraphClient()

    @contextmanager
    def adapter_scope():
        with SessionLocal() as db:
            yield KnowledgeGraphIntegrationService(db, fake, enabled=True)

    app.dependency_overrides[get_knowledge_graph_handlers] = lambda: (
        ManageKnowledgeGraphIntegration(adapter_scope)
    )
    api = TestClient(app)
    assert api.get("/api/v1/integrations/knowledge-graph/status").status_code == 401

    api.post(
        "/api/v1/auth/register",
        json={"username": "ordinary", "password": "password123", "role": "personal_user"},
    )
    login = api.post("/api/v1/auth/login", json={"username": "ordinary", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    status_response = api.get("/api/v1/integrations/knowledge-graph/status", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["data"]["status"] == "available"
    assert (
        api.post(
            "/api/v1/integrations/knowledge-graph/jds/unknown/sync", headers=headers
        ).status_code
        == 403
    )
