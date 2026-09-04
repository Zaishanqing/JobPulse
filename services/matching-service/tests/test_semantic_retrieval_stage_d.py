from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.application.evaluation import MatchEvaluationService
from app.application.semantic_retrieval import (
    SemanticRetrievalConfig,
    SemanticRetrievalService,
)
from app.bootstrap.application import create_app
from app.domain.profiles import CVMatchProfile
from app.domain.semantic_fragments import fragment_cv_profile
from app.domain.semantic_retrieval import SemanticMatchExplanation
from app.domain.vector_contracts import (
    EmbeddingResult,
    VectorContractViolation,
    VectorRecord,
)
from app.infrastructure.fake_vector_adapters import FakeVectorStoreAdapter
from app.infrastructure.metrics import MetricsRegistry


class ConstantEmbedding:
    def embed(self, request):
        return EmbeddingResult(
            tenant_ref=request.tenant_ref,
            request_id=request.request_id,
            embedding_model=request.embedding_model,
            embedding_revision=request.embedding_revision,
            dimension=request.dimension,
            fragment_ids=tuple(item.fragment_id for item in request.fragments),
            vectors=tuple((1.0, 0.0) for _item in request.fragments),
        )


class FailingEmbedding:
    def embed(self, request):
        raise VectorContractViolation("EMBEDDING_UNAVAILABLE", "offline")


class MismatchedEmbedding(ConstantEmbedding):
    def embed(self, request):
        return super().embed(request).model_copy(
            update={"embedding_revision": "unexpected-revision"}
        )


class RecordingStore(FakeVectorStoreAdapter):
    def __init__(self):
        super().__init__()
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return super().search(query)


def _retrieval(mode, cv_payload, *, metrics=None, store=None, embedding=None, **updates):
    vectors = store or RecordingStore()
    cv = CVMatchProfile.model_validate(cv_payload)
    records = tuple(
        VectorRecord.build(
            fragment=fragment,
            embedding=(1.0, 0.0),
            embedding_model="fixture-model",
            embedding_revision="fixture-revision",
        )
        for fragment in fragment_cv_profile(cv, tenant_ref="tenant-a")
    )
    vectors.upsert(records)
    config = SemanticRetrievalConfig(
        mode=mode,
        embedding_model="fixture-model",
        embedding_revision="fixture-revision",
        embedding_dimension=2,
        index_revision="matching-fragments-r1",
        collection="matching-fragments-r1",
        semantic_weight=0,
        **updates,
    )
    retrieval = SemanticRetrievalService(
        embedding or ConstantEmbedding(), vectors, config, metrics=metrics
    )
    return retrieval, vectors


def _service(mode, cv_payload, *, metrics=None, store=None, embedding=None, **updates):
    retrieval, vectors = _retrieval(
        mode,
        cv_payload,
        metrics=metrics,
        store=store,
        embedding=embedding,
        **updates,
    )
    return MatchEvaluationService(semantic_retrieval=retrieval), vectors


def _payload(cv, position):
    return {
        "tenant_ref": "tenant-a",
        "target_type": "standard_position",
        "cv_profile": cv,
        "position_profile": position,
    }


def test_d1_retrieval_enforces_filters_and_returns_evidence(
    ready_cv_json, ready_position_json
):
    service, vectors = _service("shadow", ready_cv_json)

    result = service.evaluate(_payload(ready_cv_json, ready_position_json))

    assert result.semantic_shadow_status == "available"
    assert result.semantic_shadow_score == 1.0
    assert result.semantic_shadow_evidence
    assert all(item.evidence_ref.quote for item in result.semantic_shadow_evidence)
    assert all(item.retrieval_trace_id for item in result.semantic_shadow_evidence)
    assert vectors.queries
    for query in vectors.queries:
        assert query.tenant_ref == "tenant-a"
        assert query.embedding_revision == "fixture-revision"
        assert query.filter.active is True
        assert query.filter.profile_version == ready_cv_json["profile_version"]
        assert query.filter.source_ids == (ready_cv_json["cv_id"],)
        assert query.filter.target_types == ("candidate_cv",)
        assert query.top_k == 20


