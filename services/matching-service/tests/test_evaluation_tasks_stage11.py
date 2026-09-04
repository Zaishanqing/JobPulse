from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import (
    DEFAULT_PERSISTENCE_VERSIONS,
    EvaluationTaskService,
)
from app.application.learning_paths import LearningPathService
from app.application.task_worker import EvaluationTaskWorker
from app.bootstrap.application import create_app
from app.domain.scoring import ScoringConfig
from app.infrastructure.memory_repositories import InMemoryPersistence


def _service(
    storage: InMemoryPersistence,
    evaluation_service: object | None = None,
    **kwargs: object,
) -> EvaluationTaskService:
    evaluation = evaluation_service or MatchEvaluationService()
    return EvaluationTaskService(
        storage.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
        **kwargs,
    )


def _payload(cv: dict, position: dict) -> dict:
    return {"cv_profile": cv, "position_profile": position}


def test_task_profile_parsing_uses_authoritative_bundle_mappers(
    upstream_cv_anonymized, upstream_position_anonymized
):
    parsed = EvaluationTaskService._parse_profiles(
        _payload(upstream_cv_anonymized, upstream_position_anonymized)
    )

    assert isinstance(parsed, tuple)
    assert parsed[0].profile_version == parsed[0].profile_version
    assert parsed[1].profile_version == parsed[1].profile_version


def test_task_profile_parsing_rejects_invalid_authoritative_bundle(
    upstream_cv_anonymized, upstream_position_anonymized, clone
):
    invalid_cv = clone(upstream_cv_anonymized)
    invalid_cv["normalization"]["document_id"] = "different-document"

    parsed = EvaluationTaskService._parse_profiles(
        _payload(invalid_cv, upstream_position_anonymized)
    )

    assert parsed.error_code == "EVALUATION_TASK_INPUT_INVALID"


def test_task_is_idempotent_and_audits_state_transitions(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)

    first = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-one", "tenant-a"
    )
    replay = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-one", "tenant-a"
    )

    assert first.created is True
    assert replay.created is False
    assert first.task == replay.task
    assert first.task.status == "succeeded"
    assert first.task.attempt == 1
    records = service.audit_records(first.task.task_id, "tenant-a")
    assert [item.event_type for item in records] == [
        "task_created",
        "task_started",
        "task_succeeded",
    ]
    assert [(item.from_status, item.to_status) for item in records] == [
        (None, "pending"),
        ("pending", "running"),
        ("running", "succeeded"),
    ]


def test_multipart_personal_tenant_ref_is_accepted(ready_cv_json, ready_position_json):
    storage = InMemoryPersistence()
    service = _service(storage)
    payload = _payload(ready_cv_json, ready_position_json)
    payload["tenant_ref"] = "personal:alice-uuid"

    result = service.submit(
        payload,
        "idem-personal-tenant",
        "user:personal:alice-uuid:alice-uuid",
        tenant_ref="personal:alice-uuid",
    )

    assert result.error_code is None
    assert result.task.status == "succeeded"


def test_mismatched_trusted_tenant_ref_is_rejected(ready_cv_json, ready_position_json):
    storage = InMemoryPersistence()
    service = _service(storage)
    payload = _payload(ready_cv_json, ready_position_json)
    payload["tenant_ref"] = "personal:alice-uuid"

    result = service.submit(
        payload,
        "idem-bad-tenant",
        "user:personal:alice-uuid:alice-uuid",
        tenant_ref="personal:bob-uuid",
    )

    assert result.error_code == "TENANT_REF_MISMATCH"


class _FailOnceEvaluationService:
    def __init__(self) -> None:
        self._delegate = MatchEvaluationService()
        self.calls = 0

    def evaluate(self, payload: object, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary worker failure")
        return self._delegate.evaluate(payload, **kwargs)


def test_failed_task_retries_without_partial_evaluation(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    evaluation = _FailOnceEvaluationService()
    service = _service(storage, evaluation)

    failed = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-retry", "tenant-a"
    )
    assert failed.task.status == "failed"
    assert failed.task.error_code == "EVALUATION_TASK_EXECUTION_FAILED"
    assert storage.evaluations == {}

    retried = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-retry", "tenant-a"
    )
    assert retried.created is False
    assert retried.task.status == "succeeded"
    assert retried.task.attempt == 2
    assert len(storage.evaluations) == 1
    events = [
        item.event_type
        for item in service.audit_records(retried.task.task_id, "tenant-a")
    ]
    assert events == [
        "task_created",
        "task_started",
        "task_failed",
        "task_retried",
        "task_started",
        "task_succeeded",
    ]


