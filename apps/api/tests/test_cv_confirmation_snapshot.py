from __future__ import annotations

from dataclasses import replace

import pytest

from app.contexts.cv_ingestion import (
    CVFieldDecision,
    CVReviewConfirmation,
    CVReviewConflict,
)
from app.contexts.cv_ingestion.review import (
    CVReviewRuleViolation,
    apply_field_decisions,
    validate_confirmed_evidence,
)
from app.models.candidate_submission import CandidateSubmission
from app.models.outbox_message import OutboxMessage
from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.resume_skill import ResumeSkill
from app.models.source_cv import CVExtractionTask, ValidatedCVSnapshot
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.main import app
from app.infrastructure.matching_contracts import _skill_feature_id, _snapshot_evidence
from tests.runtime_database import SessionLocal, reset_database_data
from tests.test_cv_ingestion import (
    FakeCVProvider,
    _actor,
    _confirm_review,
    _use_cases,
)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _scheduled(actor, provider=None):
    use_cases = _use_cases(provider or FakeCVProvider())
    scheduled = use_cases.import_and_schedule(
        actor, source_record_id="confirm-001", raw_text="熟练使用 Python"
    )
    return use_cases, scheduled


def _confirmation(task, *, idempotency_key="confirm-key", decisions=()):
    return CVReviewConfirmation(
        expected_review_id=task.review_id,
        idempotency_key=idempotency_key,
        field_decisions=decisions,
        normalization_version=task.execution_metadata["normalization_version"],
        taxonomy_version=task.execution_metadata["taxonomy_version"],
        display_name="智能抽取简历",
    )


def test_confirmed_evidence_matches_nfkc_normalized_raw_text():
    payload = {
        "skills": [
            {
                "item_id": "skill-1",
                "name": "Python",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "Python|FastAPI",
                    "start": 4,
                    "end": 18,
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            }
        ]
    }
    validate_confirmed_evidence(payload, "技术栈：Python｜FastAPI")


def test_confirmed_evidence_rejects_quote_absent_from_normalized_text():
    payload = {
        "skills": [
            {
                "item_id": "skill-1",
                "name": "Python",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "Python+FastAPI",
                    "start": 4,
                    "end": 18,
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            }
        ]
    }
    with pytest.raises(CVReviewRuleViolation, match="does not match"):
        validate_confirmed_evidence(payload, "技术栈：Python｜FastAPI")


def test_confirmed_evidence_accepts_full_width_punctuation_quote():
    quote = "搭建事件流处理管道，完成消息重试和幂等消费。"
    payload = {
        "skills": [
            {
                "item_id": "skill-1",
                "name": "Python",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": quote,
                    "start": 0,
                    "end": len(quote),
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            }
        ]
    }
    validate_confirmed_evidence(payload, quote)


