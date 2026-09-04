from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.contexts.governance_feedback import (
    ManageReviews,
    ReviewConflict,
    ReviewValidationError,
)
from app.contexts.jd_lifecycle import Actor, JDApplicationError, JDUseCases
from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json_object
from app.infrastructure.governance import SqlAlchemyGovernanceUnitOfWork
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure.jd_repository import (
    _catalog_snapshot,
    _content_hash,
    _position_catalog_snapshot,
    SqlAlchemyJDPublicationRepository,
    SqlAlchemyJDUoW,
)
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.infrastructure.outbox import SqlAlchemyOutboxRepository
from app.integrations.knowledge_graph.client import KnowledgeGraphClient
from app.main import app
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.outbox_message import OutboxMessage
from app.models.review_task import ReviewTask
from app.models.source_jd import SourceJD, SourceJDVersion
from app.models.skill import Skill
from app.models.skill_catalog_version import SkillCatalogVersion
from app.models.standard_position import StandardPosition
from tests.runtime_database import reset_database_data, SessionLocal
from tests.test_extraction_draft_import import FakeProvider, _envelope, _source_and_task
from tests.user_factory import create_internal_user


client = TestClient(app)
ADMIN = Actor("publication-admin", "admin")
REVIEWER = AccountActor("publication-reviewer", "reviewer")


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


def _jd_use_cases() -> JDUseCases:
    return JDUseCases(
        lambda: SqlAlchemyJDUoW(SessionLocal),
        OpenPyxlJDExporter(),
        VersionedJDSchemaAdapter(),
    )


def _review_use_cases() -> ManageReviews:
    return ManageReviews(lambda: SqlAlchemyGovernanceUnitOfWork(SessionLocal))


def _bind_position(parse_result_id: str) -> None:
    with SessionLocal() as session:
        position = (
            session.query(StandardPosition)
            .filter(StandardPosition.position_code == "BACKEND_ENGINEER")
            .one_or_none()
        )
        if position is None:
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
        parse_result_id,
        target_position_id=position_id,
    )


def _draft(provider: FakeProvider | None = None):
    extraction, source, task = _source_and_task(provider=provider)
    draft = extraction.import_extraction_bundle(task.id)
    _bind_position(draft.parse_result_id)
    return extraction, source, task, draft


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
    _review_use_cases().transition(REVIEWER, task.id, "claim")
    return _review_use_cases().transition(REVIEWER, task.id, "approve", "Evidence verified")


def _token(username: str, role: str) -> str:
    create_internal_user(username, role)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def test_approve_is_atomic_and_idempotent_with_parse_review_state():
    _, _, _, draft = _draft()
    task = _review_task(draft.parse_result_id)

    _review_use_cases().transition(REVIEWER, task.id, "claim")
    first = _review_use_cases().transition(REVIEWER, task.id, "approve", "Evidence verified")
    second = _review_use_cases().transition(REVIEWER, task.id, "approve", "Evidence verified")

    assert first.task_id == second.task_id
    assert first.status == second.status
    assert first.status == "approved"
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        assert parsed.workflow_status == "reviewed"
        assert parsed.need_review is False
        task_events = _review_use_cases().history(REVIEWER, task.id)
        assert len(task_events) == 3
        assert [event.action for event in task_events] == ["create", "claim", "approve"]


def test_reject_requires_reason_is_idempotent_and_blocks_publication():
    _, _, _, draft = _draft()
    task = _review_task(draft.parse_result_id)

    with pytest.raises(ReviewValidationError, match="reason"):
        _review_use_cases().transition(REVIEWER, task.id, "reject", " ")
    _review_use_cases().transition(REVIEWER, task.id, "claim")
    rejected = _review_use_cases().transition(
        REVIEWER, task.id, "reject", "Evidence coordinates are unclear"
    )
    repeated = _review_use_cases().transition(
        REVIEWER, task.id, "reject", "Evidence coordinates are unclear"
    )

    assert rejected.task_id == repeated.task_id
    assert rejected.status == repeated.status
    assert rejected.review_comment == "Evidence coordinates are unclear"
    with pytest.raises(ReviewConflict):
        _review_use_cases().transition(REVIEWER, task.id, "approve", "Changed my mind")
    with pytest.raises(JDApplicationError, match="reviewed"):
        _jd_use_cases().publish_parse_result_by_id(ADMIN, draft.parse_result_id)
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        assert parsed.workflow_status == "draft"
        assert parsed.need_review is True
        assert session.query(JDPublication).count() == 0