def test_d2_shadow_is_auditable_and_does_not_change_formal_score(
    ready_cv_json, ready_position_json
):
    baseline = MatchEvaluationService().evaluate(
        {"cv_profile": ready_cv_json, "position_profile": ready_position_json}
    )
    metrics = MetricsRegistry()
    service, _vectors = _service("shadow", ready_cv_json, metrics=metrics)

    result = service.evaluate(_payload(ready_cv_json, ready_position_json))

    assert result.final_match_result.overall_score == baseline.final_match_result.overall_score
    assert (
        result.final_match_result.recommendation_level
        == baseline.final_match_result.recommendation_level
    )
    assert (
        result.final_match_result.dimension_scores
        == baseline.final_match_result.dimension_scores
    )
    assert (
        result.final_match_result.score_contributions
        == baseline.final_match_result.score_contributions
    )
    assert result.semantic_score is None
    assert result.semantic_latency_ms is not None
    assert result.semantic_embedding_revision == "fixture-revision"
    assert result.semantic_index_revision == "matching-fragments-r1"
    assert result.semantic_explanations
    rendered = result.model_dump_json()
    assert "tenant-a" not in rendered
    assert '"embedding"' not in rendered
    assert metrics.counter_value(
        "matching_semantic_retrieval_total",
        outcome="success",
        target_type="standard_position",
    ) == 1


def test_candidate_score_counts_unmatched_position_fragments_as_coverage_gaps(
    ready_cv_json, ready_position_json
):
    position = deepcopy(ready_position_json)
    scenario = "high volume payment traffic"
    position["business_scenarios"] = {
        "values": [scenario],
        "evidence_refs": [
            {
                "source_id": "jd:scenario:1",
                "quote": scenario,
                "start": 0,
                "end": len(scenario),
                "alignment": "exact",
                "occurrence_index": 0,
            }
        ],
    }
    position["profile_version"] = "position-source.v1"
    service, _vectors = _service("shadow", ready_cv_json)

    result = service.evaluate(_payload(ready_cv_json, position))

    assert result.semantic_shadow_score == pytest.approx(2 / 3)


def test_d3_shadow_mode_keeps_formal_scoring_unchanged(
    ready_cv_json, ready_position_json
):
    baseline = MatchEvaluationService().evaluate(
        {"cv_profile": ready_cv_json, "position_profile": ready_position_json}
    )
    service, _vectors = _service("shadow", ready_cv_json)

    result = service.evaluate(_payload(ready_cv_json, ready_position_json))

    assert result.semantic_score is None
    assert result.semantic_weight == 0
    assert result.semantic_effective_weight == 0
    assert result.skill_results == baseline.skill_results
    assert not any(item.dimension == "semantic" for item in result.final_match_result.dimension_scores)
    assert result.semantic_candidates
    assert result.final_match_result.semantic_weight == 0


def test_competition_demo_rejects_enabled_semantic_mode() -> None:
    with pytest.raises(
        ValueError, match="competition semantic demo supports shadow mode only"
    ):
        create_app(
            runtime_env={
                "MATCHING_RUNTIME_MODE": "test",
                "MATCHING_SEMANTIC_DEMO": "true",
                "MATCHING_SEMANTIC_MODE": "enabled",
            },
            auth_env={},
        )


def test_d3_hard_failure_cannot_be_offset_by_semantic_score(
    ready_cv_json, ready_position_json
):
    cv = deepcopy(ready_cv_json)
    location = next(
        item for item in cv["match_features"] if item["feature_type"] == "location"
    )
    location["canonical_name"] = "Beijing"
    location["raw_text"] = "Beijing"
    location["evidence_refs"][0]["quote"] = "Beijing"
    location["evidence_refs"][0]["end"] = 7
    cv["profile_version"] = "cv-source.v1"
    baseline = MatchEvaluationService().evaluate(
        {"cv_profile": cv, "position_profile": ready_position_json}
    )
    service, _vectors = _service("shadow", cv)

    result = service.evaluate(_payload(cv, ready_position_json))

    assert result.final_match_result.hard_gate_status == "failed"
    assert result.final_match_result.overall_score == baseline.final_match_result.overall_score
    assert result.semantic_effective_weight == 0.0
    assert result.final_match_result.recommendation_level == "not_recommended"


def test_d2_dependency_failure_is_unavailable_and_keeps_baseline(
    ready_cv_json, ready_position_json
):
    baseline = MatchEvaluationService().evaluate(
        {"cv_profile": ready_cv_json, "position_profile": ready_position_json}
    )
    metrics = MetricsRegistry()
    service, _vectors = _service(
        "shadow", ready_cv_json, embedding=FailingEmbedding(), metrics=metrics
    )

    result = service.evaluate(_payload(ready_cv_json, ready_position_json))

    assert result.semantic_status == "unavailable"
    assert result.semantic_error_code == "EMBEDDING_UNAVAILABLE"
    assert result.semantic_shadow_status == "unavailable"
    assert result.final_match_result.overall_score == baseline.final_match_result.overall_score
    assert (
        result.final_match_result.dimension_scores
        == baseline.final_match_result.dimension_scores
    )
    assert metrics.counter_value(
        "matching_semantic_retrieval_total",
        outcome="error",
        component="embedding",
    ) == 1


