from __future__ import annotations

from copy import deepcopy

from app.application.evaluation import MatchEvaluationService
from app.domain.matching import (
    MatchingAlgorithmConfig,
    build_match_evaluation,
    evaluate_hard_constraints,
    evaluate_skills,
)
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.infrastructure.memory_sources import (
    InMemoryCVProfileSource,
    InMemoryPositionProfileSource,
)


def _refresh(payload: dict) -> dict:
    payload["profile_version"] = "profile-source.v1"
    return payload


def _models(cv_payload: dict, position_payload: dict):
    return (
        CVMatchProfile.model_validate(cv_payload),
        PositionMatchProfile.model_validate(position_payload),
    )


def _evaluation(cv_payload: dict, position_payload: dict):
    return MatchEvaluationService().evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )


def test_all_hard_constraints_pass_and_absent_type_is_not_required(
    ready_cv_json, ready_position_json
):
    result = _evaluation(ready_cv_json, ready_position_json)

    assert result.evaluation_status == "completed"
    assert {item.status for item in result.hard_constraint_results} == {"pass"}
    assert result.hard_constraint_pass_rate == 1.0
    assert result.summary is not None
    assert result.summary.hard_constraint_pass_count == 6

    position_without_hard = deepcopy(ready_position_json)
    position_without_hard["hard_conditions"] = []
    _refresh(position_without_hard)
    result = _evaluation(ready_cv_json, position_without_hard)
    assert len(result.hard_constraint_results) == 6
    assert {item.status for item in result.hard_constraint_results} == {"not_required"}
    assert result.hard_constraint_pass_rate is None


def test_explicit_hard_constraint_failures(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_cv_json)
    changed["education"][0]["degree_level"] = "associate"
    changed["certificates"][0]["name"] = "OTHER"
    changed["match_features"][0]["canonical_name"] = "Beijing"
    changed["match_features"][0]["raw_text"] = "Beijing"
    _refresh(changed)

    result = _evaluation(changed, ready_position_json)
    by_type = {item.constraint_type: item for item in result.hard_constraint_results}

    assert by_type["education"].status == "fail"
    assert by_type["certificate"].status == "fail"
    assert by_type["location"].reason_code == "CONSTRAINT_NOT_SATISFIED"
    assert result.hard_constraint_pass_rate == 0.5


def test_candidate_absent_fields_are_unknown_not_fail(
    ready_cv_json, ready_position_json
):
    changed = deepcopy(ready_cv_json)
    changed["education"] = []
    changed["work_experiences"] = []
    changed["certificates"] = []
    changed["languages"] = []
    changed["match_features"] = []
    _refresh(changed)
    cv, position = _models(changed, ready_position_json)

    results = evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig())
    by_type = {item.constraint_type: item for item in results}

    assert by_type["education"].status == "unknown"
    assert by_type["education"].reason_code == "EDUCATION_NOT_OBSERVED"
    assert by_type["experience"].status == "unknown"
    assert by_type["experience"].reason_code == "EXPERIENCE_NOT_OBSERVED"
    assert by_type["certificate"].status == "unknown"
    assert by_type["language"].status == "unknown"
    assert by_type["location"].status == "unknown"


def test_zero_year_experience_threshold_is_trivially_satisfied(
    ready_cv_json, ready_position_json
):
    changed_cv = deepcopy(ready_cv_json)
    changed_cv["work_experiences"] = []
    _refresh(changed_cv)
    changed_position = deepcopy(ready_position_json)
    for condition in changed_position["hard_conditions"]:
        if condition["condition_type"] == "experience":
            condition["value"] = "0 years"
    _refresh(changed_position)
    cv, position = _models(changed_cv, changed_position)

    results = evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig())
    experience = next(
        item for item in results if item.constraint_type == "experience"
    )

    assert experience.status == "pass"
    assert experience.reason_code == "CONSTRAINT_SATISFIED"


