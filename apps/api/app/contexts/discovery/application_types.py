from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from datetime import date, datetime

from app.domain.values import FrozenDict, freeze


@dataclass(frozen=True)
class RunDiscoveryCommand:
    request_id: str = ""
    time_window_start: date | None = None
    time_window_end: date | None = None
    algorithm: str = "emerge_v3_2"
    dataset_id: str | None = None
    jd_ids: tuple[str, ...] = ()
    max_samples: int | None = None


@dataclass(frozen=True)
class ClusterProjection:
    cluster_id: str
    discovery_run_id: str
    cluster_name: str
    algorithm_version: str
    sample_count: int
    core_skills: tuple[FrozenDict[str, object], ...] = field(default_factory=tuple)
    representative_titles: tuple[str, ...] = field(default_factory=tuple)
    representative_jd_ids: tuple[str, ...] = field(default_factory=tuple)
    stability_score: float = 0.0
    growth_score: float = 0.0
    distance_from_existing_positions: float = 0.0
    discovery_run_status: str = "succeeded"
    discovery_assessment: FrozenDict[str, object] = field(default_factory=FrozenDict)
    generated_definition: FrozenDict[str, object] = field(default_factory=FrozenDict)
    discovery_lineages: tuple[FrozenDict[str, object], ...] = field(default_factory=tuple)
    time_window_start: date | None = None
    time_window_end: date | None = None
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "core_skills",
            "representative_titles",
            "representative_jd_ids",
            "discovery_assessment",
            "generated_definition",
            "discovery_lineages",
        ):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class ClusterJDRecord:
    jd_id: str
    source_type: str
    source_name: str | None
    enterprise_id: str | None
    title: str
    raw_text: str
    publish_date: date | None
    url: str | None
    file_id: str | None
    parse_status: str
    input_extraction_status: str
    input_provider: str | None
    input_error_code: str | None
    input_error_message: str | None
    copy_risk_score: float | None
    inflation_score: float | None
    is_downweighted: bool
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class RecentPositionSignal:
    """A public, source-backed projection of one recently published JD fact."""

    signal_id: str
    position_name: str
    representative_title: str
    skills: tuple[str, ...] = field(default_factory=tuple)
    observed_at: date | None = None
    source_jd_ids: tuple[str, ...] = field(default_factory=tuple)
    source_count: int = 0
    projection_version: str = "recent-position-signals.v1"




@dataclass(frozen=True)
class DiscoveryCandidate:
    """Main-system DTO for an emerging-discovery lifecycle candidate.

    Fields mirror the real upstream ``candidates.v1`` payload; optional fields are
    trimmed to ``None`` when upstream does not provide them (never fabricated).
    """

    candidate_id: str
    status: str
    first_seen_window_id: str
    last_seen_window_id: str
    age: int
    current_cluster_id: str | None = None
    previous_cluster_ids: tuple[str, ...] = field(default_factory=tuple)
    canonical_title: str = ""
    display_title: str = ""
    definition: FrozenDict[str, object] = field(default_factory=FrozenDict)
    identity_profile: FrozenDict[str, object] = field(default_factory=FrozenDict)
    evidence: FrozenDict[str, object] = field(default_factory=FrozenDict)
    support_count: int = 0
    company_coverage: int = 0
    skill_similarity: float | None = None
    responsibility_similarity: float | None = None
    title_similarity: float | None = None
    membership_overlap: float | None = None
    identity_similarity: float = 0.0
    novelty_score: float = 0.0
    emergence_score: float = 0.0
    identity_stability: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "previous_cluster_ids",
            "definition",
            "identity_profile",
            "evidence",
        ):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class CandidateObservation:
    """A single window observation of a lifecycle candidate (trajectory point)."""

    observation_id: str
    candidate_id: str
    run_id: str
    cluster_id: str
    window_id: str
    title: str
    status: str
    emergence_score: float = 0.0
    support_count: int = 0
    company_count: int = 0
    identity_similarity: float = 0.0
    skill_similarity: float | None = None
    responsibility_similarity: float | None = None
    title_similarity: float | None = None
    membership_overlap: float | None = None
    semantic_similarity: float | None = None
    cluster_name: str | None = None
    evidence: FrozenDict[str, object] = field(default_factory=FrozenDict)
    match_evidence: FrozenDict[str, object] = field(default_factory=FrozenDict)
    created_at: str | None = None

    def __post_init__(self) -> None:
        for name in ("evidence", "match_evidence"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class DiscoveryCandidateDetail:
    candidate: DiscoveryCandidate
    latest_observation: CandidateObservation | None = None


@dataclass(frozen=True)
class CandidateTrajectory:
    candidate_id: str
    trajectory: tuple[CandidateObservation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "trajectory", tuple(self.trajectory)
        )


@dataclass(frozen=True)
class CandidateDiffusionGraph:
    candidate_id: str
    graph: FrozenDict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "graph", freeze(self.graph))


class DiscoveryCandidateGateway(Protocol):
    """HTTP boundary for read-only candidate lifecycle queries."""

    def list_candidates(
        self,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> tuple[DiscoveryCandidate, ...]: ...

    def get_candidate(self, candidate_id: str) -> DiscoveryCandidateDetail: ...

    def get_candidate_trajectory(self, candidate_id: str) -> CandidateTrajectory: ...

    def get_candidate_diffusion(self, candidate_id: str) -> CandidateDiffusionGraph: ...


__all__ = [
    "CandidateObservation",
    "CandidateTrajectory",
    "CandidateDiffusionGraph",
    "ClusterJDRecord",
    "ClusterProjection",
    "DiscoveryCandidate",
    "DiscoveryCandidateDetail",
    "DiscoveryCandidateGateway",
    "RecentPositionSignal",
    "RunDiscoveryCommand",
]