def test_unreviewed_blocking_or_non_exact_evidence_cannot_publish():
    _, _, _, draft = _draft()
    use_cases = _jd_use_cases()
    with pytest.raises(JDApplicationError, match="reviewed"):
        use_cases.publish_parse_result_by_id(ADMIN, draft.parse_result_id)

    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        normalized = dict(parsed.normalized_result)
        normalized["unresolved_items"] = [
            {
                "item_type": "job_title",
                "source_value": "Python Engineer",
                "reason": "Must resolve",
                "severity": "blocking",
                "source": "normalization",
            }
        ]
        parsed.normalized_result = normalized
        session.commit()
    with pytest.raises(ReviewConflict, match="Blocking review flags"):
        _approve(draft.parse_result_id)

    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        normalized = dict(parsed.normalized_result)
        normalized["unresolved_items"] = []
        parsed.normalized_result = normalized
        extraction = dict(parsed.extraction_result)
        title = dict(extraction["job_title"])
        evidence = dict(title["evidence"])
        evidence["alignment"] = "normalized_exact"
        title["evidence"] = evidence
        extraction["job_title"] = title
        parsed.extraction_result = extraction
        session.commit()
    with pytest.raises(ReviewConflict, match="exact evidence"):
        _approve(draft.parse_result_id)
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        task = (
            session.query(ReviewTask)
            .filter_by(object_type="jd_parse_result", object_id=draft.parse_result_id)
            .one()
        )
        assert parsed.workflow_status == "draft"
        assert parsed.need_review is True
        assert task.status == "claimed"
        assert session.query(JDPublication).count() == 0
        assert session.query(OutboxMessage).count() == 0


def test_reviewed_publication_atomically_creates_immutable_snapshot_and_outbox(
    monkeypatch,
):
    provider = FakeProvider()
    _, source, task, draft = _draft(provider)
    provider_calls = provider.calls
    _approve(draft.parse_result_id)

    def forbidden_kg_http(*args, **kwargs):
        raise AssertionError("KG HTTP must not be called during publication")

    monkeypatch.setattr(KnowledgeGraphClient, "_request", forbidden_kg_http)
    first = _jd_use_cases().publish_parse_result_by_id(ADMIN, draft.parse_result_id)
    second = _jd_use_cases().publish_parse_result_by_id(ADMIN, draft.parse_result_id)

    assert first == second
    assert first.source_jd_id == source.source_jd_id
    assert first.source_jd_version_id == source.source_jd_version_id
    assert first.extraction_task_id == task.id
    assert first.outbox_status == "pending"
    assert (
        first.snapshot_payload["extraction_result"]["job_title"]["evidence"]["alignment"] == "exact"
    )
    assert provider.calls == provider_calls
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        publication = session.query(JDPublication).one()
        outbox = session.query(OutboxMessage).one()
        assert parsed.workflow_status == "published"
        assert publication.id == first.id
        assert outbox.event_type == "jd.publication.created"
        assert outbox.status == "pending"
        assert outbox.payload["publication_id"] == publication.id
        assert outbox.payload["source_version"] == source.source_version
        assert session.query(KnowledgeGraphEntityMapping).count() == 0