def test_unresolved_hard_fields_are_not_missing(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_cv_json)
    changed["education"][0]["degree_level"] = None
    changed["education"][0]["resolution_status"] = "unresolved"
    _refresh(changed)
    cv = CVMatchProfile.model_validate(changed)
    position = PositionMatchProfile.model_validate(ready_position_json)

    results = evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig())

    education = next(item for item in results if item.constraint_type == "education")
    assert education.status == "unresolved"
    assert education.reason_code == "CANDIDATE_VALUE_UNRESOLVED"


def test_required_skill_exact_match_and_bonus_missing_are_separate(
    ready_cv_json, ready_position_json
):
    result = _evaluation(ready_cv_json, ready_position_json)
    required = next(
        item for item in result.skill_results if item.importance_level == "required"
    )
    bonus = next(item for item in result.skill_results if item.importance_level == "bonus")

    assert required.match_status == "matched"
    assert required.reason_code == "EXACT_SKILL_LEVEL_MET"
    assert required.candidate_demonstrated_level == "proficient"
    assert required.candidate_evidence[0].source_id == "cv:work:1"
    assert bonus.match_status == "missing"
    assert bonus.reason_code == "BONUS_SKILL_NOT_OBSERVED"
    assert result.required_skill_coverage == 1.0
    assert result.bonus_skill_coverage == 0.0
    assert result.summary is not None
    assert result.summary.required_skill_missing_count == 0
    assert result.summary.bonus_skill_missing_count == 1


def test_exact_canonical_name_reconciles_taxonomy_id_versions(
    ready_cv_json, ready_position_json
):
    changed = deepcopy(ready_position_json)
    changed["required_skills"][0]["skill_id"] = "position-taxonomy-python"
    _refresh(changed)

    result = _evaluation(ready_cv_json, changed)

    required = next(
        item for item in result.skill_results if item.importance_level == "required"
    )
    assert required.match_status == "matched"
    assert required.reason_code == "EXACT_SKILL_LEVEL_MET"


def test_skill_level_below_requirement_is_weak(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_position_json)
    changed["required_skills"][0]["required_level"] = "expert"
    _refresh(changed)

    result = _evaluation(ready_cv_json, changed)
    required = result.skill_results[0]

    assert required.match_status == "matched"
    assert required.reason_code == "EXACT_SKILL_PRESENT_LEVEL_BELOW"
    assert required.proficiency_satisfied is False
    assert required.skill_present is True
    assert result.required_skill_coverage == 1.0


def test_declared_without_experience_is_declared_only(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_cv_json)
    changed["capability_profiles"] = []
    changed["capability_evidence_links"] = []
    changed["skills"][0]["verification_status"] = "not_observed"
    changed["skills"][0]["demonstrated_level"] = "unknown"
    _refresh(changed)

    result = _evaluation(changed, ready_position_json)

    assert result.skill_results[0].match_status == "declared_only"
    assert result.skill_results[0].reason_code == "SKILL_DECLARED_WITHOUT_EVIDENCE"


def test_required_skill_absence_is_missing(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_cv_json)
    changed["skills"] = []
    changed["capability_profiles"] = []
    changed["capability_evidence_links"] = []
    _refresh(changed)

    result = _evaluation(changed, ready_position_json)

    required = next(
        item for item in result.skill_results if item.importance_level == "required"
    )
    assert required.match_status == "missing"
    assert required.reason_code == "REQUIRED_SKILL_NOT_OBSERVED"


def test_skill_unknown_unresolved_and_partial_confidence_are_distinct(
    ready_cv_json, ready_position_json
):
    unknown_payload = deepcopy(ready_cv_json)
    capability = unknown_payload["capability_profiles"][0]
    capability["demonstrated_level"] = "unknown"
    _refresh(unknown_payload)
    unknown_cv, position = _models(unknown_payload, ready_position_json)
    unknown = evaluate_skills(unknown_cv, position, MatchingAlgorithmConfig())[0]
    assert unknown.match_status == "unknown"

    unresolved_payload = deepcopy(ready_cv_json)
    capability = unresolved_payload["capability_profiles"][0]
    capability["resolution_status"] = "unresolved"
    capability["verification_status"] = "unresolved"
    _refresh(unresolved_payload)
    unresolved_cv = CVMatchProfile.model_validate(unresolved_payload)
    unresolved = evaluate_skills(
        unresolved_cv, position, MatchingAlgorithmConfig()
    )[0]
    assert unresolved.match_status == "unresolved"
    assert unresolved.reason_code == "CANDIDATE_SKILL_UNRESOLVED"

    partial_payload = deepcopy(ready_cv_json)
    partial_payload["capability_profiles"][0]["verification_status"] = (
        "partially_supported"
    )
    _refresh(partial_payload)
    partial_cv = CVMatchProfile.model_validate(partial_payload)
    partial = evaluate_skills(partial_cv, position, MatchingAlgorithmConfig())[0]
    assert partial.match_status == "matched"
    assert partial.confidence == 0.675


