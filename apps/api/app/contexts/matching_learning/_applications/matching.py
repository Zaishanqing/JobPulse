from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from hashlib import sha256
from threading import Lock
from time import monotonic, sleep
from typing import Callable
from uuid import uuid4

from app.domain.accounts import AccountActor
from app.domain.matching import MatchingRuleViolation
from app.contexts.tasks import TaskRecord, TaskWorkflowPort
from app.contexts.matching_learning._ports.matching_service import (
    MatchingIdentityPort,
    MatchingServiceError,
    MatchingServicePort,
    RemoteEvaluation,
    RemoteLearningPath,
    RemoteTask,
)
from app.contexts.matching_learning.matching_service import product_matching_method
from app.contexts.matching_learning._ports.matching import (
    LearningPathRecordData,
    MatchingServiceReferenceRecord,
    MatchingContractPort,
    MatchingPositionCatalogPort,
    MatchingUnitOfWork,
    MatchablePositionRecord,
    PositionProfilePort,
    ResumeProfilePort,
    EligibleResumeRecord,
)
from app.domain.errors import PermissionDenied
from app.domain.json_types import JsonObject
from app.contexts.matching_learning.contracts_service import (
    NO_VALID_SPECIALTY_ROUTE,
    STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION,
    StandardPositionProfileInsufficient,
    MatchingContractUnavailable,
)


INTERNAL_READ_ROLES = {"admin", "developer", "reviewer"}

_ENTERPRISE_JOB_PREFIX = "enterprise_job:"
_RANKING_ALGORITHM_VERSION = "coarse-skill-coverage.v3"
_RANKING_IDEMPOTENCY_PREFIX = "ranking-v3:"
_ACTIVE_RANKINGS: set[str] = set()
_CANCELLED_RANKINGS: set[str] = set()
_ACTIVE_RANKINGS_LOCK = Lock()
_POSITION_PROFILE_CACHE_TTL_SECONDS = 300.0
_POSITION_PROFILE_CACHE: dict[
    tuple[int, str], tuple[float, JsonObject | None, tuple[str, ...]]
] = {}
_POSITION_PROFILE_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class _RankingCandidate:
    position_id: str
    position_name: str
    coarse_score: float
    idempotency_key: str
    cv_profile_version: str
    position_profile_version: str
    graph_version: str


