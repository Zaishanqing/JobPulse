"""Deterministic Stage E rollout flags and auditable automatic rollback."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Protocol

StageName = Literal["indexing", "retrieval", "scoring", "hybrid", "reranker"]
RollbackSignal = Literal[
    "error",
    "latency",
    "empty_retrieval",
    "stale_index",
    "hard_constraint_violation",
    "cross_tenant_hit",
]


@dataclass(frozen=True)
class StageFlag:
    enabled: bool = False
    percentage: int = 0
    tenant_refs: frozenset[str] = frozenset()
    user_refs: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 0 <= self.percentage <= 100:
            raise ValueError("feature flag percentage must be within 0..100")
        if any(not value for value in self.tenant_refs | self.user_refs):
            raise ValueError("feature flag subjects cannot be empty")

    def includes(self, *, tenant_ref: str, user_ref: str | None = None) -> bool:
        if not self.enabled:
            return False
        if tenant_ref in self.tenant_refs or (user_ref is not None and user_ref in self.user_refs):
            return True
        subject = f"{tenant_ref}:{user_ref or '-'}"
        bucket = sum(ord(char) for char in subject) % 100
        return bucket < self.percentage


@dataclass(frozen=True)
class RollbackThresholds:
    minimum_samples: int = 20
    error_rate: float = 0.05
    latency_rate: float = 0.10
    empty_retrieval_rate: float = 0.10
    stale_index_rate: float = 0.01
    hard_constraint_violation_rate: float = 0.0
    cross_tenant_hit_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.minimum_samples < 1:
            raise ValueError("rollback minimum_samples must be positive")
        rates = (
            self.error_rate,
            self.latency_rate,
            self.empty_retrieval_rate,
            self.stale_index_rate,
            self.hard_constraint_violation_rate,
            self.cross_tenant_hit_rate,
        )
        if any(rate < 0 or rate > 1 for rate in rates):
            raise ValueError("rollback rates must be within 0..1")


@dataclass(frozen=True)
class RollbackAudit:
    stage: StageName
    signal: RollbackSignal
    observed: int
    samples: int
    threshold: float


class RollbackStateStore(Protocol):
    def rollback_audit(self, stage: StageName) -> RollbackAudit | None: ...

    def observe(
        self,
        stage: StageName,
        signals: tuple[RollbackSignal, ...],
        thresholds: RollbackThresholds,
        *,
        now: float,
        window_seconds: float,
    ) -> RollbackAudit | None: ...


@dataclass
class InMemoryRollbackStateStore:
    _samples: dict[StageName, deque[float]] = field(default_factory=dict)
    _signals: dict[tuple[StageName, RollbackSignal], deque[float]] = field(
        default_factory=dict
    )
    _rolled_back: dict[StageName, RollbackAudit] = field(default_factory=dict)

    def rollback_audit(self, stage: StageName) -> RollbackAudit | None:
        return self._rolled_back.get(stage)

    def observe(self, stage, signals, thresholds, *, now, window_seconds):
        if stage in self._rolled_back:
            return self._rolled_back[stage]
        cutoff = now - window_seconds
        samples = self._samples.setdefault(stage, deque())
        samples.append(now)
        while samples and samples[0] < cutoff:
            samples.popleft()
        for signal in set(signals):
            observations = self._signals.setdefault((stage, signal), deque())
            observations.append(now)
        fatal = {"hard_constraint_violation", "cross_tenant_hit"}
        for signal in signals:
            observations = self._signals[(stage, signal)]
            while observations and observations[0] < cutoff:
                observations.popleft()
            threshold = getattr(thresholds, f"{signal}_rate")
            if (signal in fatal and observations) or (
                len(samples) >= thresholds.minimum_samples
                and len(observations) / len(samples) > threshold
            ):
                audit = RollbackAudit(stage, signal, len(observations), len(samples), threshold)
                self._rolled_back[stage] = audit
                return audit
        return None


@dataclass
class FeatureFlagController:
    flags: dict[StageName, StageFlag]
    thresholds: RollbackThresholds = RollbackThresholds()
    state_store: RollbackStateStore = field(default_factory=InMemoryRollbackStateStore)
    window_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("rollback window must be positive")

    def enabled(self, stage: StageName, *, tenant_ref: str, user_ref: str | None = None) -> bool:
        return (
            self.state_store.rollback_audit(stage) is None
            and self.flags.get(stage, StageFlag()).includes(
                tenant_ref=tenant_ref, user_ref=user_ref
            )
        )

    def observe(self, stage: StageName, *signals: RollbackSignal) -> RollbackAudit | None:
        return self.state_store.observe(
            stage,
            tuple(dict.fromkeys(signals)),
            self.thresholds,
            now=time.time(),
            window_seconds=self.window_seconds,
        )

    def rollback_audit(self, stage: StageName) -> RollbackAudit | None:
        return self.state_store.rollback_audit(stage)


__all__ = [
    "FeatureFlagController",
    "InMemoryRollbackStateStore",
    "RollbackAudit",
    "RollbackSignal",
    "RollbackThresholds",
    "RollbackStateStore",
    "StageFlag",
    "StageName",
]