def test_no_required_level_uses_verified_evidence(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_position_json)
    changed["required_skills"][0]["required_level"] = None
    _refresh(changed)

    result = _evaluation(ready_cv_json, changed)

    assert result.skill_results[0].match_status == "matched"
    assert result.skill_results[0].reason_code == "EXACT_SKILL_EVIDENCE_PRESENT"


def test_same_inputs_are_deterministic_and_versions_are_recorded(
    ready_cv_json, ready_position_json
):
    first = _evaluation(ready_cv_json, ready_position_json)
    second = _evaluation(deepcopy(ready_cv_json), deepcopy(ready_position_json))

    first_data = first.model_dump(mode="json")
    second_data = second.model_dump(mode="json")
    first_data.pop("evaluation_id")
    second_data.pop("evaluation_id")
    first_data["final_match_result"].pop("source_evaluation_id", None)
    second_data["final_match_result"].pop("source_evaluation_id", None)
    assert first_data == second_data
    assert first.cv_profile_id == ready_cv_json["cv_id"]
    assert first.position_profile_id == ready_position_json["position_id"]
    cv, position = _models(ready_cv_json, ready_position_json)
    rebuilt = build_match_evaluation(cv, position, MatchingAlgorithmConfig())
    from app.application.responsibility_policy import ResponsibilityDecisionPolicy

    rebuilt = rebuilt.model_copy(
        update={
            "responsibility_results": ResponsibilityDecisionPolicy().apply(
                rebuilt.responsibility_results,
                cv,
                position,
            )
        }
    )
    from app.domain.scoring import score_match_evaluation

    rebuilt = rebuilt.model_copy(
        update={
            "final_match_result": score_match_evaluation(
                rebuilt,
                cv,
                position,
            )
        }
    )
    assert rebuilt.model_copy(update={"evaluation_id": first.evaluation_id}) == first


def test_non_ready_and_version_changes_are_business_inputs(
    ready_cv_json, ready_position_json
):
    non_ready = deepcopy(ready_cv_json)
    non_ready["review_status"] = "pending"
    _refresh(non_ready)
    result = _evaluation(non_ready, ready_position_json)
    assert result.evaluation_status == "completed"
    assert result.information_sufficient is False
    assert result.final_match_result.recommendation_level == (
        "insufficient_information"
    )

    changed = deepcopy(ready_cv_json)
    changed["source_version"] = "cv-source.v2"
    changed["profile_version"] = "cv-source.v2"
    result = _evaluation(changed, ready_position_json)
    assert result.evaluation_status == "completed"
    assert result.cv_profile_id == ready_cv_json["cv_id"]


def test_gate_rejects_missing_profiles_fingerprints_and_requirements(
    ready_cv_json, ready_position_json
):
    service = MatchEvaluationService()
    assert service.evaluate([]).error_code == "EVALUATION_REQUEST_INVALID"
    assert service.evaluate({}).error_code == "CV_PROFILE_NOT_FOUND"
    assert service.evaluate({"cv_profile": ready_cv_json}).error_code == (
        "POSITION_PROFILE_NOT_FOUND"
    )

    missing_cv_version = deepcopy(ready_cv_json)
    missing_cv_version["source_version"] = None
    missing_cv_version["profile_version"] = None
    assert _evaluation(missing_cv_version, ready_position_json).error_code == (
        "CV_PROFILE_INVALID"
    )

    missing_position_version = deepcopy(ready_position_json)
    missing_position_version["source_version"] = None
    missing_position_version["profile_version"] = None
    assert _evaluation(ready_cv_json, missing_position_version).error_code == (
        "POSITION_PROFILE_INVALID"
    )

    empty = deepcopy(ready_position_json)
    empty["hard_conditions"] = []
    empty["required_skills"] = []
    empty["preferred_skills"] = []
    _refresh(empty)
    assert _evaluation(ready_cv_json, empty).error_code == "POSITION_REQUIREMENTS_EMPTY"


