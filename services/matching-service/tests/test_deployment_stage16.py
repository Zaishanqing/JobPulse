from __future__ import annotations

import json
import os
import shutil
import socket
import statistics
import subprocess
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx
import jwt
import pytest

from app.domain.auth import derive_access_scope

ROOT = Path(__file__).parents[1]
COMPOSE = ROOT / "compose.yaml"


def test_compose_uses_one_image_with_migration_and_required_dependencies():
    compose = COMPOSE.read_text("utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text("utf-8")

    for service in (
        "matching-api:",
        "matching-worker:",
        "matching-migrate:",
        "postgres:",
        "redis:",
    ):
        assert service in compose
    assert "<<: *matching-image" in compose
    assert 'command: ["api"]' in compose
    assert 'command: ["worker"]' in compose
    assert 'command: ["migrate"]' in compose
    assert "service_completed_successfully" in compose
    assert "alembic current --check-heads" in entrypoint
    assert "alembic upgrade head" in entrypoint
    assert 'ENTRYPOINT ["/service/docker/entrypoint.sh"]' in dockerfile
    assert "change-me" not in compose


def test_deployment_example_contains_placeholders_not_generated_credentials():
    example = (ROOT / ".env.example").read_text("utf-8")

    assert "change-me-local" in example
    assert "change-me-generated-worker-jwt" in example
    assert "eyJ" not in example
    assert "MATCHING_EMBEDDING_PROVIDER" not in example
    assert "MATCHING_VECTOR_PROVIDER" not in example


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _docker_available() -> tuple[bool, str]:
    if os.getenv("MATCHING_RUN_DOCKER_INTEGRATION") != "1":
        return False, "set MATCHING_RUN_DOCKER_INTEGRATION=1 to run container integration"
    if shutil.which("docker") is None:
        return False, "docker CLI is unavailable"
    result = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, timeout=15, check=False
    )
    return result.returncode == 0, "Docker daemon is unavailable"


DOCKER_AVAILABLE, DOCKER_SKIP_REASON = _docker_available()


@pytest.fixture(scope="module")
def acceptance_upstream_stub() -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"data":{}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"data":{"authorized":true}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _token(key: str, role: str, subject: str, tenant: str, issuer: str, audience: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "tenant_id": tenant,
            "roles": [role],
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + 3600,
            "iss": issuer,
            "aud": audience,
        },
        key,
        algorithm="HS256",
    )


def _compose(project: str, env: dict[str, str], *args: str, check: bool = True):
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=check,
    )


def _wait_status(url: str, expected: int = 200, timeout: float = 60) -> httpx.Response:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5)
            if response.status_code == expected:
                return response
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(f"{url} did not return {expected}: {last_error}")


@pytest.fixture(scope="module")
def compose_stack(acceptance_upstream_stub) -> Iterator[dict[str, str]]:
    if not DOCKER_AVAILABLE:
        pytest.skip(DOCKER_SKIP_REASON)
    project = "matchingstage16" + uuid.uuid4().hex[:8]
    api_port = _free_port()
    worker_port = _free_port()
    key = "stage16-local-integration-signing-key-32-bytes"
    issuer = "https://stage16.local/issuer"
    audience = "matching-api"
    worker_token = _token(key, "matching.worker", "worker", "platform", issuer, audience)
    user_token = _token(key, "candidate", "user-a", "public", issuer, audience)
    service_token = _token(
        key, "matching.service", "pressure-service", "platform", issuer, audience
    )
    environment = {
        **os.environ,
        "MATCHING_POSTGRES_PASSWORD": "stage16-local-database-password",
        "MATCHING_AUTH_VERIFICATION_KEY": key,
        "MATCHING_AUTH_ISSUER": issuer,
        "MATCHING_AUTH_AUDIENCE": audience,
        "MATCHING_WORKER_TOKEN": worker_token,
        "MATCHING_API_PUBLISHED_PORT": str(api_port),
        "MATCHING_WORKER_PUBLISHED_PORT": str(worker_port),
        "MATCHING_QUEUE_VISIBILITY_TIMEOUT_SECONDS": "2",
        "MATCHING_QUEUE_RETRY_INTERVAL_SECONDS": "0.2",
        "MATCHING_REDIS_TIMEOUT_SECONDS": "0.5",
        "MATCHING_CV_SOURCE_URL": (
            f"http://host.docker.internal:{acceptance_upstream_stub}"
        ),
        "MATCHING_POSITION_SOURCE_URL": (
            f"http://host.docker.internal:{acceptance_upstream_stub}"
        ),
        "MATCHING_GRAPH_SOURCE_URL": (
            f"http://host.docker.internal:{acceptance_upstream_stub}"
        ),
        "MATCHING_GRAPH_VERSION": "acceptance-graph.v1",
        "MATCHING_CV_AUTHORIZATION_URL": (
            f"http://host.docker.internal:{acceptance_upstream_stub}/authorize/cv"
        ),
        "MATCHING_APPLICATION_GRANT_URL": (
            f"http://host.docker.internal:{acceptance_upstream_stub}/authorize/grant"
        ),
        "MATCHING_UPSTREAM_SERVICE_TOKEN": "acceptance-opaque-service-token",
    }
    try:
        _compose(project, environment, "up", "--build", "-d")
        api = f"http://127.0.0.1:{api_port}"
        worker = f"http://127.0.0.1:{worker_port}"
        _wait_status(f"{api}/health/ready", timeout=90)
        _wait_status(f"{worker}/health/ready", timeout=90)
        yield {
            "project": project,
            "api": api,
            "worker": worker,
            "token": user_token,
            "scope": derive_access_scope("user-a", "public", frozenset({"candidate"})),
            "env": environment,
            "worker_token": worker_token,
            "service_token": service_token,
            "service_scope": derive_access_scope(
                "pressure-service", "platform", frozenset({"matching.service"})
            ),
        }
    finally:
        _compose(project, environment, "down", "-v", "--remove-orphans", check=False)


