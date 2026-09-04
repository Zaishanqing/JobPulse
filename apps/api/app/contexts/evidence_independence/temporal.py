"""Temporal freshness and acquisition-lag aware evidence modeling (TEMP-LAG-01).

``temporal-freshness.v1`` is a wall-clock freshness layer that sits on top of the
existing Evidence Independence / Release / GraphVersion architecture.  It models
two distinct time concepts:

* ``market_age_days``: how old the published/estimated signal is relative to an
  explicit ``observation_reference_date``.  This is what feeds the exponential
  age decay.
* ``acquisition_delay_days``: how long the *crawler* took to acquire a posting
  after it was actually published (``crawler_acquired_at - published_at``).
  Running crawler-lag statistics power the ``estimated_from_source_lag``
  fallback for evidence that has only a collected timestamp.

Time provenance policy (``time-provenance.v2``):

* ``source_published``  — real JD publish time (``EvidenceRecord.published_at``).
* ``crawler_acquired``  — a timestamp proven to come from the crawler envelope
  (``EvidenceRecord.collection_time_basis == "crawler_acquired"``).  This is the
  ONLY provenance allowed to train ``SourceLagProfile`` delay samples.
* ``pipeline_observed``  — extraction run / normalization / governance
  ``created_at`` / release bookkeeping time.  Usable as an observed lower bound,
  NEVER as a crawler acquisition time.
* ``unknown``           — no confirmable provenance.

Source acquisition delay is therefore only defined as

  crawler_acquired_at - published_at

and only evidence with both a real ``published_at`` and a provenance-proven
crawler acquisition time may enter the delay samples of a ``SourceLagProfile``.
A crawler acquisition time after the explicit observation reference date is a
future-leakage anomaly: it is excluded from the lag profile and freshness is
capped by the anomaly rules.

Design constraints (TEMP-LAG-01):

* Pure functions and frozen dataclasses only.
* ``collected_at`` is never written back into ``EvidenceRecord.published_at``.
  Estimated publish dates live only in ``EvidenceTemporalProfile``.
* No ``date.today()`` / ``datetime.now()`` is used anywhere.  Every computation
  requires an explicit ``observation_reference_date``.
* All temporal anomalies are recorded in ``anomaly_reasons``; nothing is
  silently fixed.  Future/inverted timestamps are confidence-capped by
  ``TemporalFreshnessRules.anomaly_confidence_cap`` so an anomaly can never get
  top freshness.
* Per-evidence staleness is tri-state (``fresh`` / ``stale`` / ``unknown``);
  unknown-time evidence can never wash a stale cluster back to fresh.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Literal, Mapping, Sequence

from app.contexts.evidence_independence.contracts import (
    CollectionTimeBasis,
    EvidenceRecord,
)


TEMPORAL_FRESHNESS_VERSION = "temporal-freshness.v1"

REFERENCE_DATE_POLICY = "explicit_observation_reference_date_required"

TIME_PROVENANCE_POLICY = "time-provenance.v2"

TimeBasisLiteral = Literal[
    "published",
    "estimated_from_source_lag",
    "observed_lower_bound",
    "unknown",
]

TemporalStalenessState = Literal["fresh", "stale", "unknown"]


class TemporalFreshnessError(ValueError):
    """Raised for invalid temporal-freshness configuration or input."""


@dataclass(frozen=True)
class TemporalFreshnessRules:
    """Configurable knobs for temporal-freshness.v1.

    ``half_life_days`` controls the exponential decay rate; ``stale_after_days``
    is the market-age horizon after which evidence is classified stale.
    Confidence values map each time basis to a multiplicative freshness weight.
    ``min_source_delay_samples`` is the number of valid *crawler acquisition*
    delay samples a source needs before it may estimate publish dates for its
    publish-missing evidence.

    ``anomaly_confidence_cap`` bounds the freshness weight of any future /
    inverted temporal anomaly so an anomalous timestamp can never receive the
    highest freshness (no clamp-to-``1.0`` windfall).
    """

    half_life_days: float = 60.0
    stale_after_days: int = 180
    published_confidence: float = 1.0
    estimated_confidence: float = 0.8
    observed_only_confidence: float = 0.65
    unknown_time_confidence: float = 0.4
    anomaly_confidence_cap: float = 0.4
    min_source_delay_samples: int = 5
    # Optional stale gate: when enabled and every effective independent cluster
    # is stale, the conclusion state may enter ``stale_observation``.
    stale_gate_enabled: bool = False
    stale_ratio_gate: float = 0.75
    min_publish_time_coverage: float = 0.5

    def __post_init__(self) -> None:
        if (
            isinstance(self.half_life_days, bool)
            or not isinstance(self.half_life_days, (int, float))
            or self.half_life_days <= 0
        ):
            raise TemporalFreshnessError(
                "half_life_days must be a positive number"
            )
        if (
            isinstance(self.stale_after_days, bool)
            or not isinstance(self.stale_after_days, int)
            or self.stale_after_days < 0
        ):
            raise TemporalFreshnessError(
                "stale_after_days must be a non-negative integer"
            )
        for field_name in (
            "published_confidence",
            "estimated_confidence",
            "observed_only_confidence",
            "unknown_time_confidence",
            "anomaly_confidence_cap",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TemporalFreshnessError(
                    f"{field_name} must be numeric"
                )
            if not 0 <= value <= 1:
                raise TemporalFreshnessError(
                    f"{field_name} must be between 0 and 1"
                )
        if (
            isinstance(self.min_source_delay_samples, bool)
            or not isinstance(self.min_source_delay_samples, int)
            or self.min_source_delay_samples < 1
        ):
            raise TemporalFreshnessError(
                "min_source_delay_samples must be a positive integer"
            )
        if not isinstance(self.stale_gate_enabled, bool):
            raise TemporalFreshnessError("stale_gate_enabled must be boolean")
        if (
            isinstance(self.stale_ratio_gate, bool)
            or not isinstance(self.stale_ratio_gate, (int, float))
            or not 0 <= self.stale_ratio_gate <= 1
        ):
            raise TemporalFreshnessError(
                "stale_ratio_gate must be between 0 and 1"
            )
        if (
            isinstance(self.min_publish_time_coverage, bool)
            or not isinstance(self.min_publish_time_coverage, (int, float))
            or not 0 <= self.min_publish_time_coverage <= 1
        ):
            raise TemporalFreshnessError(
                "min_publish_time_coverage must be between 0 and 1"
            )


@dataclass(frozen=True)
class SourceLagProfile:
    """Validated crawler acquisition-delay statistics for one source.

    Only evidence with BOTH a real ``published_at`` AND a
    ``collection_time_basis == "crawler_acquired"`` collection time AND
    ``collected_at.date() - published_at >= 0`` counts as a valid crawler delay
    sample.  Everything else is counted explicitly and never silently mixed in:

    * ``pipeline_observation_count`` — published rows observed via pipeline time.
    * ``unknown_provenance_count`` — published rows with an unverifiable
      collected timestamp.
    * ``missing_publish_count`` — rows with no published_at.
    * ``invalid_sample_count`` — published rows without a collected at all, or a
      proven crawler delay whose sign is inverted (negative).
    """

    source_id: str
    valid_crawler_delay_samples: int
    pipeline_observation_count: int
    unknown_provenance_count: int
    missing_publish_count: int
    invalid_sample_count: int
    median_delay_days: float | None = None
    p90_delay_days: float | None = None
    max_delay_days: float | None = None

    @property
    def non_crawler_observation_count(self) -> int:
        """Published rows observed via non-crawler timestamps."""
        return self.pipeline_observation_count + self.unknown_provenance_count

    def lag_profile_sufficient(self, min_source_delay_samples: int) -> bool:
        return (
            self.valid_crawler_delay_samples >= min_source_delay_samples
            and self.median_delay_days is not None
        )


@dataclass(frozen=True)
class EvidenceTemporalProfile:
    """Per-evidence wall-clock freshness output.

    ``estimated_publish_date`` is populated only for
    ``estimated_from_source_lag`` and is never written back to the evidence
    record.  ``market_age_days`` is computed against the explicit
    ``reference_date``.  ``staleness_state`` is tri-state:

    * ``fresh`` — a trustworthy timestamp whose market age <= stale horizon.
    * ``stale`` — a trustworthy timestamp whose market age > stale horizon.
    * ``unknown`` — no usable time at all; never treated as fresh and never
      forces stale.
    """

    evidence_id: str
    reference_date: date
    time_basis: TimeBasisLiteral
    market_age_days: float | None = None
    acquisition_delay_days: float | None = None
    estimated_publish_date: date | None = None
    age_decay: float = 1.0
    time_confidence: float = 1.0
    freshness_weight: float = 1.0
    stale: bool = False
    staleness_state: TemporalStalenessState = "unknown"
    anomaly_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemporalFreshnessCertificate:
    """Deterministic certificate summarizing a temporal-freshness run."""

    algorithm_version: str = TEMPORAL_FRESHNESS_VERSION
    config_hash: str = ""
    reference_date: date | None = None
    evidence_count: int = 0
    published_time_count: int = 0
    publish_time_coverage: float = 0.0
    crawler_acquired_count: int = 0
    pipeline_observed_count: int = 0
    unknown_time_count: int = 0
    median_market_age_days: float | None = None
    p90_market_age_days: float | None = None
    fresh_evidence_count: int = 0
    stale_evidence_count: int = 0
    unknown_evidence_count: int = 0
    stale_evidence_ratio: float = 0.0
    source_lag_profiles: tuple[SourceLagProfile, ...] = ()
    profiles: tuple[EvidenceTemporalProfile, ...] = ()


def temporal_rules_hash(rules: TemporalFreshnessRules) -> str:
    """Deterministic config hash for one temporal-freshness rule set.

    Includes the time-provenance policy version so that the stricter
    crawler-only source-lag semantics is distinguishable from earlier builds.
    """
    payload = {
        "algorithm_version": TEMPORAL_FRESHNESS_VERSION,
        "time_provenance_policy": TIME_PROVENANCE_POLICY,
        "reference_date_policy": REFERENCE_DATE_POLICY,
        "rules": asdict(rules),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _round(market_age_days: float, half_life_days: float) -> float:
    """Exponential age decay ``2 ** (-market_age_days / half_life_days)``."""
    return 2.0 ** (-market_age_days / half_life_days)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _stat(delays: Sequence[int]) -> tuple[float | None, float | None, float | None]:
    """Return (median, p90, max) in days over a sorted list of int delays."""
    if not delays:
        return None, None, None
    ordered = sorted(delays)
    length = len(ordered)
    median = (
        ordered[length // 2]
        if length % 2
        else (ordered[length // 2 - 1] + ordered[length // 2]) / 2.0
    )
    p90_index = max(0, min(length - 1, int(round(0.90 * (length - 1)))))
    return float(median), float(ordered[p90_index]), float(ordered[-1])


def build_source_lag_profiles(
    records: Sequence[EvidenceRecord],
    observation_reference_date: date | None = None,
) -> dict[str, SourceLagProfile]:
    """Group raw evidence by source and compute crawler acquisition-delay stats.

    The crawler-only gate: an evidence enters the valid delay samples only when
    ``published_at`` exists AND ``collection_time_basis == "crawler_acquired"``
    AND ``collected_at.date() - published_at >= 0`` AND (when a reference date
    is supplied) ``collected_at.date() <= observation_reference_date``.
    Pipeline-observed and unknown-provenance collection timestamps are counted
    separately and never train the lag profile.  A crawler timestamp after the
    observation reference is a future-leakage anomaly: it is recorded as
    ``invalid_sample_count`` and never trains the lag profile.
    """
    groups: dict[str, dict[str, object]] = {}
    for record in records:
        source_id = str(record.source_id or "unknown")
        group = groups.setdefault(
            source_id,
            {
                "valid": [],
                "pipeline": 0,
                "unknown_provenance": 0,
                "missing_publish": 0,
                "invalid": 0,
            },
        )
        published = record.published_at
        collected = record.collected_at
        basis = record.collection_time_basis
        if published is None:
            group["missing_publish"] = int(group["missing_publish"]) + 1
            continue
        if collected is None:
            group["invalid"] = int(group["invalid"]) + 1
            continue
        if basis != CollectionTimeBasis.CRAWLER_ACQUIRED:
            if basis == CollectionTimeBasis.PIPELINE_OBSERVED:
                group["pipeline"] = int(group["pipeline"]) + 1
            else:
                group["unknown_provenance"] = (
                    int(group["unknown_provenance"]) + 1
                )
            continue
        delay = (collected.date() - published).days
        if delay < 0:
            group["invalid"] = int(group["invalid"]) + 1
            continue
        if (
            observation_reference_date is not None
            and collected.date() > observation_reference_date
        ):
            group["invalid"] = int(group["invalid"]) + 1
            continue
        group["valid"].append(delay)
    profiles: dict[str, SourceLagProfile] = {}
    for source_id, group in groups.items():
        delays = list(group["valid"])
        median, p90, maximum = _stat(delays)
        profiles[source_id] = SourceLagProfile(
            source_id=source_id,
            valid_crawler_delay_samples=len(delays),
            pipeline_observation_count=int(group["pipeline"]),
            unknown_provenance_count=int(group["unknown_provenance"]),
            missing_publish_count=int(group["missing_publish"]),
            invalid_sample_count=int(group["invalid"]),
            median_delay_days=median,
            p90_delay_days=p90,
            max_delay_days=maximum,
        )
    return profiles


def _weight(
    age_decay: float,
    confidence: float,
    market_age_days: float | None,
    rules: TemporalFreshnessRules,
    anomaly_reasons: Sequence[str],
) -> float:
    """Freshness weight with the future/inverted anomaly confidence cap.

    Without the cap, ``2 ** (-negative/age)`` becomes > 1 and is clamped to
    1.0, giving an anomalous future timestamp the highest possible freshness.
    Any market_age < 0 anomaly is therefore capped at
    ``rules.anomaly_confidence_cap``.  A crawler acquisition time after the
    observation reference (``crawler_acquired_after_reference``) is also a
    future-leakage anomaly and is capped the same way even when the publish
    market age itself is not negative.
    """
    capped = _clamp01(age_decay * confidence)
    if (
        (market_age_days is not None and market_age_days < 0)
        or "crawler_acquired_after_reference" in anomaly_reasons
    ):
        capped = min(capped, rules.anomaly_confidence_cap)
    return round(capped, 8)


def build_evidence_temporal_profile(
    record: EvidenceRecord,
    reference_date: date,
    rules: TemporalFreshnessRules,
    source_lag: Mapping[str, SourceLagProfile],
) -> EvidenceTemporalProfile:
    """Compute one evidence's freshness profile under the fallback priority.

    Priority:

    1. ``published`` — real publish time exists (confidence 1.0).
    2. ``estimated_from_source_lag`` — publish missing, collection time exists,
       collection provenance is ``crawler_acquired`` AND the source has >=
       ``min_source_delay_samples`` valid crawler delay samples.
    3. ``observed_lower_bound`` — publish missing, any real collection time
       exists (pipeline or crawler); market age is a lower bound.
    4. ``unknown`` — neither publish nor collection time exists (confidence
       ``unknown_time_confidence``).
    """
    anomaly_reasons: list[str] = []
    published = record.published_at
    collected = record.collected_at
    basis = record.collection_time_basis

    if published is not None:
        market_age_days = float((reference_date - published).days)
        # ``acquisition_delay_days`` is ONLY defined for proven crawler
        # acquisition (crawler_acquired_at - published_at).  Pipeline-observed
        # / unknown provenance collection timestamps are pipeline bookkeeping,
        # never an acquisition delay, so they must NOT populate this field.
        acquisition_delay_days: float | None = None
        if (
            collected is not None
            and basis == CollectionTimeBasis.CRAWLER_ACQUIRED
        ):
            acquisition_delay_days = float((collected.date() - published).days)
            if acquisition_delay_days < 0:
                anomaly_reasons.append("collected_before_published")
            if collected.date() > reference_date:
                anomaly_reasons.append("crawler_acquired_after_reference")
        if market_age_days < 0:
            anomaly_reasons.append("published_at_after_reference")
        age_decay = _round(market_age_days, rules.half_life_days)
        freshness_weight = _weight(
            age_decay,
            rules.published_confidence,
            market_age_days,
            rules,
            anomaly_reasons,
        )
        stale = market_age_days > rules.stale_after_days
        anomalous = market_age_days < 0
        return EvidenceTemporalProfile(
            evidence_id=record.evidence_id,
            reference_date=reference_date,
            time_basis="published",
            market_age_days=round(market_age_days, 6),
            acquisition_delay_days=(
                round(acquisition_delay_days, 6)
                if acquisition_delay_days is not None
                else None
            ),
            age_decay=round(age_decay, 8),
            time_confidence=rules.published_confidence,
            freshness_weight=freshness_weight,
            stale=stale,
            staleness_state="stale" if (stale or anomalous) else "fresh",
            anomaly_reasons=tuple(anomaly_reasons),
        )

    if collected is not None:
        lag_profile = source_lag.get(str(record.source_id or "unknown"))
        crawler_provenance = basis == CollectionTimeBasis.CRAWLER_ACQUIRED
        if (
            crawler_provenance
            and lag_profile is not None
            and lag_profile.lag_profile_sufficient(rules.min_source_delay_samples)
        ):
            estimated_publish_date = collected.date() - timedelta(
                days=int(round(lag_profile.median_delay_days or 0.0))
            )
            if collected.date() > reference_date:
                anomaly_reasons.append("crawler_acquired_after_reference")
            market_age_days = float((reference_date - estimated_publish_date).days)
            if market_age_days < 0:
                anomaly_reasons.append("estimated_publish_at_after_reference")
            age_decay = _round(market_age_days, rules.half_life_days)
            freshness_weight = _weight(
                age_decay,
                rules.estimated_confidence,
                market_age_days,
                rules,
                anomaly_reasons,
            )
            stale = market_age_days > rules.stale_after_days
            return EvidenceTemporalProfile(
                evidence_id=record.evidence_id,
                reference_date=reference_date,
                time_basis="estimated_from_source_lag",
                market_age_days=round(market_age_days, 6),
                acquisition_delay_days=None,
                estimated_publish_date=estimated_publish_date,
                age_decay=round(age_decay, 8),
                time_confidence=rules.estimated_confidence,
                freshness_weight=freshness_weight,
                stale=stale,
                staleness_state=(
                    "stale" if (stale or market_age_days < 0) else "fresh"
                ),
                anomaly_reasons=tuple(anomaly_reasons),
            )
        if basis == CollectionTimeBasis.CRAWLER_ACQUIRED and collected.date() > reference_date:
            anomaly_reasons.append("crawler_acquired_after_reference")
        market_age_days = float((reference_date - collected.date()).days)
        if market_age_days < 0:
            anomaly_reasons.append("collected_at_after_reference")
        age_decay = _round(market_age_days, rules.half_life_days)
        freshness_weight = _weight(
            age_decay,
            rules.observed_only_confidence,
            market_age_days,
            rules,
            anomaly_reasons,
        )
        stale = market_age_days > rules.stale_after_days
        return EvidenceTemporalProfile(
            evidence_id=record.evidence_id,
            reference_date=reference_date,
            time_basis="observed_lower_bound",
            market_age_days=round(market_age_days, 6),
            acquisition_delay_days=None,
            age_decay=round(age_decay, 8),
            time_confidence=rules.observed_only_confidence,
            freshness_weight=freshness_weight,
            stale=stale,
            staleness_state=(
                "stale" if (stale or market_age_days < 0) else "fresh"
            ),
            anomaly_reasons=tuple(anomaly_reasons),
        )

    # Neither published nor collected time.
    return EvidenceTemporalProfile(
        evidence_id=record.evidence_id,
        reference_date=reference_date,
        time_basis="unknown",
        market_age_days=None,
        acquisition_delay_days=None,
        age_decay=1.0,
        time_confidence=rules.unknown_time_confidence,
        freshness_weight=round(rules.unknown_time_confidence, 8),
        stale=False,
        staleness_state="unknown",
        anomaly_reasons=tuple(anomaly_reasons),
    )


def _market_ages(
    profiles: Sequence[EvidenceTemporalProfile],
) -> list[float]:
    return [
        profile.market_age_days
        for profile in profiles
        if profile.market_age_days is not None
    ]


def build_temporal_freshness(
    records: Sequence[EvidenceRecord],
    reference_date: date,
    rules: TemporalFreshnessRules = TemporalFreshnessRules(),
) -> TemporalFreshnessCertificate:
    """Build the full certificate (source lag + per-evidence profiles).

    Deterministic: profiles are sorted by evidence_id and all statistics are
    computed from stable orderings.
    """
    if reference_date is None:
        raise TemporalFreshnessError(
            "temporal-freshness.v1 requires an explicit observation_reference_date"
        )
    source_lag = build_source_lag_profiles(records, reference_date)
    profiles = tuple(
        sorted(
            (
                build_evidence_temporal_profile(
                    record, reference_date, rules, source_lag
                )
                for record in records
            ),
            key=lambda profile: profile.evidence_id,
        )
    )
    evidence_count = len(profiles)
    published_time_count = sum(
        1 for profile in profiles if profile.time_basis == "published"
    )
    publish_time_coverage = (
        round(published_time_count / evidence_count, 4) if evidence_count else 0.0
    )
    crawler_acquired_count = sum(
        1 for record in records
        if record.collection_time_basis == CollectionTimeBasis.CRAWLER_ACQUIRED
    )
    pipeline_observed_count = sum(
        1 for record in records
        if record.collection_time_basis == CollectionTimeBasis.PIPELINE_OBSERVED
    )
    unknown_time_count = sum(
        1 for record in records
        if record.collection_time_basis == CollectionTimeBasis.UNKNOWN
    )
    fresh_evidence_count = sum(
        1 for profile in profiles if profile.staleness_state == "fresh"
    )
    stale_evidence_count = sum(
        1 for profile in profiles if profile.staleness_state == "stale"
    )
    unknown_state_count = sum(
        1 for profile in profiles if profile.staleness_state == "unknown"
    )
    stale_evidence_ratio = (
        round(stale_evidence_count / evidence_count, 4) if evidence_count else 0.0
    )
    ages = _market_ages(profiles)
    median_market_age, p90_market_age, _max_market_age = _stat(ages)
    certificate = TemporalFreshnessCertificate(
        algorithm_version=TEMPORAL_FRESHNESS_VERSION,
        config_hash=temporal_rules_hash(rules),
        reference_date=reference_date,
        evidence_count=evidence_count,
        published_time_count=published_time_count,
        publish_time_coverage=publish_time_coverage,
        crawler_acquired_count=crawler_acquired_count,
        pipeline_observed_count=pipeline_observed_count,
        unknown_time_count=unknown_time_count,
        median_market_age_days=median_market_age,
        p90_market_age_days=p90_market_age,
        fresh_evidence_count=fresh_evidence_count,
        stale_evidence_count=stale_evidence_count,
        unknown_evidence_count=unknown_state_count,
        stale_evidence_ratio=stale_evidence_ratio,
        source_lag_profiles=tuple(
            source_lag[source_id] for source_id in sorted(source_lag)
        ),
        profiles=profiles,
    )
    return certificate


def cluster_staleness_state(
    members: Sequence[EvidenceTemporalProfile],
) -> TemporalStalenessState:
    """Tri-state staleness for one cluster.

    Rule (TEMP-LAG-01):

    * at least one trusted fresh member -> ``fresh``;
    * otherwise, if there is at least one trusted member and all trusted
      members are stale -> ``stale``;
    * otherwise (no fresh, unknown-time members present or empty) -> ``unknown``.

    Unknown-time evidence can therefore never wash a stale cluster back to
    ``fresh``, and an all-unknown cluster is ``unknown`` rather than forced
    stale.
    """
    trusted = [
        member for member in members if member.staleness_state in ("fresh", "stale")
    ]
    if any(member.staleness_state == "fresh" for member in members):
        return "fresh"
    if trusted and all(member.staleness_state == "stale" for member in trusted):
        return "stale"
    return "unknown"


def cluster_staleness_states(
    clusters: Sequence[tuple[str, ...]],
    certificate: TemporalFreshnessCertificate,
) -> dict[str, TemporalStalenessState]:
    """Map cluster_id -> tri-state staleness from the certificate profiles."""
    profile_by_id = {
        profile.evidence_id: profile for profile in certificate.profiles
    }
    return {
        cluster[0]: cluster_staleness_state(
            [
                profile_by_id[evidence_id]
                for evidence_id in cluster
                if evidence_id in profile_by_id
            ]
        )
        for cluster in clusters
    }


def all_clusters_stale(
    clusters: Sequence[tuple[str, ...]],
    certificate: TemporalFreshnessCertificate,
) -> bool:
    """True when every independent cluster's staleness state is ``stale``.

    Uses the tri-state rule: an unknown cluster is NOT stale, so the gate only
    fires when every cluster has a trustworthy-but-stale member set.
    """
    if not clusters:
        return False
    states = cluster_staleness_states(clusters, certificate)
    return bool(states) and all(state == "stale" for state in states.values())


def derive_temporal_reasons(
    certificate: TemporalFreshnessCertificate,
    cluster_staleness: Mapping[str, TemporalStalenessState],
    rules: TemporalFreshnessRules = TemporalFreshnessRules(),
) -> tuple[str, ...]:
    """Map a certificate + cluster staleness onto temporal-aware reasons.

    Reasons (v5 only, additive): ``temporal_coverage_low``,
    ``source_lag_profile_insufficient``, ``high_stale_evidence_ratio``,
    ``temporal_anomaly_detected``, ``temporal_state_indeterminate``.
    """
    reasons: list[str] = []
    if (
        certificate.evidence_count
        and certificate.publish_time_coverage
        < rules.min_publish_time_coverage
    ):
        reasons.append("temporal_coverage_low")
    if (
        certificate.evidence_count
        and certificate.stale_evidence_ratio > rules.stale_ratio_gate
    ):
        reasons.append("high_stale_evidence_ratio")
    if any(profile.anomaly_reasons for profile in certificate.profiles):
        reasons.append("temporal_anomaly_detected")
    # source_lag_profile_insufficient: any source that carries publish-missing
    # evidence but lacks enough valid crawler delay samples to estimate.
    if certificate.evidence_count:
        for lag_profile in certificate.source_lag_profiles:
            if (
                lag_profile.missing_publish_count > 0
                and not lag_profile.lag_profile_sufficient(
                    rules.min_source_delay_samples
                )
            ):
                reasons.append("source_lag_profile_insufficient")
                break
    # temporal_state_indeterminate: there is no fresh cluster but at least one
    # cluster is unknown (so we must NOT force stale_observation, and must
    # flag the conclusion as temporally indeterminate instead).
    if cluster_staleness:
        fresh = any(
            state == "fresh" for state in cluster_staleness.values()
        )
        unknown = any(
            state == "unknown" for state in cluster_staleness.values()
        )
        if not fresh and unknown:
            reasons.append("temporal_state_indeterminate")
    return tuple(dict.fromkeys(reasons))


__all__ = [
    "CollectionTimeBasis",
    "REFERENCE_DATE_POLICY",
    "TEMPORAL_FRESHNESS_VERSION",
    "TIME_PROVENANCE_POLICY",
    "EvidenceTemporalProfile",
    "SourceLagProfile",
    "TemporalFreshnessCertificate",
    "TemporalFreshnessError",
    "TemporalFreshnessRules",
    "TemporalStalenessState",
    "all_clusters_stale",
    "build_evidence_temporal_profile",
    "build_source_lag_profiles",
    "build_temporal_freshness",
    "cluster_staleness_state",
    "cluster_staleness_states",
    "derive_temporal_reasons",
    "temporal_rules_hash",
]
