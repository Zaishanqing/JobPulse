from copy import deepcopy
from types import SimpleNamespace

import jwt
from fastapi.testclient import TestClient

import app.dispatcher as dispatcher_entrypoint
import app.worker as worker_entrypoint
from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.learning_paths import LearningPathService
from app.application.task_worker import EvaluationTaskWorker
from app.bootstrap.application import create_app
from app.infrastructure.memory_repositories import InMemoryPersistence


def test_matching_report_radar_learning_path_stale_and_rematch_closure(
    ready_cv_json,
    ready_position_json,
    auth_provider,
    auth_headers,
    worker_auth_context,
):
    application = create_app(authentication_provider=auth_provider)
    client = TestClient(application)
    worker = EvaluationTaskWorker(
        application.state.task_queue,
        application.state.evaluation_task_service,
        worker_id="batch3-e2e-worker",
        retry_interval_seconds=0,
        auth_context=worker_auth_context,
    )
    payload = {
        "schema_version": "matching-evaluation-request.v1",
        "target_type": "standard_position",
        "use_enterprise_weights": False,
        "generate_learning_path": True,
        "cv_profile": ready_cv_json,
        "position_profile": ready_position_json,
    }
    headers = {**auth_headers, "Idempotency-Key": "batch3-full-closure"}

    submitted = client.post("/api/v1/evaluation-tasks", json=payload, headers=headers)
    assert submitted.status_code == 200
    first_task = submitted.json()["data"]["task"]
    assert first_task["target_type"] == "standard_position"
    assert first_task["use_enterprise_weights"] is False
    assert first_task["generate_learning_path"] is True
    duplicate = client.post("/api/v1/evaluation-tasks", json=payload, headers=headers)
    assert duplicate.json()["data"]["created"] is False
    assert duplicate.json()["data"]["task"]["task_id"] == first_task["task_id"]

    assert worker.run_once().outcome == "acknowledged"
    task = client.get(
        f"/api/v1/evaluation-tasks/{first_task['task_id']}", headers=auth_headers
    ).json()["data"]["task"]
    assert task["status"] == "succeeded"
    report = client.get(
        f"/api/v1/evaluations/{task['evaluation_id']}", headers=auth_headers
    ).json()["data"]["result"]
    assert report["report_metadata"]["provider"] == "matching-service"
    assert report["report_metadata"]["data_versions"]["cv_source"]
    assert len(report["radar_dimensions"]) == 6
    assert all(
        row["candidate_score"] is None or 0 <= row["candidate_score"] <= 100
        for row in report["radar_dimensions"]
    )
    assert report["gap_analysis"]["generation_status"] == "completed"
    learning = client.post(
        "/api/v1/learning-paths",
        json={"evaluation": report["evaluation"]},
        headers=auth_headers,
    )
    assert learning.status_code == 200
    learning_gap = learning.json()["data"]
    assert learning_gap["learning_path"] == [] or (
        learning_gap.get("minimal_action_set") is not None
        and learning_gap["minimal_action_set"]["status"]
        in {"no_positive_actions", "unreachable", "budget_excluded"}
    )

    changed = deepcopy(ready_position_json)
    changed["core_responsibilities"].append("Own the updated production workflow")
    changed["source_version"] = "position-source.v2"
    changed["profile_version"] = "position-source.v2"
    rematch_payload = {**payload, "position_profile": changed}
    rematch = client.post(
        "/api/v1/evaluation-tasks", json=rematch_payload, headers=headers
    ).json()["data"]
    assert rematch["created"] is True
    stale = client.get(
        f"/api/v1/evaluations/{task['evaluation_id']}", headers=auth_headers
    ).json()["data"]["result"]
    assert stale["stale"] is True
    assert "ALGORITHM_VERSION_CHANGED" in stale["stale_reason_codes"]
    assert worker.run_once().outcome == "acknowledged"
    refreshed = client.get(
        f"/api/v1/evaluation-tasks/{rematch['task']['task_id']}", headers=auth_headers
    ).json()["data"]["task"]
    assert refreshed["status"] == "succeeded"
    assert refreshed["evaluation_id"] != task["evaluation_id"]