def test_partial_normalization_publishes_only_resolved_skill_projection():
    _, _, _, draft = _draft()
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        normalized = dict(parsed.normalized_result)
        normalized["normalized_requirements"] = [
            *normalized["normalized_requirements"],
            {
                "source_name": "Unknown Framework",
                "requirement_id": "REQ_UNRESOLVED",
                "requirement_kind": "skill",
                "skill_id": None,
                "canonical_name": None,
                "category_code": None,
                "subcategory_code": None,
                "resolution_status": "unresolved",
            },
        ]
        normalized["unresolved_items"] = [
            *normalized["unresolved_items"],
            {
                "item_type": "skill",
                "source_value": "Unknown Framework",
                "reason": "not_found_in_normalization_map",
                "severity": "warning",
                "source": "normalization",
                "details": {"requirement_id": "REQ_UNRESOLVED"},
            },
        ]
        parsed.normalized_result = normalized
        session.commit()

    _approve(draft.parse_result_id)
    publication = _jd_use_cases().publish_parse_result_by_id(
        ADMIN, draft.parse_result_id
    )

    assert len(publication.snapshot_payload["normalized_result"]["normalized_requirements"]) == 1
    assert publication.snapshot_payload["normalized_result"]["projection"] == {
        "policy": "resolved-skills-only.v1",
        "excluded_skill_count": 1,
    }
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        assert len(parsed.normalized_result["normalized_requirements"]) == 2


def test_outbox_failure_rolls_back_status_snapshot_and_event(monkeypatch):
    _, _, _, draft = _draft()
    _approve(draft.parse_result_id)

    def fail_outbox(*args, **kwargs):
        raise RuntimeError("simulated outbox write failure")

    monkeypatch.setattr(SqlAlchemyOutboxRepository, "add", fail_outbox)
    with pytest.raises(RuntimeError, match="outbox write failure"):
        _jd_use_cases().publish_parse_result_by_id(ADMIN, draft.parse_result_id)

    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        assert parsed.workflow_status == "reviewed"
        assert session.query(JDPublication).count() == 0
        assert session.query(OutboxMessage).count() == 0


def test_new_source_version_publishes_independently_without_overwrite():
    _, _, _, first_draft = _draft()
    _approve(first_draft.parse_result_id)
    first = _jd_use_cases().publish_parse_result_by_id(ADMIN, first_draft.parse_result_id)

    second_envelope = _envelope(
        "Backend Engineer uses Python daily and Docker", source_version="2"
    )
    extraction, _, second_task = _source_and_task(envelope=second_envelope)
    second_draft = extraction.import_extraction_bundle(second_task.id)
    _bind_position(second_draft.parse_result_id)
    _approve(second_draft.parse_result_id)
    second = _jd_use_cases().publish_parse_result_by_id(ADMIN, second_draft.parse_result_id)

    assert first.id != second.id
    assert first.parse_result_id != second.parse_result_id
    assert first.source_jd_version_id != second.source_jd_version_id
    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 2
        assert session.query(OutboxMessage).count() == 2
        assert session.get(JDPublication, first.id).snapshot_payload == dict(first.snapshot_payload)


def test_manual_publication_snapshot_has_content_derived_source_identity():
    with SessionLocal() as session:
        jd = JobDescription(
            source_type="manual",
            source_name="manual-1",
            title="Manual JD",
            raw_text="岗位职责：负责 RAG 应用开发。",
            parse_status="completed",
            input_extraction_status="manually_edited",
            input_provider="manual",
            publish_date=date(2026, 7, 29),
        )
        session.add(jd)
        session.flush()
        parsed = JDParseResult(
            jd_id=jd.id,
            extraction_result={
                "schema_version": "v2",
                "document_id": jd.id,
            },
            normalized_result={
                "schema_version": "v2",
                "document_id": jd.id,
                "job_classification": {
                    "schema_version": "job-position-classification.v3",
                    "taxonomy_version": "position-taxonomy.v3.0.0",
                    "source_title": "Manual JD",
                    "position_code": "BACKEND_ENGINEER",
                    "position_name": "后端工程师",
                    "family_code": "SOFTWARE_ENGINEERING",
                    "family_name": "软件工程与研发",
                    "candidate_positions": [
                        {"position_code": "BACKEND_ENGINEER", "score": 1.0}
                    ],
                    "career_level": None,
                    "leadership_scope": None,
                    "technology_focus_codes": [],
                    "industry_context_codes": [],
                    "observed_skill_domain_codes": [],
                    "confidence": 1.0,
                    "classification_status": "resolved",
                    "review_reason_codes": [],
                    "evidence_refs": ["manual-jd-title"],
                    "classification_policy_version": "position-classifier.v3.0",
                },
                "normalized_requirements": [],
                "unresolved_items": [],
            },
            workflow_status="published",
            need_review=False,
        )
        session.add(parsed)
        session.commit()
        parse_result_id = parsed.id

    with SessionLocal() as session:
        publication = SqlAlchemyJDPublicationRepository(session).add(
            parse_result_id,
            published_by="publication-admin",
            published_by_role="admin",
            validation_lineage=freeze_json_object(
                {
                    "state": "absent",
                    "absent_reason": "validation_not_enforced",
                }
            ),
        )
        session.commit()

    snapshot = publication.snapshot_payload
    assert snapshot["source_version"].startswith("manual:")
    assert snapshot["source_content_hash"]
    assert snapshot["jd"]["publish_date"] == "2026-07-29"
    assert publication.idempotency_key.startswith(
        f"jd-publication:{parse_result_id}:manual:"
    )
    assert len(snapshot["skill_catalog_snapshot"]["content_hash"]) == 64
    assert len(snapshot["position_catalog_snapshot"]["content_hash"]) == 64
    assert snapshot["skill_catalog_snapshot"]["content_hash"] != snapshot[
        "position_catalog_snapshot"
    ]["content_hash"]