def test_nested_education_field_can_be_corrected_without_replacing_entry():
    evidence = {
        "source_id": "src_0001",
        "quote": "人工智能（硕士）",
        "start": 0,
        "end": 8,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    extraction = {
        "education": [
            {
                "entry_id": "edu_001",
                "school": "某大学",
                "major": "ATH AE Cit)",
                "degree": "unknown",
                "evidence": evidence,
                "field_evidence": [
                    {"field_name": "major", "evidence": evidence},
                    {"field_name": "degree", "evidence": evidence},
                ],
            }
        ]
    }
    confirmed, _ = apply_field_decisions(
        extraction,
        {"normalized_skills": []},
        (
            CVFieldDecision(
                field_id="edu_001:major",
                field_type="major",
                section="education",
                item_id="edu_001",
                field_path="major",
                decision="correct",
                corrected_value="人工智能",
                correction_reason="OCR 误识别",
                evidence_quote="人工智能（硕士）",
                evidence_start=0,
                evidence_end=8,
            ),
        ),
    )

    assert confirmed["education"][0]["school"] == "某大学"
    assert confirmed["education"][0]["major"] == "人工智能"
    assert confirmed["education"][0]["degree"] == "unknown"
    assert confirmed["education"][0]["field_evidence"][0]["evidence"][
        "source_id"
    ] == "user_correction"


def test_missing_patent_placeholder_can_be_added_by_explicit_correction():
    confirmed, _ = apply_field_decisions(
        {"patents": []},
        {"normalized_skills": []},
        (
            CVFieldDecision(
                field_id="new_patent_001:title",
                field_type="title",
                section="patents",
                item_id="new_patent_001",
                field_path="title",
                decision="correct",
                corrected_value="一种多模态情感分析方法",
                correction_reason="根据原图补录 OCR 遗漏标题",
                evidence_quote="专利：一种多模态情感分析方法",
                evidence_start=0,
                evidence_end=16,
            ),
        ),
    )

    patent = confirmed["patents"][0]
    assert patent["entry_id"] == "new_patent_001"
    assert patent["title"] == "一种多模态情感分析方法"
    assert patent["status"] == "unknown"
    assert patent["evidence"]["source_id"] == "user_correction"


def test_missing_education_placeholder_can_be_added_by_structured_correction():
    confirmed, _ = apply_field_decisions(
        {"education": []},
        {"normalized_skills": []},
        (
            CVFieldDecision(
                field_id="new_education_001:school",
                field_type="school",
                section="education",
                item_id="new_education_001",
                field_path="school",
                decision="correct",
                corrected_value="广东工业大学",
                correction_reason="OCR 误识别，根据原图补录",
                evidence_quote="教育经历\n广东工业大学 2024.09",
                evidence_start=0,
                evidence_end=15,
            ),
            CVFieldDecision(
                field_id="new_education_001:degree",
                field_type="degree",
                section="education",
                item_id="new_education_001",
                field_path="degree",
                decision="correct",
                corrected_value="硕士",
                correction_reason="OCR 误识别，根据原图补录",
                evidence_quote="教育经历\n广东工业大学 2024.09",
                evidence_start=0,
                evidence_end=15,
            ),
            CVFieldDecision(
                field_id="new_education_001:date.start",
                field_type="start",
                section="education",
                item_id="new_education_001",
                field_path="date.start",
                decision="correct",
                corrected_value="2024.09",
                correction_reason="OCR 误识别，根据原图补录",
                evidence_quote="教育经历\n广东工业大学 2024.09",
                evidence_start=0,
                evidence_end=15,
            ),
        ),
    )

    education = confirmed["education"][0]
    assert education["entry_id"] == "new_education_001"
    assert education["school"] == "广东工业大学"
    assert education["degree"] == "硕士"
    assert education["date"]["start"] == "2024.09"
    assert education["evidence"]["source_id"] == "user_correction"


def test_missing_education_placeholder_rejects_accept():
    with pytest.raises(CVReviewRuleViolation):
        apply_field_decisions(
            {"education": []},
            {"normalized_skills": []},
            (
                CVFieldDecision(
                    field_id="new_education_001:school",
                    field_type="school",
                    section="education",
                    item_id="new_education_001",
                    field_path="school",
                    decision="accept",
                ),
            ),
        )


def test_missing_education_placeholder_rejects_unknown_field_path():
    with pytest.raises(CVReviewRuleViolation):
        apply_field_decisions(
            {"education": []},
            {"normalized_skills": []},
            (
                CVFieldDecision(
                    field_id="new_education_001:nickname",
                    field_type="nickname",
                    section="education",
                    item_id="new_education_001",
                    field_path="nickname",
                    decision="correct",
                    corrected_value="不应允许",
                    correction_reason="尝试写入约定外字段",
                    evidence_quote="教育经历",
                    evidence_start=0,
                    evidence_end=4,
                ),
            ),
        )


def test_worker_success_creates_pending_review_without_snapshot_resume_or_events():
    actor = _actor("confirm_worker_state")
    use_cases, scheduled = _scheduled(actor)
    completed = use_cases.run(actor, scheduled.cv_extraction_task_id)

    assert completed.status == "succeeded"
    assert completed.confirmation_status == "pending"
    assert completed.review_payload is not None
    assert completed.review_id is not None
    with SessionLocal() as session:
        assert session.query(ValidatedCVSnapshot).count() == 0
        assert session.query(Resume).count() == 0
        assert session.query(OutboxMessage).count() == 0


def test_pending_review_entry_points_to_latest_unconfirmed_task():
    actor = _actor("pending_review_entry")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    # 简历不存在或未确认快照时没有入口
    assert use_cases.pending_review_task_for_resume(actor, "missing-resume") is None
    confirmed = _confirm_review(use_cases, actor, scheduled)
    assert use_cases.pending_review_task_for_resume(actor, confirmed.resume_id) is None
    # 重新抽取跑完后出现新的待审核任务
    fresh = use_cases.reextract(actor, scheduled.cv_extraction_task_id)
    use_cases.run(actor, fresh.task_id)
    pending = use_cases.pending_review_task_for_resume(actor, confirmed.resume_id)
    assert pending is not None
    assert pending.task_id == fresh.task_id
    # 其他用户不可见
    assert (
        use_cases.pending_review_task_for_resume(
            _actor("pending_review_other"), confirmed.resume_id
        )
        is None
    )


def test_pass_result_still_requires_user_confirmation():
    actor = _actor("confirm_pass_pending")
    use_cases, scheduled = _scheduled(actor)
    completed = use_cases.run(actor, scheduled.cv_extraction_task_id)
    assert completed.validation_conclusion == "pass"
    assert completed.confirmation_status == "pending"
    with SessionLocal() as session:
        assert session.query(ValidatedCVSnapshot).count() == 0


def test_stale_review_id_is_rejected():
    actor = _actor("confirm_stale")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    review = use_cases.get_review(actor, scheduled.cv_extraction_task_id)
    stale = _confirmation(review)
    stale = CVReviewConfirmation(
        expected_review_id="stale-review-id",
        idempotency_key=stale.idempotency_key,
    )
    with pytest.raises(CVReviewConflict, match="stale"):
        use_cases.confirm(actor, scheduled.cv_extraction_task_id, stale)


def test_same_idempotency_key_replays_the_original_confirmation():
    actor = _actor("confirm_idempotency_replay")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    review = use_cases.get_review(actor, scheduled.cv_extraction_task_id)
    first = _confirmation(review, idempotency_key="same-key")
    confirmed = use_cases.confirm(actor, scheduled.cv_extraction_task_id, first)
    assert confirmed.snapshot_id

    repeated = CVReviewConfirmation(
        expected_review_id=review.review_id,
        idempotency_key="same-key",
    )
    replayed = use_cases.confirm(actor, scheduled.cv_extraction_task_id, repeated)
    assert replayed.snapshot_id == confirmed.snapshot_id
    with SessionLocal() as session:
        assert session.query(ValidatedCVSnapshot).count() == 1


def test_confirmation_creates_snapshot_resume_and_personal_event():
    original = app.state.container
    app.state.container = replace(
        original,
        resumes=replace(original.resumes, vector_index_enabled=True),
    )
    try:
        actor = _actor("confirm_event")
        use_cases, scheduled = _scheduled(actor)
        use_cases.run(actor, scheduled.cv_extraction_task_id)
        confirmed = _confirm_review(use_cases, actor, scheduled)
    finally:
        app.state.container = original

    assert confirmed.snapshot_revision == 1
    with SessionLocal() as session:
        snapshot = session.get(ValidatedCVSnapshot, confirmed.snapshot_id)
        resume = session.get(Resume, confirmed.resume_id)
        assert snapshot is not None and snapshot.confirmed_at is not None
        assert resume.validated_cv_snapshot_id == snapshot.id
        event = session.query(OutboxMessage).one()
        assert event.payload["snapshot_id"] == snapshot.id
        assert event.payload["vector_event_type"] == "cv_profile_published"
        assert event.payload["source_version"] == (
            f"snapshot={snapshot.id}:{snapshot.snapshot_revision}"
        )


def test_cv_backed_resume_can_be_deleted_without_deleting_source_lineage():
    actor = _actor("confirm_delete_resume")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    confirmed = _confirm_review(use_cases, actor, scheduled)

    with SessionLocal() as session:
        task = session.get(CVExtractionTask, scheduled.cv_extraction_task_id)
        task.resume_id = confirmed.resume_id
        session.commit()

    app.state.container.resumes.delete(actor, confirmed.resume_id)

    with SessionLocal() as session:
        task = session.get(CVExtractionTask, scheduled.cv_extraction_task_id)
        assert session.get(Resume, confirmed.resume_id) is None
        assert task is not None and task.resume_id is None
        assert session.get(ValidatedCVSnapshot, confirmed.snapshot_id) is not None


def test_correction_without_evidence_is_rejected():
    actor = _actor("confirm_correction_no_evidence")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    review = use_cases.get_review(actor, scheduled.cv_extraction_task_id)
    confirmation = CVReviewConfirmation(
        expected_review_id=review.review_id,
        idempotency_key="correction-key",
        field_decisions=(
            CVFieldDecision(
                field_id="skill-1",
                field_type="skill",
                section="skills",
                decision="correct",
                corrected_value="FastAPI",
                correction_reason="user correction",
            ),
        ),
    )
    with pytest.raises(CVReviewRuleViolation, match="evidence"):
        use_cases.confirm(actor, scheduled.cv_extraction_task_id, confirmation)


def test_unknown_decision_keeps_skill_out_of_resolved_matching_fields():
    actor = _actor("confirm_unknown")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    review = use_cases.get_review(actor, scheduled.cv_extraction_task_id)
    confirmation = CVReviewConfirmation(
        expected_review_id=review.review_id,
        idempotency_key="unknown-key",
        field_decisions=(
            CVFieldDecision(
                field_id="skill-1",
                field_type="skill",
                section="skills",
                decision="unknown",
            ),
        ),
        normalization_version=review.execution_metadata["normalization_version"],
        taxonomy_version=review.execution_metadata["taxonomy_version"],
    )
    confirmed = use_cases.confirm(actor, scheduled.cv_extraction_task_id, confirmation)
    with SessionLocal() as session:
        snapshot = session.get(ValidatedCVSnapshot, confirmed.snapshot_id)
        normalized_skill = snapshot.normalized_payload["normalized_skills"][0]
        assert normalized_skill["resolution_status"] == "unresolved"
        assert normalized_skill["normalization_confidence"] is None
        assert normalized_skill["resolution_source"] == "unresolved"
    profile = app.state.container.matching_contracts.reader.cv_profile(
        confirmed.resume_id, snapshot_id=confirmed.snapshot_id
    )
    assert profile["normalization"]["skills"] == []


def test_revision_creates_new_snapshot_and_preserves_old():
    actor = _actor("confirm_revision")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    first = _confirm_review(use_cases, actor, scheduled)
    old = use_cases.get_snapshot(actor, first.snapshot_id)

    confirmation = CVReviewConfirmation(
        expected_review_id=old.snapshot_id,
        idempotency_key="revision-key",
        field_decisions=(
            CVFieldDecision(
                field_id="skill-1",
                field_type="skill",
                section="skills",
                decision="remove",
            ),
        ),
        normalization_version=old.normalization_version,
        taxonomy_version=old.taxonomy_version,
    )
    revised = use_cases.create_revision(actor, first.snapshot_id, confirmation)

    assert revised.snapshot_id != first.snapshot_id
    assert revised.snapshot_revision == 2
    assert revised.supersedes_snapshot_id == first.snapshot_id
    with SessionLocal() as session:
        old_row = session.get(ValidatedCVSnapshot, first.snapshot_id)
        new_row = session.get(ValidatedCVSnapshot, revised.snapshot_id)
        resume = session.get(Resume, revised.resume_id)
        assert old_row.id == first.snapshot_id
        assert new_row.supersedes_snapshot_id == first.snapshot_id
        assert resume.validated_cv_snapshot_id == revised.snapshot_id


def test_cv_contract_structure_evidence_is_a_list():
    actor = _actor("cv_contract_structure_evidence")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    confirmed = _confirm_review(use_cases, actor, scheduled)

    profile = app.state.container.matching_contracts.reader.cv_profile(
        confirmed.resume_id, snapshot_id=confirmed.snapshot_id
    )
    structure = profile["structure"]
    for section in ("education", "work_experiences", "projects", "certificates", "languages"):
        for item in structure[section]:
            assert isinstance(item["evidence"], list)
            assert item["evidence"]


def test_cv_matching_contract_preserves_normalization_provenance():
    actor = _actor("cv_normalization_provenance")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    confirmed = _confirm_review(use_cases, actor, scheduled)

    profile = app.state.container.matching_contracts.reader.cv_profile(
        confirmed.resume_id, snapshot_id=confirmed.snapshot_id
    )

    normalized_skill = profile["normalization"]["skills"][0]
    assert normalized_skill["normalization_confidence"] == 1.0
    assert normalized_skill["resolution_source"] == "canonical_name"


class _LanguageCVProvider(FakeCVProvider):
    def extract(self, *, document_id: str, raw_text: str, progress_callback=None):
        payload = thaw_json_object(
            super().extract(document_id=document_id, raw_text=raw_text)
        )
        quote = "中文"
        start = raw_text.find(quote)
        payload["extraction_result"]["languages"] = [
            {
                "entry_id": "lang-1",
                "language": quote,
                "proficiency": "native",
                "evidence": {
                    "source_document_id": document_id,
                    "source_id": f"{document_id}:lang",
                    "quote": quote,
                    "start": start,
                    "end": start + len(quote),
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            }
        ]
        return freeze_json_object(payload, field="fake_language_cv")


def test_cv_contract_language_evidence_is_a_list():
    actor = _actor("cv_language_evidence")
    use_cases = _use_cases(_LanguageCVProvider())
    scheduled = use_cases.import_and_schedule(
        actor,
        source_record_id="confirm-001",
        raw_text="熟练使用 Python，中文",
    )
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    confirmed = _confirm_review(use_cases, actor, scheduled)
    profile = app.state.container.matching_contracts.reader.cv_profile(
        confirmed.resume_id, snapshot_id=confirmed.snapshot_id
    )
    languages = profile["structure"]["languages"]
    assert languages
    assert all(
        isinstance(item["evidence"], list) and item["evidence"]
        for item in languages
    )


def test_matching_profile_is_unchanged_by_derived_table_edits():
    actor = _actor("matching_snapshot_direct")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    confirmed = _confirm_review(use_cases, actor, scheduled)
    reader = app.state.container.matching_contracts.reader
    before = reader.cv_profile(confirmed.resume_id, snapshot_id=confirmed.snapshot_id)

    with SessionLocal() as session:
        parse = session.query(ResumeParseResult).one()
        parse.skills = [{"raw_skill": "Mutated", "normalized_skill_id": "mutated"}]
        skill = session.query(ResumeSkill).one()
        skill.raw_skill = "Mutated"
        skill.skill_id = "mutated"
        session.commit()

    after = reader.cv_profile(confirmed.resume_id, snapshot_id=confirmed.snapshot_id)
    assert after == before


def test_candidate_submission_binds_confirmed_snapshot_and_ignores_resume_switch():
    from tests.test_enterprise_candidates import (
        _enterprise_job,
        _headers,
    )

    personal = _actor("candidate_snapshot_personal")
    use_cases, scheduled = _scheduled(personal)
    use_cases.run(personal, scheduled.cv_extraction_task_id)
    confirmed = _confirm_review(use_cases, personal, scheduled)
    resume_id = confirmed.resume_id

    enterprise = _headers("candidate_snapshot_enterprise", "enterprise_user")
    job_id = _enterprise_job(enterprise, "Snapshot Enterprise")
    submission = app.state.container.candidates.submit(
        personal,
        job_id,
        resume_id,
    )
    assert submission.validated_cv_snapshot_id == confirmed.snapshot_id

    old_snapshot = use_cases.get_snapshot(personal, confirmed.snapshot_id)
    revision = use_cases.create_revision(
        personal,
        confirmed.snapshot_id,
        CVReviewConfirmation(
            expected_review_id=old_snapshot.snapshot_id,
            idempotency_key="submission-revision-key",
            field_decisions=(
                CVFieldDecision(
                    field_id="skill-1",
                    field_type="skill",
                    section="skills",
                    decision="remove",
                ),
            ),
            normalization_version=old_snapshot.normalization_version,
            taxonomy_version=old_snapshot.taxonomy_version,
        ),
    )
    assert revision.resume_id == resume_id
    with SessionLocal() as session:
        row = (
            session.query(CandidateSubmission)
            .filter(CandidateSubmission.resume_id == resume_id)
            .one()
        )
        assert row.validated_cv_snapshot_id == confirmed.snapshot_id


def test_cv_contract_skill_feature_id_is_stable_across_snapshot_revisions():
    actor = _actor("stable_skill_feature_id")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    first = _confirm_review(use_cases, actor, scheduled)
    resume_id = first.resume_id
    reader = app.state.container.matching_contracts.reader

    first_profile = reader.cv_profile(resume_id, snapshot_id=first.snapshot_id)
    feature_ids = [
        item["feature_id"]
        for item in first_profile["match_features"]["features"]
    ]
    assert feature_ids
    assert feature_ids == [f"{resume_id}:skill:skill-1"]
    assert not any("feature:snapshot" in item for item in feature_ids)

    old = use_cases.get_snapshot(actor, first.snapshot_id)
    revised = use_cases.create_revision(
        actor,
        first.snapshot_id,
        CVReviewConfirmation(
            expected_review_id=old.snapshot_id,
            idempotency_key="stable-feature-revision-key",
            field_decisions=(),
            normalization_version=old.normalization_version,
            taxonomy_version=old.taxonomy_version,
        ),
    )
    revised_profile = reader.cv_profile(resume_id, snapshot_id=revised.snapshot_id)
    revised_ids = [
        item["feature_id"]
        for item in revised_profile["match_features"]["features"]
    ]
    assert revised_ids == feature_ids


def test_cv_contract_skill_feature_ids_are_unique_by_source_item():
    assert _skill_feature_id("resume-1", "skill-1") != _skill_feature_id(
        "resume-1", "skill-2"
    )
    assert _skill_feature_id("resume-1", "skill-1") == _skill_feature_id(
        "resume-1", "skill-1"
    )


def test_matching_evidence_fallback_never_claims_an_exact_span():
    evidence = _snapshot_evidence(None, "fallback-skill", "Python")

    assert evidence == {
        "source_id": "fallback-skill",
        "quote": "Python",
        "start": None,
        "end": None,
        "alignment": "unresolved",
        "occurrence_index": None,
    }


def test_new_source_version_rebinds_resume_to_latest_snapshot():
    actor = _actor("rebind_resume_source_version")
    use_cases, scheduled = _scheduled(actor)
    use_cases.run(actor, scheduled.cv_extraction_task_id)
    first = _confirm_review(use_cases, actor, scheduled)

    second = use_cases.import_and_schedule(
        actor,
        source_record_id="confirm-001",
        raw_text="熟练使用 Python 与 Docker",
        source_version="2",
    )
    assert second.source_cv_version_id != scheduled.source_cv_version_id
    use_cases.run(actor, second.cv_extraction_task_id)
    review = use_cases.get_review(actor, second.cv_extraction_task_id)
    confirmed = use_cases.confirm(
        actor,
        second.cv_extraction_task_id,
        _confirmation(review, idempotency_key="confirm-key-v2"),
    )

    with SessionLocal() as session:
        resume = session.get(Resume, confirmed.resume_id)
        assert resume.source_cv_version_id == second.source_cv_version_id
        assert resume.validated_cv_snapshot_id == confirmed.snapshot_id
    profile = app.state.container.matching_contracts.reader.cv_profile(
        confirmed.resume_id
    )
    assert profile is not None
