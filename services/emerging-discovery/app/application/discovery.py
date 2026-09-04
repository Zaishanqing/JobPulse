"""Framework-free orchestration for the versioned discovery contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.application.discovery_identity import (
    discovery_identity,
)
from app.application.discovery_mapping import (
    algorithm_metadata_contract,
    normalize_snapshot,
    position_reference_contract,
)
from app.application.input_quality import (
    INPUT_PRECHECK_POLICY_VERSION,
    precheck_discovery_input,
)
from app.application.discovery_mapping import normalize_position_reference
from app.application.discovery_projection import (
    build_cluster_aggregates,
    build_snapshot_records,
)
from app.domain.candidate_admission import (
    attach_cluster_admission_certificates,
    load_candidate_admission_policy,
)
from app.application.contracts import (
    DiscoveryContractConflict,
    DiscoveryResult,
    RunDiscoveryCommand,
)
from app.application.candidate_lifecycle import NoopCandidateLifecycle
from app.application.payload_fingerprint import (
    PAYLOAD_FINGERPRINT_VERSION,
    payload_fingerprint,
)
from app.domain.discovery import PositionReference
from app.domain.values import FrozenDict
from app.ports.records import AlgorithmConfigRecord, LineageRecord, RunRecord
from app.ports.providers import (
    CandidateLifecyclePort,
    DiscoveryAlgorithm,
    DiscoveryUnitOfWork,
    LineagePort,
    ReferencePort,
)


CONTRACT_VERSION = "discovery.v2"
CODE_VERSION = "emerging-discovery-emerge-v3.2"
INPUT_SNAPSHOT_SCHEMA_VERSION = "input-snapshot.v2"


def _reference_skill_sets(references: tuple[PositionReference, ...]) -> list[set[str]]:
    references = tuple(normalize_position_reference(item) for item in references)
    values = [
        {skill.identity.casefold() for skill in reference.required_skills if skill.identity}
        for reference in references
    ]
    return values


class RunDiscovery:
    def __init__(
        self,
        references: ReferencePort,
        algorithm: DiscoveryAlgorithm,
        uow: DiscoveryUnitOfWork,
        lineage: LineagePort,
        input_policy_version: str = INPUT_PRECHECK_POLICY_VERSION,
        candidate_lifecycle: CandidateLifecyclePort | None = None,
    ) -> None:
        self.references = references
        self.algorithm = algorithm
        self.uow = uow
        self.lineage = lineage
        self.input_policy_version = input_policy_version
        self.candidate_lifecycle = candidate_lifecycle or NoopCandidateLifecycle()

    def execute(self, command: RunDiscoveryCommand) -> DiscoveryResult:
        # Contract failures enter the same UoW boundary as persistence failures so
        # adapters always receive rollback semantics from the formal use case.
        with self.uow:
            command = self._canonicalize_contract(command)
        resolved_references = self.references.resolve(command.position_references)
        if not resolved_references:
            raise ValueError("formal standard-position references are required")
        if any(item.graph_version_id == "unavailable" for item in resolved_references):
            raise ValueError("every position reference requires an immutable graph version")
        precheck = precheck_discovery_input(
            tuple(normalize_snapshot(item) for item in command.snapshots),
            time_window_start=command.time_window_start,
            time_window_end=command.time_window_end,
            policy_version=self.input_policy_version,
        )
        if not precheck.snapshots:
            raise ValueError("no JD snapshots remain after input precheck")
        identity = discovery_identity(
            command,
            resolved_references,
            execution_snapshots=precheck.snapshots,
            input_policy_version=self.input_policy_version,
        )
        observation_window = command.time_window.current_observation_window
        fingerprint = payload_fingerprint(
            contract_version=CONTRACT_VERSION,
            windows=command.time_window.windows,
            current_observation_window_id=observation_window.window_id,
            algorithm=identity.algorithm,
            snapshots=identity.snapshots,
            config=identity.config,
            position_references=resolved_references,
            input_policy_version=self.input_policy_version,
            code_version=CODE_VERSION,
            schema_version=INPUT_SNAPSHOT_SCHEMA_VERSION,
        )
        with self.uow:
            existing = self._existing_result(command, fingerprint)
            if existing is not None:
                return existing
            latest_run = self.uow.runs.latest_succeeded()
            historical_backfill = bool(
                latest_run is not None
                and latest_run.time_window_end is not None
                and observation_window.end < latest_run.time_window_end
            )
            output = self.algorithm.execute(
                algorithm=identity.algorithm,
                snapshots=identity.snapshots,
                reference_skill_sets=_reference_skill_sets(resolved_references),
                config=identity.config,
                time_window_ids=[item.window_id for item in command.time_window.windows],
            )
            run_id = str(uuid4())
            run = RunRecord(
                id=run_id,
                request_id=command.request_id,
                status="succeeded",
                algorithm_version=output.algorithm_version,
                formula_version=output.formula_version,
                time_window_start=observation_window.start,
                time_window_end=observation_window.end,
                completed_at=datetime.now(timezone.utc),
            )
            snapshots_to_add = build_snapshot_records(run_id, identity.snapshots)
            aggregates, current_specs = build_cluster_aggregates(
                run_id,
                output,
                snapshots_to_add,
                resolved_references,
                observation_window.window_id,
            )
            admission_version = str(
                identity.config.values.get(
                    "candidate_admission_policy_version", ""
                )
            )
            if admission_version:
                policy = load_candidate_admission_policy()
                if str(policy.get("policy_version")) != admission_version:
                    raise ValueError(
                        "candidate admission policy version does not match "
                        "the requested discovery config"
                    )
                aggregates = attach_cluster_admission_certificates(aggregates, policy)
            metadata = algorithm_metadata_contract(output.metadata)
            lineage_compatibility = FrozenDict(
                {
                    "algorithm_name": metadata["algorithm_name"],
                    "algorithm_version": metadata["algorithm_version"],
                    "feature_version": metadata["feature_version"],
                    "parameters": metadata["parameters"],
                    "random_seed": metadata["random_seed"],
                    "score_config": identity.config.values,
                }
            )
            previous_specs = self.uow.clusters.latest_specs_before(
                observation_window.start,
                observation_window.end,
                lineage_compatibility,
            )
            lineages = [
                LineageRecord(str(uuid4()), run_id, relation)
                for relation in self.lineage.match(previous_specs, current_specs)
            ]
            reference_contracts = tuple(
                position_reference_contract(normalize_position_reference(item))
                for item in resolved_references
            )
            persisted_config = FrozenDict(
                {
                    "payload_fingerprint": FrozenDict(
                        {
                            "version": PAYLOAD_FINGERPRINT_VERSION,
                            "hash": fingerprint,
                        }
                    ),
                    "score_config": identity.config.values,
                    **dict(metadata),
                    "input_quality_report": precheck.report,
                    "lineage_compatibility": lineage_compatibility,
                    "lineage_config": FrozenDict(
                        {
                            "member_overlap_weight": 0.40,
                            "core_skill_overlap_weight": 0.30,
                            "semantic_similarity_weight": 0.30,
                            "match_threshold": 0.35,
                            "events": (
                                "birth",
                                "continue",
                                "split",
                                "merge",
                                "decline",
                                "absorbed",
                            ),
                        }
                    ),
                    "code_version": CODE_VERSION,
                    "schema_version": INPUT_SNAPSHOT_SCHEMA_VERSION,
                    "position_graph_versions": FrozenDict(
                        {
                            item.position_id: item.graph_version_id
                            for item in resolved_references
                        }
                    ),
                    "run_context": FrozenDict(
                        {
                            "time_window": FrozenDict(
                                {
                                    "start": (
                                        command.time_window_start.isoformat()
                                        if command.time_window_start
                                        else None
                                    ),
                                    "end": (
                                        command.time_window_end.isoformat()
                                        if command.time_window_end
                                        else None
                                    ),
                                    "windows": tuple(
                                        FrozenDict(
                                            {
                                                "window_id": item.window_id,
                                                "start": item.start.isoformat(),
                                                "end": item.end.isoformat(),
                                            }
                                        )
                                        for item in command.time_window.windows
                                    ),
                                    "current_observation_window_id": (
                                        observation_window.window_id
                                    ),
                                }
                            ),
                            "algorithm": metadata,
                            "config": identity.config.values,
                            "position_references": reference_contracts,
                            "position_graph_versions": FrozenDict(
                                {
                                    item.position_id: item.graph_version_id
                                    for item in resolved_references
                                }
                            ),
                            "code_version": CODE_VERSION,
                            "schema_version": INPUT_SNAPSHOT_SCHEMA_VERSION,
                            "historical_backfill": historical_backfill,
                        }
                    ),
                }
            )
            self.uow.runs.add(
                run,
                AlgorithmConfigRecord(
                    id=str(uuid4()),
                    config=persisted_config,
                ),
            )
            self.uow.snapshots.add_many(snapshots_to_add)
            self.uow.clusters.add_many(aggregates, lineages)
            self.candidate_lifecycle.execute(
                run_id=run_id,
                window_ids=tuple(
                    item.window_id
                    for item in command.time_window.windows
                    if item.end <= observation_window.end
                ),
                clusters=aggregates,
                snapshot_records=snapshots_to_add,
                config=identity.config,
                historical_backfill=historical_backfill,
            )
            self.uow.commit()
            return self.uow.clusters.result(run_id, command.contract_version)

    @staticmethod
    def _canonicalize_contract(command: RunDiscoveryCommand) -> RunDiscoveryCommand:
        if command.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported contract_version: {command.contract_version}")
        if len(command.time_window.windows) < 3:
            raise ValueError("at least three continuous historical windows are required")
        windows = tuple(
            sorted(
                command.time_window.windows,
                key=lambda item: (item.start, item.end, item.window_id),
            )
        )
        if command.time_window.windows != windows:
            raise ValueError("historical time windows must be supplied in chronological order")
        if len({item.window_id for item in windows}) != len(windows):
            raise ValueError("historical window_id values must be unique")
        observation_window_id = (
            command.time_window.current_observation_window_id or windows[-1].window_id
        )
        if observation_window_id not in {item.window_id for item in windows}:
            raise ValueError(
                "current observation window must be declared in historical windows"
            )
        if any(
            current.start != previous.end + timedelta(days=1)
            for previous, current in zip(windows, windows[1:], strict=False)
        ):
            raise ValueError("historical time windows must be continuous and non-overlapping")
        if not command.snapshots:
            raise ValueError("historical JD input cannot be empty")
        if any(not item.window_id for item in command.snapshots):
            raise ValueError("every JD snapshot must be assigned to a historical window")
        window_by_id = {item.window_id: item for item in windows}
        versions_by_jd: dict[str, set[tuple[str, str]]] = {}
        for snapshot in command.snapshots:
            if snapshot.publish_date is None:
                raise ValueError("every JD snapshot requires publish_date for temporal discovery")
            window = window_by_id.get(snapshot.window_id)
            if window is None:
                raise ValueError(
                    f"snapshot window_id is not declared: {snapshot.window_id}"
                )
            if not window.start <= snapshot.publish_date <= window.end:
                raise ValueError(
                    "snapshot publish_date must belong to its declared historical window"
                )
            versions_by_jd.setdefault(snapshot.jd_id, set()).add(
                (snapshot.source_fact_id, snapshot.source_fact_version)
            )
        conflicts = sorted(
            jd_id for jd_id, versions in versions_by_jd.items() if len(versions) > 1
        )
        if conflicts:
            raise ValueError(
                "conflicting source versions for jd_id: " + ", ".join(conflicts)
            )
        if len(command.snapshots) != len(versions_by_jd):
            raise ValueError("duplicate JD snapshots are not allowed")
        return replace(
            command,
            time_window=replace(
                command.time_window,
                start=windows[0].start,
                end=windows[-1].end,
                windows=windows,
                current_observation_window_id=observation_window_id,
            ),
        )

    @staticmethod
    def _validate_contract(command: RunDiscoveryCommand) -> None:
        RunDiscovery._canonicalize_contract(command)

    def _existing_result(
        self,
        command: RunDiscoveryCommand,
        fingerprint: str,
    ) -> DiscoveryResult | None:
        by_request = self.uow.runs.by_request_id(command.request_id)
        if by_request is not None:
            stored_fingerprint = self.uow.runs.fingerprint_by_run_id(by_request.id)
            if stored_fingerprint != fingerprint:
                raise DiscoveryContractConflict(
                    f"request_id {command.request_id} was already used with a "
                    "different payload; resubmit with a new request_id"
                )
            return self.uow.clusters.result(by_request.id, CONTRACT_VERSION)
        return None


def get_discovery_result(
    uow: DiscoveryUnitOfWork,
    *,
    run_id: str | None = None,
    request_id: str | None = None,
) -> DiscoveryResult:
    with uow:
        run = uow.runs.by_id(run_id) if run_id else uow.runs.by_request_id(request_id or "")
        if run is None:
            raise LookupError("Discovery run not found")
        return uow.clusters.result(run.id, CONTRACT_VERSION)