def test_validation_stage_tracks_raw_and_cleaned_source_changes():
    cleaned = "岗位职责：负责 RAG 应用开发。"
    with SessionLocal() as session:
        jd = JobDescription(
            source_type="manual",
            source_name="manual-1",
            title="Manual JD",
            raw_text=cleaned + "  ",
            cleaned_text=cleaned,
            parse_status="pending",
            input_extraction_status="manually_edited",
            input_provider="manual",
        )
        session.add(jd)
        session.flush()
        parsed = JDParseResult(
            jd_id=jd.id,
            extraction_result={
                "schema_version": "v2",
                "document_id": jd.id,
            },
            normalized_result={
                "schema_version": "v2",
                "document_id": jd.id,
                "job_classification": {
                    "schema_version": "job-position-classification.v3",
                    "taxonomy_version": "position-taxonomy.v3.0.0",
                    "source_title": "Manual JD",
                    "position_code": "BACKEND_ENGINEER",
                    "position_name": "后端工程师",
                    "family_code": "SOFTWARE_ENGINEERING",
                    "family_name": "软件工程与研发",
                    "candidate_positions": [
                        {"position_code": "BACKEND_ENGINEER", "score": 1.0}
                    ],
                    "career_level": None,
                    "leadership_scope": None,
                    "technology_focus_codes": [],
                    "industry_context_codes": [],
                    "observed_skill_domain_codes": [],
                    "confidence": 1.0,
                    "classification_status": "resolved",
                    "review_reason_codes": [],
                    "evidence_refs": ["manual-jd-title"],
                    "classification_policy_version": "position-classifier.v3.0",
                },
                "normalized_requirements": [],
                "unresolved_items": [],
            },
            workflow_status="draft",
            need_review=True,
        )
        session.add(parsed)
        session.commit()
        jd_id = jd.id
        parse_result_id = parsed.id

    def staged_version():
        with SqlAlchemyJDUoW(
            SessionLocal,
            data_validation_mode="enforce",
        ) as uow:
            uow.stage_validation_for_parse_result(parse_result_id)
            uow.commit()
        with SessionLocal() as session:
            return session.get(
                SourceJDVersion,
                session.get(JobDescription, jd_id).source_jd_version_id,
            )

    first = staged_version()

    with SessionLocal() as session:
        jd = session.get(JobDescription, jd_id)
        jd.raw_text = cleaned
        session.commit()
    second = staged_version()
    assert second.id != first.id
    assert second.source_version != first.source_version
    assert second.raw_text != first.raw_text
    assert second.raw_text == cleaned

    with SessionLocal() as session:
        jd = session.get(JobDescription, jd_id)
        jd.cleaned_text = "岗位职责：负责 Java 后端服务开发。"
        session.commit()
    third = staged_version()
    assert third.id != second.id
    assert third.source_version != second.source_version
    assert third.raw_text == cleaned

    with SessionLocal() as session:
        assert session.query(SourceJD).count() == 1
        assert session.query(SourceJDVersion).count() == 3