def test_enterprise_weights_are_validated_instead_of_silently_ignored(
    ready_cv_json, ready_position_json, auth_provider, auth_headers
):
    client = TestClient(create_app(authentication_provider=auth_provider))
    response = client.post(
        "/api/v1/evaluation-tasks",
        json={
            "target_type": "standard_position",
            "use_enterprise_weights": True,
            "generate_learning_path": False,
            "cv_profile": ready_cv_json,
            "position_profile": ready_position_json,
        },
        headers={**auth_headers, "Idempotency-Key": "invalid-enterprise-weights"},
    )
    assert response.json()["data"]["error_code"] == "ENTERPRISE_WEIGHTS_TARGET_INVALID"


def test_enterprise_weight_selection_changes_persisted_scoring_version(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    evaluation = MatchEvaluationService()
    service = EvaluationTaskService(
        storage.unit_of_work, evaluation, LearningPathService(evaluation)
    )
    result = service.submit(
        {
            "target_type": "enterprise_job",
            "use_enterprise_weights": True,
            "generate_learning_path": False,
            "cv_profile": ready_cv_json,
            "position_profile": ready_position_json,
        },
        "enterprise-weights",
        "tenant-enterprise",
    )
    persisted = service.get_evaluation(
        result.task.evaluation_id, "tenant-enterprise"
    ).result
    assert result.task.versions.use_enterprise_weights is True
    assert result.task.versions.scoring_config_version == "scoring-config.enterprise.v3"
    assert persisted.evaluation.final_match_result.scoring_config_version == (
        "scoring-config.enterprise.v3"
    )
    assert persisted.gap_analysis.error_code == "LEARNING_PATH_NOT_REQUESTED"
    gap = LearningPathService(evaluation).generate(
        {
            "evaluation": persisted.evaluation.model_dump(mode="json"),
            "time_budget_hours": 10,
        }
    )
    assert gap.generation_status == "completed"
    assert gap.time_budget_hours == 10
    assert gap.counterfactual_suggestions
    assert gap.learning_path == ()
    assert gap.minimal_action_set is None or gap.minimal_action_set.status in {
        "reached",
        "no_positive_actions",
        "unreachable",
        "budget_excluded",
    }


def test_root_compose_worker_can_mint_and_use_its_internal_credential(monkeypatch):
    monkeypatch.delenv("MATCHING_WORKER_SIGNING_KEY", raising=False)
    assert worker_entrypoint._worker_credential("worker-no-key") == ""

    monkeypatch.setenv("MATCHING_WORKER_SIGNING_KEY", "batch3-worker-signing-key")
    monkeypatch.setenv("MATCHING_AUTH_ISSUER", "https://batch3.test/issuer")
    monkeypatch.setenv("MATCHING_AUTH_AUDIENCE", "batch3-api")
    token = worker_entrypoint._worker_credential("batch3-worker")
    claims = jwt.decode(
        token,
        "batch3-worker-signing-key",
        algorithms=["HS256"],
        issuer="https://batch3.test/issuer",
        audience="batch3-api",
    )
    assert claims["sub"] == "batch3-worker"
    assert claims["roles"] == ["matching.worker"]

    authenticated = []
    state = SimpleNamespace(
        authentication_provider=SimpleNamespace(
            authenticate=lambda credential: authenticated.append(credential) or object()
        ),
        task_queue=object(),
        evaluation_task_service=object(),
        queue_retry_interval_seconds=2.0,
        metrics_registry=object(),
        structured_logger=object(),
        health_service=object(),
    )
    monkeypatch.setattr(worker_entrypoint, "create_app", lambda: SimpleNamespace(state=state))
    monkeypatch.setattr(worker_entrypoint, "require_worker", lambda context: None)
    monkeypatch.setattr(
        worker_entrypoint,
        "EvaluationTaskWorker",
        lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs),
    )
    built = worker_entrypoint.build_worker("batch3-worker")
    authenticated_claims = jwt.decode(
        authenticated[0],
        "batch3-worker-signing-key",
        algorithms=["HS256"],
        issuer="https://batch3.test/issuer",
        audience="batch3-api",
    )
    assert authenticated_claims["sub"] == "batch3-worker"
    assert built.kwargs["worker_id"] == "batch3-worker"
    assert built.kwargs["retry_interval_seconds"] == 2.0


