from __future__ import annotations

from copy import deepcopy
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.contract_mapping import map_cv_bundle, map_position_bundle
from app.application.evaluation import MatchEvaluationService
from app.application.integration import ContractIntegrationService
from app.application.learning_paths import LearningPathService
from app.application.validation import ProfileValidationService
from app.bootstrap.application import create_app
from app.domain.privacy import find_pii
from app.infrastructure.http_sources import HttpCVProfileSource
from app.infrastructure.memory_sources import (
    InMemoryCVProfileSource,
    InMemoryPositionProfileSource,
)
from app.infrastructure.relation_sources import InMemorySkillRelationSource
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError


def _service(cv_payload: dict, position_payload: dict, relations=()):
    evaluation = MatchEvaluationService(relation_source=InMemorySkillRelationSource(relations))
    learning = LearningPathService(evaluation)
    return ContractIntegrationService(
        InMemoryCVProfileSource({"cv-ref": cv_payload}),
        InMemoryPositionProfileSource({"position-ref": position_payload}),
        evaluation,
        learning,
    )


def test_async_task_reference_resolution_uses_strict_upstream_mapping(
    upstream_cv_anonymized, upstream_position_anonymized
):
    service = _service(upstream_cv_anonymized, upstream_position_anonymized)
    payload, code, message = service.resolve_task_payload(
        {"cv_id": "cv-ref", "position_id": "position-ref"}
    )
    assert code is None and message is None
    assert payload is not None
    assert payload["cv_profile"]["contract_version"] == "cv-match-profile.v1"
    assert (
        payload["position_profile"]["contract_version"]
        == "position-match-profile.v1"
    )
    assert payload["cv_profile"]["evidence_refs"]


def test_async_task_reference_resolution_rejects_dirty_contract_without_task_payload(
    upstream_cv_anonymized, upstream_position_anonymized
):
    dirty = deepcopy(upstream_cv_anonymized)
    dirty["contract_version"] = "unsupported"
    payload, code, message = _service(
        dirty, upstream_position_anonymized
    ).resolve_task_payload({"cv_id": "cv-ref", "position_id": "position-ref"})
    assert payload is None
    assert code == "UPSTREAM_CONTRACT_INCOMPATIBLE"
    assert message == "one or more upstream contracts could not be mapped"


def test_anonymized_upstream_contracts_map_without_losing_versions_or_evidence(
    upstream_cv_anonymized, upstream_position_anonymized
):
    assert find_pii(upstream_cv_anonymized) == ()
    assert find_pii(upstream_position_anonymized) == ()

    cv_result = map_cv_bundle(upstream_cv_anonymized)
    position_result = map_position_bundle(upstream_position_anonymized)

    assert cv_result.value is not None
    assert position_result.value is not None
    assert cv_result.issues == ()
    assert position_result.issues == ()
    cv = cv_result.value
    position = position_result.value
    assert cv.taxonomy_version == "position-taxonomy.v3.0.0"
    assert "features=cv-match-feature.v1.3" in cv.derivation_version
    assert cv.verification_snapshot_id == "cv_verify_snapshot_20260727_001"
    assert cv.skills[0].resolution_source == "canonical_name"
    assert cv.capability_profiles[0].demonstrated_level == "proficient"
    assert cv.capability_evidence_links[0].evidence_refs
    assert cv.match_features[0].evidence_refs
    assert position.graph_version == "graph-integration-v1"
    assert position.quality_context.snapshot_id == "quality_snapshot_20260727_001"
    assert position.required_skills[0].skill_id == "LANG_PYTHON"
    assert position.required_skills[0].required_level == "proficient"
    assert position.required_skills[0].evidence_refs
    assert {item.contract_version for item in cv_result.versions} == {
        "cv-matching-input-bundle.v1",
        "CVMatchFeatureResult",
        "CVCapabilityVerificationResult",
    }


def test_contract_version_and_required_field_incompatibilities_return_issue_list(
    upstream_cv_anonymized, upstream_position_anonymized
):
    cv = deepcopy(upstream_cv_anonymized)
    cv["normalization"]["skills"][0].pop("normalization_confidence")
    cv_result = map_cv_bundle(cv)
    assert cv_result.value is None
    assert {item.code for item in cv_result.issues} == {"CV_NORMALIZATION_CONFIDENCE_MISSING"}

    cv_without_source = deepcopy(upstream_cv_anonymized)
    cv_without_source["normalization"]["skills"][0].pop("resolution_source")
    source_result = map_cv_bundle(cv_without_source)
    assert source_result.value is None
    assert {item.code for item in source_result.issues} == {
        "CV_NORMALIZATION_RESOLUTION_SOURCE_MISSING"
    }

    position = deepcopy(upstream_position_anonymized)
    position["standard_position"]["taxonomy_version"] = "taxonomy-incompatible"
    position_result = map_position_bundle(position)
    assert position_result.value is None
    assert "POSITION_TAXONOMY_VERSION_MISMATCH" in {item.code for item in position_result.issues}

    wrong_version = deepcopy(upstream_position_anonymized)
    wrong_version["jd_extraction"]["schema_version"] = "v1"
    schema_result = map_position_bundle(wrong_version)
    assert schema_result.value is None
    assert {item.code for item in schema_result.issues} == {"UPSTREAM_SCHEMA_INVALID"}


