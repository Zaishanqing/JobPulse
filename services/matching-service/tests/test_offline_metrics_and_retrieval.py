from __future__ import annotations

import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.evaluation import (
    HardConstraintResult,
    MatchEvaluation,
    SkillResult,
)
from app.domain.profiles import PositionMatchProfile
from app.domain.semantic_fragments import fragment_position_profile
from app.domain.feature_flags import (
    FeatureFlagController,
    InMemoryRollbackStateStore,
    RollbackThresholds,
    StageFlag,
)
from app.domain.vector_contracts import VectorContractViolation
from app.evaluation.metrics import (
    binary_metrics,
    calibrate_thresholds,
    confusion_matrix,
    dimension_metrics,
    mean_reciprocal_rank,
    top_k_recall,
    uncertainty_coverage,
)
from app.evaluation.models import RequirementAnnotation
from app.infrastructure.feature_flag_configuration import build_feature_flags
from app.infrastructure.http_retrieval_adapters import (
    HttpRerankerAdapter,
    HttpSparseRetrievalAdapter,
)
from app.infrastructure.redis_feature_flags import RedisRollbackStateStore
from app.ports.retrieval import (
    RerankItem,
    RerankRequest,
    RerankScore,
    SparseHit,
    SparseQuery,
)


def _annotation(**values) -> RequirementAnnotation:
    defaults = {
        "requirement_id": "req-1",
        "dimension": "required_skill",
        "label": "matched",
    }
    defaults.update(values)
    return RequirementAnnotation(**defaults)


def test_binary_metrics_and_confusion_matrix_cover_labels():
    pairs = (
        ("matched", "matched"),
        ("partial", "unknown"),
        ("not_matched", "partial"),
        ("unknown", "matched"),
    )

    metrics = binary_metrics(pairs)
    matrix = confusion_matrix(pairs)

    assert metrics.precision == 0.5
    assert metrics.recall == 0.5
    assert metrics.f1 == 0.5
    assert metrics.support == 3
    assert len(matrix) == 16
    assert next(item for item in matrix if item.actual == "matched" and item.predicted == "matched").count == 1


def test_dimension_metrics_groups_by_dimension():
    labeled = (
        ("required_skill", "matched", "matched"),
        ("required_skill", "partial", "not_matched"),
        ("hard_constraint", "matched", "unknown"),
    )

    dimensions = dimension_metrics(labeled)

    required = next(item for item in dimensions if item.dimension == "required_skill")
    assert required.support == 2
    assert required.metrics.recall == 0.5
    hard = next(item for item in dimensions if item.dimension == "hard_constraint")
    assert hard.support == 1


def test_top_k_recall_and_mean_reciprocal_rank():
    annotations = (
        _annotation(requirement_id="a", relevant_rank=1),
        _annotation(requirement_id="b", relevant_rank=3),
        _annotation(requirement_id="c", relevant_rank=8),
        _annotation(requirement_id="d", label="not_matched", relevant_rank=None),
    )

    recall = top_k_recall(annotations, enabled=True, ks=(1, 3, 5))
    assert [item.recall for item in recall] == [
        round(1 / 3, 6),
        round(2 / 3, 6),
        round(2 / 3, 6),
    ]
    assert mean_reciprocal_rank(annotations, enabled=True) == round(
        (1 + 1 / 3 + 1 / 8) / 3, 6
    )
    assert mean_reciprocal_rank(annotations, enabled=False) is None
    assert all(item.recall is None for item in top_k_recall([], enabled=True))


def test_calibrate_thresholds_requires_valid_inputs_and_ranks_candidates():
    annotations = (
        _annotation(requirement_id="a", label="partial", semantic_score=0.9),
        _annotation(requirement_id="b", label="matched", semantic_score=0.75),
        _annotation(requirement_id="c", label="not_matched", semantic_score=0.2),
    )

    with pytest.raises(ValueError, match="semantic thresholds"):
        calibrate_thresholds(annotations, ())
    with pytest.raises(ValueError, match="semantic thresholds"):
        calibrate_thresholds(annotations, (1.5,))

    report = calibrate_thresholds(annotations, (0.5, 0.8))
    assert report.recommended_threshold == 0.5
    assert len(report.candidates) == 2
    assert calibrate_thresholds((), (0.5,)).recommended_threshold is None


def test_uncertainty_coverage_counts_unknown_and_unresolved():
    evaluation = MatchEvaluation(
        evaluation_id="eval-1",
        algorithm_version="v1",
        evaluation_status="completed",
        hard_constraint_results=(
            HardConstraintResult(
                requirement_id="h1",
                constraint_type="education",
                status="unknown",
                required_value=None,
                candidate_value=None,
                reason_code="unknown",
                confidence=0.5,
            ),
        ),
        skill_results=(
            SkillResult(
                requirement_id="s1",
                skill_id=None,
                skill_name="Python",
                importance_level="required",
                required_level=None,
                candidate_declared_level=None,
                candidate_demonstrated_level=None,
                verification_status=None,
                match_status="unresolved",
                reason_code="unresolved",
                confidence=0.5,
            ),
            SkillResult(
                requirement_id="s2",
                skill_id="SKILL_SQL",
                skill_name="SQL",
                importance_level="required",
                required_level=None,
                candidate_declared_level=None,
                candidate_demonstrated_level=None,
                verification_status=None,
                match_status="matched",
                reason_code="exact",
                confidence=1.0,
            ),
        ),
    )

    coverage = uncertainty_coverage((evaluation,))

    assert coverage.total_results == 3
    assert coverage.unknown_count == 1
    assert coverage.unresolved_count == 1
    assert coverage.unknown_rate > 0
    assert coverage.unresolved_rate > 0


