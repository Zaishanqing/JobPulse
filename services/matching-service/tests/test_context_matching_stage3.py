from __future__ import annotations

from copy import deepcopy

from app.application.evaluation import (
    MatchEvaluationService,
    attach_semantic_responsibility_candidates,
)
from app.domain.context_matching import (
    ContextMatchingConfig,
    _candidate_texts,
    _same_experience,
    _text_match,
    evaluate_projects,
    evaluate_scenarios,
)
from app.domain.evaluation import ResponsibilityResult
from app.domain.matching import MatchingAlgorithmConfig, build_match_evaluation
from app.domain.profiles import (
    CVMatchProfile,
    Evidence,
    ExperienceFeature,
    MatchFeature,
    PositionMatchProfile,
)
from app.domain.scoring import ScoringConfig, score_match_evaluation
from app.domain.semantic_retrieval import SemanticRetrievalEvidence


def test_text_matching_rejects_substring_and_low_coverage_false_positives():
    config = ContextMatchingConfig()

    status, rules, _ = _text_match("Java", "JavaScript", config)
    assert (status, rules) == ("not_observed", ())

    status, rules, _ = _text_match("Design backend services", "Design reports", config)
    assert (status, rules) == ("not_observed", ())


def test_text_matching_accepts_meaningful_partial_coverage():
    status, rules, confidence = _text_match(
        "Design backend services", "Develop backend services", ContextMatchingConfig()
    )
    assert status == "partial"
    assert "keyword_overlap" in rules
    assert confidence > 0


def _refresh(payload: dict) -> dict:
    payload["profile_version"] = "profile-source.v1"
    return payload


def _context_payloads(cv_payload: dict, position_payload: dict, overrides: dict):
    cv = deepcopy(cv_payload)
    position = deepcopy(position_payload)
    cv["projects"] = deepcopy(overrides["cv"]["projects"])
    cv["match_features"].extend(deepcopy(overrides["cv"]["match_features"]))
    for key, value in overrides["position"].items():
        position[key] = deepcopy(value)
    return _refresh(cv), _refresh(position)


def _models(cv_payload: dict, position_payload: dict):
    return (
        CVMatchProfile.model_validate(cv_payload),
        PositionMatchProfile.model_validate(position_payload),
    )


def _evaluate(cv_payload: dict, position_payload: dict):
    return MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )


def test_full_responsibility_project_and_scenario_match(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )

    result = _evaluate(cv_payload, position_payload)

    responsibility = result.responsibility_results[0]
    assert responsibility.match_status == "matched"
    assert responsibility.matching_rules == ("normalized_text_exact",)
    assert responsibility.position_evidence
    assert responsibility.candidate_evidence
    project = result.project_results[0]
    assert project.match_status == "matched"
    assert project.covered_skill_ids == ("skill_python",)
    assert "project_task_overlap" in project.matching_rules
    assert "project_role_overlap" in project.matching_rules
    assert "project_achievement_evidence" in project.matching_rules
    assert project.candidate_achievements == ("Reduced latency by 30%",)
    assert {item.match_status for item in result.scenario_results} == {"matched"}
    assert result.responsibility_coverage == 1.0
    assert result.project_coverage == 1.0
    assert result.scenario_coverage == 1.0


