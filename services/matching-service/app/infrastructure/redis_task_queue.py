"""Redis Streams TaskQueue Adapter with visibility recovery and retry scheduling."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from app.domain.privacy import find_pii
from app.domain.queue import (
    DeadLetterRecord,
    QueueDelivery,
    TaskQueueMessage,
    dead_letter_for_delivery,
    task_message_id,
)
from app.ports.task_queue import TaskQueueError


class RedisTaskQueue:
    def __init__(
        self,
        client: Any,
        *,
        queue_name: str,
        visibility_timeout_seconds: float,
        consumer_group: str | None = None,
    ) -> None:
        if not queue_name:
            raise ValueError("queue_name is required")
        if visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be positive")
        self._client = client
        self.queue_name = queue_name
        self.consumer_group = consumer_group or f"{queue_name}:workers"
        self.retry_queue_name = f"{queue_name}:retry"
        self.delivery_count_hash_name = f"{queue_name}:delivery-counts"
        # v2 is a Redis hash, intentionally separated from the legacy stream key.
        self.dead_letter_queue_name = f"{queue_name}:dead-letter:v2"
        self.visibility_timeout_seconds = visibility_timeout_seconds
        self._group_ready = False

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        queue_name: str,
        visibility_timeout_seconds: float,
        socket_timeout_seconds: float = 5.0,
    ) -> RedisTaskQueue:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - dependency is declared for production
            raise TaskQueueError(
                "QUEUE_REDIS_DEPENDENCY_MISSING",
                "redis package is required for the Redis queue provider",
                retryable=False,
            ) from exc
        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_timeout_seconds,
        )
        return cls(
            client,
            queue_name=queue_name,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )

    def publish(self, message: TaskQueueMessage) -> None:
        self._validate_safe_message(message)
        self._invoke(
            self._client.xadd,
            self.queue_name,
            self._stream_fields(message, delivery_count=0),
        )

    def check_health(self) -> None:
        self._invoke(self._client.ping)

    def consume(self, worker_id: str) -> QueueDelivery | None:
        if not worker_id:
            raise TaskQueueError(
                "QUEUE_WORKER_ID_INVALID", "worker_id is required", retryable=False
            )
        for _ in range(100):
            self._ensure_group()
            self._promote_due_retries()
            reclaimed = self._invoke_group(
                self._client.xautoclaim,
                self.queue_name,
                self.consumer_group,
                worker_id,
                int(self.visibility_timeout_seconds * 1000),
                "0-0",
                count=1,
            )
            messages = reclaimed[1] if reclaimed and len(reclaimed) > 1 else ()
            if messages:
                stream_id, fields = messages[0]
            else:
                records = self._invoke_group(
                    self._client.xreadgroup,
                    self.consumer_group,
                    worker_id,
                    {self.queue_name: ">"},
                    count=1,
                    block=1,
                )
                if not records:
                    return None
                _, stream_messages = records[0]
                if not stream_messages:
                    return None
                stream_id, fields = stream_messages[0]
            delivery = self._delivery_or_dead_letter(worker_id, stream_id, fields)
            if delivery is not None:
                return delivery
        return None

    def acknowledge(self, delivery: QueueDelivery) -> None:
        self._acknowledge_entry(delivery.receipt_id)

    def retry(
        self, delivery: QueueDelivery, *, delay_seconds: float, reason_code: str
    ) -> None:
        if delay_seconds < 0:
            raise TaskQueueError(
                "QUEUE_RETRY_DELAY_INVALID", "retry delay cannot be negative", retryable=False
            )
        payload = json.dumps(
            {
                "message": delivery.message.model_dump(mode="json"),
                "delivery_count": delivery.delivery_count,
                "reason_code": reason_code,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        due_at = datetime.now(timezone.utc).timestamp() + delay_seconds
        # ZADD member identity is deterministic, so a settlement retry cannot enqueue
        # a second retry record even when NOGROUP interrupts the following ACK.
        self._invoke(self._client.zadd, self.retry_queue_name, {payload: due_at})
        self._acknowledge_entry(delivery.receipt_id)

    def dead_letter(self, delivery: QueueDelivery, *, reason_code: str) -> None:
        self._validate_safe_message(delivery.message)
        envelope = dead_letter_for_delivery(delivery, reason_code, datetime.now(timezone.utc))
        self._write_dlq(envelope, self._settlement_id(delivery, reason_code))
        self._acknowledge_entry(delivery.receipt_id)

    def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            self._client.xgroup_create(
                self.queue_name, self.consumer_group, id="0-0", mkstream=True
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise self._mapped_error(exc) from exc
        self._group_ready = True

    def _promote_due_retries(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        payloads = self._invoke(
            self._client.zrangebyscore,
            self.retry_queue_name,
            0,
            now,
            start=0,
            num=100,
        )
        for raw in payloads:
            try:
                payload = json.loads(self._text(raw))
                message = TaskQueueMessage.model_validate(payload["message"])
                self._validate_safe_message(message)
                delivery_count = int(payload["delivery_count"])
                if delivery_count < 0:
                    raise ValueError("negative delivery count")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
                self._invoke(self._client.zrem, self.retry_queue_name, raw)
                self._write_poison_dlq(
                    "QUEUE_RETRY_PAYLOAD_INVALID", 1, self._text(raw)
                )
                continue
            pipeline = self._client.pipeline(transaction=True)
            pipeline.zrem(self.retry_queue_name, raw)
            pipeline.xadd(
                self.queue_name,
                self._stream_fields(message, delivery_count),
            )
            self._invoke(pipeline.execute)

    def _delivery_or_dead_letter(
        self, worker_id: str, stream_id: Any, fields: Any
    ) -> QueueDelivery | None:
        receipt_id = self._text(stream_id)
        delivery_count = 1
        reason_code: str | None = None
        poison_reference = receipt_id
        try:
            if not isinstance(fields, dict):
                raise TypeError("stream fields must be a mapping")
            normalized = {self._text(key): self._text(value) for key, value in fields.items()}
            poison_reference = receipt_id
            raw_count = normalized.get("delivery_count", "0")
            base_delivery_count = int(raw_count)
            delivery_count = base_delivery_count + 1
            if base_delivery_count < 0:
                raise ValueError("delivery count is invalid")
            raw_payload = normalized["payload"]
            try:
                decoded = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError):
                reason_code = "QUEUE_MESSAGE_JSON_INVALID"
                raise ValueError("invalid JSON") from None
            try:
                message = TaskQueueMessage.model_validate(decoded)
            except ValidationError:
                reason_code = "QUEUE_MESSAGE_CONTRACT_INVALID"
                raise ValueError("invalid message contract") from None
            self._validate_safe_message(message)
            expected = task_message_id(message.task_id, message.version_signature)
            if message.message_id != expected:
                reason_code = "TASK_MESSAGE_IDENTITY_MISMATCH"
                raise ValueError("invalid message identity")
            delivery_count = self._next_delivery_count(
                receipt_id, base_delivery_count
            )
        except KeyError:
            reason_code = "QUEUE_MESSAGE_FIELDS_INVALID"
        except (TypeError, ValueError, TaskQueueError) as exc:
            if reason_code is None:
                reason_code = (
                    "QUEUE_MESSAGE_PII_FORBIDDEN"
                    if isinstance(exc, TaskQueueError)
                    and exc.code == "QUEUE_MESSAGE_PII_FORBIDDEN"
                    else "QUEUE_MESSAGE_FIELDS_INVALID"
                )
        if reason_code is not None:
            self._settle_poison(
                receipt_id, reason_code, delivery_count, poison_reference
            )
            return None
        return QueueDelivery(
            receipt_id=receipt_id,
            message=message,
            worker_id=worker_id,
            delivery_count=delivery_count,
            leased_until=datetime.now(timezone.utc)
            + timedelta(seconds=self.visibility_timeout_seconds),
        )

    def _settle_poison(
        self,
        receipt_id: str,
        reason_code: str,
        delivery_count: int,
        poison_reference: str,
    ) -> None:
        self._write_poison_dlq(
            reason_code, max(delivery_count, 1), poison_reference
        )
        self._acknowledge_entry(receipt_id)

    def _write_poison_dlq(
        self, reason_code: str, delivery_count: int, safe_reference: str
    ) -> None:
        envelope = DeadLetterRecord(
            reason_code=reason_code,
            delivery_count=delivery_count,
            message_id=f"stream:{safe_reference}",
            timestamp=datetime.now(timezone.utc),
        )
        self._write_dlq(envelope, f"poison:{reason_code}:{safe_reference}")

    def _write_dlq(self, envelope: DeadLetterRecord, settlement_id: str) -> None:
        # Redis hash field identity makes the DLQ write atomic and idempotent. It also
        # avoids the XADD-then-crash duplicate window of a second Redis stream.
        if find_pii(envelope.model_dump(mode="python")):
            raise TaskQueueError(
                "QUEUE_DLQ_ENVELOPE_UNSAFE",
                "dead-letter envelope failed privacy validation",
                retryable=False,
            )
        self._invoke(
            self._client.hsetnx,
            self.dead_letter_queue_name,
            settlement_id,
            envelope.model_dump_json(),
        )

    @staticmethod
    def _settlement_id(delivery: QueueDelivery, reason_code: str) -> str:
        return f"{delivery.message.message_id}|{reason_code}"

    def _acknowledge_entry(self, receipt_id: str) -> None:
        acknowledged = self._invoke_group(
            self._client.xack,
            self.queue_name,
            self.consumer_group,
            receipt_id,
        )
        if int(acknowledged or 0) == 0:
            # A recreated group has no PEL entry. Inspect the stream instead of
            # pretending XACK succeeded: delete an extant entry only because the
            # application has already validated the durable terminal/retry/DLQ state.
            records = self._invoke(
                self._client.xrange,
                self.queue_name,
                min=receipt_id,
                max=receipt_id,
                count=1,
            )
            if records:
                deleted = self._invoke(self._client.xdel, self.queue_name, receipt_id)
                if int(deleted or 0) == 0:
                    raise TaskQueueError(
                        "QUEUE_SETTLEMENT_UNCONFIRMED",
                        "queue settlement could not be confirmed",
                    )
                self._clear_delivery_count(receipt_id)
                return
            # Missing entry means it was already settled or the stream was rebuilt;
            # the deterministic retry/DLQ side effect remains the source of truth.
            self._clear_delivery_count(receipt_id)
            return
        self._invoke(self._client.xdel, self.queue_name, receipt_id)
        self._clear_delivery_count(receipt_id)

    def _next_delivery_count(self, receipt_id: str, base_count: int) -> int:
        self._invoke(
            self._client.hsetnx,
            self.delivery_count_hash_name,
            receipt_id,
            base_count,
        )
        return int(
            self._invoke(
                self._client.hincrby,
                self.delivery_count_hash_name,
                receipt_id,
                1,
            )
        )

    def _clear_delivery_count(self, receipt_id: str) -> None:
        self._invoke(self._client.hdel, self.delivery_count_hash_name, receipt_id)

    @staticmethod
    def _validate_safe_message(message: TaskQueueMessage) -> None:
        if find_pii(message.model_dump(mode="python")):
            raise TaskQueueError(
                "QUEUE_MESSAGE_PII_FORBIDDEN",
                "queue message contains prohibited PII",
                retryable=False,
            )

    @staticmethod
    def _stream_fields(
        message: TaskQueueMessage,
        delivery_count: int,
        reason_code: str | None = None,
    ) -> dict[str, str]:
        fields = {
            "payload": message.model_dump_json(),
            "delivery_count": str(delivery_count),
        }
        if reason_code is not None:
            fields["reason_code"] = reason_code
        return fields

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def _invoke(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except TaskQueueError:
            raise
        except Exception as exc:
            raise self._mapped_error(exc) from exc

    def _invoke_group(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        for _ in range(3):
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                if "NOGROUP" not in str(exc).upper():
                    raise self._mapped_error(exc) from exc
                self._group_ready = False
                self._ensure_group()
        raise TaskQueueError(
            "TASK_QUEUE_GROUP_RECOVERY_FAILED",
            "Redis consumer group could not be recovered",
        )

    @staticmethod
    def _mapped_error(exc: Exception) -> TaskQueueError:
        name = type(exc).__name__.lower()
        if "timeout" in name or "timeout" in str(exc).lower():
            return TaskQueueError("TASK_QUEUE_TIMEOUT", "Redis queue request timed out")
        return TaskQueueError("TASK_QUEUE_UNAVAILABLE", "Redis queue is unavailable")