def test_feature_flag_configuration_builds_controller_and_enforces_redis():
    controller = build_feature_flags(
        {
            "MATCHING_RUNTIME_MODE": "test",
            "MATCHING_FF_RETRIEVAL_ENABLED": "true",
            "MATCHING_FF_RETRIEVAL_PERCENT": "100",
            "MATCHING_FF_HYBRID_ENABLED": "1",
            "MATCHING_FF_HYBRID_PERCENT": "50",
            "MATCHING_FF_RERANKER_ENABLED": "on",
            "MATCHING_FF_RERANKER_PERCENT": "25",
            "MATCHING_FF_RERANKER_TENANTS": "tenant-a,tenant-b",
            "MATCHING_FF_SCORING_USERS": "user-1",
            "MATCHING_ROLLBACK_MIN_SAMPLES": "5",
            "MATCHING_ROLLBACK_ERROR_RATE": "0.5",
            "MATCHING_ROLLBACK_EMPTY_RETRIEVAL_RATE": "0.4",
        }
    )

    assert controller.flags["retrieval"].enabled is True
    assert controller.flags["hybrid"].percentage == 50
    assert controller.flags["reranker"].tenant_refs == frozenset({"tenant-a", "tenant-b"})
    assert controller.thresholds.minimum_samples == 5

    with pytest.raises(ValueError, match="shared rollback Redis"):
        build_feature_flags(
            {
                "MATCHING_RUNTIME_MODE": "production",
                "MATCHING_FF_INDEXING_ENABLED": "true",
                "MATCHING_FF_INDEXING_PERCENT": "10",
            }
        )

    with_redis = build_feature_flags(
        {
            "MATCHING_RUNTIME_MODE": "production",
            "MATCHING_FF_INDEXING_ENABLED": "true",
            "MATCHING_FF_INDEXING_PERCENT": "10",
            "MATCHING_REDIS_URL": "redis://127.0.0.1:6379/0",
        }
    )
    assert with_redis.state_store is not None


def test_feature_flag_controller_enabled_and_rollback_store():
    with pytest.raises(ValueError, match="percentage"):
        StageFlag(enabled=True, percentage=101)
    with pytest.raises(ValueError, match="subjects"):
        StageFlag(enabled=True, tenant_refs=frozenset({""}))
    with pytest.raises(ValueError, match="minimum_samples"):
        RollbackThresholds(minimum_samples=0)
    with pytest.raises(ValueError, match="rates"):
        RollbackThresholds(error_rate=2)

    disabled = StageFlag(enabled=False, percentage=100)
    assert disabled.includes(tenant_ref="tenant-a") is False
    by_tenant = StageFlag(enabled=True, percentage=0, tenant_refs=frozenset({"tenant-a"}))
    assert by_tenant.includes(tenant_ref="tenant-a") is True
    by_user = StageFlag(enabled=True, percentage=0, user_refs=frozenset({"user-1"}))
    assert by_user.includes(tenant_ref="tenant-b", user_ref="user-1") is True
    hashed = StageFlag(enabled=True, percentage=100)
    assert hashed.includes(tenant_ref="tenant-c") is True

    store = InMemoryRollbackStateStore()
    controller = FeatureFlagController(
        flags={"retrieval": StageFlag(enabled=True, percentage=100)},
        thresholds=RollbackThresholds(minimum_samples=2, error_rate=0.5),
        state_store=store,
        window_seconds=300,
    )
    assert controller.enabled("retrieval", tenant_ref="tenant-a") is True
    audit = controller.observe("retrieval", "error")
    assert audit is None
    assert controller.rollback_audit("retrieval") is None

    with pytest.raises(ValueError, match="window"):
        FeatureFlagController(flags={}, window_seconds=0)


