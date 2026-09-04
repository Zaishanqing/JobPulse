from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.extraction_tasks import get_extraction_task_use_cases
from app.contexts.extraction_tasks import (
    ExtractionDraftNotReady,
    ExtractionDraftValidationError,
    ExtractionTaskUseCases,
)
from app.contexts.source_jds import SourceJDUseCases
from app.infrastructure.extraction_tasks import (
    SqlAlchemyExtractionDraftRepository,
    SqlAlchemyExtractionTaskUnitOfWork,
)
from app.infrastructure.source_jds import SqlAlchemySourceJDUnitOfWork
from app.main import app
from app.models.extraction_task import ExtractionTask
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.review_task import ReviewTask
from app.models.skill import Skill
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1
from jobgraph_contracts.extraction_v2 import (
    Evidence,
    JDExtractionResult,
    SkillItem,
    SkillRequirement,
    SourcedText,
    TaskRequirement,
)
from jobgraph_contracts.normalization_v2 import (
    JDNormalizedResult,
    JobClassification,
    NormalizedRequirement,
    NormalizedSkill,
    UnresolvedItem,
)
from tests.runtime_database import reset_database_data, SessionLocal
from tests.user_factory import create_internal_user


NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


def _envelope(
    raw_text: str = "Backend Engineer uses Python daily",
    *,
    source_version: str = "1",
) -> CrawlerJDEnvelopeV1:
    return CrawlerJDEnvelopeV1(
        source_platform="boss",
        source_record_id="draft-job-1",
        source_version=source_version,
        source_url="https://example.test/draft-job-1",
        crawl_time=NOW,
        raw_text=raw_text,
        raw_payload={"raw_text": raw_text},
        job_title_raw="Backend Engineer",
        company_name_raw="Example Co",
        text_canonicalization_version="raw-v1",
    )


def _exact(raw_text: str, quote: str, document_id: str) -> Evidence:
    start = raw_text.index(quote)
    return Evidence(
        source_id=document_id,
        quote=quote,
        start=start,
        end=start + len(quote),
        alignment="exact",
        occurrence_index=0,
    )


def _bundle(envelope: CrawlerJDEnvelopeV1) -> ExtractedJDBundleV1:
    document_id = f"jdv1_{envelope.source_record_id[-32:]}"
    title = envelope.job_title_raw or "Backend Engineer"
    return ExtractedJDBundleV1(
        source_platform=envelope.source_platform,
        source_record_id=envelope.source_record_id,
        source_version=envelope.source_version,
        cleaned_text=envelope.raw_text,
        extraction_result=JDExtractionResult(
            document_id=document_id,
            job_title=SourcedText(
                text=title,
                evidence=_exact(envelope.raw_text, title, document_id),
            ),
            responsibilities=[
                TaskRequirement(
                    requirement_id="task-1",
                    text="uses Python",
                    evidence=_exact(envelope.raw_text, "uses Python", document_id),
                )
            ],
            requirements=[
                SkillRequirement(
                    requirement_id="skill-1",
                    kind="skill",
                    modality="required",
                    items=[SkillItem(name="Python", item_type="language")],
                    evidence=_exact(envelope.raw_text, "Python", document_id),
                )
            ],
        ),
        normalized_result=JDNormalizedResult(
            document_id=document_id,
            job_classification=JobClassification(
                source_title=title,
                classification_status="catalog_gap",
                review_reason_codes=["CLASSIFICATION_NOT_RUN"],
            ),
            normalized_requirements=[
                NormalizedRequirement(
                    requirement_id="skill-1",
                    kind="skill",
                    normalized_skills=[
                        NormalizedSkill(
                            source_name="Python",
                            skill_id="skill-python",
                            canonical_name="Python",
                            resolution_status="resolved",
                            resolution_source="explicit_mapping",
                        )
                    ],
                )
            ],
            unresolved_items=[
                UnresolvedItem(
                    source_name=title,
                    item_type="position",
                    reason="position mapping needs review",
                )
            ],
        ),
        review_flags=[
            {
                "jd_id": document_id,
                "requirement_id": "skill-1",
                "issue_type": "manual_sampling",
                "severity": "warning",
                "issue_description": "Sample this normalized skill.",
                "raw_text": "Python",
            }
        ],
        extraction_provider="fake-deepseek",
        model_version="deepseek-test-v1",
        extraction_run_id=f"run-{envelope.source_version[-8:]}",
        extraction_started_at=NOW,
        extraction_finished_at=NOW + timedelta(seconds=1),
    )


