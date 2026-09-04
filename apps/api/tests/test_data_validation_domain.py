from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from app.contexts.data_validation import (
    DataValidationError,
    DataValidationTask,
    DataValidationTaskStatus,
    ValidatedBundleSnapshot,
    ValidationConclusion,
    ValidationReport,
)


NOW = datetime(2026, 7, 24, 8, tzinfo=timezone.utc)


def _task() -> DataValidationTask:
    return DataValidationTask.create(
        extraction_task_id="extraction-1",
        source_jd_version_id="source-version-1",
        bundle_id="bundle-1",
        policy_version="validation-policy-v1",
        max_attempts=2,
        now=NOW,
    )


def _succeeded_task() -> DataValidationTask:
    return _task().mark_running(NOW).mark_succeeded(NOW)


def test_task_statuses_and_report_conclusions_are_separate_closed_sets():
    assert {item.value for item in DataValidationTaskStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
    }
    assert {item.value for item in ValidationConclusion} == {
        "pass",
        "warn",
        "block",
    }

    task = _succeeded_task()
    report = ValidationReport.create(
        task=task,
        conclusion=ValidationConclusion.BLOCK,
        report_payload={"issues": [{"code": "blocked"}]},
        now=NOW,
    )

    assert task.status is DataValidationTaskStatus.SUCCEEDED
    assert report.conclusion is ValidationConclusion.BLOCK


def test_task_state_machine_supports_bounded_retry():
    pending = _task()
    first_run = pending.mark_running(NOW)
    failed = first_run.mark_failed(
        error_code="validation_runtime_error",
        error_message="safe failure",
        retryable=True,
        now=NOW,
    )
    second_run = failed.mark_running(NOW)
    exhausted = second_run.mark_failed(
        error_code="validation_runtime_error",
        error_message="safe failure",
        retryable=True,
        now=NOW,
    )

    assert first_run.attempt_count == 1
    assert failed.status is DataValidationTaskStatus.FAILED
    assert failed.retryable is True
    assert second_run.attempt_count == 2
    assert exhausted.retryable is False
    with pytest.raises(DataValidationError, match="not retryable"):
        exhausted.mark_running(NOW)


def test_invalid_task_transitions_are_rejected():
    pending = _task()

    with pytest.raises(DataValidationError, match="running"):
        pending.mark_succeeded(NOW)
    with pytest.raises(DataValidationError, match="running"):
        pending.mark_failed(
            error_code="invalid",
            error_message="invalid",
            retryable=False,
            now=NOW,
        )
    with pytest.raises(ValueError):
        DataValidationTask(**{**pending.__dict__, "status": "unknown"})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"attempt_count": 1}, "pending"),
        ({"status": "running", "attempt_count": 0}, "running"),
        (
            {
                "status": "running",
                "attempt_count": 1,
                "started_at": NOW,
                "finished_at": NOW,
            },
            "running",
        ),
        (
            {
                "status": "succeeded",
                "attempt_count": 0,
                "started_at": NOW,
                "finished_at": NOW,
            },
            "succeeded",
        ),
        (
            {
                "status": "succeeded",
                "attempt_count": 1,
                "started_at": NOW,
                "finished_at": None,
            },
            "succeeded",
        ),
        (
            {
                "status": "succeeded",
                "attempt_count": 1,
                "started_at": NOW,
                "finished_at": NOW,
                "retryable": True,
            },
            "succeeded",
        ),
        (
            {
                "status": "failed",
                "attempt_count": 1,
                "started_at": NOW,
                "finished_at": NOW,
            },
            "failed",
        ),
        (
            {
                "status": "failed",
                "attempt_count": 2,
                "started_at": NOW,
                "finished_at": NOW,
                "last_error_code": "runtime_error",
                "last_error_message": "failed",
                "retryable": True,
            },
            "failed",
        ),
    ],
)
def test_direct_construction_rejects_invalid_task_state_combinations(
    overrides, message
):
    pending = _task()

    with pytest.raises(DataValidationError, match=message):
        DataValidationTask(**{**pending.__dict__, **overrides})


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_task_rejects_finished_at_before_started_at(status):
    pending = _task()
    overrides = {
        "status": status,
        "attempt_count": 1,
        "started_at": NOW,
        "finished_at": NOW - timedelta(microseconds=1),
    }
    if status == "failed":
        overrides.update(
            last_error_code="runtime_error",
            last_error_message="failed",
        )

    with pytest.raises(DataValidationError, match="cannot precede"):
        DataValidationTask(**{**pending.__dict__, **overrides})


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_task_rejects_mixed_naive_and_aware_timestamps(status):
    pending = _task()
    overrides = {
        "status": status,
        "attempt_count": 1,
        "started_at": NOW,
        "finished_at": NOW.replace(tzinfo=None),
    }
    if status == "failed":
        overrides.update(
            last_error_code="runtime_error",
            last_error_message="failed",
        )

    with pytest.raises(DataValidationError, match="timezone-aware"):
        DataValidationTask(**{**pending.__dict__, **overrides})


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_task_allows_equal_timestamps(status):
    pending = _task()
    overrides = {
        "status": status,
        "attempt_count": 1,
        "started_at": NOW,
        "finished_at": NOW,
    }
    if status == "failed":
        overrides.update(
            last_error_code="runtime_error",
            last_error_message="failed",
        )

    task = DataValidationTask(**{**pending.__dict__, **overrides})

    assert task.finished_at == task.started_at