def test_pii_is_rejected_before_domain_mapping(upstream_cv_anonymized):
    payload = deepcopy(upstream_cv_anonymized)
    payload["phone"] = "13800138000"

    result = map_cv_bundle(payload)

    assert result.value is None
    assert result.issues
    assert all(item.code == "PII_FORBIDDEN" for item in result.issues)


def test_unresolved_is_preserved_and_requires_review_not_reported_as_missing(
    upstream_cv_anonymized,
):
    payload = deepcopy(upstream_cv_anonymized)
    payload["normalization"]["skills"][0].update(
        {
            "skill_id": None,
            "canonical_name": None,
            "resolution_status": "unresolved",
            "normalization_confidence": None,
            "resolution_source": "unresolved",
        }
    )
    payload["unresolved_items"] = [
        {
            "item_id": "unresolved_skill_001",
            "item_type": "skill",
            "raw_value": "Anonymous Emerging Skill",
            "reason": "taxonomy mapping not reviewed",
            "evidence": [],
        }
    ]

    mapped = map_cv_bundle(payload)

    assert mapped.value is not None
    assert mapped.value.skills[0].resolution_status == "unresolved"
    assert mapped.value.unresolved_items[0].item_id == "unresolved_skill_001"
    validation = ProfileValidationService().validate_cv(mapped.value.model_dump(mode="json"))
    assert validation.profile_status == "review_required"


def test_full_contract_pipeline_is_stable_and_keeps_unknown_missing_distinct(
    upstream_cv_anonymized,
    upstream_position_anonymized,
    upstream_relations_anonymized,
):
    service = _service(
        upstream_cv_anonymized,
        upstream_position_anonymized,
        upstream_relations_anonymized,
    )

    first = service.run({"cv_id": "cv-ref", "position_id": "position-ref"})
    second = service.run({"cv_id": "cv-ref", "position_id": "position-ref"})

    assert first == second
    assert first.integration_status == "completed"
    assert first.contract_issues == ()
    assert first.evaluation is not None
    assert first.evaluation.evaluation_status == "completed"
    assert first.evaluation.final_match_result is not None
    assert first.gap_analysis is not None
    assert first.gap_analysis.generation_status == "completed"
    required = next(
        item for item in first.evaluation.skill_results if item.importance_level == "required"
    )
    bonus = next(
        item for item in first.evaluation.skill_results if item.importance_level == "bonus"
    )
    assert required.match_status == "matched"
    assert required.match_type == "exact"
    assert bonus.match_status == "partial"
    assert bonus.match_type == "transferable"
    business = next(
        item
        for item in first.evaluation.scenario_results
        if item.scenario_type == "business_scenario"
    )
    assert business.match_status == "unknown"
    assert not any(
        item.match_status == "missing" and item.requirement_id == business.requirement_id
        for item in first.evaluation.skill_results
    )
    assert first.cv_profile.profile_version == second.cv_profile.profile_version
    assert first.position_profile.profile_version == (
        second.position_profile.profile_version
    )