def _request_headers(stack: dict[str, str], key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {stack['token']}",
        "X-Access-Scope": stack["scope"],
        "Idempotency-Key": key,
    }


def _service_request_headers(stack: dict[str, str], key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {stack['service_token']}",
        "X-Access-Scope": stack["service_scope"],
        "Idempotency-Key": key,
    }


def _postgres_scalar(stack: dict[str, str], sql: str) -> str:
    result = _compose(
        stack["project"],
        stack["env"],
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "matching",
        "-d",
        "matching",
        "-Atc",
        sql,
    )
    return result.stdout.strip()


def _wait_task(
    stack: dict[str, str],
    task_id: str,
    timeout: float = 60,
    *,
    headers: dict[str, str] | None = None,
) -> dict:
    deadline = time.monotonic() + timeout
    request_headers = headers or _request_headers(stack, "query-only")
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{stack['api']}/api/v1/evaluation-tasks/{task_id}",
            headers=request_headers,
            timeout=3,
        )
        if response.status_code == 200:
            task = response.json()["data"]["task"]
            if task["status"] == "succeeded":
                return task
        time.sleep(0.5)
    raise AssertionError(f"task {task_id} did not succeed")


@pytest.mark.container_integration
def test_compose_persistence_queue_recovery_dependency_recovery_and_safe_logs(
    compose_stack, ready_cv_json, ready_position_json
):
    stack = compose_stack
    payload = {"cv_profile": ready_cv_json, "position_profile": ready_position_json}
    headers = _request_headers(stack, "compose-idempotency")
    created = httpx.post(
        f"{stack['api']}/api/v1/evaluation-tasks",
        json=payload,
        headers={**headers, "X-Request-ID": "pii-probe@example.com"},
        timeout=10,
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task"]["task_id"]
    task = _wait_task(stack, task_id)
    evaluation_id = task["evaluation_id"]

    duplicate = httpx.post(
        f"{stack['api']}/api/v1/evaluation-tasks",
        json=payload,
        headers=headers,
        timeout=10,
    )
    assert duplicate.json()["data"]["task"]["task_id"] == task_id

    _compose(stack["project"], stack["env"], "restart", "matching-api")
    _wait_status(f"{stack['api']}/health/ready")
    persisted = httpx.get(
        f"{stack['api']}/api/v1/evaluations/{evaluation_id}",
        headers=headers,
        timeout=10,
    )
    assert persisted.status_code == 200
    assert persisted.json()["data"]["result"]["task_id"] == task_id

    _compose(stack["project"], stack["env"], "stop", "matching-worker")
    queued = httpx.post(
        f"{stack['api']}/api/v1/evaluation-tasks",
        json=payload,
        headers=_request_headers(stack, "worker-recovery"),
        timeout=10,
    ).json()["data"]["task"]
    pending_delivery = _compose(
        stack["project"],
        stack["env"],
        "exec",
        "-T",
        "redis",
        "redis-cli",
        "XREADGROUP",
        "GROUP",
        "matching:evaluation-tasks:workers",
        "crashed-worker",
        "COUNT",
        "1",
        "STREAMS",
        "matching:evaluation-tasks",
        ">",
    ).stdout
    assert queued["task_id"] in pending_delivery
    _postgres_scalar(
        stack,
        "UPDATE evaluation_tasks SET status='running', attempt=1, "
        "lease_owner='crashed-worker', lease_expires_at=now()-interval '1 second' "
        f"WHERE task_id='{queued['task_id']}'",
    )
    time.sleep(2.1)
    _compose(stack["project"], stack["env"], "start", "matching-worker")
    _wait_status(f"{stack['worker']}/health/ready")
    recovered_worker_task = _wait_task(stack, queued["task_id"])
    assert recovered_worker_task["attempt"] == 2

    _compose(stack["project"], stack["env"], "stop", "redis")
    _wait_status(f"{stack['api']}/health/ready", expected=503)
    unavailable = httpx.post(
        f"{stack['api']}/api/v1/evaluation-tasks",
        json=payload,
        headers=_request_headers(stack, "redis-recovery"),
        timeout=10,
    ).json()["data"]
    assert unavailable["error_code"] in {"TASK_QUEUE_TIMEOUT", "TASK_QUEUE_UNAVAILABLE"}
    _compose(stack["project"], stack["env"], "start", "redis")
    _wait_status(f"{stack['api']}/health/ready")
    recovered = httpx.post(
        f"{stack['api']}/api/v1/evaluation-tasks",
        json=payload,
        headers=_request_headers(stack, "redis-recovery"),
        timeout=10,
    ).json()["data"]["task"]
    _wait_task(stack, recovered["task_id"])

    _compose(stack["project"], stack["env"], "stop", "postgres")
    _wait_status(f"{stack['api']}/health/ready", expected=503)
    _compose(stack["project"], stack["env"], "start", "postgres")
    _wait_status(f"{stack['api']}/health/ready")
    assert httpx.get(
        f"{stack['api']}/api/v1/evaluations/{evaluation_id}",
        headers=headers,
        timeout=10,
    ).status_code == 200

    logs = _compose(stack["project"], stack["env"], "logs", "--no-color").stdout
    metrics = httpx.get(f"{stack['api']}/metrics", timeout=5).text
    for secret in (stack["token"], stack["worker_token"], "pii-probe@example.com"):
        assert secret not in logs
        assert secret not in metrics


@pytest.mark.container_integration
def test_compose_two_dispatcher_two_worker_pressure_and_claim_recovery(
    compose_stack, ready_cv_json, ready_position_json
):
    stack = compose_stack
    run_id = uuid.uuid4().hex[:10]
    prefix = f"stress-{run_id}-"
    payload = {"cv_profile": ready_cv_json, "position_profile": ready_position_json}

    _compose(stack["project"], stack["env"], "stop", "matching-dispatcher", "redis")
    _compose(
        stack["project"],
        stack["env"],
        "run",
        "-d",
        "--no-deps",
        "-e",
        "MATCHING_DISPATCHER_ID=matching-dispatcher-pressure",
        "matching-dispatcher",
    )
    _compose(
        stack["project"],
        stack["env"],
        "run",
        "-d",
        "--no-deps",
        "-e",
        "MATCHING_WORKER_ID=matching-worker-pressure",
        "matching-worker",
    )

    started: dict[str, float] = {}

    def create(index: int) -> tuple[str, str | None, int, str]:
        key = f"{prefix}{index}"
        started[key] = time.monotonic()
        response = httpx.post(
            f"{stack['api']}/api/v1/evaluation-tasks",
            json=payload,
            headers=_service_request_headers(stack, key),
            timeout=15,
        )
        if response.status_code != 200:
            return key, None, response.status_code, response.text
        data = response.json()["data"]
        assert data["task"] is not None
        assert data["error_code"] in {
            None,
            "TASK_QUEUE_TIMEOUT",
            "TASK_QUEUE_UNAVAILABLE",
        }
        return key, data["task"]["task_id"], response.status_code, ""

    with ThreadPoolExecutor(max_workers=20) as executor:
        responses = list(executor.map(create, range(100)))
    failures = [item for item in responses if item[1] is None]
    if failures:
        api_logs = _compose(
            stack["project"], stack["env"], "logs", "--no-color", "matching-api"
        ).stdout
        pytest.fail(f"pressure request failures={failures}; api_logs_tail={api_logs[-8000:]}")
    created = [(key, task_id) for key, task_id, _, _ in responses if task_id is not None]
    assert len({task_id for _, task_id in created}) == 100

    updated = _postgres_scalar(
        stack,
        "WITH updated AS (UPDATE outbox_records o SET status='claimed', "
        "claimed_by='crashed-dispatcher', claim_expires_at=now()+interval '2 seconds' "
        "FROM evaluation_tasks t WHERE o.access_scope=t.access_scope "
        "AND o.task_id=t.task_id AND t.idempotency_key LIKE '"
        f"{prefix}%' RETURNING o.outbox_id) SELECT count(*) FROM updated",
    )
    assert int(updated) == 100

    _compose(stack["project"], stack["env"], "start", "matching-dispatcher", "redis")
    _wait_status(f"{stack['api']}/health/ready", timeout=60)

    def complete(item: tuple[str, str]) -> float:
        key, task_id = item
        _wait_task(
            stack,
            task_id,
            timeout=120,
            headers=_service_request_headers(stack, f"query-{key}"),
        )
        return time.monotonic() - started[key]

    with ThreadPoolExecutor(max_workers=20) as executor:
        latencies = list(executor.map(complete, created))

    # Replay a sample after successful commit; no second task/outbox/result is created.
    for index in range(10):
        response = httpx.post(
            f"{stack['api']}/api/v1/evaluation-tasks",
            json=payload,
            headers=_service_request_headers(stack, f"{prefix}{index}"),
            timeout=10,
        )
        if response.status_code != 200:
            api_logs = _compose(
                stack["project"], stack["env"], "logs", "--no-color", "matching-api"
            ).stdout
            pytest.fail(
                f"idempotent replay status={response.status_code} body={response.text}; "
                f"api_logs_tail={api_logs[-12000:]}"
            )
        assert response.json()["data"]["task"]["task_id"] == created[index][1]

    counts = _postgres_scalar(
        stack,
        "SELECT count(*) FILTER (WHERE status='succeeded'),"
        "count(*) FILTER (WHERE status='failed'),"
        "count(*) FILTER (WHERE status='pending'),"
        "count(*) FILTER (WHERE status='running') "
        "FROM evaluation_tasks WHERE idempotency_key LIKE '"
        f"{prefix}%'",
    )
    succeeded, failed, pending, running = map(int, counts.split("|"))
    succeeded_audits = int(
        _postgres_scalar(
            stack,
            "SELECT count(*) FROM audit_records a JOIN evaluation_tasks t "
            "ON a.access_scope=t.access_scope AND a.task_id=t.task_id "
            "WHERE t.idempotency_key LIKE '"
            f"{prefix}%' AND a.event_type='task_succeeded'",
        )
    )
    retries = int(
        _postgres_scalar(
            stack,
            "SELECT count(*) FROM audit_records a JOIN evaluation_tasks t "
            "ON a.access_scope=t.access_scope AND a.task_id=t.task_id "
            "WHERE t.idempotency_key LIKE '"
            f"{prefix}%' AND a.event_type='task_retried'",
        )
    )
    results = int(
        _postgres_scalar(
            stack,
            "SELECT count(*) FROM persisted_evaluations p JOIN evaluation_tasks t "
            "ON p.access_scope=t.access_scope AND p.task_id=t.task_id "
            "WHERE t.idempotency_key LIKE '"
            f"{prefix}%'",
        )
    )
    dlq = int(
        _compose(
            stack["project"],
            stack["env"],
            "exec",
            "-T",
            "redis",
            "redis-cli",
            "HLEN",
            "matching:evaluation-tasks:dead-letter:v2",
        ).stdout.strip()
    )
    logs = _compose(stack["project"], stack["env"], "logs", "--no-color").stdout
    lower_logs = logs.lower()
    p95 = sorted(latencies)[94]
    stats = {
        "submitted": 100,
        "succeeded": succeeded,
        "failed": failed,
        "pending": pending,
        "running": running,
        "results": results,
        "succeeded_audits": succeeded_audits,
        "duplicate_business_executions": max(0, succeeded_audits - succeeded),
        "retries": retries,
        "dlq": dlq,
        "database_pool_errors": lower_logs.count("queuepool limit"),
        "redis_errors": lower_logs.count("queue error code="),
        "average_seconds": round(statistics.mean(latencies), 3),
        "p95_seconds": round(p95, 3),
    }
    print("MATCHING_PRESSURE_STATS=" + json.dumps(stats, sort_keys=True))
    assert stats == {
        **stats,
        "succeeded": 100,
        "failed": 0,
        "pending": 0,
        "running": 0,
        "results": 100,
        "succeeded_audits": 100,
        "duplicate_business_executions": 0,
        "dlq": 0,
    }

    for forbidden in (
        stack["token"],
        stack["worker_token"],
        stack["service_token"],
        "acceptance-opaque-service-token",
        "Python services",
        "Build backend services",
    ):
        assert forbidden not in logs
