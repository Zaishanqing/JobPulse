"""Environment configuration for Stage E rollout and rollback controls."""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.feature_flags import (
    FeatureFlagController,
    RollbackThresholds,
    StageFlag,
)
from app.infrastructure.redis_feature_flags import RedisRollbackStateStore

_STAGES = ("indexing", "retrieval", "scoring", "hybrid", "reranker")


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _refs(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def build_feature_flags(env: Mapping[str, str]) -> FeatureFlagController:
    flags = {}
    for stage in _STAGES:
        prefix = f"MATCHING_FF_{stage.upper()}"
        flags[stage] = StageFlag(
            enabled=_bool(env.get(f"{prefix}_ENABLED", "false")),
            percentage=int(env.get(f"{prefix}_PERCENT", "0")),
            tenant_refs=_refs(env.get(f"{prefix}_TENANTS", "")),
            user_refs=_refs(env.get(f"{prefix}_USERS", "")),
        )
    thresholds = RollbackThresholds(
        minimum_samples=int(env.get("MATCHING_ROLLBACK_MIN_SAMPLES", "20")),
        error_rate=float(env.get("MATCHING_ROLLBACK_ERROR_RATE", "0.05")),
        latency_rate=float(env.get("MATCHING_ROLLBACK_LATENCY_RATE", "0.10")),
        empty_retrieval_rate=float(
            env.get("MATCHING_ROLLBACK_EMPTY_RETRIEVAL_RATE", "0.10")
        ),
        stale_index_rate=float(env.get("MATCHING_ROLLBACK_STALE_INDEX_RATE", "0.01")),
        hard_constraint_violation_rate=float(
            env.get("MATCHING_ROLLBACK_HARD_CONSTRAINT_RATE", "0")
        ),
        cross_tenant_hit_rate=float(
            env.get("MATCHING_ROLLBACK_CROSS_TENANT_RATE", "0")
        ),
    )
    redis_url = env.get("MATCHING_ROLLBACK_REDIS_URL", "").strip() or env.get(
        "MATCHING_REDIS_URL", ""
    ).strip()
    if (
        env.get("MATCHING_RUNTIME_MODE", "production").strip().lower() == "production"
        and any(flag.enabled for flag in flags.values())
        and not redis_url
    ):
        raise ValueError("production feature flags require shared rollback Redis state")
    state_store = (
        RedisRollbackStateStore.from_url(
            redis_url,
            namespace=env.get(
                "MATCHING_ROLLBACK_REDIS_NAMESPACE", "matching:feature-flags:v1"
            ),
            recovery_seconds=int(env.get("MATCHING_ROLLBACK_RECOVERY_SECONDS", "3600")),
            socket_timeout_seconds=float(
                env.get("MATCHING_REDIS_TIMEOUT_SECONDS", "3")
            ),
        )
        if redis_url
        else None
    )
    options = {
        "flags": flags,
        "thresholds": thresholds,
        "window_seconds": float(env.get("MATCHING_ROLLBACK_WINDOW_SECONDS", "300")),
    }
    if state_store is not None:
        options["state_store"] = state_store
    return FeatureFlagController(**options)


__all__ = ["build_feature_flags"]
