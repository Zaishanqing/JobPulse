"""Application use cases for the Discovery bounded context."""

from __future__ import annotations

from dataclasses import dataclass as _dataclass, field as _field
from datetime import date as _date
from datetime import timedelta as _timedelta
import hashlib as _hashlib
import json as _json
from threading import RLock as _RLock
from time import monotonic as _monotonic
from typing import Callable as _Callable

from app.contexts.discovery.application_types import (
    CandidateTrajectory as _CandidateTrajectory,
    ClusterJDRecord as _ClusterJDRecord,
    ClusterProjection as _ClusterProjection,
    DiscoveryCandidate as _DiscoveryCandidate,
    DiscoveryCandidateDetail as _DiscoveryCandidateDetail,
    DiscoveryCandidateGateway as _DiscoveryCandidateGateway,
    RecentPositionSignal as _RecentPositionSignal,
    RunDiscoveryCommand,
)
from app.contexts.discovery.asset_projection import emerging_asset as _emerging_asset
from app.contexts.discovery.contracts import (
    DiscoveryRunRequest as _DiscoveryRunRequest,
    HistoricalTimeWindow as _HistoricalTimeWindow,
    released_jd_contract as _released_jd_contract,
)
from app.contexts.discovery.domain import (
    Actor as _Actor,
    FROZEN_DISCOVERY_DATASET_ID as _FROZEN_DISCOVERY_DATASET_ID,
)
from app.contexts.discovery.ports import (
    DiscoveryGateway as _DiscoveryGateway,
    DiscoveryUnitOfWork as _DiscoveryUnitOfWork,
)
from app.contexts.tasks import TaskPayload as _TaskPayload, TaskRecord as _TaskRecord
from app.domain.errors import (
    NoReleasedJDFacts as _NoReleasedJDFacts,
    PermissionDenied as _PermissionDenied,
    ProjectionConflict as _ProjectionConflict,
)
from app.domain.values import freeze as _freeze, thaw as _thaw


_DISCOVERY_ROLES = {"admin"}
_PUBLIC_DISCOVERY_ROLES = {
    "admin",
    "developer",
    "enterprise_user",
    "personal_user",
    "reviewer",
}
_CONTRACT_VERSION = "discovery.v2"
_DISCOVERY_WINDOW_DAYS = 3
_DISCOVERY_WINDOW_ANCHOR = _date(1970, 1, 1)
_RECENT_SIGNAL_CACHE_TTL_SECONDS = 60.0
_UoWFactory = _Callable[[], _DiscoveryUnitOfWork]


@_dataclass
class _RecentPositionSignalCache:
    value: tuple[_RecentPositionSignal, ...] | None = None
    expires_at: float = 0.0
    lock: _RLock = _field(default_factory=_RLock, repr=False)

    def get_or_load(
        self,
        loader: _Callable[[], tuple[_RecentPositionSignal, ...]],
    ) -> tuple[_RecentPositionSignal, ...]:
        now = _monotonic()
        if self.value is not None and now < self.expires_at:
            return self.value
        with self.lock:
            now = _monotonic()
            if self.value is not None and now < self.expires_at:
                return self.value
            value = loader()
            self.value = value
            self.expires_at = now + _RECENT_SIGNAL_CACHE_TTL_SECONDS
            return value


class PositionClusterNotFound(LookupError):
    pass


def _require_admin(actor: _Actor) -> None:
    if actor.role not in _DISCOVERY_ROLES:
        raise _PermissionDenied("No permission to manage position clusters")


def _require_public_reader(actor: _Actor) -> None:
    if actor.role not in _PUBLIC_DISCOVERY_ROLES:
        raise _PermissionDenied("No permission to read published discovery signals")