def test_factory_transitions_apply_the_same_timezone_rule():
    running = _task().mark_running(NOW)

    with pytest.raises(DataValidationError, match="timezone-aware"):
        running.mark_succeeded(NOW.replace(tzinfo=None))


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed"])
def test_terminal_transition_clamps_small_clock_regression(terminal_status):
    running = _task().mark_running(NOW)
    regressed_now = NOW - timedelta(microseconds=1)

    if terminal_status == "succeeded":
        terminal = running.mark_succeeded(regressed_now)
    else:
        terminal = running.mark_failed(
            error_code="validation_runtime_error",
            error_message="safe failure",
            retryable=False,
            now=regressed_now,
        )

    assert terminal.finished_at == running.started_at


def test_terminal_task_allows_two_naive_equal_timestamps():
    naive_now = NOW.replace(tzinfo=None)

    succeeded = _task().mark_running(naive_now).mark_succeeded(naive_now)

    assert succeeded.started_at == succeeded.finished_at == naive_now


def test_direct_construction_rejects_forged_derived_idempotency_keys():
    task = _succeeded_task()
    report = ValidationReport.create(
        task=task,
        conclusion=ValidationConclusion.PASS,
        report_payload={},
        now=NOW,
    )
    snapshot = ValidatedBundleSnapshot.create(
        task=task,
        report=report,
        bundle_payload={},
        now=NOW,
    )

    with pytest.raises(DataValidationError, match="idempotency_key"):
        DataValidationTask(**{**task.__dict__, "idempotency_key": "forged"})
    with pytest.raises(DataValidationError, match="idempotency_key"):
        ValidationReport(**{**report.__dict__, "idempotency_key": "forged"})
    with pytest.raises(DataValidationError, match="idempotency_key"):
        ValidatedBundleSnapshot(
            **{**snapshot.__dict__, "idempotency_key": "forged"}
        )


@pytest.mark.parametrize(
    "conclusion", [ValidationConclusion.PASS, ValidationConclusion.WARN]
)
def test_non_blocking_report_creates_immutable_snapshot(conclusion):
    task = _succeeded_task()
    report = ValidationReport.create(
        task=task,
        conclusion=conclusion,
        report_payload={"checks": [{"name": "schema", "result": "ok"}]},
        now=NOW,
    )
    snapshot = ValidatedBundleSnapshot.create(
        task=task,
        report=report,
        bundle_payload={"bundle": {"title": "Engineer"}},
        now=NOW,
    )

    assert snapshot.extraction_task_id == task.extraction_task_id
    assert snapshot.source_jd_version_id == task.source_jd_version_id
    assert snapshot.validation_report_id == report.id
    assert snapshot.data_validation_task_id == task.id
    with pytest.raises(FrozenInstanceError):
        snapshot.bundle_id = "changed"
    with pytest.raises(TypeError):
        snapshot.bundle_payload["bundle"] = {}
    with pytest.raises(TypeError):
        snapshot.bundle_payload["bundle"]["title"] = "changed"


def test_block_report_cannot_create_snapshot():
    task = _succeeded_task()
    report = ValidationReport.create(
        task=task,
        conclusion=ValidationConclusion.BLOCK,
        report_payload={"checks": [{"result": "block"}]},
        now=NOW,
    )

    with pytest.raises(DataValidationError, match="Blocked"):
        ValidatedBundleSnapshot.create(
            task=task,
            report=report,
            bundle_payload={"bundle": "must-not-flow"},
            now=NOW,
        )


def test_snapshot_rejects_report_from_another_task():
    task = _succeeded_task()
    other = DataValidationTask.create(
        extraction_task_id="extraction-2",
        source_jd_version_id="source-version-2",
        bundle_id="bundle-2",
        policy_version="validation-policy-v1",
        now=NOW,
    ).mark_running(NOW).mark_succeeded(NOW)
    report = ValidationReport.create(
        task=other,
        conclusion=ValidationConclusion.PASS,
        report_payload={},
        now=NOW,
    )

    with pytest.raises(DataValidationError, match="does not belong"):
        ValidatedBundleSnapshot.create(
            task=task,
            report=report,
            bundle_payload={},
            now=NOW,
        )