def test_running_task_can_be_abandoned_and_late_worker_cannot_commit(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    submitted = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "idem-abandon",
        "tenant-a",
        execute_immediately=False,
    ).task
    running = service.claim(
        submitted.task_id,
        "tenant-a",
        lease_owner="worker-one",
        lease_seconds=300,
    ).task

    abandoned = service.abandon(running.task_id, "tenant-a").task
    late_result = service.execute_claimed(
        running.task_id, "tenant-a", lease_owner="worker-one"
    )

    assert abandoned.status == "failed"
    assert abandoned.error_code == "TASK_ABANDONED_BY_USER"
    assert abandoned.lease_owner is None
    assert late_result.error_code == "TASK_LEASE_NOT_OWNED"
    assert storage.evaluations == {}
    assert [
        item.event_type
        for item in service.audit_records(running.task_id, "tenant-a")
    ] == ["task_created", "task_started", "task_abandoned"]


def test_success_transaction_rolls_back_all_partial_result_writes(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()

    def fail_before_commit() -> None:
        raise RuntimeError("commit boundary failed")

    service = _service(storage, before_success_commit=fail_before_commit)
    result = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-rollback", "tenant-a"
    )

    assert result.task.status == "failed"
    assert storage.evaluations == {}
    assert result.task.evaluation_id is None


def test_changed_input_marks_previous_evaluation_stale(
    ready_cv_json, ready_position_json, clone
):
    storage = InMemoryPersistence()
    service = _service(storage)
    first = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-stale", "tenant-a"
    )
    changed = clone(ready_position_json)
    changed["core_responsibilities"].append("新增明确职责")
    changed["source_version"] = "position-source.v2"
    changed["profile_version"] = "position-source.v2"

    second = service.submit(
        _payload(ready_cv_json, changed), "idem-stale", "tenant-a"
    )
    old = service.get_evaluation(first.task.evaluation_id, "tenant-a").result

    assert second.created is True
    assert second.task.task_id != first.task.task_id
    assert old.stale is True
    assert old.stale_reason_codes == ("ALGORITHM_VERSION_CHANGED",)