class FakeProvider:
    name = "fake-deepseek"
    request_id = "fake-deepseek-request-v1"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, envelope):
        self.calls += 1
        return _bundle(envelope)


def _use_cases(provider: FakeProvider | None = None) -> ExtractionTaskUseCases:
    return ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        provider or FakeProvider(),
        3,
    )


def _source_and_task(
    envelope: CrawlerJDEnvelopeV1 | None = None,
    provider: FakeProvider | None = None,
    *,
    seed_catalog: bool = True,
):
    if seed_catalog:
        with SessionLocal() as session:
            if session.get(Skill, "skill-python") is None:
                session.add(
                    Skill(
                        id="skill-python",
                        skill_name="Python",
                        category="programming_language",
                    )
                )
                session.commit()
    envelope = envelope or _envelope()
    source_result = SourceJDUseCases(
        lambda: SqlAlchemySourceJDUnitOfWork(SessionLocal)
    ).import_source_jd(envelope)
    use_cases = _use_cases(provider)
    task = use_cases.create_extraction_task(source_result.source_jd_version_id, "llm")
    succeeded = use_cases.run_extraction_task(task.id)
    return use_cases, source_result, succeeded


def test_succeeded_task_imports_draft_and_review_task_without_publication():
    use_cases, source, task = _source_and_task()

    draft = use_cases.import_extraction_bundle(task.id)

    assert draft.source_jd_id == source.source_jd_id
    assert draft.source_jd_version_id == source.source_jd_version_id
    assert draft.extraction_task_id == task.id
    assert draft.workflow_status == "draft"
    assert draft.need_review is True
    with SessionLocal() as session:
        jd = session.get(JobDescription, draft.jd_id)
        result = session.get(JDParseResult, draft.parse_result_id)
        review = session.get(ReviewTask, draft.review_task_id)
        assert jd.parse_status == "completed"
        assert jd.extraction_bundle_version == "extracted-jd-bundle-v1"
        assert result.workflow_status == "draft"
        assert result.need_review is True
        assert review.status == "pending"
        assert review.object_type == "jd_parse_result"
        assert session.query(KnowledgeGraphEntityMapping).count() == 0


def test_explicit_position_binding_is_applied_only_during_draft_creation():
    use_cases, _, task = _source_and_task()
    with SessionLocal() as session:
        row = session.get(ExtractionTask, task.id)
        payload = dict(row.bundle_payload)
        normalized = dict(payload["normalized_result"])
        normalized["job_classification"] = {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": "Backend Engineer",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端开发工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件研发",
            "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.93}],
            "confidence": 0.93,
            "classification_status": "resolved",
            "evidence_refs": ["skill-1"],
            "classification_policy_version": "position-classifier.v3.0",
        }
        payload["normalized_result"] = normalized
        row.bundle_payload = payload
        session.commit()

    draft = use_cases.import_extraction_bundle(
        task.id,
        position_bindings={"BACKEND_ENGINEER": ("main-position-id", "后端开发工程师")},
    )

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        classification = result.normalized_result["job_classification"]
        assert classification["position_id"] == "main-position-id"
        assert classification["position_name"] == "后端开发工程师"
        stored_task = session.get(ExtractionTask, task.id)
        assert (
            stored_task.bundle_payload["normalized_result"]["job_classification"]["position_code"]
            == "BACKEND_ENGINEER"
        )


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_non_succeeded_tasks_are_rejected(status: str):
    envelope = _envelope()
    source = SourceJDUseCases(lambda: SqlAlchemySourceJDUnitOfWork(SessionLocal)).import_source_jd(
        envelope
    )
    use_cases = _use_cases()
    task = use_cases.create_extraction_task(source.source_jd_version_id, "llm")
    with SessionLocal() as session:
        row = session.get(ExtractionTask, task.id)
        row.status = status
        session.commit()

    with pytest.raises(ExtractionDraftNotReady):
        use_cases.import_extraction_bundle(task.id)


def test_repeated_import_is_idempotent_and_does_not_call_provider_or_local_rules(
    monkeypatch,
):
    provider = FakeProvider()
    use_cases, _, task = _source_and_task(provider=provider)
    provider_calls = provider.calls

    def forbidden_local_extract(*args, **kwargs):
        raise AssertionError("local extract_jd must not be called")

    monkeypatch.setattr("app.infrastructure.jd_schema.extract_jd", forbidden_local_extract)
    first = use_cases.import_extraction_bundle(task.id)
    second = use_cases.import_extraction_bundle(task.id)

    assert second == first
    assert provider.calls == provider_calls
    with SessionLocal() as session:
        assert session.query(JobDescription).count() == 1
        assert session.query(JDParseResult).count() == 1
        assert session.query(ReviewTask).count() == 1