def test_skill_catalog_snapshot_identity_tracks_published_version_snapshot():
    snapshot_v1 = {
        "skills": [{"skill_id": "s1", "canonical_name": "Python"}],
        "aliases": [],
        "classifications": {},
    }
    with SessionLocal() as session:
        session.add(
            SkillCatalogVersion(
                version_number=1,
                catalog_version="skill-catalog.v1",
                snapshot=snapshot_v1,
                change_summary={},
                published_by="catalog-admin",
            )
        )
        session.add(
            Skill(
                id="live-skill",
                catalog_code="LANG_PY",
                skill_name="Python",
                category="programming_language",
            )
        )
        session.commit()
        first = _catalog_snapshot(session, datetime.now(timezone.utc))
        assert first["catalog_version"] == "skill-catalog.v1"
        assert first["content_hash"] == _content_hash({"snapshot": snapshot_v1})

        live = session.get(Skill, "live-skill")
        live.skill_name = "Python 3"
        session.commit()
        unchanged = _catalog_snapshot(session, datetime.now(timezone.utc))
        assert unchanged["catalog_version"] == first["catalog_version"]
        assert unchanged["content_hash"] == first["content_hash"]

        session.add(
            SkillCatalogVersion(
                version_number=2,
                catalog_version="skill-catalog.v2",
                snapshot={
                    "skills": [{"skill_id": "s2", "canonical_name": "Java"}],
                    "aliases": [],
                    "classifications": {},
                },
                change_summary={},
                published_by="catalog-admin",
            )
        )
        session.commit()
        second = _catalog_snapshot(session, datetime.now(timezone.utc))
        assert second["catalog_version"] == "skill-catalog.v2"
        assert second["content_hash"] != first["content_hash"]


def test_position_catalog_snapshot_pair_is_content_deterministic():
    with SessionLocal() as session:
        session.add(
            StandardPosition(
                position_code="BACKEND_ENGINEER",
                position_name="Backend Engineer",
                taxonomy_family_code="SOFTWARE_ENGINEERING",
                taxonomy_family_name="软件工程与研发",
                taxonomy_version="position-taxonomy.v3.0.0",
                lifecycle_status="active",
                sample_support_status="sufficient",
            )
        )
        session.commit()
        first = _position_catalog_snapshot(session, datetime.now(timezone.utc))
        repeated = _position_catalog_snapshot(session, datetime.now(timezone.utc))
        assert first["catalog_version"] == repeated["catalog_version"]
        assert first["content_hash"] == repeated["content_hash"]

        row = session.query(StandardPosition).one()
        row.position_name = "Backend"
        session.commit()
        second = _position_catalog_snapshot(session, datetime.now(timezone.utc))
        assert second["catalog_version"] == first["catalog_version"]
        assert second["content_hash"] != first["content_hash"]


def test_publication_and_review_endpoints_require_authentication():
    assert client.post("/api/v1/review-tasks/example/approve").status_code == 401
    assert client.post("/api/v1/jd-parse-results/example/publish").status_code == 401
    assert client.get("/api/v1/jd-parse-results/example/publication").status_code == 401


def test_review_and_publication_api_happy_path():
    _, _, _, draft = _draft()
    reviewer_token = _token("atomic-reviewer", "reviewer")
    admin_token = _token("atomic-admin", "admin")
    task = _review_task(draft.parse_result_id)

    claimed = client.post(
        f"/api/v1/review-tasks/{task.id}/claim",
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    approved = client.post(
        f"/api/v1/review-tasks/{task.id}/approve",
        json={"review_comment": "Checked"},
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    published = client.post(
        f"/api/v1/jd-parse-results/{draft.parse_result_id}/publish",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    fetched = client.get(
        f"/api/v1/jd-parse-results/{draft.parse_result_id}/publication",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert claimed.status_code == 200
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"
    assert published.status_code == 200
    assert published.json()["data"]["outbox_status"] == "pending"
    assert fetched.json()["data"] == published.json()["data"]
