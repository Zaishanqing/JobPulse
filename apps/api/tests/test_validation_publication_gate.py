from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from app.contexts.data_validation.domain import FindingSeverity
from app.contexts.governance_feedback import ManageReviews
from app.contexts.jd_lifecycle import Actor, JDApplicationError, JDUseCases
from app.domain.accounts import AccountActor
from app.infrastructure.data_validation import (
    SqlAlchemyValidationGovernanceAdapter,
)
from app.infrastructure.data_validation import frozen_catalog_identity
from app.infrastructure.governance import SqlAlchemyGovernanceUnitOfWork
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure.jd_repository import SqlAlchemyJDUoW
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.main import app
from app.models.data_validation import (
    DataValidationTask,
    ValidationReport,
    ValidatedBundleSnapshot,
)
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.outbox_message import OutboxMessage
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent
from app.models.skill import Skill
from app.models.skill_catalog_version import SkillCatalogVersion
from app.models.standard_position import StandardPosition
from app.workers.validation_tasks import ValidationWorkerResult
from jobgraph_contracts.normalization_v2 import JobClassification
from tests.runtime_database import reset_database_data, SessionLocal
from tests.test_extraction_draft_import import FakeProvider
from tests.test_extraction_validation_bridge import _run_validation, _succeed


PUBLISHER = Actor("validation-publication-admin", "admin")
REVIEWER = AccountActor("validation-governance-reviewer", "reviewer")


def _skill_catalog_snapshot_v1() -> dict:
    return {
        "schema": "skill-catalog-snapshot.v1",
        "taxonomy_catalog_version": "skill-taxonomy-catalog.v1",
        "skills": [
            {
                "skill_id": "skill-python",
                "catalog_code": "LANG_PYTHON",
                "skill_name": "Python",
                "category": "programming_language",
                "description": None,
                "parent_skill_id": None,
                "status": "active",
                "redirect_target_skill_id": None,
            }
        ],
        "aliases": [],
        "classifications": [
            {
                "classification_id": "classification-python-1",
                "skill_id": "skill-python",
                "taxonomy_node_id": "taxonomy-concept-1",
                "facet": "concept_class",
                "code": "technology",
                "name_zh": "技术实体",
                "name_en": "Technology",
                "is_primary": True,
            },
            {
                "classification_id": "classification-python-2",
                "skill_id": "skill-python",
                "taxonomy_node_id": "taxonomy-kind-1",
                "facet": "technology_kind",
                "code": "language",
                "name_zh": "编程与查询语言",
                "name_en": "Programming and query language",
                "is_primary": True,
            },
        ],
        "redirects": [],
    }


def _skill_catalog_snapshot_v2() -> dict:
    snapshot = _skill_catalog_snapshot_v1()
    snapshot["skills"].append(
        {
            "skill_id": "skill-java",
            "catalog_code": "LANG_JAVA",
            "skill_name": "Java",
            "category": "programming_language",
            "description": None,
            "parent_skill_id": None,
            "status": "active",
            "redirect_target_skill_id": None,
        }
    )
    return snapshot


class _ResolvedPositionProvider(FakeProvider):
    def extract(self, envelope):
        bundle = super().extract(envelope)
        normalized = bundle.normalized_result.model_copy(
            update={
                "job_classification": JobClassification(
                    source_title=envelope.job_title_raw,
                    position_code="BACKEND_ENGINEER",
                    position_name="Backend Engineer",
                    family_code="SOFTWARE_ENGINEERING",
                    family_name="软件工程与研发",
                    candidate_positions=[
                        {"position_code": "BACKEND_ENGINEER", "score": 0.93}
                    ],
                    confidence=0.93,
                    classification_status="resolved",
                    evidence_refs=["task-1", "skill-1"],
                )
            }
        )
        return bundle.model_copy(update={"normalized_result": normalized})