def test_bundle_identity_mismatch_is_rejected_without_partial_draft():
    use_cases, _, task = _source_and_task()
    with SessionLocal() as session:
        row = session.get(ExtractionTask, task.id)
        row.bundle_payload = {**row.bundle_payload, "source_record_id": "other-job"}
        session.commit()

    with pytest.raises(ExtractionDraftValidationError, match="source_record_id"):
        use_cases.import_extraction_bundle(task.id)
    with SessionLocal() as session:
        assert session.query(JobDescription).count() == 0
        assert session.query(JDParseResult).count() == 0
        assert session.query(ReviewTask).count() == 0


def test_evidence_and_bundle_review_flags_are_preserved():
    use_cases, _, task = _source_and_task()
    draft = use_cases.import_extraction_bundle(task.id)

    with SessionLocal() as session:
        result = session.get(JDParseResult, draft.parse_result_id)
        evidence = result.extraction_result["requirements"][0]["evidence"]
        assert evidence == task.bundle_payload["extraction_result"]["requirements"][0]["evidence"]
        flags = result.normalized_result["unresolved_items"]
        imported_flag = next(item for item in flags if item["code"] == "manual_sampling")
        assert imported_flag["details"] == task.bundle_payload["review_flags"][0]


def test_new_source_version_creates_new_draft_without_overwriting_published_history():
    provider = FakeProvider()
    first_cases, source_one, task_one = _source_and_task(provider=provider)
    first = first_cases.import_extraction_bundle(task_one.id)
    with SessionLocal() as session:
        old = session.get(JDParseResult, first.parse_result_id)
        old.workflow_status = "published"
        old.need_review = False
        session.commit()

    second_envelope = _envelope("Backend Engineer uses Python and SQL daily", source_version="2")
    source_two = SourceJDUseCases(
        lambda: SqlAlchemySourceJDUnitOfWork(SessionLocal)
    ).import_source_jd(second_envelope)
    second_cases = _use_cases(provider)
    task_two = second_cases.run_extraction_task(
        second_cases.create_extraction_task(source_two.source_jd_version_id, "llm").id
    )
    second = second_cases.import_extraction_bundle(task_two.id)

    assert source_two.source_jd_id == source_one.source_jd_id
    assert source_two.source_jd_version_id != source_one.source_jd_version_id
    assert second.jd_id != first.jd_id
    with SessionLocal() as session:
        old = session.get(JDParseResult, first.parse_result_id)
        new = session.get(JDParseResult, second.parse_result_id)
        assert old.workflow_status == "published"
        assert old.need_review is False
        assert new.workflow_status == "draft"
        assert session.query(JobDescription).count() == 2


def test_import_failure_rolls_back_every_draft_write(monkeypatch):
    use_cases, _, task = _source_and_task()
    original = SqlAlchemyExtractionDraftRepository.add

    def fail_after_add(self, draft):
        original(self, draft)
        raise RuntimeError("forced failure after flush")

    monkeypatch.setattr(SqlAlchemyExtractionDraftRepository, "add", fail_after_add)
    with pytest.raises(RuntimeError, match="forced failure"):
        use_cases.import_extraction_bundle(task.id)
    with SessionLocal() as session:
        assert session.query(JobDescription).count() == 0
        assert session.query(JDParseResult).count() == 0
        assert session.query(ReviewTask).count() == 0


def _headers() -> dict[str, str]:
    payload = {
        "role": "developer",
        "username": "draft_import_user",
        "password": "password123",
        "email": "draft_import_user@example.com",
        "phone": "13800000001",
    }
    assert create_internal_user(payload["username"], payload["role"])
    login = client.post(
        "/api/v1/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_import_and_draft_query_apis_require_authentication():
    use_cases, source, task = _source_and_task()
    app.dependency_overrides[get_extraction_task_use_cases] = lambda: use_cases
    import_path = f"/api/v1/extraction-tasks/{task.id}/import-draft"
    assert client.post(import_path).status_code == 401
    headers = _headers()

    imported = client.post(import_path, headers=headers)
    fetched = client.get(f"/api/v1/extraction-tasks/{task.id}/draft", headers=headers)
    listed = client.get(
        f"/api/v1/source-jd-versions/{source.source_jd_version_id}/drafts",
        headers=headers,
    )

    assert imported.status_code == fetched.status_code == listed.status_code == 200
    assert fetched.json()["data"]["jd_id"] == imported.json()["data"]["jd_id"]
    assert listed.json()["data"][0]["jd_id"] == imported.json()["data"]["jd_id"]
