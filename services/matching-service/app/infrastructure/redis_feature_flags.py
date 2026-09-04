"""Redis-backed rollback observations shared by all API/worker instances."""

from __future__ import annotations

import json
from uuid import uuid4

from app.domain.feature_flags import (
    RollbackAudit,
    StageName,
)


class RedisRollbackStateStore:
    def __init__(self, client, *, namespace: str, recovery_seconds: int) -> None:
        if not namespace or recovery_seconds <= 0:
            raise ValueError("rollback Redis namespace and recovery period are required")
        self._client = client
        self._namespace = namespace
        self._recovery_seconds = recovery_seconds

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        namespace: str = "matching:feature-flags:v1",
        recovery_seconds: int = 3600,
        socket_timeout_seconds: float = 3.0,
    ) -> RedisRollbackStateStore:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_timeout_seconds,
        )
        return cls(client, namespace=namespace, recovery_seconds=recovery_seconds)

    def _key(self, stage: StageName, suffix: str) -> str:
        return f"{self._namespace}:{stage}:{suffix}"

    def rollback_audit(self, stage: StageName) -> RollbackAudit | None:
        value = self._client.get(self._key(stage, "rollback"))
        if not value:
            return None
        return RollbackAudit(**json.loads(value))

    def observe(self, stage, signals, thresholds, *, now, window_seconds):
        existing = self.rollback_audit(stage)
        if existing is not None:
            return existing
        cutoff = now - window_seconds
        member = f"{now:.6f}:{uuid4()}"
        sample_key = self._key(stage, "samples")
        signal_keys = {signal: self._key(stage, f"signal:{signal}") for signal in signals}
        pipeline = self._client.pipeline(transaction=True)
        pipeline.zremrangebyscore(sample_key, "-inf", cutoff)
        pipeline.zadd(sample_key, {member: now})
        pipeline.expire(sample_key, max(int(window_seconds * 2), 1))
        for key in signal_keys.values():
            pipeline.zremrangebyscore(key, "-inf", cutoff)
            pipeline.zadd(key, {member: now})
            pipeline.expire(key, max(int(window_seconds * 2), 1))
        pipeline.execute()
        samples = int(self._client.zcard(sample_key))
        fatal = {"hard_constraint_violation", "cross_tenant_hit"}
        for signal, key in signal_keys.items():
            observed = int(self._client.zcard(key))
            threshold = getattr(thresholds, f"{signal}_rate")
            if (signal in fatal and observed > 0) or (
                samples >= thresholds.minimum_samples and observed / samples > threshold
            ):
                audit = RollbackAudit(stage, signal, observed, samples, threshold)
                payload = json.dumps(audit.__dict__, sort_keys=True, separators=(",", ":"))
                rollback_key = self._key(stage, "rollback")
                self._client.set(rollback_key, payload, nx=True, ex=self._recovery_seconds)
                return self.rollback_audit(stage)
        return None


__all__ = ["RedisRollbackStateStore"]