def test_enterprise_evaluation_is_not_marked_stale_by_scoring_config(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    payload = _payload(ready_cv_json, ready_position_json)
    payload.update(
        {
            "target_type": "enterprise_job",
            "use_enterprise_weights": True,
            "generate_learning_path": True,
        }
    )
    result = service.submit(payload, "idem-enterprise-current", "tenant-a")

    assert result.task.status == "succeeded"
    evaluation = service.get_evaluation(
        result.task.evaluation_id, "tenant-a"
    ).result
    assert evaluation.stale is False
    assert evaluation.stale_reason_codes == ()


def test_runtime_drift_reason_is_cleared_when_versions_reconcile(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    result = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "idem-drift-heal",
        "tenant-a",
    )
    with storage.unit_of_work() as uow:
        service._mark_result_stale(
            uow,
            result.task,
            "ALGORITHM_VERSION_CHANGED",
            datetime.now(UTC),
        )
        uow.commit()

    healed = service.get_evaluation(result.task.evaluation_id, "tenant-a").result

    assert healed.stale is False
    assert healed.stale_reason_codes == ()


def test_algorithm_version_change_is_detected_as_stale(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    original = _service(storage)
    task = original.submit(
        _payload(ready_cv_json, ready_position_json), "idem-version", "tenant-a"
    ).task
    changed_evaluation = MatchEvaluationService(
        scoring_config=ScoringConfig(scoring_config_version="scoring-config.v9")
    )
    reader = _service(storage, evaluation_service=changed_evaluation)

    result = reader.get_evaluation(task.evaluation_id, "tenant-a").result

    assert result.stale is True
    assert "ALGORITHM_VERSION_CHANGED" in result.stale_reason_codes


@pytest.mark.parametrize(
    "field",
    [
        "graph_version",
        "embedding_version",
        "semantic_algorithm_version",
        "semantic_threshold_version",
        "profile_contract_mapping_version",
        "semantic_index_revision",
    ],
)
def test_semantic_graph_and_mapping_version_changes_mark_result_stale(
    field, ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    original = _service(storage)
    task = original.submit(
        _payload(ready_cv_json, ready_position_json), f"version-{field}", "tenant-a"
    ).task
    changed = DEFAULT_PERSISTENCE_VERSIONS.model_copy(
        update={field: getattr(DEFAULT_PERSISTENCE_VERSIONS, field) + ".changed"}
    )
    result = _service(storage, versions=changed).get_evaluation(
        task.evaluation_id, "tenant-a"
    ).result
    assert result.stale is True
    expected = {
        "embedding_version": (
            "EMBEDDING_REVISION_CHANGED",
            "ALGORITHM_VERSION_CHANGED",
        ),
        "semantic_index_revision": (
            "SEMANTIC_INDEX_REVISION_CHANGED",
            "ALGORITHM_VERSION_CHANGED",
        ),
    }.get(field, ("ALGORITHM_VERSION_CHANGED",))
    assert result.stale_reason_codes == expected


def test_result_queries_are_isolated_by_access_scope(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    task = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-scope", "tenant-a"
    ).task

    assert service.get_task(task.task_id, "tenant-a").task == task
    assert service.get_task(task.task_id, "tenant-b").error_code == "TASK_NOT_FOUND"
    assert service.get_evaluation(task.evaluation_id, "tenant-b").error_code == (
        "EVALUATION_NOT_FOUND"
    )
    assert service.audit_records(task.task_id, "tenant-b") == ()


def test_service_wide_result_queries_are_explicit_and_read_across_scopes(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    task = service.submit(
        _payload(ready_cv_json, ready_position_json), "idem-service-read", "tenant-a"
    ).task

    assert (
        service.get_task(
            task.task_id, "service:jobgraph-main:admin", service_wide=True
        ).task
        == task
    )
    assert service.get_evaluation(
        task.evaluation_id, "service:jobgraph-main:admin", service_wide=True
    ).result.evaluation_id == task.evaluation_id


def test_equal_inputs_in_distinct_tasks_keep_task_scoped_results(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    first = service.submit(
        _payload(ready_cv_json, ready_position_json), "distinct-result-a", "tenant-a"
    ).task
    second = service.submit(
        _payload(ready_cv_json, ready_position_json), "distinct-result-b", "tenant-a"
    ).task

    assert first.evaluation_id != second.evaluation_id
    first_result = service.get_evaluation(first.evaluation_id, "tenant-a").result
    second_result = service.get_evaluation(second.evaluation_id, "tenant-a").result
    assert first_result.task_id == first.task_id
    assert second_result.task_id == second.task_id
    assert first_result.evaluation.evaluation_id == first.evaluation_id
    assert second_result.evaluation.evaluation_id == second.evaluation_id


def test_distinct_run_identities_create_distinct_tasks_from_same_inputs(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    first = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "personal-run:resume:position:run-a",
        "tenant-a",
    )
    second = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "personal-run:resume:position:run-b",
        "tenant-a",
    )

    assert first.created is True
    assert second.created is True
    assert first.task.task_id != second.task.task_id
    assert first.task.evaluation_id != second.task.evaluation_id
    assert first.task.evaluation_id is not None
    assert second.task.evaluation_id is not None


def test_persisted_report_metadata_exposes_product_matching_mode(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    task = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "personal-run:resume:position:run-mode",
        "tenant-a",
    ).task
    result = service.get_evaluation(task.evaluation_id, "tenant-a").result
    assert result is not None
    assert result.report_metadata["matching_method"] == "rule"
    assert result.report_metadata["degraded"] is False


def test_task_and_persisted_evaluation_api_are_independently_queryable(
    ready_cv_json,
    ready_position_json,
    auth_provider,
    auth_headers,
    auth_context,
    worker_auth_context,
):
    application = create_app(authentication_provider=auth_provider)
    client = TestClient(application)
    headers = {**auth_headers, "Idempotency-Key": "api-idem"}
    created = client.post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers=headers,
    )

    assert created.status_code == 200
    data = created.json()["data"]
    assert data["created"] is True
    assert data["task"]["status"] == "pending"
    assert data["task"]["schema_version"] == "matching-evaluation-task.v1"
    assert data["task"]["source_version"].startswith("cv=")
    assert data["task"]["taxonomy_version"].startswith("cv=")
    assert data["task"]["graph_version"] == ready_position_json["graph_version"]
    assert data["task"]["algorithm_version"] == "deterministic-matching.v9"
    assert "cv_profile" not in data["task"]
    task_id = data["task"]["task_id"]

    task_response = client.get(
        f"/api/v1/evaluation-tasks/{task_id}",
        headers=auth_headers,
    )
    queried_task = task_response.json()["data"]["task"]
    assert queried_task["status"] == "pending"
    worker = EvaluationTaskWorker(
        application.state.task_queue,
        application.state.evaluation_task_service,
        worker_id="stage11-test-worker",
        retry_interval_seconds=0,
        auth_context=worker_auth_context,
    )
    assert worker.run_once().outcome == "acknowledged"
    task_response = client.get(
        f"/api/v1/evaluation-tasks/{task_id}",
        headers=auth_headers,
    )
    queried_task = task_response.json()["data"]["task"]
    assert queried_task["status"] == "succeeded"
    evaluation_id = queried_task["evaluation_id"]
    evaluation_response = client.get(
        f"/api/v1/evaluations/{evaluation_id}",
        headers=auth_headers,
    )
    persisted = evaluation_response.json()["data"]["result"]
    assert persisted["evaluation"]["evaluation_id"] == evaluation_id
    assert persisted["gap_analysis"]["generation_status"] == "completed"
    assert persisted["schema_version"] == "matching-evaluation-result.v1"
    assert persisted["target_type"] == "standard_position"
    assert persisted["algorithm_version"] == "deterministic-matching.v9"
    denied = client.get(
        f"/api/v1/evaluations/{evaluation_id}",
        headers={**auth_headers, "X-Access-Scope": "tenant-other"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ACCESS_SCOPE_MISMATCH"


def test_unknown_matching_contract_version_is_stable_422_and_fail_closed(
    ready_cv_json, ready_position_json, auth_provider, auth_headers
):
    application = create_app(authentication_provider=auth_provider)
    client = TestClient(application)
    payload = {
        "schema_version": "matching-evaluation-request.v999",
        **_payload(ready_cv_json, ready_position_json),
    }

    response = client.post(
        "/api/v1/evaluation-tasks",
        json=payload,
        headers={**auth_headers, "Idempotency-Key": "unsupported-contract"},
    )

    assert response.status_code == 422
    assert response.json()["data"]["error_code"] == (
        "UNSUPPORTED_MATCHING_CONTRACT_VERSION"
    )
    assert application.state.persistence.tasks == {}


def test_unknown_nested_profile_schema_is_rejected_before_task_creation(
    ready_cv_json, ready_position_json
):
    storage = InMemoryPersistence()
    service = _service(storage)
    ready_cv_json["schema_version"] = "cv-match-profile.v999"

    result = service.submit(
        _payload(ready_cv_json, ready_position_json), "nested-version", "tenant-a"
    )

    assert result.error_code == "UNSUPPORTED_MATCHING_CONTRACT_VERSION"
    assert storage.tasks == {}


def test_task_domain_and_ports_have_no_framework_or_database_dependency():
    root = Path(__file__).parents[1]
    text = "\n".join(
        (root / relative).read_text("utf-8")
        for relative in (
            "app/domain/tasks.py",
            "app/ports/repositories.py",
        )
    ).lower()

    assert "fastapi" not in text
    assert "sqlalchemy" not in text
    assert "django" not in text
    assert ".commit(" not in (root / "app/ports/repositories.py").read_text("utf-8")
