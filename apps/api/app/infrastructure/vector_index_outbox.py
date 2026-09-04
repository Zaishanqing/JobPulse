"""Delivery of transactional Profile events to matching-service indexing."""

from __future__ import annotations

from app.domain.json_types import thaw_json_object
from app.integration_events import DispatchResult, IdempotencyKey, IntegrationEvent
from app.profile_index_events import PROFILE_INDEX_EVENT_TYPE
from app.contexts.matching_learning import MatchingServiceError


class MatchingVectorIndexOutboxHandler:
    event_type = PROFILE_INDEX_EVENT_TYPE

    def __init__(self, client) -> None:
        self._client = client

    def handle(
        self, event: IntegrationEvent, _idempotency_key: IdempotencyKey
    ) -> DispatchResult:
        payload = thaw_json_object(event.payload)
        if (
            event.event_type != self.event_type
            or payload.get("event_id") != event.event_id
            or payload.get("event_type") != self.event_type
            or event.aggregate_id != payload.get("entity_id")
            or payload.get("schema_version") != "matching-vector-profile-event.v1"
        ):
            return DispatchResult(False, False, "matching_vector_event_invalid")
        request_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "schema_version",
                "event_id",
                "event_type",
                "vector_event_type",
            }
        }
        request_payload["event_type"] = payload.get("vector_event_type")
        request_payload["correlation_id"] = event.trace_id or event.event_id
        try:
            self._client.deliver_profile_index_event(
                request_payload, correlation_id=event.trace_id or event.event_id
            )
            return DispatchResult(True)
        except MatchingServiceError as exc:
            retryable = exc.status_code >= 500 or exc.status_code in {408, 429}
            return DispatchResult(False, retryable, exc.code)


__all__ = ["MatchingVectorIndexOutboxHandler"]