_RECENT_SIGNAL_LENSES = (
    ("data-agent", ("data agent",), "Data Agent 研发工程师", ("Multi-Agent", "RAG", "知识图谱")),
    ("ai-coding", ("ai coding",), "AI Coding 算法工程师", ("Coding Agent", "LLM 评测", "数据合成")),
    ("agentic-ops", ("agentic ops",), "Agentic Ops 研发工程师", ("AIOps", "根因定位", "可观测性")),
    ("self-evolving-agent", ("自进化",), "Agent 自进化算法工程师", ("Agent 评测", "归因分析", "自动优化")),
    ("ai-for-science-agent", ("ai for science",), "AI for Science Agent 平台工程师", ("Auto Research", "Agent Runtime", "多 Agent 协作")),
)
_SIGNAL_KEYWORD_RULES = (
    ("Multi-Agent", ("multi-agent", "mutil - agent", "多agent", "多 agent")),
    ("RAG", ("rag",)),
    ("知识图谱", ("知识图谱",)),
    ("Coding Agent", ("coding agent",)),
    ("LLM 评测", ("llm", "评测")),
    ("数据合成", ("数据合成",)),
    ("AIOps", ("aiops",)),
    ("根因定位", ("根因",)),
    ("可观测性", ("可观测", "observability")),
    ("Agent 评测", ("agent", "评测")),
    ("归因分析", ("归因",)),
    ("自动优化", ("自动优化", "自动调优")),
    ("Auto Research", ("auto research",)),
    ("Agent Runtime", ("agent runtime",)),
    ("多 Agent 协作", ("多agent协作", "多 agent 协作")),
)


def _signal_text(fact) -> tuple[str, list[str]]:
    structured = _thaw(fact.structured_data)
    responsibilities = [
        str(value)
        for value in structured.get("responsibilities", [])
        if str(value).strip()
    ]
    raw_skills: list[str] = []
    for value in (
        *structured.get("required_skills", []),
        *structured.get("bonus_skills", []),
    ):
        if isinstance(value, dict):
            label = value.get("raw_skill") or value.get("canonical_name") or value.get("name")
        else:
            label = value
        if label and str(label).strip():
            raw_skills.append(str(label).strip())
    return "\n".join((fact.title, *responsibilities, *raw_skills)).lower(), raw_skills


def _signal_skills(fact, preferred: tuple[str, ...]) -> tuple[str, ...]:
    text, _ = _signal_text(fact)
    rules = dict(_SIGNAL_KEYWORD_RULES)
    return tuple(
        label
        for label in preferred
        if (
            all(needle in text for needle in rules[label])
            if label in {"LLM 评测", "Agent 评测"}
            else any(needle in text for needle in rules[label])
        )
    )


def _recent_position_signals(facts) -> tuple[_RecentPositionSignal, ...]:
    """Project a signal only when an immutable published JD fact supports it."""
    published = [
        fact
        for fact in facts
        if fact.consumption_path == "published" and fact.publish_date is not None
    ]
    result: list[_RecentPositionSignal] = []
    for signal_id, needles, label, preferred_skills in _RECENT_SIGNAL_LENSES:
        matches = [
            fact
            for fact in published
            if all(needle in fact.title.lower() for needle in needles)
        ]
        if not matches:
            continue
        latest = max(
            matches,
            key=lambda fact: (fact.publish_date, fact.source_fact_version, fact.jd_id),
        )
        result.append(
            _RecentPositionSignal(
                signal_id=signal_id,
                position_name=label,
                representative_title=latest.title,
                skills=_signal_skills(latest, preferred_skills),
                observed_at=latest.publish_date,
                source_jd_ids=(latest.jd_id,),
                source_count=1,
            )
        )
    return tuple(result)