def test_d1_rejects_embedding_results_with_mismatched_lineage(
    ready_cv_json, ready_position_json
):
    service, _vectors = _service(
        "shadow", ready_cv_json, embedding=MismatchedEmbedding()
    )

    result = service.evaluate(_payload(ready_cv_json, ready_position_json))

    assert result.evaluation_status == "completed"
    assert result.semantic_status == "unavailable"
    assert result.semantic_error_code == "EMBEDDING_RESPONSE_MISMATCH"
    assert result.semantic_score is None


@pytest.mark.parametrize(
    ("updates", "target_type"),
    [
        ({"disabled_tenant_refs": frozenset({"tenant-a"})}, "standard_position"),
        ({"disabled_target_types": frozenset({"enterprise_job"})}, "enterprise_job"),
    ],
)
def test_d2_can_be_disabled_by_tenant_or_target(
    ready_cv_json, ready_position_json, updates, target_type
):
    service, vectors = _service("shadow", ready_cv_json, **updates)
    payload = _payload(ready_cv_json, ready_position_json)
    payload["target_type"] = target_type

    result = service.evaluate(payload)

    assert result.semantic_shadow_status == "disabled"
    assert not vectors.queries


def test_d4_explanations_distinguish_semantic_and_hide_internal_data(
    ready_cv_json, ready_position_json
):
    service, _vectors = _service("shadow", ready_cv_json)

    result = service.evaluate(_payload(ready_cv_json, ready_position_json))

    explanation = result.semantic_explanations[0]
    assert explanation.match_kind == "semantic_related"
    assert explanation.dimension == "skill_semantic_match"
    assert explanation.position_text
    assert explanation.resume_evidence
    assert explanation.evidence_ref.startswith("cv:")
    assert not hasattr(explanation, "tenant_ref")
    assert not hasattr(explanation, "embedding")
    with pytest.raises(ValidationError):
        SemanticMatchExplanation(
            dimension="responsibility_semantic_match",
            score=0.9,
            position_text="contact alice@example.com",
            resume_evidence="safe",
            evidence_ref="cv:1:0:4",
            embedding_revision="r1",
        )


def test_stage_d_configuration_is_fail_closed():
    with pytest.raises(ValueError):
        SemanticRetrievalConfig(mode="enabled")
    with pytest.raises(ValueError):
        SemanticRetrievalConfig(
            mode="enabled",
            embedding_model="m",
            embedding_revision="r",
            embedding_dimension=2,
            index_revision="i",
            semantic_weight=0.21,
        )
    with pytest.raises(ValueError):
        SemanticRetrievalConfig(
            disabled_target_types=frozenset({"candidate_cv"})
        )


def test_enabled_mode_async_task_preserves_lineage_and_enterprise_target(
    ready_cv_json, ready_position_json
):
    retrieval, _vectors = _retrieval("shadow", ready_cv_json)
    app = create_app(
        semantic_retrieval_service=retrieval,
        runtime_env={"MATCHING_RUNTIME_MODE": "test"},
        auth_env={},
    )
    payload = _payload(ready_cv_json, ready_position_json)
    payload["target_type"] = "enterprise_job"

    submitted = app.state.evaluation_task_service.submit(
        payload,
        "stage-d-enterprise",
        "tenant:tenant-a",
    )

    assert submitted.task.status == "succeeded", (
        submitted.task.error_code,
        submitted.task.error_message,
    )
    assert submitted.task.versions.embedding_model == "fixture-model"
    assert submitted.task.versions.embedding_version == "fixture-revision"
    assert submitted.task.versions.embedding_dimension == 2
    assert submitted.task.versions.semantic_index_revision == "matching-fragments-r1"
    persisted = app.state.evaluation_task_service.get_evaluation(
        submitted.task.evaluation_id,
        "tenant:tenant-a",
    ).result
    assert persisted.stale is False
    assert persisted.versions.target_type == "enterprise_job"
    assert persisted.evaluation.semantic_target_type == "enterprise_job"