def _succeed_v3(mode: str):
    with SessionLocal() as session:
        if session.query(StandardPosition).filter_by(
            position_code="BACKEND_ENGINEER"
        ).one_or_none() is None:
            session.add(
                StandardPosition(
                    id="position-backend-v3",
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
    return _succeed(mode, _ResolvedPositionProvider())


@pytest.fixture(autouse=True)
def reset_database():
    app.dependency_overrides.clear()
    reset_database_data()
    yield
    app.dependency_overrides.clear()
    reset_database_data()


def _jd_use_cases(mode: str) -> JDUseCases:
    return JDUseCases(
        lambda: SqlAlchemyJDUoW(
            SessionLocal,
            data_validation_mode=mode,
        ),
        OpenPyxlJDExporter(),
        VersionedJDSchemaAdapter(),
        data_validation_mode=mode,
    )


def _reviews() -> ManageReviews:
    return ManageReviews(lambda: SqlAlchemyGovernanceUnitOfWork(SessionLocal))


def _review_task(object_type: str, object_id: str) -> ReviewTask:
    with SessionLocal() as session:
        return (
            session.query(ReviewTask)
            .filter(
                ReviewTask.object_type == object_type,
                ReviewTask.object_id == object_id,
            )
            .one()
        )


def _approve_draft(parse_result_id: str) -> None:
    task = _review_task("jd_parse_result", parse_result_id)
    _reviews().transition(REVIEWER, task.id, "claim")
    _reviews().transition(REVIEWER, task.id, "approve", "Draft verified")


def _approve_validation(report_id: str) -> None:
    task = _review_task("data_validation_report", report_id)
    _reviews().transition(REVIEWER, task.id, "claim")
    _reviews().transition(
        REVIEWER,
        task.id,
        "approve",
        "Validation warning accepted",
    )


def _validated_draft(severity: FindingSeverity | None):
    extraction, task = _succeed_v3("enforce")
    result = _run_validation(severity)
    assert result is ValidationWorkerResult.SUCCEEDED
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    _approve_draft(draft.parse_result_id)
    with SessionLocal() as session:
        report = session.query(ValidationReport).one()
        report_id = report.id
    return draft, report_id


def _assert_conflict(parse_result_id: str, detail: str) -> None:
    with pytest.raises(JDApplicationError) as exc_info:
        _jd_use_cases("enforce").publish_parse_result_by_id(
            PUBLISHER,
            parse_result_id,
        )
    assert exc_info.value.error_code == "conflict"
    assert exc_info.value.detail == detail
    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 0
        assert session.query(OutboxMessage).count() == 0
        assert session.get(JDParseResult, parse_result_id).workflow_status == "reviewed"


def test_pass_snapshot_allows_atomic_publication():
    draft, _ = _validated_draft(None)

    publication = _jd_use_cases("enforce").publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )

    assert publication.parse_result_id == draft.parse_result_id
    lineage = publication.snapshot_payload["validation_lineage"]
    assert publication.snapshot_payload["contract_version"] == "jd-publication-snapshot.v3"
    assert lineage["state"] == "present"
    assert lineage["validation_conclusion"] == "pass"
    assert lineage["data_validation_task_id"]
    assert lineage["validation_report_id"]
    assert lineage["validated_bundle_snapshot_id"]
    assert lineage["bundle_id"]
    catalog = publication.snapshot_payload["skill_catalog_snapshot"]
    assert catalog["source"] == "main-system-skill-catalog"
    assert catalog["catalog_version"]
    position_catalog = publication.snapshot_payload["position_catalog_snapshot"]
    assert position_catalog["source"] == "main-system-position-catalog"
    assert position_catalog["catalog_version"] == "position-taxonomy.v3.0.0"
    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 1
        assert session.query(OutboxMessage).count() == 1
        assert session.get(JDParseResult, draft.parse_result_id).workflow_status == (
            "published"
        )


def test_warn_requires_existing_human_review_then_allows_publication():
    draft, report_id = _validated_draft(FindingSeverity.WARN)

    governance = _review_task("data_validation_report", report_id)
    assert governance.status == "pending"
    _assert_conflict(draft.parse_result_id, "validation_review_pending")

    _approve_validation(report_id)
    publication = _jd_use_cases("enforce").publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )

    assert publication.parse_result_id == draft.parse_result_id


def test_block_report_never_allows_enforce_publication():
    extraction, task = _succeed_v3("observe")
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    _approve_draft(draft.parse_result_id)
    result = _run_validation(FindingSeverity.BLOCK)
    assert result is ValidationWorkerResult.SUCCEEDED

    _assert_conflict(draft.parse_result_id, "validation_blocked")
    with SessionLocal() as session:
        governance = (
            session.query(ReviewTask)
            .filter(ReviewTask.object_type == "data_validation_report")
            .one()
        )
        assert governance.priority == "urgent"
        governance_id = governance.id
    _reviews().transition(REVIEWER, governance_id, "claim")
    _reviews().transition(
        REVIEWER,
        governance_id,
        "approve",
        "Approval cannot override a blocking conclusion",
    )
    _assert_conflict(draft.parse_result_id, "validation_blocked")