def _profile_skill_ids(profile: JsonObject, fields: tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for field in fields:
        values: object = profile
        for part in field.split("."):
            values = values.get(part) if isinstance(values, dict) else None
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            skill_id = value.get("skill_id") or value.get("normalized_skill_id")
            if isinstance(skill_id, str) and skill_id.strip():
                result.add(skill_id.strip().casefold())
    return result


def _coarse_match_score(cv_profile: JsonObject, position_profile: JsonObject) -> float:
    """High-recall, low-cost ordering before the formal explainable scorer."""
    candidate = _profile_skill_ids(
        cv_profile,
        ("skills", "capability_profiles", "normalization.skills", "capabilities.profiles"),
    )
    required = _profile_skill_ids(position_profile, ("required_skills",))
    preferred = _profile_skill_ids(position_profile, ("preferred_skills", "bonus_skills"))
    required_coverage = len(candidate & required) / len(required) if required else 0.0
    preferred_coverage = len(candidate & preferred) / len(preferred) if preferred else 0.0
    if required and preferred:
        score = required_coverage * 0.85 + preferred_coverage * 0.15
    elif required:
        score = required_coverage
    elif preferred:
        score = preferred_coverage
    else:
        score = 0.0
    return round(score * 100, 2)


def _remote_failure_is_retryable(task: RemoteTask) -> bool:
    """The matching service retries failed tasks while attempts remain, so a
    per-attempt ``failed`` status is transient rather than terminal."""
    if task.status != "failed":
        return False
    max_attempts = task.raw.get("max_attempts")
    if not isinstance(max_attempts, int) or max_attempts < 1:
        return False
    return task.attempt < max_attempts


def _ranking_failure_details(exc: Exception) -> tuple[str, str]:
    """Map a ranking candidate failure to a stable code and message."""
    if isinstance(exc, StandardPositionProfileInsufficient):
        return exc.code, f"{exc.message} ({exc.reason_code})"
    if isinstance(exc, MatchingServiceError):
        return exc.code, str(exc)
    if isinstance(exc, MatchingRuleViolation):
        return str(exc), str(exc)
    if isinstance(exc, PermissionDenied):
        return "MATCHING_PERMISSION_DENIED", str(exc)
    if isinstance(exc, MatchingContractUnavailable):
        return "MATCHING_CONTRACT_UNAVAILABLE", (
            str(exc) or "matching contract unavailable"
        )
    return "RANKING_CANDIDATE_FAILED", str(exc)


def _resolve_position_profile(
    contracts: MatchingContractPort,
    reference: MatchingServiceReferenceRecord,
) -> JsonObject | None:
    """Resolve the authoritative position profile for a matching reference.

    Enterprise-job references store their position identity as
    ``enterprise_job:<job_id>`` and must resolve through the enterprise-job
    contract, not the standard-position contract. Standard positions resolve
    through the standard-position contract.
    """
    if reference.target_type == "enterprise_job":
        job_id = reference.position_id
        if job_id.startswith(_ENTERPRISE_JOB_PREFIX):
            job_id = job_id[len(_ENTERPRISE_JOB_PREFIX):]
        return contracts.enterprise_job_profile(job_id)
    return contracts.position_profile(reference.position_id)


class MatchingEvaluationNotFound(LookupError):
    pass


class LearningPathNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageMatching:
    uow_factory: Callable[[], MatchingUnitOfWork]
    resumes: ResumeProfilePort
    positions: PositionProfilePort
    service: MatchingServicePort
    identities: MatchingIdentityPort
    historical_tasks: TaskWorkflowPort
    contracts: MatchingContractPort
    position_catalog: MatchingPositionCatalogPort | None = None

    def matchable_positions(self, actor: AccountActor) -> list[MatchablePositionRecord]:
        del actor
        if self.position_catalog is None:
            return []
        catalog = self.position_catalog.list()
        self._prime_standard_position_readiness(catalog)
        result: list[MatchablePositionRecord] = []
        for item in catalog:
            profile, blockers = self._standard_position_readiness(item.position_id)
            result.append(
                MatchablePositionRecord(
                    position_id=item.position_id,
                    position_name=item.position_name,
                    taxonomy_family_name=item.taxonomy_family_name,
                    status=item.status,
                    lifecycle_status=item.lifecycle_status,
                    matchable=not blockers,
                    reason=blockers[0] if blockers else "MATCHABLE",
                    blockers=blockers,
                    position_graph_version=(
                        str(profile.get("graph_version")) if profile is not None else None
                    ),
                    position_profile_version=(
                        str(profile.get("profile_version") or profile.get("source_version"))
                        if profile is not None
                        and (profile.get("profile_version") or profile.get("source_version"))
                        else None
                    ),
                )
            )
        return result

    def position_name(self, position_id: str | None) -> str | None:
        if not position_id:
            return None
        item = self.position_catalog.get(position_id) if self.position_catalog else None
        if item is not None:
            return item.position_name
        job_id = position_id.removeprefix(_ENTERPRISE_JOB_PREFIX)
        try:
            profile = self.contracts.enterprise_job_profile(job_id)
        except (MatchingContractUnavailable, LookupError):
            return None
        name = profile.get("canonical_title") if profile else None
        return str(name) if name else None

    def _prime_standard_position_readiness(self, catalog) -> None:
        batch_reader = getattr(self.contracts, "position_profiles_batch", None)
        if not callable(batch_reader):
            return
        eligible_ids = tuple(
            item.position_id
            for item in catalog
            if item.lifecycle_status != "deprecated"
            and item.position_code
            and item.taxonomy_version == "position-taxonomy.v3.0.0"
        )
        with _POSITION_PROFILE_CACHE_LOCK:
            now = monotonic()
            missing_ids = tuple(
                position_id
                for position_id in eligible_ids
                if (id(self.contracts), position_id) not in _POSITION_PROFILE_CACHE
                or now - _POSITION_PROFILE_CACHE[(id(self.contracts), position_id)][0]
                >= _POSITION_PROFILE_CACHE_TTL_SECONDS
            )
            if not missing_ids:
                return
            try:
                profiles = batch_reader(missing_ids)
            except MatchingContractUnavailable:
                return
            for position_id in missing_ids:
                value = profiles.get(position_id)
                if isinstance(value, StandardPositionProfileInsufficient):
                    result = (None, (value.code, value.reason_code))
                elif value is None or not value.get("graph_version"):
                    result = (None, ("POSITION_GRAPH_VERSION_UNAVAILABLE",))
                else:
                    requirement_graph = value.get("requirement_graph")
                    if not (
                        isinstance(requirement_graph, dict)
                        and requirement_graph.get("graph_version")
                        == STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION
                    ):
                        result = (
                            None,
                            (
                                StandardPositionProfileInsufficient.code,
                                NO_VALID_SPECIALTY_ROUTE,
                            ),
                        )
                    else:
                        result = (value, ())
                _POSITION_PROFILE_CACHE[(id(self.contracts), position_id)] = (
                    monotonic(),
                    result[0],
                    result[1],
                )

    def _standard_position_readiness(
        self, position_id: str
    ) -> tuple[JsonObject | None, tuple[str, ...]]:
        with _POSITION_PROFILE_CACHE_LOCK:
            cache_key = (id(self.contracts), position_id)
            cached = _POSITION_PROFILE_CACHE.get(cache_key)
            if (
                cached is not None
                and monotonic() - cached[0] < _POSITION_PROFILE_CACHE_TTL_SECONDS
            ):
                return cached[1], cached[2]

            candidate = self.position_catalog.get(position_id) if self.position_catalog else None
            if candidate is not None and candidate.lifecycle_status == "deprecated":
                result = (None, ("POSITION_DEPRECATED",))
            elif self.position_catalog is not None and (
                candidate is None
                or not candidate.position_code
                or candidate.taxonomy_version != "position-taxonomy.v3.0.0"
            ):
                result = (None, ("POSITION_PROFILE_UNAVAILABLE",))
            else:
                try:
                    profile = self.contracts.position_profile(position_id)
                except StandardPositionProfileInsufficient as exc:
                    result = (None, (exc.code, exc.reason_code))
                except MatchingContractUnavailable:
                    result = (None, ("POSITION_GRAPH_VERSION_UNAVAILABLE",))
                else:
                    if profile is None or not profile.get("graph_version"):
                        result = (None, ("POSITION_GRAPH_VERSION_UNAVAILABLE",))
                    else:
                        requirement_graph = profile.get("requirement_graph")
                        if not (
                            isinstance(requirement_graph, dict)
                            and requirement_graph.get("graph_version")
                            == STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION
                        ):
                            result = (
                                None,
                                (
                                    StandardPositionProfileInsufficient.code,
                                    NO_VALID_SPECIALTY_ROUTE,
                                ),
                            )
                        else:
                            result = (profile, ())
            _POSITION_PROFILE_CACHE[cache_key] = (monotonic(), result[0], result[1])
            return result

    def eligible_resumes(self, actor: AccountActor) -> list[EligibleResumeRecord]:
        return [
            EligibleResumeRecord(
                item.resume_id,
                item.validated_cv_snapshot_id,
                len(item.skills),
                len(item.projects),
            )
            for item in self.resumes.list_for_owner(actor.account_id)
            if item.validated_cv_snapshot_id
        ]

    def preflight(
        self,
        actor: AccountActor,
        *,
        resume_id: str,
        position_id: str,
        target_type: str = "standard_position",
    ) -> JsonObject:
        if target_type not in {"standard_position", "enterprise_job"}:
            raise MatchingRuleViolation("Unsupported matching target_type")
        self.identities.authorize_request(
            actor,
            cv_id=resume_id,
            position_id=position_id,
            target_type=target_type,
        )
        resume = self.resumes.get(resume_id)
        snapshot_ready = bool(resume and resume.validated_cv_snapshot_id)
        try:
            cv_profile = self.contracts.cv_profile(resume_id) if snapshot_ready else None
        except MatchingContractUnavailable:
            cv_profile = None
        if target_type == "enterprise_job":
            try:
                position_profile = self.contracts.enterprise_job_profile(position_id)
            except MatchingContractUnavailable:
                position_profile = None
            position_blockers = () if position_profile is not None else (
                "POSITION_PROFILE_UNAVAILABLE",
            )
        else:
            position_profile, position_blockers = self._standard_position_readiness(
                position_id
            )
        blockers: list[str] = []
        if not snapshot_ready:
            blockers.append("CV_SNAPSHOT_UNAVAILABLE")
        elif cv_profile is None:
            blockers.append("CV_PROFILE_UNAVAILABLE")
        blockers.extend(position_blockers)
        return {
            "ready": not blockers,
            "cv_snapshot_ready": snapshot_ready,
            "cv_profile_ready": cv_profile is not None,
            "position_profile_ready": position_profile is not None,
            "blockers": blockers,
            "validated_cv_snapshot_id": (
                resume.validated_cv_snapshot_id if resume is not None else None
            ),
            "position_graph_version": (
                str(position_profile.get("graph_version"))
                if position_profile is not None
                else None
            ),
        }

    def ranking(self, actor: AccountActor, *, resume_id: str) -> JsonObject:
        """Return a progressively refined ranking for one validated resume."""
        resume = self.resumes.get(resume_id)
        if (
            resume is None
            or resume.owner_id != actor.account_id
            or not resume.validated_cv_snapshot_id
        ):
            raise MatchingRuleViolation("CV_SNAPSHOT_UNAVAILABLE")
        try:
            cv_profile = self.contracts.cv_profile(resume_id)
        except MatchingContractUnavailable as exc:
            raise MatchingRuleViolation("CV_PROFILE_UNAVAILABLE") from exc
        if cv_profile is None:
            raise MatchingRuleViolation("CV_PROFILE_UNAVAILABLE")

        candidates = self._ranking_candidates(resume, cv_profile)
        all_references = [
            item
            for item in self.list(actor)
            if item.resume_id == resume_id
        ]
        ranking_references = {
            item.idempotency_key: item
            for item in all_references
            if item.idempotency_key.startswith(_RANKING_IDEMPOTENCY_PREFIX)
        }
        ranking_key = self._ranking_key(actor.account_id, resume.validated_cv_snapshot_id)
        with _ACTIVE_RANKINGS_LOCK:
            active = ranking_key in _ACTIVE_RANKINGS
            cancelled = ranking_key in _CANCELLED_RANKINGS
        items: list[JsonObject] = []
        completed = 0
        terminal = 0
        for candidate in candidates:
            current_reference = next(
                (
                    item
                    for item in all_references
                    if item.position_id == candidate.position_id
                    and item.target_type == "standard_position"
                    and item.status in {"current", "succeeded"}
                    and item.overall_score is not None
                    and item.cv_profile_version == candidate.cv_profile_version
                    and item.position_profile_version
                    == candidate.position_profile_version
                    and item.graph_version == candidate.graph_version
                ),
                None,
            )
            reference = current_reference or ranking_references.get(
                candidate.idempotency_key
            )
            formal_score = reference.overall_score if reference is not None else None
            if formal_score is not None and reference is not None and reference.status in {
                "current", "succeeded"
            }:
                calculation_status = "completed"
                completed += 1
                terminal += 1
            elif reference is not None and reference.status in {"pending", "running"}:
                calculation_status = reference.status
            elif reference is not None and reference.status == "failed":
                calculation_status = "failed"
                terminal += 1
            elif (
                reference is not None
                and reference.status in {"current", "succeeded"}
                and active
            ):
                # The worker persists the remote terminal state before it fetches
                # the evaluation and writes the formal score. Keep that short
                # synchronization window non-terminal instead of flashing a
                # false failure in the ranking UI.
                calculation_status = "running"
            elif reference is not None and reference.status in {"current", "succeeded"}:
                # A terminal remote task without a persisted score is not a
                # usable ranking result. Surface the broken state explicitly
                # instead of returning to ``ready`` and hiding it in the UI.
                calculation_status = "failed"
                terminal += 1
            else:
                calculation_status = "preliminary"
            item: JsonObject = {
                "position_id": candidate.position_id,
                "position_name": candidate.position_name,
                "score": formal_score if formal_score is not None else candidate.coarse_score,
                "score_source": "formal" if formal_score is not None else "coarse",
                "calculation_status": calculation_status,
                "evaluation_id": reference.evaluation_id if reference is not None else None,
                "task_id": reference.task_id if reference is not None else None,
            }
            if reference is not None and reference.error_code:
                item["error_code"] = reference.error_code
            elif (
                reference is not None
                and reference.status in {"current", "succeeded"}
                and formal_score is None
                and not active
            ):
                item["error_code"] = "MATCHING_RESULT_SCORE_MISSING"
            items.append(item)
        items.sort(
            key=lambda item: (
                float(item["score"]),
                1 if item["score_source"] == "formal" else 0,
                str(item["position_name"]),
            ),
            reverse=True,
        )
        for index, item in enumerate(items, start=1):
            item["rank"] = index
        return {
            "resume_id": resume_id,
            "validated_cv_snapshot_id": resume.validated_cv_snapshot_id,
            "algorithm_version": _RANKING_ALGORITHM_VERSION,
            "status": (
                "completed"
                if terminal == len(items)
                else "cancelled"
                if cancelled
                else "running"
                if active
                else "ready"
            ),
            "total": len(items),
            "completed": completed,
            "items": items,
        }

    def run_ranking(
        self,
        actor: AccountActor,
        *,
        resume_id: str,
        correlation_id: str = "",
        concurrency: int = 4,
    ) -> None:
        """Refine coarse candidates with the existing formal scorer in priority order."""
        resume = self.resumes.get(resume_id)
        if resume is None or not resume.validated_cv_snapshot_id:
            return
        ranking_key = self._ranking_key(actor.account_id, resume.validated_cv_snapshot_id)
        with _ACTIVE_RANKINGS_LOCK:
            # cancel may arrive after the HTTP response but before this background
            # function starts. Checking it before claiming the run closes that race.
            if ranking_key in _CANCELLED_RANKINGS:
                return
            if ranking_key in _ACTIVE_RANKINGS:
                return
            _ACTIVE_RANKINGS.add(ranking_key)
        try:
            try:
                cv_profile = self.contracts.cv_profile(resume_id)
            except MatchingContractUnavailable:
                return
            if cv_profile is None:
                return
            candidates = self._ranking_candidates(resume, cv_profile)
            existing = {
                item.idempotency_key: item
                for item in self.list(actor)
                if item.resume_id == resume_id
            }
            pending = [
                candidate
                for candidate in candidates
                if not (
                    candidate.idempotency_key in existing
                    and existing[candidate.idempotency_key].overall_score is not None
                    and existing[candidate.idempotency_key].status in {"current", "succeeded"}
                )
            ]
            with ThreadPoolExecutor(max_workers=max(1, min(concurrency, 8))) as executor:
                tuple(
                    executor.map(
                        lambda candidate: self._run_ranking_candidate(
                            actor, resume_id, candidate, correlation_id, ranking_key
                        ),
                        pending,
                    )
                )
        finally:
            with _ACTIVE_RANKINGS_LOCK:
                _ACTIVE_RANKINGS.discard(ranking_key)

    def _ranking_candidates(
        self, resume, cv_profile: JsonObject
    ) -> list[_RankingCandidate]:
        candidates: list[_RankingCandidate] = []
        catalog = self.position_catalog.list() if self.position_catalog else []
        self._prime_standard_position_readiness(catalog)
        for item in catalog:
            position_profile, blockers = self._standard_position_readiness(item.position_id)
            if blockers or position_profile is None:
                continue
            position_version = str(
                position_profile.get("profile_version")
                or position_profile.get("source_version")
                or "unspecified"
            )
            graph_version = str(position_profile.get("graph_version") or "unspecified")
            cv_version = str(
                cv_profile.get("profile_version")
                or cv_profile.get("source_version")
                or "unspecified"
            )
            raw_key = "|".join(
                (
                    resume.resume_id,
                    resume.validated_cv_snapshot_id,
                    item.position_id,
                    cv_version,
                    position_version,
                    graph_version,
                    _RANKING_ALGORITHM_VERSION,
                )
            )
            candidates.append(
                _RankingCandidate(
                    position_id=item.position_id,
                    position_name=item.position_name,
                    coarse_score=_coarse_match_score(cv_profile, position_profile),
                    idempotency_key=(
                        f"{_RANKING_IDEMPOTENCY_PREFIX}"
                        f"{sha256(raw_key.encode('utf-8')).hexdigest()}"
                    ),
                    cv_profile_version=cv_version,
                    position_profile_version=position_version,
                    graph_version=graph_version,
                )
            )
        return sorted(
            candidates,
            key=lambda item: (item.coarse_score, item.position_name),
            reverse=True,
        )

    def _run_ranking_candidate(
        self,
        actor: AccountActor,
        resume_id: str,
        candidate: _RankingCandidate,
        correlation_id: str,
        ranking_key: str,
    ) -> None:
        # The executor may already have queued every candidate. Cooperative
        # cancellation prevents queued candidates from starting while allowing
        # the small in-flight batch to finish and preserve valid results.
        with _ACTIVE_RANKINGS_LOCK:
            if ranking_key in _CANCELLED_RANKINGS:
                return
        try:
            task = self.run(
                actor,
                resume_id=resume_id,
                target_type="standard_position",
                target_id=candidate.position_id,
                use_enterprise_weights=False,
                generate_learning_path=False,
                idempotency_key=candidate.idempotency_key,
                correlation_id=correlation_id,
            )
            for _ in range(240):
                if task.status == "succeeded" or (
                    task.status == "failed"
                    and not _remote_failure_is_retryable(task)
                ):
                    break
                sleep(0.5)
                task = self.task(actor, task.task_id, correlation_id=correlation_id)
                if isinstance(task, TaskRecord):
                    return
            if task.status == "succeeded" and task.evaluation_id:
                self.get(actor, task.evaluation_id, correlation_id=correlation_id)
        except Exception as exc:
            self._record_ranking_failure(actor, resume_id, candidate, exc)

    def _record_ranking_failure(
        self,
        actor: AccountActor,
        resume_id: str,
        candidate: _RankingCandidate,
        exc: Exception,
    ) -> None:
        """Persist a terminal failure reference so the ranking can complete."""
        try:
            identity = self.identities.resolve(actor)
            error_code, error_message = _ranking_failure_details(exc)
            with self.uow_factory() as uow:
                existing = next(
                    (
                        item
                        for item in uow.matching.list_service_references(
                            actor.account_id
                        )
                        if item.idempotency_key == candidate.idempotency_key
                    ),
                    None,
                )
                if (
                    existing is not None
                    and existing.status in {"current", "succeeded"}
                    and existing.overall_score is not None
                ):
                    return
                record = (
                    replace(
                        existing,
                        status="failed",
                        error_code=error_code[:64],
                        error_message=error_message[:480],
                    )
                    if existing is not None
                    else MatchingServiceReferenceRecord(
                        task_id=f"ranking-failed:{candidate.idempotency_key}",
                        evaluation_id=None,
                        user_id=actor.account_id,
                        tenant_id=identity.tenant_id,
                        resume_id=resume_id,
                        position_id=candidate.position_id,
                        provider="matching-service",
                        target_type="standard_position",
                        status="failed",
                        idempotency_key=candidate.idempotency_key,
                        created_at=None,
                        updated_at=None,
                        access_scope=identity.access_scope,
                        error_code=error_code[:64],
                        error_message=error_message[:480],
                    )
                )
                uow.matching.upsert_service_reference(record)
                uow.commit()
        except Exception:
            return

    @staticmethod
    def _ranking_key(user_id: str, snapshot_id: str) -> str:
        return f"{user_id}:{snapshot_id}:{_RANKING_ALGORITHM_VERSION}"

    def prepare_ranking(self, actor: AccountActor, *, resume_id: str) -> None:
        """Clear a prior cancellation before an explicit user start."""
        resume = self.resumes.get(resume_id)
        if (
            resume is None
            or resume.owner_id != actor.account_id
            or not resume.validated_cv_snapshot_id
        ):
            raise MatchingRuleViolation("CV_SNAPSHOT_UNAVAILABLE")
        ranking_key = self._ranking_key(actor.account_id, resume.validated_cv_snapshot_id)
        with _ACTIVE_RANKINGS_LOCK:
            _CANCELLED_RANKINGS.discard(ranking_key)

    def cancel_ranking(self, actor: AccountActor, *, resume_id: str) -> None:
        """Stop unstarted candidates in the current batch ranking run."""
        resume = self.resumes.get(resume_id)
        if (
            resume is None
            or resume.owner_id != actor.account_id
            or not resume.validated_cv_snapshot_id
        ):
            raise MatchingRuleViolation("CV_SNAPSHOT_UNAVAILABLE")
        ranking_key = self._ranking_key(actor.account_id, resume.validated_cv_snapshot_id)
        with _ACTIVE_RANKINGS_LOCK:
            _CANCELLED_RANKINGS.add(ranking_key)

    def run(
        self,
        actor: AccountActor,
        *,
        resume_id: str,
        target_type: str,
        target_id: str,
        use_enterprise_weights: bool,
        generate_learning_path: bool,
        idempotency_key: str = "",
        correlation_id: str = "",
    ) -> RemoteTask:
        if target_type not in {"standard_position", "enterprise_job"}:
            raise MatchingRuleViolation("Unsupported matching target_type")
        if use_enterprise_weights and target_type != "enterprise_job":
            raise MatchingRuleViolation(
                "Enterprise weights require target_type=enterprise_job"
            )
        self.identities.authorize_request(
            actor, cv_id=resume_id, position_id=target_id, target_type=target_type
        )
        resume = self.resumes.get(resume_id)
        if resume is None or not resume.validated_cv_snapshot_id:
            raise MatchingRuleViolation("CV_SNAPSHOT_UNAVAILABLE")
        try:
            cv_profile = self.contracts.cv_profile(resume_id)
        except MatchingContractUnavailable as exc:
            raise MatchingRuleViolation("CV_PROFILE_UNAVAILABLE") from exc
        if target_type == "enterprise_job":
            try:
                position_profile = self.contracts.enterprise_job_profile(target_id)
            except MatchingContractUnavailable as exc:
                raise MatchingRuleViolation("POSITION_PROFILE_UNAVAILABLE") from exc
        else:
            position_profile, position_blockers = self._standard_position_readiness(target_id)
            if position_blockers:
                if position_blockers[0] == StandardPositionProfileInsufficient.code:
                    reason_code = (
                        position_blockers[1]
                        if len(position_blockers) > 1
                        else NO_VALID_SPECIALTY_ROUTE
                    )
                    raise StandardPositionProfileInsufficient(reason_code)
                raise MatchingRuleViolation(position_blockers[0])
        if cv_profile is None:
            raise MatchingRuleViolation("CV_PROFILE_UNAVAILABLE")
        if position_profile is None:
            raise MatchingRuleViolation("POSITION_PROFILE_UNAVAILABLE")
        identity = self.identities.resolve(actor)
        stable_key = idempotency_key.strip()
        if not stable_key:
            raise MatchingRuleViolation("idempotency_key is required")
        task = self.service.create_task(
            identity,
            cv_id=resume_id,
            position_id=target_id,
            idempotency_key=stable_key,
            correlation_id=correlation_id,
            target_type=target_type if target_type == "enterprise_job" else "standard_position",
            use_enterprise_weights=use_enterprise_weights,
            generate_learning_path=generate_learning_path,
            cv_profile=cv_profile,
            position_profile=position_profile,
        )
        reference_status = (
            "running" if _remote_failure_is_retryable(task) else task.status
        )
        with self.uow_factory() as uow:
            uow.matching.upsert_service_reference(
                MatchingServiceReferenceRecord(
                    task_id=task.task_id,
                    evaluation_id=task.evaluation_id,
                    user_id=actor.account_id,
                    tenant_id=identity.tenant_id,
                    resume_id=resume_id,
                    position_id=target_id,
                    provider=str(task.raw.get("provider", "matching-service")),
                    target_type=task.target_type,
                    status=reference_status,
                    idempotency_key=stable_key,
                    created_at=None,
                    updated_at=None,
                    access_scope=identity.access_scope,
                    source_version=str(task.raw.get("source_version", "legacy-unspecified")),
                    cv_profile_version=str(
                        cv_profile.get("profile_version")
                        or cv_profile.get("source_version")
                        or ""
                    ),
                    position_profile_version=str(
                        position_profile.get("profile_version")
                        or position_profile.get("source_version")
                        or ""
                    ),
                    taxonomy_version=str(
                        task.raw.get("taxonomy_version", "legacy-unspecified")
                    ),
                    graph_version=str(
                        position_profile.get(
                            "graph_version", "legacy-unspecified"
                        )
                    ),
                    algorithm_version=str(
                        task.raw.get("algorithm_version", "legacy-unspecified")
                    ),
                    matching_method=None,
                    degraded=None,
                    overall_score=None,
                )
            )
            uow.commit()
        return task

    def task(
        self, actor: AccountActor, task_id: str, *, correlation_id: str = ""
    ) -> RemoteTask | TaskRecord:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(task_id)
        if reference is not None and (
            reference.user_id != actor.account_id
            and actor.role not in INTERNAL_READ_ROLES
        ):
            raise PermissionDenied("Matching resource was not found")
        if reference is None:
            return self.historical_tasks.get(actor, task_id, {"match"})
        task = self.service.get_task(
            self.identities.resolve(actor), task_id, correlation_id=correlation_id
        )
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(task_id)
            if reference is not None:
                if (
                    reference.user_id != actor.account_id
                    and actor.role not in INTERNAL_READ_ROLES
                ):
                    raise PermissionDenied("Matching resource was not found")
                retryable_failure = _remote_failure_is_retryable(task)
                uow.matching.upsert_service_reference(
                    replace(
                        reference,
                        evaluation_id=task.evaluation_id,
                        status="running" if retryable_failure else task.status,
                        error_code=None if retryable_failure else task.error_code,
                        error_message=None if retryable_failure else task.error_message,
                    )
                )
                uow.commit()
        return task

    def abandon_and_restart(
        self,
        actor: AccountActor,
        task_id: str,
        *,
        idempotency_key: str,
        correlation_id: str = "",
    ) -> RemoteTask:
        """Abandon an owned personal task and submit the same CV-position pair again."""
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(task_id)
        if reference is None or (
            reference.user_id != actor.account_id
            and actor.role not in INTERNAL_READ_ROLES
        ):
            raise PermissionDenied("Matching resource was not found")
        if reference.target_type != "standard_position":
            raise MatchingRuleViolation("Only personal standard-position tasks can restart here")
        identity = self.identities.resolve(actor)
        abandoned = self.service.abandon_task(
            identity, task_id, correlation_id=correlation_id
        )
        with self.uow_factory() as uow:
            current = uow.matching.get_service_reference(task_id)
            if current is not None:
                uow.matching.upsert_service_reference(
                    replace(current, evaluation_id=abandoned.evaluation_id, status=abandoned.status)
                )
                uow.commit()
        return self.run(
            actor,
            resume_id=reference.resume_id,
            target_type="standard_position",
            target_id=reference.position_id,
            use_enterprise_weights=False,
            generate_learning_path=True,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def abandon(
        self, actor: AccountActor, task_id: str, *, correlation_id: str = ""
    ) -> RemoteTask:
        """Abandon an owned unfinished personal matching task without restarting it."""
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(task_id)
        if reference is None or (
            reference.user_id != actor.account_id
            and actor.role not in INTERNAL_READ_ROLES
        ):
            raise PermissionDenied("Matching resource was not found")
        task = self.service.abandon_task(
            self.identities.resolve(actor), task_id, correlation_id=correlation_id
        )
        with self.uow_factory() as uow:
            current = uow.matching.get_service_reference(task_id)
            if current is not None:
                uow.matching.upsert_service_reference(
                    replace(current, evaluation_id=task.evaluation_id, status=task.status)
                )
                uow.commit()
        return task

    def get(
        self, actor: AccountActor, evaluation_id: str, *, correlation_id: str = ""
    ) -> RemoteEvaluation:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is not None and (
            reference.user_id != actor.account_id
            and actor.role not in INTERNAL_READ_ROLES
        ):
            raise MatchingEvaluationNotFound("Matching evaluation not found")
        result = self.service.get_evaluation(
            self.identities.resolve(actor), evaluation_id, correlation_id=correlation_id
        )
        if reference is not None:
            resume = self.resumes.get(reference.resume_id)
            if resume is None or not resume.validated_cv_snapshot_id:
                raise MatchingRuleViolation("Validated CV snapshot lineage is unavailable")
            try:
                current_cv_profile = self.contracts.cv_profile(reference.resume_id)
                current_position_profile = (
                    self.contracts.enterprise_job_profile(reference.position_id)
                    if reference.target_type == "enterprise_job"
                    else self.contracts.position_profile(reference.position_id)
                )
            except MatchingContractUnavailable:
                current_cv_profile = None
                current_position_profile = None
            current_cv_source_version = (
                str(current_cv_profile.get("source_version"))
                if current_cv_profile is not None
                and current_cv_profile.get("source_version")
                else None
            )
            current_position_source_version = (
                str(current_position_profile.get("source_version"))
                if current_position_profile is not None
                and current_position_profile.get("source_version")
                else None
            )
            source_versions = dict(
                part.split("=", 1)
                for part in reference.source_version.split("|")
                if "=" in part
            )
            current = result.stale is False if reference.target_type == "enterprise_job" else bool(
                current_cv_source_version
                and current_position_source_version
                and source_versions.get("cv") == current_cv_source_version
                and source_versions.get("position") == current_position_source_version
            )
            if not current and not result.stale:
                result = replace(
                    result,
                    stale=True,
                        stale_reason_codes=tuple(
                        dict.fromkeys((*result.stale_reason_codes, "INPUT_VERSION_CHANGED"))
                    ),
                )
            with self.uow_factory() as uow:
                refreshed = uow.matching.get_service_reference(reference.task_id)
                if refreshed is not None:
                    final_result = result.evaluation.get("final_match_result")
                    final_result = (
                        final_result
                        if isinstance(final_result, dict)
                        else {}
                    )
                    overall_score = final_result.get("overall_score")
                    overall_score = (
                        float(overall_score)
                        if isinstance(overall_score, (int, float))
                        else None
                    )
                    uow.matching.upsert_service_reference(
                        replace(
                            refreshed,
                            status="stale" if result.stale else "current",
                            matching_method=(
                                result.matching_method
                                or product_matching_method(result.evaluation)
                            ),
                            degraded=result.degraded,
                            overall_score=overall_score,
                        )
                    )
                    uow.commit()
        return replace(
            result,
            user_id=reference.user_id if reference else actor.account_id,
            resume_id=reference.resume_id if reference else None,
            validated_cv_snapshot_id=(
                resume.validated_cv_snapshot_id
                if reference is not None and resume is not None
                else None
            ),
            position_id=reference.position_id if reference else None,
        )

    def list(self, actor: AccountActor) -> list[MatchingServiceReferenceRecord]:
        with self.uow_factory() as uow:
            return uow.matching.list_service_references(
                None if actor.role in INTERNAL_READ_ROLES else actor.account_id
            )

    def what_if(
        self,
        actor: AccountActor,
        evaluation_id: str,
        *,
        actions: tuple[JsonObject, ...],
        correlation_id: str = "",
    ) -> JsonObject:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is None or (
            reference.user_id != actor.account_id
            and actor.role not in INTERNAL_READ_ROLES
        ):
            raise MatchingEvaluationNotFound("Matching evaluation not found")
        current = self.get(actor, evaluation_id, correlation_id=correlation_id)
        if current.stale:
            raise MatchingRuleViolation("Stale evaluations cannot run What-if")
        cv_profile = self.contracts.cv_profile(reference.resume_id)
        position_profile = _resolve_position_profile(self.contracts, reference)
        if cv_profile is None or position_profile is None:
            raise MatchingRuleViolation("Validated matching profiles are required")
        return self.service.evaluate_what_if(
            self.identities.resolve(actor),
            baseline_evaluation=current.evaluation,
            cv_profile=cv_profile,
            position_profile=position_profile,
            actions=actions,
            target_type=reference.target_type,
            use_enterprise_weights=reference.target_type == "enterprise_job",
            correlation_id=correlation_id,
        )

    def explanation_deletion(
        self,
        actor: AccountActor,
        evaluation_id: str,
        *,
        deletion_kind: str,
        evidence_source_ids: tuple[str, ...],
        correlation_id: str = "",
    ) -> JsonObject:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is None or (
            reference.user_id != actor.account_id
            and actor.role not in INTERNAL_READ_ROLES
        ):
            raise MatchingEvaluationNotFound("Matching evaluation not found")
        current = self.get(actor, evaluation_id, correlation_id=correlation_id)
        if current.stale:
            raise MatchingRuleViolation(
                "Stale evaluations cannot run explanation deletion"
            )
        cv_profile = self.contracts.cv_profile(reference.resume_id)
        position_profile = _resolve_position_profile(self.contracts, reference)
        if cv_profile is None or position_profile is None:
            raise MatchingRuleViolation(
                "Validated matching profiles are required"
            )
        return self.service.evaluate_explanation_deletion(
            self.identities.resolve(actor),
            baseline_evaluation=current.evaluation,
            cv_profile=cv_profile,
            position_profile=position_profile,
            deletion_kind=deletion_kind,
            evidence_source_ids=evidence_source_ids,
            target_type=reference.target_type,
            use_enterprise_weights=reference.target_type == "enterprise_job",
            correlation_id=correlation_id,
        )


@dataclass(frozen=True)
class ManageLearningPaths:
    uow_factory: Callable[[], MatchingUnitOfWork]
    resumes: ResumeProfilePort
    positions: PositionProfilePort
    service: MatchingServicePort
    identities: MatchingIdentityPort
    contracts: MatchingContractPort | None = None

    def create(
        self,
        actor: AccountActor,
        *,
        evaluation_id: str,
        target_position_id: str | None,
        time_budget_hours: float | None,
        correlation_id: str = "",
    ) -> RemoteLearningPath:
        reference = self._owned_evaluation(actor, evaluation_id)
        result = self.service.get_evaluation(
            self.identities.resolve(actor),
            evaluation_id,
            correlation_id=correlation_id,
        )
        if result.evaluation_id != evaluation_id:
            raise MatchingRuleViolation("Matching evaluation identity mismatch")
        if reference.status not in {"current", "succeeded"}:
            raise MatchingRuleViolation(
                "Evaluation is not available for learning paths",
                code="EVALUATION_STALE",
            )
        self._require_current_evaluation(reference, result)
        source_position_id = result.position_id or reference.position_id
        if target_position_id is not None and target_position_id != source_position_id:
            raise MatchingRuleViolation(
                "Learning path target must match the source evaluation",
                code="LEARNING_PATH_TARGET_MISMATCH",
            )
        gap = result.gap_analysis
        if (
            time_budget_hours is not None
            or gap.get("generation_status") != "completed"
            or not gap.get("learning_path")
            or not gap.get("learning_routes")
        ):
            profiles = self._profiles(result.evaluation_id)
            gap = self.service.generate_learning_path(
                self.identities.resolve(actor),
                result.evaluation,
                correlation_id=correlation_id,
                time_budget_hours=time_budget_hours,
                **profiles,
            )
        if gap.get("generation_status") != "completed":
            raise MatchingRuleViolation(
                str(gap.get("error_message") or "Learning path generation was rejected"),
                code=str(
                    gap.get("error_code") or "LEARNING_PATH_GENERATION_REJECTED"
                ),
            )
        minimal_status = (
            (gap.get("minimal_action_set") or {}).get("status")
            if isinstance(gap.get("minimal_action_set"), dict)
            else None
        )
        if (
            gap.get("prioritized_gaps")
            and not gap.get("learning_path")
            and minimal_status
            not in {
                "hard_blocked",
                "position_evidence_insufficient",
                "no_positive_actions",
                "budget_excluded",
                "unreachable",
            }
        ):
            raise MatchingRuleViolation(
                "Learning path generation returned no steps for existing gaps",
                code="LEARNING_PATH_EMPTY",
            )
        item = self._remote_path(
            result,
            source_position_id,
            gap,
            time_budget_hours=time_budget_hours,
            **self._lineage(result.evaluation_id),
        )
        with self.uow_factory() as uow:
            saved = uow.matching.add_learning_path(
                LearningPathRecordData(
                    path_id=item.path_id,
                    evaluation_id=item.evaluation_id,
                    user_id=reference.user_id,
                    tenant_id=reference.tenant_id,
                    target_position_id=item.target_position_id,
                    time_budget_hours=item.time_budget_hours,
                    gap_analysis=item.gap_analysis,
                    status=item.status,
                    provider=item.provider,
                    algorithm_versions=dict(item.algorithm_versions or {}),
                    data_versions=dict(item.data_versions or {}),
                    versions=dict(item.versions or {}),
                    resume_id=item.resume_id,
                    validated_cv_snapshot_id=item.validated_cv_snapshot_id,
                    position_id=item.position_id,
                )
            )
            uow.commit()
        return self._from_record(saved)

    def list(self, actor: AccountActor) -> list[RemoteLearningPath]:
        with self.uow_factory() as uow:
            records = uow.matching.list_learning_paths(
                None if actor.role in INTERNAL_READ_ROLES else actor.account_id
            )
        return [self._from_record(record) for record in records]

    def get(
        self, actor: AccountActor, path_id: str, *, correlation_id: str = ""
    ) -> RemoteLearningPath:
        with self.uow_factory() as uow:
            record = uow.matching.get_learning_path(path_id)
        if record is None or (
            record.user_id != actor.account_id and actor.role not in INTERNAL_READ_ROLES
        ):
            raise LearningPathNotFound("Learning path not found")
        return self._from_record(record)

    def delete(self, actor: AccountActor, path_id: str) -> None:
        raise MatchingRuleViolation("Learning paths are read-only")

    @staticmethod
    def _remote_path(
        result: RemoteEvaluation,
        target_position_id: str | None,
        gap_analysis=None,
        *,
        resume_id: str | None = None,
        validated_cv_snapshot_id: str | None = None,
        position_id: str | None = None,
        time_budget_hours: float | None = None,
    ) -> RemoteLearningPath:
        gap = gap_analysis or result.gap_analysis
        status = "stale" if result.stale else str(
            gap.get("generation_status", "current")
        )
        return RemoteLearningPath(
            path_id=f"learning-path:{uuid4()}",
            evaluation_id=result.evaluation_id,
            target_position_id=target_position_id,
            gap_analysis=gap,
            status=status,
            created_at=result.created_at,
            updated_at=result.updated_at,
            provider=result.provider,
            algorithm_versions=result.algorithm_versions,
            data_versions=result.data_versions,
            versions=result.versions,
            resume_id=resume_id,
            validated_cv_snapshot_id=validated_cv_snapshot_id,
            position_id=position_id,
            time_budget_hours=time_budget_hours,
        )

    @staticmethod
    def _from_record(record: LearningPathRecordData) -> RemoteLearningPath:
        return RemoteLearningPath(
            path_id=record.path_id,
            evaluation_id=record.evaluation_id,
            target_position_id=record.target_position_id,
            gap_analysis=record.gap_analysis,
            status=record.status,
            created_at=record.created_at.isoformat() if record.created_at else None,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
            provider=record.provider,
            algorithm_versions=record.algorithm_versions,
            data_versions=record.data_versions,
            versions=record.versions,
            resume_id=record.resume_id,
            validated_cv_snapshot_id=record.validated_cv_snapshot_id,
            position_id=record.position_id,
            time_budget_hours=record.time_budget_hours,
        )

    def _owned_evaluation(
        self, actor: AccountActor, evaluation_id: str
    ) -> MatchingServiceReferenceRecord:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is None or reference.evaluation_id != evaluation_id or (
            reference.user_id != actor.account_id and actor.role not in INTERNAL_READ_ROLES
        ):
            raise MatchingEvaluationNotFound("Matching evaluation not found")
        return reference

    def _require_current_evaluation(
        self,
        reference: MatchingServiceReferenceRecord,
        result: RemoteEvaluation,
    ) -> None:
        if result.stale or reference.status == "stale":
            raise MatchingRuleViolation(
                "Stale evaluations cannot generate learning paths",
                code="EVALUATION_STALE",
            )
        resume = self.resumes.get(reference.resume_id)
        if resume is None or not resume.validated_cv_snapshot_id:
            raise MatchingRuleViolation(
                "Validated CV snapshot lineage is unavailable",
                code="CV_SNAPSHOT_UNAVAILABLE",
            )
        if self.contracts is None:
            return
        try:
            cv_profile = self.contracts.cv_profile(reference.resume_id)
            position_profile = _resolve_position_profile(self.contracts, reference)
        except MatchingContractUnavailable as exc:
            raise MatchingRuleViolation(
                "Matching profile lineage is unavailable",
                code="LEARNING_PATH_PROFILE_INVALID",
            ) from exc
        if cv_profile is None or position_profile is None:
            raise MatchingRuleViolation(
                "Matching profile lineage is unavailable",
                code="LEARNING_PATH_PROFILE_INVALID",
            )
        frozen_versions = dict(
            part.split("=", 1)
            for part in reference.source_version.split("|")
            if "=" in part
        )
        current_cv_version = str(cv_profile.get("source_version") or "")
        current_position_version = str(position_profile.get("source_version") or "")
        if (
            not current_cv_version
            or not current_position_version
            or frozen_versions.get("cv") != current_cv_version
            or frozen_versions.get("position") != current_position_version
        ):
            raise MatchingRuleViolation(
                "Stale evaluations cannot generate learning paths",
                code="EVALUATION_STALE",
            )

    def _lineage(self, evaluation_id: str) -> dict[str, str | None]:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is None:
            return {
                "resume_id": None,
                "validated_cv_snapshot_id": None,
                "position_id": None,
            }
        resume = self.resumes.get(reference.resume_id)
        return {
            "resume_id": reference.resume_id,
            "validated_cv_snapshot_id": (
                resume.validated_cv_snapshot_id
                if resume is not None
                else None
            ),
            "position_id": reference.position_id,
        }

    def _profiles(self, evaluation_id: str) -> dict[str, object]:
        with self.uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is None:
            return {}
        if self.contracts is None:
            return {}
        cv_profile = self.contracts.cv_profile(reference.resume_id)
        position_profile = _resolve_position_profile(self.contracts, reference)
        if cv_profile is None or position_profile is None:
            return {}
        return {
            "cv_profile": cv_profile,
            "position_profile": position_profile,
            "target_type": reference.target_type,
            "use_enterprise_weights": reference.target_type == "enterprise_job",
        }