def test_pii_is_rejected_before_matching(ready_cv_json, ready_position_json):
    changed = deepcopy(ready_cv_json)
    changed["evidence_refs"] = [
        {
            "source_id": "cv:bad",
            "quote": "candidate@example.com",
            "start": 0,
            "end": 21,
            "alignment": "exact",
            "occurrence_index": 0,
        }
    ]
    _refresh(changed)

    result = _evaluation(changed, ready_position_json)

    assert result.evaluation_status == "rejected"
    assert result.error_code == "CV_PROFILE_CONTAINS_PII"


def test_fixed_contract_fixtures_flow_through_memory_adapters(
    ready_cv_json, ready_position_json
):
    cv_source = InMemoryCVProfileSource({"cv_fixture_001": ready_cv_json})
    position_source = InMemoryPositionProfileSource(
        {"position_fixture_001": ready_position_json}
    )

    result = _evaluation(
        cv_source.fetch_cv_profile("cv_fixture_001"),
        position_source.fetch_position_profile("position_fixture_001"),
    )

    assert result.evaluation_status == "completed"


def test_verified_status_without_evidence_is_unknown(
    ready_cv_json, ready_position_json
):
    changed = deepcopy(ready_cv_json)
    changed["capability_evidence_links"][0]["evidence_refs"] = []
    _refresh(changed)

    result = _evaluation(changed, ready_position_json)

    assert result.skill_results[0].match_status == "unknown"
    assert result.skill_results[0].reason_code == "CANDIDATE_EVIDENCE_UNKNOWN"


def test_unknown_and_unresolved_are_excluded_from_coverage_denominators(
    ready_cv_json, ready_position_json
):
    unknown_payload = deepcopy(ready_cv_json)
    unknown_payload["capability_profiles"][0]["demonstrated_level"] = "unknown"
    _refresh(unknown_payload)
    cv, position = _models(unknown_payload, ready_position_json)

    evaluation = build_match_evaluation(cv, position, MatchingAlgorithmConfig())

    assert evaluation.required_skill_coverage is None
    assert evaluation.bonus_skill_coverage == 0.0
    assert sum(
        item.match_status == "unknown" for item in evaluation.skill_results
    ) == 1
    assert evaluation.unknown_count == 1 + sum(
        item.match_status == "unknown"
        for item in (
            evaluation.responsibility_results
            + evaluation.project_results
            + evaluation.scenario_results
        )
    )
    assert evaluation.summary is not None
    assert evaluation.summary.coverage_denominator_policy == (
        "exclude_unknown_unresolved_and_not_required"
    )

    unresolved_position_payload = deepcopy(ready_position_json)
    unresolved_position_payload["preferred_skills"][0].update(
        {
            "skill_id": None,
            "canonical_name": None,
            "resolution_status": "unresolved",
        }
    )
    _refresh(unresolved_position_payload)
    unresolved_position = PositionMatchProfile.model_validate(
        unresolved_position_payload
    )
    ready_cv = CVMatchProfile.model_validate(ready_cv_json)
    evaluation = build_match_evaluation(
        ready_cv, unresolved_position, MatchingAlgorithmConfig()
    )

    assert evaluation.required_skill_coverage == 1.0
    assert evaluation.bonus_skill_coverage is None
    assert evaluation.unresolved_count == 1


def test_language_below_required_level_is_partial(
    ready_cv_json, ready_position_json
):
    changed = deepcopy(ready_cv_json)
    changed["languages"][0]["proficiency"] = "basic"
    _refresh(changed)
    cv, position = _models(changed, ready_position_json)

    results = evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig())
    language = next(item for item in results if item.constraint_type == "language")

    assert language.status == "partial"
    assert language.reason_code == "CONSTRAINT_PARTIALLY_SATISFIED"