def test_policy_binding_change_fails_closed():
    draft, _ = _validated_draft(None)
    with SessionLocal() as session:
        task = session.query(DataValidationTask).one()
        task.policy_version = "vpb1:validation-policy-v1:catalog-other"
        session.commit()

    _assert_conflict(draft.parse_result_id, "validation_task_missing")


@pytest.mark.parametrize("corruption", ["report", "snapshot"])
def test_report_or_snapshot_lineage_corruption_fails_closed(corruption: str):
    draft, _ = _validated_draft(None)
    with SessionLocal() as session:
        if corruption == "report":
            report = session.query(ValidationReport).one()
            payload = dict(report.report_payload)
            payload["lineage"] = {
                **payload["lineage"],
                "extraction_task_id": "corrupt-extraction-task",
            }
            report.report_payload = payload
        else:
            snapshot = session.query(ValidatedBundleSnapshot).one()
            session.execute(
                text(
                    "UPDATE validated_bundle_snapshots "
                    "SET bundle_id = :fingerprint WHERE id = :id"
                ),
                {
                    "fingerprint": "sha256:" + ("0" * 64),
                    "id": snapshot.id,
                },
            )
        session.commit()

    _assert_conflict(
        draft.parse_result_id,
        "validation_result_inconsistent",
    )


@pytest.mark.parametrize("corruption", ["jd", "parse"])
def test_draft_jd_lineage_corruption_fails_closed(corruption: str):
    draft, _ = _validated_draft(None)
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        if corruption == "jd":
            jd = session.get(JobDescription, parsed.jd_id)
            jd.title = "Content not derived from the validated snapshot"
        else:
            parsed.responsibilities = ["Unvalidated responsibility"]
        session.commit()

    _assert_conflict(
        draft.parse_result_id,
        "validation_result_inconsistent",
    )


def test_validation_governance_ensure_is_idempotent_in_existing_review_system():
    _, report_id = _validated_draft(FindingSeverity.WARN)
    with SessionLocal() as session:
        report = session.get(ValidationReport, report_id)
        adapter = SqlAlchemyValidationGovernanceAdapter(session)
        first = adapter.ensure_for_report(
            validation_report_id=report.id,
            data_validation_task_id=report.data_validation_task_id,
            extraction_task_id=(
                session.query(ValidatedBundleSnapshot).one().extraction_task_id
            ),
            source_jd_version_id=(
                session.query(ValidatedBundleSnapshot).one().source_jd_version_id
            ),
            conclusion=report.conclusion,
        )
        second = adapter.ensure_for_report(
            validation_report_id=report.id,
            data_validation_task_id=report.data_validation_task_id,
            extraction_task_id=(
                session.query(ValidatedBundleSnapshot).one().extraction_task_id
            ),
            source_jd_version_id=(
                session.query(ValidatedBundleSnapshot).one().source_jd_version_id
            ),
            conclusion=report.conclusion,
        )

    assert first.task_id == second.task_id
    assert first.created is False
    assert second.created is False
    with SessionLocal() as session:
        assert (
            session.query(ReviewTask)
            .filter(ReviewTask.object_type == "data_validation_report")
            .count()
            == 1
        )
        assert (
            session.query(ReviewTaskEvent)
            .filter(ReviewTaskEvent.task_id == first.task_id)
            .count()
            == 1
        )


def test_governance_creation_failure_rolls_back_validation_success(
    monkeypatch,
):
    _succeed_v3("enforce")

    def fail_governance(*args, **kwargs):
        raise RuntimeError("governance unavailable")

    monkeypatch.setattr(
        SqlAlchemyValidationGovernanceAdapter,
        "ensure_for_report",
        fail_governance,
    )

    assert (
        _run_validation(FindingSeverity.WARN)
        is ValidationWorkerResult.FAILED
    )
    with SessionLocal() as session:
        task = session.query(DataValidationTask).one()
        assert task.status == "failed"
        assert session.query(ValidationReport).count() == 0
        assert session.query(ValidatedBundleSnapshot).count() == 0
        assert (
            session.query(ReviewTask)
            .filter(ReviewTask.object_type == "data_validation_report")
            .count()
            == 0
        )