class _FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.observations = []

    def get(self, key):
        return self.values.get(key)

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    def zcard(self, key):
        return len(self.observations)

    def set(self, key, value, nx=True, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis

    def zremrangebyscore(self, *_args, **_kwargs):
        return self

    def zadd(self, *_args, **_kwargs):
        return self

    def expire(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.redis.observations.append(1)


def test_redis_rollback_state_store_observes_and_reports_audit():
    thresholds = build_feature_flags(
        {
            "MATCHING_ROLLBACK_MIN_SAMPLES": "5",
            "MATCHING_ROLLBACK_ERROR_RATE": "0.5",
            "MATCHING_ROLLBACK_LATENCY_RATE": "0.1",
            "MATCHING_ROLLBACK_EMPTY_RETRIEVAL_RATE": "0.1",
            "MATCHING_ROLLBACK_STALE_INDEX_RATE": "0.01",
            "MATCHING_ROLLBACK_HARD_CONSTRAINT_RATE": "0",
            "MATCHING_ROLLBACK_CROSS_TENANT_RATE": "0",
        }
    ).thresholds
    store = RedisRollbackStateStore(
        _FakeRedis(),
        namespace="matching:feature-flags:v1",
        recovery_seconds=3600,
    )

    assert store._key("retrieval", "rollback") == (
        "matching:feature-flags:v1:retrieval:rollback"
    )
    assert store.rollback_audit("retrieval") is None
    audit = store.observe(
        "retrieval",
        ("error",),
        thresholds=thresholds,
        now=1000.0,
        window_seconds=300,
    )
    assert audit is None

    with pytest.raises(ValueError, match="namespace"):
        RedisRollbackStateStore(_FakeRedis(), namespace="", recovery_seconds=1)
    with pytest.raises(ValueError, match="recovery"):
        RedisRollbackStateStore(_FakeRedis(), namespace="ns", recovery_seconds=0)


class _Response:
    def __init__(self, payload: object, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _RawResponse:
    def __init__(self, content: bytes):
        self._content = content

    def read(self) -> bytes:
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _sparse_query(ready_position_json) -> SparseQuery:
    fragment = fragment_position_profile(
        PositionMatchProfile.model_validate(ready_position_json),
        tenant_ref="tenant-a",
        target_type="standard_position",
    )[0]
    return SparseQuery(
        tenant_ref="tenant-a",
        fragment=fragment,
        filter={},
        index_revision="index-r1",
        top_k=10,
    )


def test_http_sparse_retrieval_adapter_contracts_and_transport(
    monkeypatch, ready_position_json
):
    adapter = HttpSparseRetrievalAdapter(
        "https://sparse.invalid", timeout_seconds=1, api_key="key"
    )
    with pytest.raises(ValueError, match="endpoint"):
        HttpSparseRetrievalAdapter("", timeout_seconds=1, api_key=None)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({"ok": True}),
    )
    adapter.check_health()

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({"ok": True}, status=500),
    )
    with pytest.raises(VectorContractViolation, match="dependency"):
        adapter.check_health()

    query = _sparse_query(ready_position_json)
    valid_hit = {
        "tenant_ref": "tenant-a",
        "fragment": query.fragment.model_dump(mode="json"),
        "score": 0.9,
        "active": True,
        "superseded": False,
        "profile_version": "a" * 64,
        "fragment_fingerprint": "b" * 64,
        "source_version": "v1",
        "index_revision": "index-r1",
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({"hits": [valid_hit]}),
    )
    hits = adapter.search(query)
    assert len(hits) == 1
    assert isinstance(hits[0], SparseHit)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({}),
    )
    with pytest.raises(VectorContractViolation, match="contract"):
        adapter.search(query)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({"hits": [{**valid_hit, "fragment": {}}]}),
    )
    with pytest.raises(VectorContractViolation, match="hit is invalid"):
        adapter.search(query)

    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    with pytest.raises(VectorContractViolation, match="dependency"):
        adapter._post({})

    def timeout(*_args, **_kwargs):
        raise TimeoutError("slow")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    with pytest.raises(TimeoutError, match="timed out"):
        adapter._post({})

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _RawResponse(b"not-json"),
    )
    with pytest.raises(VectorContractViolation, match="dependency"):
        adapter._post({})


def test_http_reranker_adapter_revision_and_score_contracts(monkeypatch):
    adapter = HttpRerankerAdapter(
        "https://reranker.invalid", timeout_seconds=1, api_key=None
    )
    request = RerankRequest(
        tenant_ref="tenant-a",
        model_revision="reranker-r1",
        items=(
            RerankItem(
                retrieval_item_id="item-1",
                candidate_fragment_id="fragment-1",
                query_text="负责后端开发",
                candidate_text="使用 Python 编写后端服务",
                retrieval_score=0.5,
            ),
        ),
        top_n=5,
    )

    with pytest.raises(ValueError, match="PII"):
        RerankItem(
            retrieval_item_id="item-1",
            candidate_fragment_id="fragment-1",
            query_text="alice@example.com",
            candidate_text="safe",
            retrieval_score=0.5,
        )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "model_revision": "reranker-r1",
                "scores": [{"retrieval_item_id": "item-1", "score": 0.8}],
            }
        ),
    )
    scores = adapter.rerank(request)
    assert isinstance(scores[0], RerankScore)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({"model_revision": "old"}),
    )
    with pytest.raises(VectorContractViolation, match="revision"):
        adapter.rerank(request)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {"model_revision": "reranker-r1", "scores": "invalid"}
        ),
    )
    with pytest.raises(VectorContractViolation, match="contract"):
        adapter.rerank(request)


def test_offline_cli_writes_report(monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "offline_dataset_v1.json"
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "offline",
            "--dataset",
            str(fixture),
            "--output",
            str(output),
            "--semantic-thresholds",
            "0.7,0.8",
        ],
    )

    from app.evaluation.offline import main as offline_main

    offline_main()

    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "result_id" in payload
