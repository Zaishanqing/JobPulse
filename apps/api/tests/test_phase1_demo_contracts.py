"""Phase-1 contract freeze: competition-demo-v1, RAG, and execution modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobgraph_contracts.cv_extraction_http import CVEvidence
from jobgraph_contracts.demo_manifest import (
    COMPETITION_DEMO_MANIFEST_V1,
    COMPETITION_DEMO_V1,
    CompetitionDemoManifestV1,
)
from jobgraph_contracts.execution_modes import (
    EXECUTION_MODE_SEMANTICS,
    ExecutionMode,
    ExecutionModeResultV1,
)
from jobgraph_contracts.extraction_v2 import Evidence as JDEvidence
from jobgraph_contracts.position_profile import PositionProfileV1, PositionProfileV2
from jobgraph_contracts.published_jd import PUBLISHED_JD_FACT_V3
from jobgraph_contracts.rag import (
    EVIDENCE_RAG_QUERY_VERSION,
    EVIDENCE_RAG_RESPONSE_VERSION,
    EvidenceRAGQueryV1,
    EvidenceRAGResponseV1,
    RAGEvidenceReferenceV1,
)


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "config" / "competition-demo-v1" / "manifest.json"


def _manifest_payload() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _query(**overrides) -> dict:
    payload = {
        "contract_version": EVIDENCE_RAG_QUERY_VERSION,
        "business_object": {
            "object_type": "standard_position",
            "object_id": "position-ai-application-engineer",
        },
        "query_text": "Which skills are required for this position?",
        "evidence_types": ["jd_evidence"],
        "graph_version": "2026-q2",
        "permission": {
            "user_id": "user-demo-001",
            "tenant_ref": "tenant-demo",
            "permission_scope": "competition-demo-v1",
            "assembled_by": "main-system-bff",
        },
    }
    payload.update(overrides)
    return payload


def _reference(**overrides) -> dict:
    payload = {
        "evidence_id": "evidence-jd-003-001",
        "source_object_type": "published_jd_fact",
        "source_object_id": "published-jd-fact-003",
        "source_document_id": "jd-demo-003",
        "quote": "Python and SQL are required.",
        "location_start": 12,
        "location_end": 40,
        "occurrence_index": 0,
        "alignment": "exact",
        "graph_version": "2026-q2",
        "source_version": "published-jd-fact.v2",
        "tenant_ref": "tenant-demo",
        "permission_scope": "competition-demo-v1",
    }
    payload.update(overrides)
    return payload


def _answered_response(**overrides) -> dict:
    payload = {
        "contract_version": EVIDENCE_RAG_RESPONSE_VERSION,
        "status": "answered",
        "answer": "Python and SQL are required by the published position.",
        "references": [_reference()],
        "provider": "rag-provider-demo",
        "model": "rag-model-demo",
        "model_version": "2026-08-05",
        "trace_id": "trace-rag-demo-001",
        "explanation_only": True,
        "version_scope": "single_object",
        "graph_version": "2026-q2",
        "permission": {
            "user_id": "user-demo-001",
            "tenant_ref": "tenant-demo",
            "permission_scope": "competition-demo-v1",
            "assembled_by": "main-system-bff",
        },
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# competition-demo-v1 manifest
# ===========================================================================


class TestCompetitionDemoManifest:
    def test_manifest_file_is_valid_competition_demo_v1(self):
        payload = _manifest_payload()
        manifest = CompetitionDemoManifestV1.model_validate(payload)

        assert manifest.contract_version == COMPETITION_DEMO_MANIFEST_V1
        assert manifest.dataset_version == COMPETITION_DEMO_V1
        assert manifest.demo_only.label == "demo-only"
        assert manifest.demo_only.removed_by == "remove-demo-only"
        assert manifest.demo_only.excluded_from_official_experiments is True
        assert manifest.implementation_status == "loadable_foundation"
        assert len(manifest.jds) >= 1
        assert len(manifest.cvs) >= 1
        assert len(manifest.trend_windows) == 3
        assert len(manifest.graph_versions) >= 1
        assert manifest.published_position.profile_contract == "position-profile.v2"
        assert (
            manifest.matching_target.evaluation_contract
            == "matching-evaluation-result.v1"
        )
        assert manifest.success_case.kind == "success"
        assert manifest.insufficient_evidence_case.kind == "insufficient_evidence"
        assert manifest.success_case.expected_status == "completed"
        assert (
            manifest.insufficient_evidence_case.expected_status
            == "insufficient_evidence"
        )
        assert len(manifest.expected_resources) >= 1
        assert len(manifest.relations) >= 1
        matching_resource = next(
            item
            for item in manifest.expected_resources
            if item.resource_type == "matching_evaluation"
        )
        assert matching_resource.expected_status == "completed"

    def test_manifest_uses_logical_aliases_not_db_ids(self):
        payload = _manifest_payload()
        for version in payload["graph_versions"]:
            assert version["graph_version_id"] is None
        assert all(item["alias"] for item in payload["jds"])
        assert all(item["alias"] for item in payload["cvs"])
        assert all(item["alias"] for item in payload["expected_resources"])

    def test_manifest_contains_success_and_insufficient_cases(self):
        payload = _manifest_payload()
        cases = [payload["success_case"], payload["insufficient_evidence_case"]]
        assert len({item["alias"] for item in cases}) == 2
        assert {item["kind"] for item in cases} == {
            "success",
            "insufficient_evidence",
        }
        assert payload["success_case"]["expected_status"] == "completed"
        assert (
            payload["insufficient_evidence_case"]["expected_status"]
            == "insufficient_evidence"
        )

    def test_manifest_contains_expected_resources_and_relations(self):
        manifest = CompetitionDemoManifestV1.model_validate(_manifest_payload())
        resource_types = {item.resource_type for item in manifest.expected_resources}
        assert {
            "position_family",
            "published_jd_fact",
            "graph_version",
            "position_profile",
            "validated_cv_snapshot",
            "matching_evaluation",
            "trend_report",
            "discovery_snapshot",
            "rag_response",
        } <= resource_types
        relation_types = {item.relation_type for item in manifest.relations}
        assert "publishes_position_profile" in relation_types
        assert "supplies_position_reference" in relation_types
        assert "supplies_analysis_window" in relation_types
        assert "supplies_position_profile_context" in relation_types
        assert "inputs_matching" in relation_types
        assert "maps_to_source_jd" in relation_types
        assert "maps_to_source_cv" in relation_types
        assert "time_range_for_graph_version" in relation_types

    def test_manifest_maps_inputs_and_windows_explicitly(self):
        manifest = CompetitionDemoManifestV1.model_validate(_manifest_payload())
        relations = manifest.relations
        endpoints = {
            *(item.source for item in relations),
            *(item.target for item in relations),
        }
        input_aliases = {
            *(item.alias for item in manifest.jds),
            *(item.alias for item in manifest.cvs),
            *(item.alias for item in manifest.trend_windows),
        }
        assert input_aliases <= endpoints
        mapped_jds = {
            item.source
            for item in relations
            if item.relation_type == "maps_to_source_jd"
        }
        assert mapped_jds == {item.alias for item in manifest.jds}
        mapped_cvs = {
            item.source
            for item in relations
            if item.relation_type == "maps_to_source_cv"
        }
        assert mapped_cvs == {item.alias for item in manifest.cvs}
        window_relations = {
            item.source
            for item in relations
            if item.relation_type == "time_range_for_graph_version"
        }
        assert window_relations == {item.alias for item in manifest.trend_windows}

    def test_manifest_contains_no_privacy_or_secret_markers(self):
        forbidden_keys = {"raw_text", "password", "secret", "private_key", "phone", "email"}
        stack = [json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                assert not (set(value) & forbidden_keys)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    def test_manifest_rejects_unknown_fields(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            CompetitionDemoManifestV1.model_validate(
                {**_manifest_payload(), "unexpected": True}
            )

    def test_manifest_requires_exactly_three_trend_windows(self):
        payload = _manifest_payload()
        payload["trend_windows"] = payload["trend_windows"][:2]
        with pytest.raises(ValidationError):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_missing_graph_version_reference(self):
        payload = _manifest_payload()
        payload["published_position"]["graph_version_alias"] = "graph-missing"
        with pytest.raises(ValidationError, match="graph version"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_overlapping_windows(self):
        payload = _manifest_payload()
        payload["trend_windows"][1]["start"] = "2025-12-31T00:00:00+08:00"
        with pytest.raises(ValidationError, match="must not overlap"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_unordered_windows(self):
        payload = _manifest_payload()
        payload["trend_windows"] = list(reversed(payload["trend_windows"]))
        with pytest.raises(ValidationError, match="ordered by start"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_invalid_window_interval(self):
        payload = _manifest_payload()
        payload["trend_windows"][0]["end"] = "2025-09-30T00:00:00+08:00"
        with pytest.raises(ValidationError, match="start must be before end"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_isolated_input(self):
        payload = _manifest_payload()
        payload["relations"] = [
            item
            for item in payload["relations"]
            if item["alias"] != "rel-input-jd-001"
        ]
        with pytest.raises(ValidationError, match="at least one relation"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_duplicate_relation_alias(self):
        payload = _manifest_payload()
        payload["relations"][1]["alias"] = payload["relations"][0]["alias"]
        with pytest.raises(ValidationError, match="relation aliases must be unique"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_graph_version_position_mismatch(self):
        payload = _manifest_payload()
        payload["graph_versions"][0]["position_alias"] = "position-other"
        with pytest.raises(ValidationError, match="manifest position"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_wrong_case_status(self):
        payload = _manifest_payload()
        payload["success_case"]["expected_status"] = "succeeded"
        with pytest.raises(ValidationError, match="expected_status=completed"):
            CompetitionDemoManifestV1.model_validate(payload)

    def test_manifest_rejects_unknown_relation_target(self):
        payload = _manifest_payload()
        payload["relations"][0]["target"] = "not-defined-anywhere"
        with pytest.raises(ValidationError, match="undefined alias"):
            CompetitionDemoManifestV1.model_validate(payload)


# ===========================================================================
# Evidence RAG contract
# ===========================================================================


class TestEvidenceRAGContract:
    def test_valid_query_accepts_version_and_permission_context(self):
        query = EvidenceRAGQueryV1.model_validate(_query())
        assert query.contract_version == EVIDENCE_RAG_QUERY_VERSION
        assert query.graph_version == "2026-q2"
        assert query.permission.tenant_ref == "tenant-demo"
        assert query.permission.permission_scope == "competition-demo-v1"
        assert query.permission.assembled_by == "main-system-bff"

    def test_query_requires_business_version(self):
        payload = _query()
        del payload["graph_version"]
        with pytest.raises(ValidationError, match="exactly one"):
            EvidenceRAGQueryV1.model_validate(payload)

    def test_query_rejects_conflicting_version_identities(self):
        payload = _query()
        payload["business_version"] = "cv-extraction-http.v2"
        with pytest.raises(ValidationError, match="exactly one"):
            EvidenceRAGQueryV1.model_validate(payload)

    def test_multi_object_query_uses_per_object_graph_versions(self):
        payload = _query(
            business_object={
                "object_type": "standard_position",
                "object_id": "position-a",
                "object_version": "27",
            },
            business_objects=[
                {
                    "object_type": "standard_position",
                    "object_id": "position-a",
                    "object_version": "27",
                },
                {
                    "object_type": "standard_position",
                    "object_id": "position-b",
                    "object_version": "40",
                },
            ],
            version_scope="multi_object",
            graph_version=None,
            graph_version_id=None,
            business_version=None,
        )

        query = EvidenceRAGQueryV1.model_validate(payload)

        assert query.version_scope == "multi_object"
        assert query.graph_version_id is None
        assert [
            (item.object_id, item.object_version)
            for item in query.business_objects or []
        ] == [("position-a", "27"), ("position-b", "40")]

        with pytest.raises(ValidationError, match="global version identity"):
            EvidenceRAGQueryV1.model_validate(
                {**payload, "graph_version_id": 27}
            )

    def test_permission_context_must_be_assembled_by_bff(self):
        query = EvidenceRAGQueryV1.model_validate(_query())
        assert query.permission.assembled_by == "main-system-bff"
        payload = _query()
        payload["permission"]["assembled_by"] = "browser"
        with pytest.raises(ValidationError):
            EvidenceRAGQueryV1.model_validate(payload)

    def test_query_requires_permission_context(self):
        payload = _query()
        del payload["permission"]
        with pytest.raises(ValidationError):
            EvidenceRAGQueryV1.model_validate(payload)

    def test_query_rejects_unknown_fields(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            EvidenceRAGQueryV1.model_validate({**_query(), "unexpected": True})

    def test_answered_response_requires_answer_and_references(self):
        response = EvidenceRAGResponseV1.model_validate(_answered_response())
        assert response.status == "answered"
        assert response.references[0].evidence_id == "evidence-jd-003-001"
        assert response.permission.tenant_ref == "tenant-demo"
        assert response.permission.permission_scope == "competition-demo-v1"
        assert response.permission.user_id == "user-demo-001"

        with pytest.raises(ValidationError, match="reference"):
            EvidenceRAGResponseV1.model_validate(
                {**_answered_response(), "references": []}
            )
        with pytest.raises(ValidationError, match="answer"):
            EvidenceRAGResponseV1.model_validate(
                {**_answered_response(), "answer": None}
            )

    def test_multi_object_response_allows_distinct_reference_graph_versions(self):
        references = [
            _reference(
                evidence_id="evidence-a",
                business_object_id="position-a",
                graph_version=None,
                graph_version_id=27,
            ),
            _reference(
                evidence_id="evidence-b",
                business_object_id="position-b",
                graph_version=None,
                graph_version_id=40,
            ),
        ]

        response = EvidenceRAGResponseV1.model_validate(
            _answered_response(
                version_scope="multi_object",
                graph_version=None,
                graph_version_id=None,
                references=references,
            )
        )

        assert response.version_scope == "multi_object"
        assert response.graph_version_id is None
        assert [
            (reference.business_object_id, reference.graph_version_id)
            for reference in response.references
        ] == [("position-a", 27), ("position-b", 40)]

        with pytest.raises(ValidationError, match="global version identity"):
            EvidenceRAGResponseV1.model_validate(
                _answered_response(
                    version_scope="multi_object",
                    graph_version=None,
                    graph_version_id=27,
                    references=references,
                )
            )

    def test_insufficient_evidence_refuses_to_answer(self):
        payload = _answered_response(
            status="insufficient_evidence",
            answer=None,
            references=[],
            error={
                "code": "EVIDENCE_INSUFFICIENT",
                "message": "No valid evidence covers the query.",
            },
        )
        response = EvidenceRAGResponseV1.model_validate(payload)
        assert response.status == "insufficient_evidence"
        assert response.answer is None
        assert response.references == []
        assert response.error is not None

    def test_insufficient_evidence_rejects_fabricated_answer(self):
        with pytest.raises(ValidationError, match="cannot fabricate"):
            EvidenceRAGResponseV1.model_validate(
                _answered_response(
                    status="insufficient_evidence",
                    references=[],
                    error={
                        "code": "EVIDENCE_INSUFFICIENT",
                        "message": "No valid evidence.",
                    },
                )
            )

    def test_failed_response_requires_error(self):
        with pytest.raises(ValidationError, match="error"):
            EvidenceRAGResponseV1.model_validate(
                {**_answered_response(), "status": "failed", "answer": None, "references": []}
            )

    def test_reference_requires_evidence_identity_source_and_version(self):
        with pytest.raises(ValidationError, match="evidence_id"):
            RAGEvidenceReferenceV1.model_validate(_reference(evidence_id=""))
        with pytest.raises(ValidationError, match="source_object_id"):
            RAGEvidenceReferenceV1.model_validate(_reference(source_object_id=""))
        with pytest.raises(ValidationError, match="source_version"):
            RAGEvidenceReferenceV1.model_validate(_reference(source_version=""))
        with pytest.raises(ValidationError, match="at least one"):
            RAGEvidenceReferenceV1.model_validate(
                _reference(graph_version=None, graph_version_id=None)
            )

    def test_reference_rejects_conflicting_version_identities(self):
        with pytest.raises(ValidationError, match="business_version cannot be combined"):
            RAGEvidenceReferenceV1.model_validate(
                _reference(business_version="cv-extraction-http.v2")
            )

    def test_reference_accepts_graph_version_id_and_name_together(self):
        reference = RAGEvidenceReferenceV1.model_validate(
            _reference(graph_version_id=94)
        )

        assert reference.graph_version_id == 94
        assert reference.graph_version == "2026-q2"

    def test_reference_requires_quote_or_location_span(self):
        with pytest.raises(ValidationError, match="quote or location"):
            RAGEvidenceReferenceV1.model_validate(
                _reference(
                    quote=None,
                    location_start=None,
                    location_end=None,
                    alignment="unresolved",
                )
            )
        with pytest.raises(ValidationError, match="exact evidence"):
            RAGEvidenceReferenceV1.model_validate(
                _reference(
                    quote=None,
                    location_start=12,
                    location_end=40,
                    alignment="exact",
                )
            )

    def test_response_rejects_cross_tenant_reference(self):
        with pytest.raises(ValidationError, match="tenant"):
            EvidenceRAGResponseV1.model_validate(
                _answered_response(
                    references=[_reference(tenant_ref="tenant-other")]
                )
            )

    def test_response_rejects_cross_graph_version_reference(self):
        with pytest.raises(ValidationError, match="graph versions"):
            EvidenceRAGResponseV1.model_validate(
                _answered_response(
                    references=[_reference(graph_version="2026-q1")]
                )
            )

    def test_response_rejects_cross_graph_version_id_reference(self):
        payload = _answered_response(
            graph_version_id=3,
            graph_version=None,
            references=[_reference(graph_version=None, graph_version_id=1)],
        )
        with pytest.raises(ValidationError, match="graph versions"):
            EvidenceRAGResponseV1.model_validate(payload)

    def test_response_rejects_cross_business_version_reference(self):
        payload = _answered_response(
            business_version="cv-extraction-http.v2",
            graph_version=None,
            references=[
                _reference(
                    graph_version=None,
                    business_version="cv-extraction-http.v1",
                )
            ],
        )
        with pytest.raises(ValidationError, match="business versions"):
            EvidenceRAGResponseV1.model_validate(payload)

    def test_response_rejects_cross_permission_scope_reference(self):
        with pytest.raises(ValidationError, match="permission scopes"):
            EvidenceRAGResponseV1.model_validate(
                _answered_response(
                    references=[
                        _reference(permission_scope="competition-demo-v1-other")
                    ]
                )
            )

    def test_source_cv_business_version_answer_is_valid(self):
        query = EvidenceRAGQueryV1.model_validate(
            _query(
                business_object={
                    "object_type": "source_cv",
                    "object_id": "cv-demo-001",
                },
                business_version="cv-extraction-http.v2",
                graph_version=None,
            )
        )
        response = EvidenceRAGResponseV1.model_validate(
            _answered_response(
                business_version="cv-extraction-http.v2",
                graph_version=None,
                references=[
                    _reference(
                        graph_version=None,
                        business_version="cv-extraction-http.v2",
                        source_object_type="validated_cv_snapshot",
                        source_object_id="validated-cv-snapshot-demo-001",
                        source_document_id="cv-demo-001",
                    )
                ],
            )
        )
        assert query.permission.tenant_ref == response.permission.tenant_ref
        assert response.status == "answered"
        assert response.references[0].business_version == "cv-extraction-http.v2"

    def test_response_retains_actual_permission_context(self):
        response = EvidenceRAGResponseV1.model_validate(_answered_response())
        assert response.permission.user_id == "user-demo-001"
        assert response.permission.tenant_ref == "tenant-demo"
        assert response.permission.permission_scope == "competition-demo-v1"
        assert response.permission.assembled_by == "main-system-bff"

    def test_response_rejects_unknown_fields(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            EvidenceRAGResponseV1.model_validate(
                {**_answered_response(), "unexpected": True}
            )

    def test_old_evidence_composer_payload_is_not_a_rag_response(self):
        old_composer = {
            "answer": "Concatenated evidence snippets without status or references.",
            "evidence_ids": ["evidence-001"],
        }
        with pytest.raises(ValidationError):
            EvidenceRAGResponseV1.model_validate(old_composer)


# ===========================================================================
# execution modes
# ===========================================================================


class TestExecutionModeContract:
    def test_all_execution_modes_are_declared(self):
        assert set(EXECUTION_MODE_SEMANTICS) == {
            "rule",
            "llm",
            "human_confirmed",
            "demo",
            "semantic_shadow",
            "rag_explanation",
        }
        assert set(ExecutionMode.__args__) == set(EXECUTION_MODE_SEMANTICS)

    def test_mock_is_not_an_execution_mode(self):
        assert "mock" not in ExecutionMode.__args__
        assert "mock" not in EXECUTION_MODE_SEMANTICS

    def test_semantics_are_frozen_per_mode(self):
        semantics = EXECUTION_MODE_SEMANTICS
        assert semantics["rule"]["changes_official_score"] is True
        assert semantics["semantic_shadow"]["changes_official_score"] is False
        assert semantics["rag_explanation"]["creates_business_facts"] is False
        assert semantics["demo"]["demo_only"] is True
        assert semantics["llm"]["model_required"] is True

    def test_llm_failure_must_not_fall_back_to_rule(self):
        with pytest.raises(ValidationError, match="must not fall back"):
            ExecutionModeResultV1(
                requested_mode="llm",
                result_mode="rule",
                status="succeeded",
            )

    def test_llm_failure_must_not_fall_back_to_demo_or_shadow(self):
        for result_mode in ("demo", "semantic_shadow", "human_confirmed"):
            with pytest.raises(ValidationError, match="must not fall back"):
                ExecutionModeResultV1(
                    requested_mode="llm",
                    result_mode=result_mode,  # type: ignore[arg-type]
                    status="succeeded",
                )

    def test_semantic_shadow_must_not_fall_back_to_rule_or_demo(self):
        for result_mode in ("rule", "demo"):
            with pytest.raises(ValidationError, match="must not fall back"):
                ExecutionModeResultV1(
                    requested_mode="semantic_shadow",
                    result_mode=result_mode,  # type: ignore[arg-type]
                    status="succeeded",
                )

    def test_rag_explanation_must_not_fall_back_to_rule_demo_or_human(self):
        for result_mode in ("rule", "demo", "human_confirmed"):
            with pytest.raises(ValidationError, match="must not fall back"):
                ExecutionModeResultV1(
                    requested_mode="rag_explanation",
                    result_mode=result_mode,  # type: ignore[arg-type]
                    status="succeeded",
                )

    def test_llm_failure_can_remain_a_real_failure(self):
        result = ExecutionModeResultV1(
            requested_mode="llm",
            result_mode="llm",
            status="failed",
            error_code="LLM_TIMEOUT",
            error_message="upstream timed out",
        )
        assert result.status == "failed"

    def test_rag_explanation_failure_can_remain_a_real_failure(self):
        result = ExecutionModeResultV1(
            requested_mode="rag_explanation",
            result_mode="rag_explanation",
            status="failed",
            error_code="RAG_PROVIDER_UNAVAILABLE",
            error_message="upstream unavailable",
        )
        assert result.status == "failed"

    def test_failed_result_requires_error_code(self):
        with pytest.raises(ValidationError, match="error_code"):
            ExecutionModeResultV1(
                requested_mode="rule",
                result_mode="rule",
                status="failed",
            )

    def test_succeeded_or_available_cannot_carry_error_fields(self):
        with pytest.raises(ValidationError, match="error fields"):
            ExecutionModeResultV1(
                requested_mode="rule",
                result_mode="rule",
                status="succeeded",
                error_code="RULE_FAILED",
            )
        with pytest.raises(ValidationError, match="error fields"):
            ExecutionModeResultV1(
                requested_mode="semantic_shadow",
                result_mode="semantic_shadow",
                status="available",
                error_message="shadow unavailable",
            )

    def test_demo_result_requires_dataset_version(self):
        with pytest.raises(ValidationError, match="dataset_version"):
            ExecutionModeResultV1(
                requested_mode="demo",
                result_mode="demo",
                status="succeeded",
                is_demo=True,
            )
        result = ExecutionModeResultV1(
            requested_mode="demo",
            result_mode="demo",
            status="succeeded",
            is_demo=True,
            dataset_version="competition-demo-v1",
        )
        assert result.dataset_version == "competition-demo-v1"

    def test_semantic_shadow_uses_shadow_status_semantics(self):
        with pytest.raises(ValidationError, match="shadow status"):
            ExecutionModeResultV1(
                requested_mode="semantic_shadow",
                result_mode="semantic_shadow",
                status="succeeded",
            )
        ExecutionModeResultV1(
            requested_mode="semantic_shadow",
            result_mode="semantic_shadow",
            status="available",
        )


# ===========================================================================
# frozen existing contracts
# ===========================================================================


def test_portal_demo_task_schema_fields_are_frozen():
    from app.schemas.task import PortalDemoTaskResponse

    assert set(PortalDemoTaskResponse.model_fields) == {
        "task_id",
        "task_type",
        "object_type",
        "object_id",
        "service",
        "status",
        "progress",
        "error",
        "result_reference",
        "created_at",
        "updated_at",
    }
    task_type = PortalDemoTaskResponse.model_fields["task_type"].annotation
    assert set(task_type.__args__) == {
        "jd_extraction",
        "cv_extraction",
        "trend",
        "discovery",
        "matching",
    }


def test_portal_demo_task_statuses_are_frozen():
    from app.domain.tasks import TaskStatus

    assert set(TaskStatus.__args__) == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_position_profile_v2_carries_graph_version_id():
    assert "graph_version" in PositionProfileV1.model_fields
    assert "graph_version_id" not in PositionProfileV1.model_fields
    assert "graph_version_id" in PositionProfileV2.model_fields
    assert "dependencies" in PositionProfileV2.model_fields
    annotation = PositionProfileV2.model_fields["contract_version"].annotation
    assert "position-profile.v2" in str(annotation)


def test_trend_and_profile_share_graph_version_identity():
    from app.contexts.market_intelligence._ports.trend_reports import TrendReportRecord

    assert "graph_version_id" in TrendReportRecord.__dataclass_fields__
    assert "position_id" in TrendReportRecord.__dataclass_fields__
    assert "graph_version_id" in PositionProfileV2.model_fields


def test_evidence_contracts_freeze_identity_quote_and_version_fields():
    assert set(JDEvidence.model_fields) == {
        "source_id",
        "quote",
        "start",
        "end",
        "alignment",
        "occurrence_index",
    }
    assert set(CVEvidence.model_fields) == {
        "source_document_id",
        "source_id",
        "quote",
        "start",
        "end",
        "alignment",
        "occurrence_index",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        JDEvidence(source_id="evidence-1", quote="Python", unexpected=True)
    assert PUBLISHED_JD_FACT_V3 == "published-jd-fact.v3"


def test_rag_contract_versions_are_frozen():
    assert EVIDENCE_RAG_QUERY_VERSION == "evidence-rag-query.v1"
    assert EVIDENCE_RAG_RESPONSE_VERSION == "evidence-rag-response.v1"