@pytest.mark.parametrize("mode", ["off", "observe"])
def test_off_and_observe_keep_existing_publication_behavior(mode: str):
    extraction, task = _succeed_v3(mode)
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    _approve_draft(draft.parse_result_id)

    publication = _jd_use_cases(mode).publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )

    assert publication.parse_result_id == draft.parse_result_id


def test_existing_publication_is_not_rechecked_after_enforce_is_enabled():
    extraction, task = _succeed_v3("off")
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    _approve_draft(draft.parse_result_id)
    first = _jd_use_cases("off").publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )

    repeated = _jd_use_cases("enforce").publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )

    assert repeated.id == first.id
    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 1
        assert session.query(OutboxMessage).count() == 1


def test_concurrent_warn_review_and_publication_cannot_bypass_gate():
    draft, report_id = _validated_draft(FindingSeverity.WARN)
    governance = _review_task("data_validation_report", report_id)
    barrier = Barrier(2)

    def approve():
        barrier.wait()
        _reviews().transition(REVIEWER, governance.id, "claim")
        return _reviews().transition(
            REVIEWER,
            governance.id,
            "approve",
            "Concurrent warning review",
        )

    def publish():
        barrier.wait()
        try:
            return _jd_use_cases("enforce").publish_parse_result_by_id(
                PUBLISHER,
                draft.parse_result_id,
            )
        except JDApplicationError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        approval_future = executor.submit(approve)
        publication_future = executor.submit(publish)
        approval = approval_future.result()
        outcome = publication_future.result()

    assert approval.status == "approved"
    if isinstance(outcome, JDApplicationError):
        assert outcome.error_code == "conflict"
        assert outcome.detail == "validation_review_pending"
        outcome = _jd_use_cases("enforce").publish_parse_result_by_id(
            PUBLISHER,
            draft.parse_result_id,
        )
    assert outcome.parse_result_id == draft.parse_result_id
    with SessionLocal() as session:
        assert session.query(JDPublication).count() == 1
        assert session.query(OutboxMessage).count() == 1


def test_jd_publication_keeps_catalog_identity_used_at_normalization():
    snapshot_v1 = _skill_catalog_snapshot_v1()
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
        session.commit()
        v1_identity = frozen_catalog_identity(session)

    extraction, task = _succeed_v3("enforce")
    assert _run_validation(None) is ValidationWorkerResult.SUCCEEDED
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        assert parsed.execution_metadata["catalog_identity"]["skill"] == v1_identity

    with SessionLocal() as session:
        session.add(
            SkillCatalogVersion(
                version_number=2,
                catalog_version="skill-catalog.v2",
                snapshot=_skill_catalog_snapshot_v2(),
                change_summary={},
                published_by="catalog-admin",
            )
        )
        session.commit()

    _approve_draft(draft.parse_result_id)
    publication = _jd_use_cases("enforce").publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )

    skill_snapshot = publication.snapshot_payload["skill_catalog_snapshot"]
    assert skill_snapshot["catalog_version"] == "skill-catalog.v1"
    assert skill_snapshot["content_hash"] == v1_identity["content_hash"]


def test_unpublished_live_catalog_drift_cannot_masquerade_as_published_version():
    snapshot_v1 = _skill_catalog_snapshot_v1()
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
        session.commit()
        v1_identity = frozen_catalog_identity(session)

    extraction, task = _succeed_v3("enforce")
    with SessionLocal() as session:
        live = session.get(Skill, "skill-python")
        assert live is not None
        live.skill_name = "Python 3"
        session.commit()

    assert _run_validation(None) is ValidationWorkerResult.SUCCEEDED
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    with SessionLocal() as session:
        parsed = session.get(JDParseResult, draft.parse_result_id)
        skill = parsed.normalized_result["normalized_requirements"][0]
        assert skill["canonical_name"] == "Python"
        assert skill["resolution_status"] == "resolved"
        assert (
            parsed.execution_metadata["catalog_identity"]["skill"]
            == v1_identity
        )

    _approve_draft(draft.parse_result_id)
    publication = _jd_use_cases("enforce").publish_parse_result_by_id(
        PUBLISHER,
        draft.parse_result_id,
    )
    skill_snapshot = publication.snapshot_payload["skill_catalog_snapshot"]
    assert skill_snapshot["catalog_version"] == "skill-catalog.v1"
    assert skill_snapshot["content_hash"] == v1_identity["content_hash"]