def _window_start(value: _date) -> _date:
    elapsed_days = (value - _DISCOVERY_WINDOW_ANCHOR).days
    return _DISCOVERY_WINDOW_ANCHOR + _timedelta(
        days=(elapsed_days // _DISCOVERY_WINDOW_DAYS) * _DISCOVERY_WINDOW_DAYS
    )


def _fixed_window(value: _date, *, scope: str = "") -> _HistoricalTimeWindow:
    start = _window_start(value)
    end = start + _timedelta(days=_DISCOVERY_WINDOW_DAYS - 1)
    window_id = f"{start.isoformat()}..{end.isoformat()}"
    if scope:
        window_id = f"{window_id}@{scope}"
    return _HistoricalTimeWindow(
        window_id=window_id,
        start=start,
        end=end,
    )


def _selection_scope(facts: list) -> str:
    payload = [_released_jd_contract(fact) for fact in facts]
    return _hashlib.sha256(
        _json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]


def _rolling_request_id(
    command: RunDiscoveryCommand,
    window_id: str,
    window_facts: list,
    config,
    *,
    window_count: int,
) -> str:
    if window_count == 1:
        return command.request_id
    identity = {
        "algorithm": command.algorithm,
        "window_id": window_id,
        "facts": [_released_jd_contract(fact) for fact in window_facts],
        "config": _thaw(config),
    }
    digest = _hashlib.sha256(
        _json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"rolling-{window_id}-{digest}"


def _deterministic_request_id(
    command: RunDiscoveryCommand,
    facts: list,
    window_start: _date | None,
    window_end: _date | None,
    config: object,
) -> str:
    # Identical inputs must map to one logical run: the remote service dedupes by
    # request_id, so a content-derived id lets a retry after a client-side timeout
    # pick up the already-computed run instead of starting a full reclustering.
    basis = _json.dumps(
        {
            "algorithm": command.algorithm,
            "time_window_start": window_start.isoformat() if window_start else None,
            "time_window_end": window_end.isoformat() if window_end else None,
            "config": _thaw(config),
            "facts": sorted(
                f"{fact.source_fact_id}:{fact.source_fact_version}" for fact in facts
            ),
        },
        sort_keys=True,
    )
    return "run-" + _hashlib.sha256(basis.encode()).hexdigest()[:24]


def build_rolling_discovery_requests(
    command: RunDiscoveryCommand,
    facts: list,
    config,
    fixed_windows: tuple[_HistoricalTimeWindow, ...] | None = None,
) -> tuple[_DiscoveryRunRequest, ...]:
    """Build causal rolling runs from the JD's immutable source date.

    Normal production input uses fixed three-day windows. A preregistered frozen
    dataset supplies its immutable window manifest instead. Each request carries
    at most the trailing three-window sample and never includes future JD data.
    """
    dated = [fact for fact in facts if fact.publish_date is not None]
    if not dated:
        raise _NoReleasedJDFacts(
            "No approved JD V2 snapshots with JD publish_date are available for discovery"
        )
    available_start = min(fact.publish_date for fact in dated)
    available_end = max(fact.publish_date for fact in dated)
    selected_start = command.time_window_start or available_start
    selected_end = command.time_window_end or available_end
    if selected_start > selected_end:
        raise ValueError("discovery time_window_start must not be after time_window_end")
    selected = sorted(
        (
            fact
            for fact in dated
            if selected_start <= fact.publish_date <= selected_end
        ),
        key=lambda fact: (fact.publish_date, fact.source_fact_id, fact.jd_id),
    )
    if not selected:
        raise _NoReleasedJDFacts(
            "No approved JD V2 snapshots fall inside the requested JD publish_date range"
        )

    scope = _selection_scope(selected) if command.jd_ids or command.dataset_id else ""

    if fixed_windows:
        windows = list(fixed_windows)
    else:
        first_observation_start = _window_start(selected[0].publish_date)
        context_start = first_observation_start - _timedelta(
            days=2 * _DISCOVERY_WINDOW_DAYS
        )
        last_observation_start = _window_start(selected[-1].publish_date)
        windows = []
        cursor = context_start
        while cursor <= last_observation_start:
            windows.append(_fixed_window(cursor, scope=scope))
            cursor += _timedelta(days=_DISCOVERY_WINDOW_DAYS)
    window_index = {window.window_id: index for index, window in enumerate(windows)}

    def assigned_window_id(fact) -> str:
        if fixed_windows:
            matches = [
                window.window_id
                for window in windows
                if window.start <= fact.publish_date <= window.end
            ]
            if len(matches) != 1:
                raise _NoReleasedJDFacts(
                    "Frozen discovery fact does not belong to exactly one preregistered window"
                )
            return matches[0]
        return _fixed_window(fact.publish_date, scope=scope).window_id

    facts_by_window: dict[str, list] = {}
    for fact in selected:
        facts_by_window.setdefault(assigned_window_id(fact), []).append(fact)

    requests = []
    for window_id, window_facts in sorted(
        facts_by_window.items(), key=lambda item: window_index[item[0]]
    ):
        current_index = window_index[window_id]
        if fixed_windows and current_index < 2:
            # The executable discovery.v2 contract requires three historical
            # windows. Do not fabricate pre-experiment windows; start once the
            # frozen manifest has accumulated a truthful three-window context.
            continue
        context = tuple(windows[max(0, current_index - 2) : current_index + 1])
        context_window_ids = {window.window_id for window in context}
        context_facts = [
            fact
            for fact in selected
            if assigned_window_id(fact) in context_window_ids
        ]
        requests.append(
            _DiscoveryRunRequest(
                contract_version=_CONTRACT_VERSION,
                request_id=_rolling_request_id(
                    command,
                    window_id,
                    context_facts,
                    config,
                    window_count=len(facts_by_window),
                ),
                algorithm=command.algorithm,
                time_window_start=context[0].start,
                time_window_end=context[-1].end,
                snapshots=tuple(context_facts),
                config=config,
                time_windows=context,
                current_observation_window_id=window_id,
            )
        )
    return tuple(requests)


def run_position_discovery(
    command: RunDiscoveryCommand,
    actor: _Actor,
    uow: _DiscoveryUnitOfWork,
    gateway: _DiscoveryGateway,
) -> _TaskRecord:
    _require_admin(actor)
    with uow:
        if command.dataset_id:
            if command.dataset_id != _FROZEN_DISCOVERY_DATASET_ID:
                raise _NoReleasedJDFacts(
                    f"Unknown discovery dataset: {command.dataset_id}"
                )
            facts = uow.repository.list_dataset_jd_facts(command.dataset_id)
        else:
            facts = uow.repository.list_released_jd_facts()
        if not facts:
            if command.dataset_id:
                raise _NoReleasedJDFacts(
                    f"Discovery dataset is unavailable or has no approved facts: {command.dataset_id}"
                )
            raise _NoReleasedJDFacts("No approved JD V2 snapshots are available for discovery")

        if command.jd_ids:
            requested = set(command.jd_ids)
            facts = [fact for fact in facts if fact.jd_id in requested]
            found = {fact.jd_id for fact in facts}
            missing = sorted(requested - found)
            if missing:
                raise _NoReleasedJDFacts(
                    "Requested published JD IDs are unavailable for discovery: "
                    + ", ".join(missing[:10])
                )

        if command.max_samples is not None:
            # Deterministic prefix (repository order is stable), so repeated runs
            # with the same limit stay idempotent and map to the same request_id.
            facts = facts[: command.max_samples]

        config = _freeze(
            {
                **uow.repository.discovery_config(),
                "dataset_id": command.dataset_id,
            }
        )
        fixed_windows = (
            uow.repository.dataset_time_windows(command.dataset_id)
            if command.dataset_id
            else None
        )
        requests = build_rolling_discovery_requests(
            command,
            facts,
            config,
            fixed_windows=fixed_windows,
        )
        selected_dates = [fact.publish_date for request in requests for fact in request.snapshots]
        window_start = min(selected_dates)
        window_end = max(selected_dates)
        request_id = _deterministic_request_id(command, facts, window_start, window_end, config)
        task_id = f"position_cluster_{request_id}"
        existing_task = uow.repository.get_task(task_id)
        if existing_task is not None:
            return existing_task

        results = tuple(gateway.create_run(request) for request in requests)

        projections: list[_ClusterProjection] = []
        for request, result in zip(requests, results, strict=True):
            current_window = request.time_windows[-1]
            for item in result.clusters:
                existing = uow.repository.get_cluster(item.cluster_id)
                if existing is not None:
                    if existing.discovery_run_id != result.run_id:
                        raise _ProjectionConflict(
                            "Discovery cluster identity conflicts with an existing projection"
                        )
                    projections.append(existing)
                    continue
                projection = _ClusterProjection(
                    cluster_id=item.cluster_id,
                    discovery_run_id=result.run_id,
                    cluster_name=item.cluster_name,
                    algorithm_version=result.algorithm_version,
                    sample_count=item.sample_count,
                    core_skills=item.core_skills,
                    representative_titles=item.representative_titles,
                    representative_jd_ids=item.representative_jd_ids,
                    stability_score=item.stability_score,
                    growth_score=item.growth_score,
                    distance_from_existing_positions=item.distance_from_existing_positions,
                    discovery_run_status=result.status,
                    discovery_assessment=_freeze(
                        {
                            **dict(item.emergence_assessment),
                            "standard_position_comparison": dict(
                                item.standard_position_comparison
                            ),
                            "explainability": dict(item.explainability),
                            "lineage_relations": [
                                dict(relation) for relation in item.lineage_relations
                            ],
                            "input_quality_report": dict(result.input_quality_report),
                            "run_context": dict(result.run_context),
                            "request_id": result.request_id or request.request_id,
                            "run_id": result.run_id,
                            "input_fingerprint": result.input_fingerprint,
                        }
                    ),
                    generated_definition=item.generated_definition,
                    discovery_lineages=result.lineages,
                    time_window_start=current_window.start,
                    time_window_end=current_window.end,
                )
                uow.repository.add_cluster(projection)
                projections.append(projection)

        latest_result = results[-1]
        run_ids = [result.run_id for result in results]
        missing_publish_date_count = sum(fact.publish_date is None for fact in facts)

        task = uow.repository.record_succeeded_task(
            actor_id=actor.actor_id,
            input_payload=_TaskPayload.from_mapping(
                {
                    "algorithm": command.algorithm,
                    "time_window_start": window_start.isoformat() if window_start else None,
                    "time_window_end": window_end.isoformat() if window_end else None,
                    "dataset_id": command.dataset_id,
                    "max_samples": command.max_samples,
                    "discovery_run_id": latest_result.run_id,
                    "discovery_run_ids": run_ids,
                    "observation_window_ids": [
                        request.current_observation_window_id for request in requests
                    ],
                    "excluded_missing_publish_date_count": missing_publish_date_count,
                    "dataset_id": command.dataset_id,
                    "requested_jd_count": len(command.jd_ids),
                }
            ),
            result_payload=_TaskPayload.from_mapping(
                {
                    "created_count": len(projections),
                    "cluster_ids": [item.cluster_id for item in projections],
                    "discovery_run_id": latest_result.run_id,
                    "discovery_run_ids": run_ids,
                    "run_id": latest_result.run_id,
                    "provider": latest_result.provider,
                    "algorithm_version": latest_result.algorithm_version,
                    "request_id": request_id,
                    "contract_version": _CONTRACT_VERSION,
                    "implementation_status": latest_result.implementation_status,
                    "mock": latest_result.mock,
                    "rule_based": latest_result.rule_based,
                    "excluded_missing_publish_date_count": missing_publish_date_count,
                    "requested_jd_count": len(command.jd_ids),
                }
            ),
            task_id=task_id,
        )
        try:
            uow.commit()
        except Exception:
            uow.rollback()
            existing_task = uow.repository.get_task(task_id)
            if existing_task is not None:
                return existing_task
            raise
        return task


@_dataclass(frozen=True)
class StartPositionDiscovery:
    uow_factory: _UoWFactory
    gateway: _DiscoveryGateway

    def execute(self, command: RunDiscoveryCommand, actor: _Actor) -> _TaskRecord:
        return run_position_discovery(command, actor, self.uow_factory(), self.gateway)


@_dataclass(frozen=True)
class QueryPositionDiscovery:
    uow_factory: _UoWFactory
    experiment_report_loader: _Callable[[], dict[str, object]] | None = _field(
        default=None,
        compare=False,
        repr=False,
    )
    experiment_clusters_loader: _Callable[[], tuple[dict[str, object], ...]] | None = _field(
        default=None,
        compare=False,
        repr=False,
    )
    experiment_replay_loader: _Callable[[], dict[str, object]] | None = _field(
        default=None,
        compare=False,
        repr=False,
    )
    recent_signal_cache: _RecentPositionSignalCache = _field(
        default_factory=_RecentPositionSignalCache,
        compare=False,
        repr=False,
    )

    def task(self, task_id: str, actor: _Actor) -> _TaskRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            task = uow.repository.get_task(task_id)
            if task is None or task.task_type != "position_cluster":
                raise PositionClusterNotFound("Task not found")
            return task

    def list(self, actor: _Actor) -> list[_ClusterProjection]:
        _require_admin(actor)
        with self.uow_factory() as uow:
            return uow.repository.list_clusters()

    def get(self, cluster_id: str, actor: _Actor) -> _ClusterProjection:
        _require_admin(actor)
        with self.uow_factory() as uow:
            cluster = uow.repository.get_cluster(cluster_id)
            if cluster is None:
                raise PositionClusterNotFound("Position cluster not found")
            return cluster

    def jds(self, cluster_id: str, actor: _Actor) -> list[_ClusterJDRecord]:
        _require_admin(actor)
        with self.uow_factory() as uow:
            if uow.repository.get_cluster(cluster_id) is None:
                raise PositionClusterNotFound("Position cluster not found")
            return uow.repository.cluster_jds(cluster_id)

    def recent_signals(self, actor: _Actor) -> tuple[_RecentPositionSignal, ...]:
        _require_public_reader(actor)
        def load() -> tuple[_RecentPositionSignal, ...]:
            with self.uow_factory() as uow:
                return _recent_position_signals(uow.repository.list_released_jd_facts())

        return self.recent_signal_cache.get_or_load(load)

    def formal_experiment(self, actor: _Actor) -> object:
        _require_admin(actor)
        if self.experiment_report_loader is None:
            raise RuntimeError("Formal discovery experiment report is not configured")
        return _freeze(self.experiment_report_loader())

    def emerging_assets(self, actor: _Actor) -> object:
        _require_public_reader(actor)
        if self.experiment_clusters_loader is None or self.experiment_report_loader is None:
            raise RuntimeError("Discovery assets are not configured")
        report = self.experiment_report_loader()
        if report.get("status") != "accepted":
            raise RuntimeError("Discovery asset is not accepted")
        return _freeze([
            _emerging_asset(cluster, str(report["experiment_id"]))
            for cluster in self.experiment_clusters_loader()
            if cluster.get("state") == "emerging"
        ])

    def formal_experiment_clusters(self, actor: _Actor) -> object:
        _require_admin(actor)
        if self.experiment_clusters_loader is None:
            raise RuntimeError("Formal discovery cluster projection is not configured")
        return _freeze(self.experiment_clusters_loader())

    def replay_formal_experiment(self, actor: _Actor) -> object:
        _require_admin(actor)
        if self.experiment_replay_loader is None:
            raise RuntimeError("Formal discovery experiment replay is not configured")
        return _freeze(self.experiment_replay_loader())


@_dataclass(frozen=True)
class DeletePositionCluster:
    uow_factory: _UoWFactory

    def execute(self, cluster_id: str, actor: _Actor) -> None:
        _require_admin(actor)
        with self.uow_factory() as uow:
            if uow.repository.get_cluster(cluster_id) is None:
                raise PositionClusterNotFound("Position cluster not found")
            uow.repository.delete_cluster(cluster_id)
            uow.commit()


@_dataclass(frozen=True)
class PositionDiscoveryHandlers:
    start: StartPositionDiscovery
    query: QueryPositionDiscovery
    delete: DeletePositionCluster


@_dataclass(frozen=True)
class QueryDiscoveryCandidates:
    """Read-only lifecycle candidate queries proxied to emerging-discovery."""

    gateway: _DiscoveryCandidateGateway

    def list(
        self,
        actor: _Actor,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> tuple[_DiscoveryCandidate, ...]:
        _require_admin(actor)
        return self.gateway.list_candidates(
            status=status,
            candidate_id=candidate_id,
            window_id=window_id,
        )

    def get(self, candidate_id: str, actor: _Actor) -> _DiscoveryCandidateDetail:
        _require_admin(actor)
        return self.gateway.get_candidate(candidate_id)

    def trajectory(self, candidate_id: str, actor: _Actor) -> _CandidateTrajectory:
        _require_admin(actor)
        return self.gateway.get_candidate_trajectory(candidate_id)

    def diffusion(self, candidate_id: str, actor: _Actor):
        _require_admin(actor)
        return self.gateway.get_candidate_diffusion(candidate_id)


@_dataclass(frozen=True)
class DiscoveryCandidateHandlers:
    query: QueryDiscoveryCandidates


__all__ = [
    "DeletePositionCluster",
    "DiscoveryCandidateHandlers",
    "PositionClusterNotFound",
    "PositionDiscoveryHandlers",
    "QueryDiscoveryCandidates",
    "QueryPositionDiscovery",
    "RunDiscoveryCommand",
    "StartPositionDiscovery",
    "build_rolling_discovery_requests",
    "run_position_discovery",
]

del annotations