def test_http_adapter_maps_timeout_invalid_response_and_http_error(monkeypatch):
    class TimeoutClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, headers):
            raise httpx.ReadTimeout("timeout", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", TimeoutClient)
    adapter = HttpCVProfileSource("http://cv", "/contracts/cv", timeout_seconds=1)
    try:
        adapter.fetch_cv_profile("cv-ref")
    except UpstreamTimeoutError as exc:
        assert "contracts/cv" in str(exc)
    else:
        raise AssertionError("timeout was not mapped")

    class ErrorResponse:
        status_code = 503

        def raise_for_status(self):
            request = httpx.Request("GET", "http://cv/contracts/cv/cv-ref")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    class ErrorClient(TimeoutClient):
        def get(self, url, headers):
            return ErrorResponse()

    monkeypatch.setattr(httpx, "Client", ErrorClient)
    try:
        adapter.fetch_cv_profile("cv-ref")
    except UpstreamResponseError as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("HTTP error was not mapped")

    class InvalidJsonResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid JSON")

    class InvalidJsonClient(TimeoutClient):
        def get(self, url, headers):
            return InvalidJsonResponse()

    monkeypatch.setattr(httpx, "Client", InvalidJsonClient)
    try:
        adapter.fetch_cv_profile("cv-ref")
    except UpstreamResponseError as exc:
        assert exc.status_code is None
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("invalid JSON was not mapped")


def test_relation_transport_failure_is_a_stable_integration_rejection(
    upstream_cv_anonymized, upstream_position_anonymized
):
    class TimeoutRelationSource:
        def fetch_relations(self, skill_ids):
            raise UpstreamTimeoutError("timeout fetching skill relations")

    evaluation = MatchEvaluationService(relation_source=TimeoutRelationSource())
    service = ContractIntegrationService(
        InMemoryCVProfileSource({"cv-ref": upstream_cv_anonymized}),
        InMemoryPositionProfileSource(
            {"position-ref": upstream_position_anonymized}
        ),
        evaluation,
        LearningPathService(evaluation),
    )

    result = service.run({"cv_id": "cv-ref", "position_id": "position-ref"})

    assert result.integration_status == "rejected"
    assert result.error_code == "UPSTREAM_TIMEOUT"
    assert result.contract_issues[0].path == "$.upstream"


def test_end_to_end_integration_api_runs_evaluation_scoring_gaps_and_path(
    upstream_cv_anonymized,
    upstream_position_anonymized,
    upstream_relations_anonymized,
):
    application = create_app(
        cv_source=InMemoryCVProfileSource({"cv-ref": upstream_cv_anonymized}),
        position_source=InMemoryPositionProfileSource(
            {"position-ref": upstream_position_anonymized}
        ),
        relation_source=InMemorySkillRelationSource(upstream_relations_anonymized),
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/integrations/evaluate",
            json={"cv_id": "cv-ref", "position_id": "position-ref"},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["integration_status"] == "completed"
    assert data["evaluation"]["final_match_result"]["overall_score"] is not None
    gap = data["gap_analysis"]
    assert gap["learning_path"] == [] or (
        gap.get("minimal_action_set") is not None
        and gap["minimal_action_set"]["status"]
        in {"no_positive_actions", "unreachable", "budget_excluded"}
    )
    assert data["source_versions"][0]["snapshot_id"] == ("cv_verify_snapshot_20260727_001")


def test_what_if_api_maps_upstream_bundles_and_accepts_empty_control(
    upstream_cv_anonymized,
    upstream_position_anonymized,
):
    cv = map_cv_bundle(upstream_cv_anonymized).value
    position = map_position_bundle(upstream_position_anonymized).value
    assert cv is not None and position is not None
    baseline = MatchEvaluationService().evaluate(
        {
            "cv_profile": cv.model_dump(mode="json"),
            "position_profile": position.model_dump(mode="json"),
        },
        include_semantic=False,
    )

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/what-if",
            json={
                "baseline_evaluation": baseline.model_dump(mode="json"),
                "cv_profile": upstream_cv_anonymized,
                "position_profile": upstream_position_anonymized,
                "actions": [],
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generation_status"] == "completed"
    assert data["score_delta"] == 0
    assert data["baseline_score"] == data["scenario_score"]


def test_learning_path_api_maps_upstream_bundles_before_strict_profile_validation(
    upstream_cv_anonymized,
    upstream_position_anonymized,
):
    cv = map_cv_bundle(upstream_cv_anonymized).value
    position = map_position_bundle(upstream_position_anonymized).value
    assert cv is not None and position is not None
    evaluation = MatchEvaluationService().evaluate(
        {
            "cv_profile": cv.model_dump(mode="json"),
            "position_profile": position.model_dump(mode="json"),
        },
        include_semantic=False,
    )

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/learning-paths",
            json={
                "evaluation": evaluation.model_dump(mode="json"),
                "cv_profile": upstream_cv_anonymized,
                "position_profile": upstream_position_anonymized,
                "time_budget_hours": 40,
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generation_status"] == "completed"
    assert data["error_code"] is None
    assert data["minimal_action_set"]["status"] in {
        "reached",
        "no_positive_actions",
        "position_evidence_insufficient",
        "unreachable",
        "budget_excluded",
    }


def test_evidence_deletion_api_maps_upstream_bundles(
    upstream_cv_anonymized,
    upstream_position_anonymized,
):
    cv = map_cv_bundle(upstream_cv_anonymized).value
    position = map_position_bundle(upstream_position_anonymized).value
    assert cv is not None and position is not None
    baseline = MatchEvaluationService().evaluate(
        {
            "cv_profile": cv.model_dump(mode="json"),
            "position_profile": position.model_dump(mode="json"),
        },
        include_semantic=False,
    )
    source_id = next(
        item.candidate_evidence[0].source_id
        for item in baseline.skill_results
        if item.importance_level == "required" and item.candidate_evidence
    )

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/explanation-deletions",
            json={
                "baseline_evaluation": baseline.model_dump(mode="json"),
                "cv_profile": upstream_cv_anonymized,
                "position_profile": upstream_position_anonymized,
                "deletion_kind": "critical",
                "evidence_source_ids": [source_id],
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["generation_status"] == "completed"


def test_structured_ids_with_version_patterns_pass_contract_mapping(
    upstream_cv_anonymized, upstream_position_anonymized,
):
    """CV/Position bundles with versioned document_ids must map without PII issues."""
    VID = "cv-extraction-2026-0728-001"
    cv = deepcopy(upstream_cv_anonymized)
    cv["structure"]["document_id"] = VID
    cv["normalization"]["document_id"] = VID
    cv["match_features"]["document_id"] = VID
    cv["capabilities"]["document_id"] = VID
    # Per-item document_id fields must also match
    for feat in cv["match_features"]["features"]:
        feat["document_id"] = VID
    for prof in cv["capabilities"]["profiles"]:
        prof["document_id"] = VID
    for link in cv["capabilities"]["evidence_links"]:
        link["document_id"] = VID

    cv_result = map_cv_bundle(cv)
    assert cv_result.value is not None, (
        f"Expected successful mapping, got issues: {cv_result.issues}"
    )
    assert cv_result.issues == ()

    PID = "position-extraction-2026-0728-005"
    pos = deepcopy(upstream_position_anonymized)
    pos["jd_extraction"]["document_id"] = PID
    pos["jd_normalization"]["document_id"] = PID

    pos_result = map_position_bundle(pos)
    assert pos_result.value is not None, (
        f"Expected successful mapping, got issues: {pos_result.issues}"
    )
    assert pos_result.issues == ()


def _with_experience_dates(
    payload: dict, start: str | None, end: str | None
) -> dict:
    payload = deepcopy(payload)
    work = payload["structure"]["work_experiences"][0]
    work["start_date"] = start
    work["end_date"] = end
    return payload


def test_cv_mapping_supports_year_precision_interval(upstream_cv_anonymized):
    mapped = map_cv_bundle(
        _with_experience_dates(upstream_cv_anonymized, "2019", "2023")
    )

    assert mapped.value is not None
    work = mapped.value.work_experiences[0]
    assert work.start_date == date(2019, 1, 1)
    assert work.end_date == date(2024, 1, 1)
    assert mapped.value.unresolved_items == ()
    assert mapped.issues == ()


def test_cv_mapping_supports_month_precision_interval(upstream_cv_anonymized):
    mapped = map_cv_bundle(
        _with_experience_dates(upstream_cv_anonymized, "2021.07", "2022.12")
    )

    assert mapped.value is not None
    work = mapped.value.work_experiences[0]
    assert work.start_date == date(2021, 7, 1)
    assert work.end_date == date(2023, 1, 1)
    assert mapped.value.unresolved_items == ()
    assert mapped.issues == ()


@pytest.mark.parametrize("end_value", ["至今", "present", "current", ""])
def test_cv_mapping_supports_open_ended_end_date(
    upstream_cv_anonymized, end_value
):
    mapped = map_cv_bundle(
        _with_experience_dates(upstream_cv_anonymized, "2023.01", end_value)
    )

    assert mapped.value is not None
    work = mapped.value.work_experiences[0]
    assert work.start_date == date(2023, 1, 1)
    assert work.end_date == date(2026, 7, 27)
    assert mapped.value.unresolved_items == ()
    assert mapped.issues == ()


def test_cv_mapping_treats_year_level_as_start_year(upstream_cv_anonymized):
    mapped = map_cv_bundle(
        _with_experience_dates(upstream_cv_anonymized, "2020级", "2023")
    )

    assert mapped.value is not None
    work = mapped.value.work_experiences[0]
    assert work.start_date == date(2020, 1, 1)
    assert work.end_date == date(2024, 1, 1)
    assert mapped.value.unresolved_items == ()
    assert mapped.issues == ()


def test_cv_mapping_keeps_dirty_dates_unresolved(upstream_cv_anonymized):
    mapped = map_cv_bundle(
        _with_experience_dates(upstream_cv_anonymized, "2023.13", "至今")
    )

    assert mapped.value is not None
    assert any(
        item.item_type == "experience_date" and item.raw_value == "2023.13"
        for item in mapped.value.unresolved_items
    )
    assert any(
        item.code == "CV_DATE_PRECISION_UNSUPPORTED" for item in mapped.issues
    )
    validation = ProfileValidationService().validate_cv(
        mapped.value.model_dump(mode="json")
    )
    assert validation.profile_status == "review_required"