def test_responsibility_partial_and_not_observed(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    position_payload["core_responsibilities"] = ["Design backend services"]
    position_payload["evidence_refs"] = [
        {
            "source_id": "jd:responsibility:partial",
            "quote": "Design backend services",
            "start": 0,
            "end": 23,
            "alignment": "exact",
            "occurrence_index": 0,
        }
    ]
    _refresh(position_payload)

    result = _evaluate(cv_payload, position_payload)

    assert result.responsibility_results[0].match_status == "partial"
    assert "keyword_overlap" in result.responsibility_results[0].matching_rules
    assert result.responsibility_coverage == 0.5

    cv_payload["work_experiences"] = []
    cv_payload["projects"][0]["responsibilities"] = ["Analyze reports"]
    _refresh(cv_payload)
    result = _evaluate(cv_payload, position_payload)
    assert result.responsibility_results[0].match_status == "not_observed"
    assert result.responsibility_coverage == 0.0


def test_candidate_evidence_missing_cannot_be_matched(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv_payload["projects"][0]["evidence_refs"] = []
    cv_payload["work_experiences"] = []
    for feature in cv_payload["match_features"]:
        if feature["source_object_id"] == "project_context_001":
            feature["evidence_refs"] = []
    _refresh(cv_payload)

    result = _evaluate(cv_payload, position_payload)

    assert result.responsibility_results[0].match_status == "unknown"
    assert result.responsibility_results[0].reason_code == (
        "CANDIDATE_EVIDENCE_UNKNOWN"
    )
    assert result.project_results[0].match_status == "unknown"
    assert result.project_results[0].reason_code == "PROJECT_EVIDENCE_UNKNOWN"
    assert all(
        not item.candidate_evidence
        for item in result.responsibility_results + result.project_results
    )


def test_semantic_responsibility_candidates_remain_shadow_only(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)
    baseline = _evaluate(cv_payload, position_payload)
    position_evidence = position.evidence_refs[0]

    def evidence(source_id: str, quote: str) -> Evidence:
        return Evidence(
            source_id=source_id,
            quote=quote,
            start=0,
            end=len(quote),
            alignment="exact",
            occurrence_index=0,
        )

    strong = SemanticRetrievalEvidence(
        query_fragment_id="jd-fragment-1",
        candidate_fragment_id="cv-fragment-strong",
        query_fragment_type="responsibility",
        candidate_fragment_type="work_experience",
        candidate_source_id=cv.cv_id,
        similarity=0.86,
        rank=2,
        rerank_score=0.90,
        evidence_ref=evidence("cv:semantic:strong", "Build backend services"),
        position_evidence_ref=position_evidence,
        profile_version=cv.profile_version,
        embedding_model="fixture-model",
        embedding_revision="fixture-revision",
        retrieval_trace_id="trace-strong",
    )
    highest_similarity = strong.model_copy(
        update={
            "candidate_fragment_id": "cv-fragment-similarity",
            "similarity": 0.95,
            "rerank_score": None,
            "evidence_ref": evidence(
                "cv:semantic:similarity", "Build backend services"
            ),
            "retrieval_trace_id": "trace-similarity",
        }
    )
    low_score = strong.model_copy(
        update={
            "candidate_fragment_id": "cv-fragment-low",
            "similarity": 0.40,
            "rerank_score": None,
            "retrieval_trace_id": "trace-low",
        }
    )
    wrong_fragment_type = strong.model_copy(
        update={
            "candidate_fragment_id": "cv-fragment-skill",
            "candidate_fragment_type": "skill",
            "retrieval_trace_id": "trace-skill",
        }
    )
    unrelated = strong.model_copy(
        update={
            "candidate_fragment_id": "cv-fragment-unrelated",
            "evidence_ref": evidence("cv:semantic:unrelated", "Unrelated text"),
            "retrieval_trace_id": "trace-unrelated",
        }
    )
    extra_result = ResponsibilityResult(
        requirement_id="responsibility:2",
        position_requirement="Unrelated responsibility",
        candidate_experience_id=None,
        candidate_experience=None,
        match_status="not_observed",
        reason_code="RESPONSIBILITY_NOT_OBSERVED",
        confidence=0.0,
    )

    attached = attach_semantic_responsibility_candidates(
        baseline.responsibility_results + (extra_result,),
        (
            strong,
            highest_similarity,
            low_score,
            wrong_fragment_type,
            unrelated,
        ),
        cv,
        position,
    )

    result = attached[0]
    assert result.match_status == baseline.responsibility_results[0].match_status
    assert result.candidate_evidence == baseline.responsibility_results[0].candidate_evidence
    assert result.semantic_candidate_evidence == (
        highest_similarity.evidence_ref,
        strong.evidence_ref,
    )
    assert result.semantic_candidate_score == 0.95
    assert result.candidate_feature_id == "cv-fragment-similarity"
    assert result.embedding_model == "fixture-model"
    assert result.embedding_version == "fixture-revision"
    assert result.semantic_reason_code == "RESPONSIBILITY_SEMANTIC_CANDIDATE"
    assert attached[1] == extra_result


def test_project_name_similarity_alone_never_matches(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    project = cv_payload["projects"][0]
    project.update(
        {
            "role": None,
            "responsibilities": [],
            "business_scenarios": [],
            "tool_skill_ids": [],
        }
    )
    cv_payload["match_features"] = [
        item
        for item in cv_payload["match_features"]
        if item["source_object_id"] != "project_context_001"
    ]
    name_feature = deepcopy(context_overrides_json["cv"]["match_features"][0])
    name_feature.update(
        {
            "feature_id": "feature_project_name_only",
            "feature_type": "experience",
            "source_scope": "project:project_context_001:name",
            "raw_text": "Backend Engineer",
        }
    )
    cv_payload["match_features"].append(name_feature)
    _refresh(cv_payload)
    cv, position = _models(cv_payload, position_payload)

    result = evaluate_projects(cv, position, ContextMatchingConfig())[0]

    assert result.match_status == "not_observed"
    assert result.matching_rules == ()
    assert result.reason_code == "PROJECT_NOT_OBSERVED"
    assert result.candidate_experience_id is None
    assert result.candidate_evidence == ()


def test_standard_position_project_matches_cross_taxonomy_canonical_skill_name(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv_payload["skills"][0]["skill_id"] = "legacy_python"
    cv_payload["capability_profiles"][0]["skill_id"] = "legacy_python"
    cv_payload["projects"][0].update(
        {
            "role": None,
            "responsibilities": ["Analyze reports"],
            "business_scenarios": [],
            "tool_skill_ids": ["legacy_python"],
        }
    )
    cv_payload["work_experiences"] = []
    cv_payload["match_features"] = [
        item
        for item in cv_payload["match_features"]
        if item["source_object_id"] != "project_context_001"
    ]
    _refresh(cv_payload)
    cv, position = _models(cv_payload, position_payload)

    standard = build_match_evaluation(
        cv,
        position,
        MatchingAlgorithmConfig(),
        target_type="standard_position",
    ).project_results[0]
    enterprise = build_match_evaluation(
        cv,
        position,
        MatchingAlgorithmConfig(),
        target_type="enterprise_job",
    ).project_results[0]

    assert standard.match_status == "partial"
    assert standard.covered_skill_ids == ("skill_python",)
    assert standard.matching_rules == ("standard_skill_canonical_name_overlap",)
    assert standard.candidate_experience_id == "project_context_001"
    assert enterprise.match_status == "not_observed"
    assert enterprise.covered_skill_ids == ()


def test_standard_position_applied_experience_includes_work_history(
    ready_cv_json, ready_position_json
):
    cv, position = _models(deepcopy(ready_cv_json), deepcopy(ready_position_json))

    standard = build_match_evaluation(
        cv,
        position,
        MatchingAlgorithmConfig(),
        target_type="standard_position",
    ).project_results[0]
    enterprise = build_match_evaluation(
        cv,
        position,
        MatchingAlgorithmConfig(),
        target_type="enterprise_job",
    ).project_results[0]

    assert standard.match_status == "matched"
    assert standard.candidate_experience_id == "work_fixture_001"
    assert standard.covered_skill_ids == ("skill_python",)
    assert enterprise.match_status == "unknown"
    assert enterprise.candidate_experience_id is None


def test_industry_and_business_scenario_exact_and_normalized_match(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    position_payload["industries"]["values"] = ["INTERNET"]
    position_payload["business_scenarios"]["values"] = ["high-concurrency"]
    _refresh(position_payload)
    cv, position = _models(cv_payload, position_payload)

    results = evaluate_scenarios(cv, position, ContextMatchingConfig())

    assert len(results) == 2
    assert {item.match_status for item in results} == {"matched"}
    assert all(item.position_evidence for item in results)
    assert all(item.candidate_evidence for item in results)


def test_context_unknown_and_unresolved_are_not_not_observed(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv_payload["projects"] = []
    cv_payload["match_features"] = [
        item
        for item in cv_payload["match_features"]
        if item["feature_type"] not in {"experience", "task"}
    ]
    _refresh(cv_payload)
    cv, position = _models(cv_payload, position_payload)

    scenario_results = evaluate_scenarios(cv, position, ContextMatchingConfig())
    project_results = evaluate_projects(cv, position, ContextMatchingConfig())
    assert {item.match_status for item in scenario_results} == {"unknown"}
    assert project_results[0].match_status == "unknown"

    unresolved_cv_payload = deepcopy(cv_payload)
    unresolved_cv_payload["unresolved_items"] = [
        {
            "item_id": "unresolved_industry",
            "item_type": "industry",
            "raw_value": "internet",
            "reason": "taxonomy_no_match",
            "evidence_refs": [],
        }
    ]
    _refresh(unresolved_cv_payload)
    unresolved_cv = CVMatchProfile.model_validate(unresolved_cv_payload)
    scenario_results = evaluate_scenarios(
        unresolved_cv, position, ContextMatchingConfig()
    )
    industry = next(item for item in scenario_results if item.scenario_type == "industry")
    assert industry.match_status == "unresolved"
    assert industry.reason_code == "SCENARIO_UNRESOLVED"


def test_context_results_extend_second_stage_without_changing_existing_fields(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)

    result = build_match_evaluation(cv, position, MatchingAlgorithmConfig())

    assert result.evaluation_status == "completed"
    assert result.required_skill_coverage == 1.0
    assert result.bonus_skill_coverage == 0.0
    assert result.hard_constraint_pass_rate == 1.0
    assert len(result.hard_constraint_results) == 6
    assert len(result.skill_results) == 2
    assert result.responsibility_results
    assert result.project_results
    assert result.scenario_results


def test_every_matched_or_partial_context_result_has_bilateral_evidence(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    result = _evaluate(cv_payload, position_payload)

    context_results = (
        result.responsibility_results
        + result.project_results
        + result.scenario_results
    )
    assert context_results
    for item in context_results:
        if item.match_status in {"matched", "partial"}:
            assert item.position_evidence
            assert item.candidate_evidence


def test_native_scoped_feature_binds_to_work_and_project_experience():
    work_feature = MatchFeature(
        feature_id="feature_work_task",
        document_id="cv_000001",
        side="cv",
        feature_type="task",
        source_object_id="work_001",
        source_scope="work_experience:work_001:responsibility",
        raw_text="负责后端服务开发",
        resolution_status="resolved",
        taxonomy_version="taxonomy.v1",
        derivation_version="cv-match-feature.v1",
    )
    work = ExperienceFeature(
        experience_id="cv_000001:work_experience:1",
        kind="work",
        responsibilities=("负责后端服务开发",),
    )
    project_feature = MatchFeature(
        feature_id="feature_project_description",
        document_id="cv_000001",
        side="cv",
        feature_type="task",
        source_object_id="proj_001",
        source_scope="project_experience:proj_001:description",
        raw_text="使用 Java 构建订单系统",
        resolution_status="resolved",
        taxonomy_version="taxonomy.v1",
        derivation_version="cv-match-feature.v1",
    )
    project = ExperienceFeature(
        experience_id="cv_000001:project_experience:1",
        kind="project",
        responsibilities=("使用 Java 构建订单系统",),
    )

    assert _same_experience(work_feature, work)
    assert not _same_experience(work_feature, project)
    assert _same_experience(project_feature, project)


def test_candidate_modes_are_distinct_and_combined_deduplicates(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)
    structured = _candidate_texts(
        cv,
        ContextMatchingConfig(responsibility_candidate_mode="structured_sentence"),
    )
    features = _candidate_texts(
        cv,
        ContextMatchingConfig(responsibility_candidate_mode="match_feature"),
    )
    combined = _candidate_texts(
        cv,
        ContextMatchingConfig(responsibility_candidate_mode="combined"),
    )

    assert structured
    assert features
    assert {item.text for item in structured} != {item.text for item in features}
    assert len(combined) == len(
        {(item.experience_id, item.text) for item in combined}
    )
    assert {item.text for item in combined} == {
        item.text for item in structured + features
    }


def test_responsibility_disabled_preserves_denominator(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)
    config = ContextMatchingConfig(responsibility_matching_enabled=False)
    evaluation = build_match_evaluation(
        cv,
        position,
        MatchingAlgorithmConfig(context=config),
    )
    score = score_match_evaluation(evaluation, cv, position, ScoringConfig())

    assert len(evaluation.responsibility_results) == len(
        position.core_responsibilities
    )
    assert all(
        item.match_status == "not_observed"
        and item.reason_code == "RESPONSIBILITY_MECHANISM_DISABLED"
        for item in evaluation.responsibility_results
    )
    assert "responsibilities" in score.expected_dimensions
    responsibility_dimension = next(
        item
        for item in score.dimension_scores
        if item.dimension == "responsibilities"
    )
    assert responsibility_dimension.applicable_count == len(
        evaluation.responsibility_results
    )


def test_pure_context_disabled_keeps_responsibility_and_drops_project_scenario(
    ready_cv_json, ready_position_json, context_overrides_json
):
    cv_payload, position_payload = _context_payloads(
        ready_cv_json, ready_position_json, context_overrides_json
    )
    cv, position = _models(cv_payload, position_payload)
    config = ContextMatchingConfig(
        context_matching_enabled=False,
        responsibility_matching_enabled=True,
        responsibility_candidate_mode="combined",
    )
    candidates = _candidate_texts(cv, config)
    evaluation = build_match_evaluation(
        cv,
        position,
        MatchingAlgorithmConfig(context=config),
    )

    assert candidates
    assert evaluation.responsibility_results
    assert len(evaluation.responsibility_results) == len(
        position.core_responsibilities
    )
    assert evaluation.project_results == ()
    assert evaluation.scenario_results == ()
