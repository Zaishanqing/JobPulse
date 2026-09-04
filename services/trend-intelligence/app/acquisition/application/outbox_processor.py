from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.acquisition.ports.acquisition import AcquisitionStore
from app.acquisition.ports.trend_input import TrendInputAdapter


logger = logging.getLogger(__name__)


class OutboxLeaseLost(RuntimeError):
    pass


class OutboxProcessor:
    def __init__(
        self,
        store: AcquisitionStore,
        trend_input: TrendInputAdapter,
        *,
        worker_id: str,
        lease_seconds: float = 30,
        batch_size: int = 20,
    ) -> None:
        self.store = store
        self.trend_input = trend_input
        self.worker_id = worker_id
        self.lease = timedelta(seconds=lease_seconds)
        self.batch_size = batch_size

    def run_once(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        try:
            self.store.recover_expired_outbox(now=current)
            entries = self.store.claim_outbox(
                self.worker_id,
                now=current,
                lease=self.lease,
                limit=self.batch_size,
            )
        except Exception:
            logger.exception(
                "acquisition_outbox_poll_failed",
                extra={"worker_id": self.worker_id},
            )
            return False
        for entry in entries:
            try:
                self._process(entry)
            except Exception as exc:
                marked_failed = self.store.mark_outbox_failed(
                    str(entry["id"]),
                    self.worker_id,
                    f"{type(exc).__name__}: {exc}",
                )
                logger.exception(
                    "acquisition_outbox_entry_failed",
                    extra={
                        "worker_id": self.worker_id,
                        "outbox_id": str(entry["id"]),
                        "aggregate_id": str(entry.get("aggregate_id", "")),
                        "event_type": str(entry.get("event_type", "")),
                        "failure_recorded": marked_failed,
                    },
                )
        return bool(entries)

    def _process(self, entry: dict[str, object]) -> None:
        event_type = str(entry["event_type"])
        if event_type != "bundle_ready":
            raise ValueError(f"unsupported acquisition outbox event: {event_type}")
        if str(entry.get("aggregate_type")) != "Bundle":
            raise ValueError("bundle_ready outbox aggregate_type must be Bundle")
        bundle_id = str(entry["aggregate_id"])
        payload = entry.get("payload")
        if not isinstance(payload, dict) or str(payload.get("bundle_id")) != bundle_id:
            raise ValueError("bundle_ready outbox payload does not match aggregate_id")
        self.trend_input.import_bundle(bundle_id)
        if not self.store.mark_outbox_processed(str(entry["id"]), self.worker_id):
            raise OutboxLeaseLost("outbox lease was lost before acknowledgement")
