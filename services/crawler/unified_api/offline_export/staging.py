"""Persist immutable crawler Envelopes for later offline export.

The staging write shares the crawler record transaction. This file contains no
HTTP client, main-backend URL, or main-backend token.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.source_identity import compute_content_hash


def publication_idempotency_key(envelope: CrawlerJDEnvelopeV1) -> str:
    material = (
        f"{envelope.source_platform}\n"
        f"{envelope.source_record_id}\n"
        f"{envelope.source_version}"
    )
    return "crawler-publish:v1:" + material.replace("\n", ":")


def _normalize_stored_payload(
    stored_payload: dict[str, object],
    envelope: CrawlerJDEnvelopeV1,
) -> dict[str, object]:
    """Backfill content_hash for legacy crawler_publications rows.

    Older rows were persisted before ``CrawlerJDEnvelopeV1`` automatically
    populated ``content_hash``.  Comparing the new envelope directly against
    those rows would raise a false identity conflict, so the stored payload is
    normalized in memory only; the database row is not rewritten here.
    """
    if stored_payload.get("content_hash"):
        return stored_payload
    raw_text = stored_payload.get("raw_text") or envelope.raw_text
    normalized = dict(stored_payload)
    normalized["content_hash"] = compute_content_hash(str(raw_text))
    return normalized


def ensure_export_candidate_in_transaction(
    connection,
    envelope: CrawlerJDEnvelopeV1,
    *,
    source_kind: str,
    source_job_id: str,
    task_id: str | None = None,
) -> str:
    key = publication_idempotency_key(envelope)
    publication_id = str(uuid4())
    payload = envelope.model_dump(mode="json")
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO crawler_publications (
                id, idempotency_key, source_kind, source_job_id,
                source_platform, source_record_id, source_version,
                envelope_payload, max_attempts
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE id = id
            """,
            (
                publication_id,
                key,
                source_kind,
                source_job_id,
                envelope.source_platform,
                envelope.source_record_id,
                envelope.source_version,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        cursor.execute(
            "SELECT id, source_kind, envelope_payload FROM crawler_publications "
            "WHERE idempotency_key=%s",
            (key,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Envelope staging row disappeared after insert")
        stored_payload = row["envelope_payload"]
        if isinstance(stored_payload, str):
            stored_payload = json.loads(stored_payload)
        normalized_stored = _normalize_stored_payload(stored_payload, envelope)
        if row["source_kind"] != source_kind or normalized_stored != payload:
            raise RuntimeError("Envelope staging identity conflicts with stored data")
        publication_row_id = str(row["id"])
        if task_id:
            cursor.execute(
                """
                INSERT INTO crawler_task_publications (
                    id, task_id, publication_id, observed_at
                ) VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE id = id
                """,
                (
                    str(uuid4()),
                    task_id,
                    publication_row_id,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
        return publication_row_id
    finally:
        cursor.close()
