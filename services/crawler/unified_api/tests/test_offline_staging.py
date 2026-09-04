from __future__ import annotations

import json

import pytest

from unified_api.offline_export.staging import (
    ensure_export_candidate_in_transaction,
    publication_idempotency_key,
)
from unified_api.tests.bundle_test_support import envelope


class StagingCursor:
    def __init__(self, stored: dict[str, object] | None = None) -> None:
        self.stored = stored
        self.task_observations: list[tuple[str, str]] = []
        self.closed = False

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        if "INSERT INTO crawler_publications" in sql and self.stored is None:
            self.stored = {
                "id": parameters[0],
                "source_kind": parameters[2],
                "envelope_payload": parameters[7],
            }
        if "INSERT INTO crawler_task_publications" in sql:
            self.task_observations.append((str(parameters[1]), str(parameters[2])))

    def fetchone(self) -> dict[str, object] | None:
        return self.stored

    def close(self) -> None:
        self.closed = True


class StagingConnection:
    def __init__(self, stored: dict[str, object] | None = None) -> None:
        self.cursor_instance = StagingCursor(stored)

    def cursor(self) -> StagingCursor:
        return self.cursor_instance


def test_completed_envelope_is_staged_with_stable_identity() -> None:
    value = envelope(1)
    connection = StagingConnection()

    first_id = ensure_export_candidate_in_transaction(
        connection, value, source_kind="test_job", source_job_id="crawl-1"
    )
    repeated_id = ensure_export_candidate_in_transaction(
        connection, value, source_kind="test_job", source_job_id="crawl-1"
    )

    assert first_id == repeated_id
    assert publication_idempotency_key(value).startswith("crawler-publish:v1:")
    assert connection.cursor_instance.closed is True


def test_existing_identity_with_changed_payload_is_rejected() -> None:
    value = envelope(2)
    changed = value.model_dump(mode="json")
    changed["job_title_raw"] = "tampered"
    connection = StagingConnection(
        {
            "id": "stored-id",
            "source_kind": "test_job",
            "envelope_payload": json.dumps(changed),
        }
    )

    with pytest.raises(RuntimeError, match="identity conflicts"):
        ensure_export_candidate_in_transaction(
            connection, value, source_kind="test_job", source_job_id="crawl-2"
        )


def test_legacy_stored_payload_without_content_hash_is_accepted() -> None:
    value = envelope(3)
    legacy_payload = value.model_dump(mode="json")
    legacy_payload.pop("content_hash", None)
    connection = StagingConnection(
        {
            "id": "legacy-stored-id",
            "source_kind": "test_job",
            "envelope_payload": json.dumps(legacy_payload),
        }
    )

    result_id = ensure_export_candidate_in_transaction(
        connection, value, source_kind="test_job", source_job_id="crawl-3"
    )

    assert result_id == "legacy-stored-id"


def test_task_observation_is_recorded_for_existing_publication() -> None:
    value = envelope(4)
    connection = StagingConnection()
    first_id = ensure_export_candidate_in_transaction(
        connection,
        value,
        source_kind="test_job",
        source_job_id="crawl-a",
        task_id="task-a",
    )
    repeated_id = ensure_export_candidate_in_transaction(
        connection,
        value,
        source_kind="test_job",
        source_job_id="crawl-b",
        task_id="task-b",
    )

    assert first_id == repeated_id
    assert connection.cursor_instance.task_observations == [
        ("task-a", first_id),
        ("task-b", repeated_id),
    ]
