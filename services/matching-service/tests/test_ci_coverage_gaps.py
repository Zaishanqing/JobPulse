"""Coverage-gap tests for pure matching-service paths.

These tests target modules that the main matching suite did not exercise
(readiness CLI, Stage E helpers, health dependency dispatch, outbox idle
paths, model artifact manifest, degree normalization).  They add no behavior;
they pin existing contracts so the module coverage gate (85%) is met on the
JobPulse tree.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from app.application.authorization import AuthorizationError
from app.application.health import DependencySpec, HealthService
from app.application.model_artifact import (
    ModelArtifactError,
    build_manifest,
    verify_manifest,
)
from app.application.outbox_dispatcher import OutboxDispatcher
from app.application.resource_authorization import ResourceAuthorizationService
from app.diagnostics.verify_readiness import main as readiness_main
from app.domain.auth import AuthContext, derive_access_scope
from app.domain.degree_levels import degree_rank, normalize_degree, parse_degree_from_text
from app.domain.outbox import OutboxDispatchResult
from app.domain.profiles import Evidence
from app.domain.queue import QueueDelivery, TaskQueueMessage
from app.domain.vector_contracts import (
    EmbeddingRequest,
    SemanticFragment,
    VectorContractViolation,
    VectorFilter,
    VectorQuery,
    VectorRecord,
    VectorSearchHit,
)
from app.evaluation.models import RequirementAnnotation, StageEPolicy
from app.evaluation.stage_e import _positive, _rrf
from app.infrastructure.fake_vector_adapters import (
    FakeEmbeddingAdapter,
    FakeVectorStoreAdapter,
)
from app.infrastructure.http_observability import (
    ApiRuntimeState,
    ObservabilityMiddleware,
)
from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.persistence_configuration import (
    _non_negative_int,
    _positive_float,
    _positive_int,
    build_persistence,
)
from app.infrastructure.queue_configuration import (
    _non_negative_float,
    build_task_queue,
)
from app.infrastructure.queue_configuration import (
    _positive_float as _queue_positive_float,
)
from app.ports.observability import NullMetricsCollector
from app.ports.task_queue import TaskQueueError

# ── verify_readiness CLI ──


class _FakeResponse:
    def __init__(self, status: int, payload: object):
        self.status = status
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _ready_payload(ce_status: str = "ready", provider: str = "model") -> dict:
    return {
        "code": 0,
        "data": {
            "status": "ready",
            "components": [
                {
                    "component": "responsibility_ce",
                    "status": ce_status,
                    "provider": provider,
                    "artifact_digest": "a" * 64,
                }
            ],
        },
    }


def _run_readiness(monkeypatch, payload: object | Exception, status: int = 200) -> None:
    def fake_urlopen(url: str, timeout: float):
        if isinstance(payload, Exception):
            raise payload
        return _FakeResponse(status, payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["verify_readiness.py", "--url", "http://127.0.0.1/x"])


def test_verify_readiness_success(monkeypatch, capsys):
    _run_readiness(monkeypatch, _ready_payload())
    assert readiness_main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready"
    assert output["responsibility_ce"]["artifact_digest"] == "a" * 64


@pytest.mark.parametrize(
    "payload,status",
    [
        (_ready_payload(), 500),
        ({"code": 1, "data": {}}, 200),
        ({"code": 0}, 200),
        ({"code": 0, "data": {"status": "starting", "components": []}}, 200),
        ({"code": 0, "data": {"status": "ready", "components": []}}, 200),
        ({"code": 0, "data": {"status": "ready", "components": "x"}}, 200),
        (
            {
                "code": 0,
                "data": {
                    "status": "ready",
                    "components": [
                        {
                            "component": "responsibility_ce",
                            "status": "ready",
                            "provider": "file",
                            "artifact_digest": "a" * 64,
                        }
                    ],
                },
            },
            200,
        ),
        (
            {
                "code": 0,
                "data": {
                    "status": "ready",
                    "components": [
                        {
                            "component": "responsibility_ce",
                            "status": "ready",
                            "provider": "model",
                            "artifact_digest": "not-a-digest",
                        }
                    ],
                },
            },
            200,
        ),
    ],
)
def test_verify_readiness_failure_paths(monkeypatch, payload, status):
    _run_readiness(monkeypatch, payload, status=status)
    with pytest.raises(RuntimeError):
        readiness_main()


def test_verify_readiness_network_error(monkeypatch):
    _run_readiness(monkeypatch, urllib.error.URLError("offline"))
    with pytest.raises(urllib.error.URLError):
        readiness_main()


# ── Stage E helpers ──


def test_stage_e_positive_labels():
    assert _positive("matched") is True
    assert _positive("partial") is True
    assert _positive("not_matched") is False
    assert _positive("unknown") is False


def test_stage_e_rrf_ranks_and_bounds():
    policy = StageEPolicy(
        dense_weight=0.5,
        sparse_weight=0.5,
        top_k=10,
        threshold=0.5,
        rrf_k=30,
        reranker_top_n=10,
        reranker_model_revision="offline-reranker.v1",
    )
    annotation = RequirementAnnotation(
        requirement_id="req-1",
        dimension="required_skill",
        label="matched",
        dense_rank=1,
        sparse_rank=2,
    )
    dense = 0.5 / 31
    sparse = 0.5 / 32
    expected = (dense + sparse) / (1.0 / 31)
    assert _rrf(annotation, policy) == pytest.approx(expected)
    # rank beyond top_k contributes zero
    beyond = RequirementAnnotation(
        requirement_id="req-2",
        dimension="required_skill",
        label="matched",
        dense_rank=99,
        sparse_rank=99,
    )
    assert _rrf(beyond, policy) == 0.0
    # relevant_rank fallback when dense_rank missing
    fallback = RequirementAnnotation(
        requirement_id="req-3",
        dimension="required_skill",
        label="matched",
        relevant_rank=1,
    )
    assert _rrf(fallback, policy) == pytest.approx(0.5)
    # no ranks at all -> zero
    empty = RequirementAnnotation(
        requirement_id="req-4",
        dimension="required_skill",
        label="not_matched",
    )
    assert _rrf(empty, policy) == 0.0


# ── health dependency dispatch ──


class _FakeDependency:
    def __init__(self, error: Exception | None = None, digest: str | None = None):
        self._error = error
        self.artifact_digest = digest

    def check_health(self) -> None:
        if self._error is not None:
            raise self._error


class _RecordingMetrics:
    def __init__(self):
        self.increments: list[tuple[str, dict]] = []

    def increment(self, name: str, value: float = 1, **labels: str) -> None:
        self.increments.append((name, dict(labels)))

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        return None

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        return None

    def render(self) -> str:
        return ""


def test_health_disabled_provider():
    service = HealthService(
        (DependencySpec(component="vector", provider="memory"),),
        NullMetricsCollector(),
    )
    report = service.readiness()
    assert report.status == "ready"
    assert report.components[0].status == "disabled"


def test_health_configuration_error_and_fallback_codes():
    metrics = _RecordingMetrics()
    service = HealthService(
        (
            DependencySpec(
                component="postgresql",
                provider="postgres",
                configuration_error="MISSING_URL",
            ),
            DependencySpec(
                component="postgresql",
                provider="postgres",
                dependency=_FakeDependency(error=RuntimeError("boom")),
            ),
            DependencySpec(
                component="application_grant",
                provider="http",
                dependency=_FakeDependency(error=RuntimeError("boom")),
            ),
            DependencySpec(
                component="embedding",
                provider="http",
                dependency=_FakeDependency(
                    error=VectorContractViolation("EMBEDDING_TIMEOUT", "slow")
                ),
            ),
            DependencySpec(
                component="responsibility_ce",
                provider="model",
                dependency=_FakeDependency(digest="d1"),
            ),
        ),
        metrics,
    )
    report = service.readiness()
    assert report.status == "not_ready"
    by_code = {item.error_code: item for item in report.components}
    assert by_code["MISSING_URL"].status == "unavailable"
    assert by_code["POSTGRES_UNAVAILABLE"].status == "unavailable"
    assert by_code["APPLICATION_GRANT_UNAVAILABLE"].status == "unavailable"
    assert by_code["EMBEDDING_TIMEOUT"].status == "unavailable"
    ready = next(item for item in report.components if item.status == "ready")
    assert ready.artifact_digest == "d1"
    assert len(metrics.increments) == 4


# ── outbox dispatcher ──


class _FakeOutboxUnitOfWork:
    def __init__(self, record=None):
        self._record = record

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def commit(self) -> None:
        return None

    @property
    def outbox(self):
        record = self._record

        class _Claim:
            def claim(self, *args, **kwargs):
                return record

        return _Claim()


def test_outbox_dispatcher_validation():
    with pytest.raises(ValueError):
        OutboxDispatcher(
            lambda: _FakeOutboxUnitOfWork(),
            object(),
            dispatcher_id="",
        )


def test_outbox_dispatcher_idle():
    dispatcher = OutboxDispatcher(
        lambda: _FakeOutboxUnitOfWork(),
        object(),
        dispatcher_id="d1",
    )
    result = dispatcher.dispatch_once()
    assert isinstance(result, OutboxDispatchResult)
    assert result.outcome == "idle"


def test_outbox_dispatcher_run_forever_stops_immediately():
    from threading import Event

    dispatcher = OutboxDispatcher(
        lambda: _FakeOutboxUnitOfWork(),
        object(),
        dispatcher_id="d1",
    )
    stop = Event()
    stop.set()
    dispatcher.run_forever(stop_event=stop, idle_sleep_seconds=0.0)


# ── queue configuration ──


def test_build_task_queue_selection_and_validation():
    memory = build_task_queue({"MATCHING_QUEUE_PROVIDER": "memory"})
    assert memory.provider == "memory"
    with pytest.raises(ValueError):
        build_task_queue(
            {
                "MATCHING_QUEUE_PROVIDER": "redis",
                "MATCHING_REDIS_URL": "",
            }
        )
    with pytest.raises(ValueError):
        build_task_queue({"MATCHING_QUEUE_PROVIDER": "unknown"})
    with pytest.raises(ValueError):
        build_task_queue(
            {"MATCHING_QUEUE_VISIBILITY_TIMEOUT_SECONDS": "0"}
        )
    with pytest.raises(ValueError):
        build_task_queue({"MATCHING_QUEUE_RETRY_INTERVAL_SECONDS": "-1"})
    with pytest.raises(ValueError):
        build_task_queue({"MATCHING_QUEUE_VISIBILITY_TIMEOUT_SECONDS": "abc"})
    assert _queue_positive_float("2.5", "x") == 2.5
    assert _non_negative_float("0", "x") == 0.0
    with pytest.raises(ValueError):
        _non_negative_float("-1", "x")


# ── persistence configuration (validation + sqlite test mode) ──


def test_build_persistence_selection_and_validation():
    memory = build_persistence({"MATCHING_PERSISTENCE_PROVIDER": "memory"})
    assert memory.provider == "memory"
    with pytest.raises(ValueError):
        build_persistence({"MATCHING_PERSISTENCE_PROVIDER": "unknown"})
    with pytest.raises(ValueError):
        build_persistence(
            {"MATCHING_PERSISTENCE_PROVIDER": "postgres", "MATCHING_DATABASE_URL": ""}
        )
    with pytest.raises(ValueError):
        build_persistence(
            {
                "MATCHING_PERSISTENCE_PROVIDER": "postgres",
                "MATCHING_DATABASE_URL": "mysql://x",
            }
        )
    sqlite = build_persistence(
        {
            "MATCHING_PERSISTENCE_PROVIDER": "postgres",
            "MATCHING_DATABASE_URL": "sqlite:///:memory:",
            "MATCHING_PERSISTENCE_SQLITE_TEST_MODE": "true",
        }
    )
    assert sqlite.provider == "postgres"
    with pytest.raises(ValueError):
        _positive_float("abc", "x")
    with pytest.raises(ValueError):
        _positive_float("0", "x")
    assert _positive_int("3", "x") == 3
    with pytest.raises(ValueError):
        _positive_int("0", "x")
    with pytest.raises(ValueError):
        _non_negative_int("-1", "x")


# ── fake vector adapters ──


def _fragment(fragment_id: str = "frag-1") -> SemanticFragment:
    return SemanticFragment(
        tenant_ref="tenant-a",
        fragment_id=fragment_id,
        source_type="cv",
        target_type="candidate_cv",
        source_id="cv:1",
        source_version="v1",
        source_profile_id="p" * 64,
        fragment_type="skill_context",
        normalized_text="Python",
        evidence_ref=Evidence(source_id="evidence:1", quote="Python"),
        language="en",
        sequence=0,
        taxonomy_version="taxonomy.v1",
    )


def test_fake_embedding_adapter_validation_and_lineage():
    with pytest.raises(ValueError):
        FakeEmbeddingAdapter(model="", revision="r", dimension=4)
    adapter = FakeEmbeddingAdapter(model="m", revision="r", dimension=4)
    request = EmbeddingRequest(
        tenant_ref="tenant-a",
        request_id="req-1",
        embedding_model="other",
        embedding_revision="r",
        dimension=4,
        text_derivation_version="v1",
        fragments=(_fragment(),),
    )
    with pytest.raises(VectorContractViolation):
        adapter.embed(request)


def test_fake_vector_store_search_filters_and_pii():
    store = FakeVectorStoreAdapter()
    record = VectorRecord.build(
        fragment=_fragment(),
        embedding=(1.0, 0.0, 0.0, 0.0),
        embedding_model="m",
        embedding_revision="r",
        payload={"text": "plain payload"},
    )
    store.upsert((record,))
    with pytest.raises(VectorContractViolation):
        store.upsert(
            (
                record.model_copy(
                    update={
                        "point_id": "pii-1",
                        "payload": {"phone": "13800138000"},
                    }
                ),
            )
        )
    query = VectorQuery(
        tenant_ref="tenant-a",
        embedding=(1.0, 0.0, 0.0, 0.0),
        embedding_model="m",
        embedding_revision="r",
        dimension=4,
        top_k=5,
        filter=VectorFilter(
            fragment_types=("skill_context",),
            source_ids=("cv:1",),
            target_types=("candidate_cv",),
            profile_version=None,
        ),
    )
    hits = store.search(query)
    assert isinstance(hits[0], VectorSearchHit)
    assert hits[0].point_id == record.point_id
    # filter excludes the record
    filtered = VectorFilter(
        fragment_types=("project_context",),
        source_ids=(),
        target_types=(),
        profile_version=None,
    )
    assert store.search(query.model_copy(update={"filter": filtered})) == ()
    store.deactivate(tenant_ref="tenant-a", point_ids=("missing",))
    store.activate(tenant_ref="tenant-a", point_ids=("missing",))
    assert len(store.list_points(tenant_ref="tenant-a")) == 1


# ── model artifact manifest ──


def test_model_artifact_build_and_verify(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"weights")
    (model_dir / "nested").mkdir()
    (model_dir / "nested" / "extra.bin").write_bytes(b"more")
    (model_dir / "manifest.json").write_text("{}", encoding="utf-8")
    manifest = build_manifest(
        model_dir,
        model_id="responsibility-ce",
        model_revision="rev-1",
    )
    assert manifest["schema_version"] == "responsibility-ce-artifact.v1"
    assert {item["path"] for item in manifest["files"]} == {
        "model.bin",
        "nested/extra.bin",
    }
    (model_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    assert verify_manifest(model_dir) == manifest["artifact_sha256"]


def test_model_artifact_errors(tmp_path):
    model_dir = tmp_path / "model"
    with pytest.raises(ModelArtifactError):
        build_manifest(model_dir, model_id="m", model_revision="r")
    model_dir.mkdir()
    with pytest.raises(ModelArtifactError):
        build_manifest(model_dir, model_id="m", model_revision="r")
    outside = tmp_path / "manifest.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ModelArtifactError):
        verify_manifest(model_dir, manifest_path=outside)
    with pytest.raises(ModelArtifactError):
        verify_manifest(model_dir)
    (model_dir / "manifest.json").write_text("not json {", encoding="utf-8")
    with pytest.raises(ModelArtifactError):
        verify_manifest(model_dir)
    (model_dir / "manifest.json").write_text(
        json.dumps({"schema_version": "other", "files": []}), encoding="utf-8"
    )
    with pytest.raises(ModelArtifactError):
        verify_manifest(model_dir)
    (model_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "responsibility-ce-artifact.v1",
                "files": [],
                "artifact_sha256": "x",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ModelArtifactError):
        verify_manifest(model_dir)


# ── degree normalization ──


def test_degree_normalization_and_parsing():
    assert normalize_degree(None) is None
    assert normalize_degree("") is None
    assert normalize_degree("硕士") == "master"
    assert normalize_degree("Master") == "master"
    assert normalize_degree("本科学历") == "bachelor"
    assert degree_rank("postdoc") == 5
    assert degree_rank("unknown") is None
    assert parse_degree_from_text("") is None
    assert parse_degree_from_text("要求硕士及以上学历") == "master"
    assert parse_degree_from_text("无学历要求") is None


# ── observability middleware ──


class _FakeMetricsRegistry:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def increment(self, name: str, **labels: str) -> None:
        self.calls.append((name, dict(labels)))

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        self.calls.append((name, dict(labels)))

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        return None


class _FakeLogger:
    def __init__(self):
        self.events: list[dict] = []

    def event(self, event: str, **fields: object) -> str:
        self.events.append({"event": event, **fields})
        return event


def test_observability_middleware_routes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    runtime = ApiRuntimeState()
    metrics = _FakeMetricsRegistry()
    logger = _FakeLogger()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    app.add_middleware(
        ObservabilityMiddleware,
        metrics=metrics,
        logger=logger,
        runtime=runtime,
    )
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/ok").status_code == 200
    assert client.get("/boom").status_code == 500
    runtime.accepting_requests = False
    assert client.get("/health/live").status_code == 200
    assert client.get("/ok").status_code == 503
    assert logger.events
    assert metrics.calls


# ── resource authorization ──


def _auth(roles: frozenset[str]) -> AuthContext:
    return AuthContext(
        subject_id="user-1",
        tenant_id="tenant-1",
        roles=roles,
        access_scope=derive_access_scope("user-1", "tenant-1", roles),
        token_id="token-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


class _CvAuth:
    def __init__(self, owner: bool = False):
        self._owner = owner

    def is_owner(self, context: AuthContext, cv_id: object) -> bool:
        return self._owner


class _Grants:
    def __init__(self, active: bool = True):
        self._active = active

    def has_active_grant(self, context: AuthContext, cv_id: object, position_id: object) -> bool:
        return self._active


def test_resource_authorization_allowed_paths():
    service = ResourceAuthorizationService(
        _CvAuth(owner=True),
        _Grants(active=True),
        _Grants(active=True),
    )
    candidate = _auth(frozenset({"candidate"}))
    service.authorize_cv(candidate, "cv-1")
    service.authorize_cv(candidate, None)
    service.authorize_match(candidate, "cv-1", "pos-1")
    enterprise = _auth(frozenset({"enterprise"}))
    service.authorize_match(enterprise, "cv-1", "pos-1", target_type="enterprise_job")
    service.authorize_match(enterprise, "cv-1", "pos-1")
    service_ctx = _auth(frozenset({"matching.service"}))
    service.authorize_cv(service_ctx, "cv-1")
    service.authorize_match(service_ctx, "cv-1", "pos-1")
    service.authorize_match(candidate, None, None)
    service.authorize_payload(candidate, "not-a-mapping")
    service.authorize_payload(candidate, {"cv_profile": {"cv_id": "cv-1"}})
    service.authorize_payload(
        candidate,
        {"cv_profile": {"cv_id": "cv-1"}, "position_profile": {"position_id": "p1"}},
    )


def test_resource_authorization_denied_paths():
    from app.application.resource_authorization import ResourceNotFoundError

    service = ResourceAuthorizationService(_CvAuth(owner=False), _Grants(active=False))
    candidate = _auth(frozenset({"candidate"}))
    with pytest.raises(ResourceNotFoundError):
        service.authorize_cv(candidate, "cv-1")
    with pytest.raises(ResourceNotFoundError):
        service.authorize_match(candidate, "cv-1", "pos-1")
    enterprise = _auth(frozenset({"enterprise"}))
    with pytest.raises(ResourceNotFoundError):
        service.authorize_match(enterprise, "cv-1", "pos-1", target_type="enterprise_job")
    with pytest.raises(ResourceNotFoundError):
        service.authorize_match(enterprise, "cv-1", "pos-1")
    with pytest.raises(AuthorizationError):
        service.authorize_cv(_auth(frozenset({"matching.worker"})), "cv-1")


# ── in-memory task queue edge paths ──


def _message(access_scope: str = "scope-a") -> TaskQueueMessage:
    return TaskQueueMessage(
        message_id="m1",
        task_id="t1",
        access_scope=access_scope,
        version_signature="v1",
        published_at=datetime.now(UTC),
    )


def test_memory_task_queue_validation_and_lifecycle():
    queue = InMemoryTaskQueue(visibility_timeout_seconds=60)
    with pytest.raises(TaskQueueError):
        queue.publish(_message(access_scope="13800138000"))
    with pytest.raises(TaskQueueError):
        queue.consume("")
    queue.publish(_message())
    delivery = queue.consume("worker-1")
    assert isinstance(delivery, QueueDelivery)
    queue.acknowledge(delivery)
    queue.publish(_message())
    delivery = queue.consume("worker-1")
    with pytest.raises(TaskQueueError):
        queue.retry(delivery, delay_seconds=-1, reason_code="X")
    queue.retry(delivery, delay_seconds=0, reason_code="RETRY")
    delivery = queue.consume("worker-1")
    with pytest.raises(TaskQueueError):
        queue.dead_letter(delivery, reason_code="")
    queue.dead_letter(delivery, reason_code="DEAD")
    with pytest.raises(ValueError):
        InMemoryTaskQueue(visibility_timeout_seconds=0)