def test_worker_entrypoint_stops_monitor_and_worker_on_interrupt(monkeypatch):
    events = []

    class WorkerStub:
        metrics_registry = object()

        def run_forever(self):
            events.append("run")
            raise KeyboardInterrupt

        def shutdown(self, timeout):
            events.append(("shutdown", timeout))

    class MonitorStub:
        def __init__(self, *args, **kwargs):
            events.append(("monitor", kwargs["port"]))

        def start(self):
            events.append("monitor-start")

        def stop(self):
            events.append("monitor-stop")

    monkeypatch.setenv("MATCHING_WORKER_SHUTDOWN_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("MATCHING_WORKER_METRICS_PORT", "9191")
    monkeypatch.setattr(worker_entrypoint, "build_worker", lambda: WorkerStub())
    monkeypatch.setattr(worker_entrypoint, "WorkerMonitoringServer", MonitorStub)
    monkeypatch.setattr(worker_entrypoint.signal, "signal", lambda *args: None)
    assert worker_entrypoint.main() == 0
    assert events == [
        ("monitor", 9191),
        "monitor-start",
        "run",
        ("shutdown", 3.0),
        ("shutdown", 3.0),
        "monitor-stop",
    ]


def test_worker_entrypoint_signal_requests_graceful_shutdown(monkeypatch):
    handlers = {}
    shutdowns = []

    class WorkerStub:
        metrics_registry = object()

        def run_forever(self):
            handlers[worker_entrypoint.signal.SIGTERM](worker_entrypoint.signal.SIGTERM, None)

        def shutdown(self, timeout):
            shutdowns.append(timeout)

    class MonitorStub:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setenv("MATCHING_WORKER_SHUTDOWN_TIMEOUT_SECONDS", "4")
    monkeypatch.setattr(worker_entrypoint, "build_worker", lambda: WorkerStub())
    monkeypatch.setattr(worker_entrypoint, "WorkerMonitoringServer", MonitorStub)
    monkeypatch.setattr(
        worker_entrypoint.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    assert worker_entrypoint.main() == 0
    assert shutdowns == [4.0, 4.0]


def test_root_compose_dispatcher_builds_with_redis_queue(monkeypatch):
    captured = {}
    persistence = SimpleNamespace(provider="postgres", unit_of_work=object())
    queue = SimpleNamespace(provider="redis", queue=object())
    monkeypatch.setenv("MATCHING_RUNTIME_MODE", "production")
    monkeypatch.setenv("MATCHING_OUTBOX_LEASE_SECONDS", "12")
    monkeypatch.setenv("MATCHING_OUTBOX_RETRY_INTERVAL_SECONDS", "2")
    monkeypatch.setattr(dispatcher_entrypoint, "build_persistence", lambda: persistence)
    monkeypatch.setattr(dispatcher_entrypoint, "build_task_queue", lambda: queue)
    monkeypatch.setattr(
        dispatcher_entrypoint,
        "OutboxDispatcher",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or captured,
    )
    built = dispatcher_entrypoint.build_dispatcher("batch3-dispatcher")
    assert built["args"] == (persistence.unit_of_work, queue.queue)
    assert built["kwargs"] == {
        "dispatcher_id": "batch3-dispatcher",
        "lease_seconds": 12.0,
        "retry_interval_seconds": 2.0,
    }
