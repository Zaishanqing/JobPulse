"""Immutable task, persistence and audit contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, computed_field, model_validator

from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import GapAnalysis
from app.domain.privacy import find_pii
from app.domain.profiles import CVMatchProfile, ImmutableDTO, PositionMatchProfile

TaskStatus = Literal["pending", "running", "succeeded", "failed"]
POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE = "requirement-graph.not-applicable"


class PersistenceVersions(ImmutableDTO):
    profile_contract_mapping_version: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_version: str = Field(min_length=1)
    embedding_dimension: int = Field(ge=0)
    vector_text_derivation_version: str = Field(min_length=1)
    semantic_algorithm_version: str = Field(min_length=1)
    semantic_threshold_version: str = Field(min_length=1)
    evaluation_algorithm_version: str = Field(min_length=1)
    scoring_algorithm_version: str = Field(min_length=1)
    scoring_config_version: str = Field(min_length=1)
    gap_algorithm_version: str = Field(min_length=1)
    gap_config_version: str = Field(min_length=1)
    semantic_index_revision: str = "index.disabled"
    target_type: str = "standard_position"
    use_enterprise_weights: bool = False
    generate_learning_path: bool = True
    cv_profile_version: str = "unspecified"
    position_profile_version: str = "unspecified"
    cv_source_version: str = "unspecified"
    position_source_version: str = "unspecified"
    cv_taxonomy_version: str = "unspecified"
    position_taxonomy_version: str = "unspecified"
    position_graph_version: str = "unspecified"
    position_requirement_graph_version: str = POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE

    @property
    def signature(self) -> str:
        prefix = (
            self.target_type,
            str(self.use_enterprise_weights),
            str(self.generate_learning_path),
            self.cv_profile_version,
            self.position_profile_version,
            self.cv_source_version,
            self.position_source_version,
            self.cv_taxonomy_version,
            self.position_taxonomy_version,
            self.position_graph_version,
        )
        # Keep the legacy signature byte-for-byte stable for profiles that do
        # not use the standard-position specialty-route projection. A
        # non-default projection identity is part of the signature and thus
        # cannot reuse an older flat/Top5 task.
        requirement_graph = (
            (self.position_requirement_graph_version,)
            if self.position_requirement_graph_version
            != POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE
            else ()
        )
        return "|".join(
            (
                *prefix,
                *requirement_graph,
                self.profile_contract_mapping_version,
                self.graph_version,
                self.embedding_model,
                self.embedding_version,
                str(self.embedding_dimension),
                self.vector_text_derivation_version,
                self.semantic_algorithm_version,
                self.semantic_threshold_version,
                self.semantic_index_revision,
                self.evaluation_algorithm_version,
                self.scoring_algorithm_version,
                self.scoring_config_version,
                self.gap_algorithm_version,
                self.gap_config_version,
            )
        )


class EvaluationTask(ImmutableDTO):
    schema_version: Literal["matching-evaluation-task.v1"] = "matching-evaluation-task.v1"
    task_id: str = Field(min_length=1)
    access_scope: str = Field(min_length=1, exclude=True, repr=False)
    idempotency_key: str = Field(min_length=1, max_length=200)
    versions: PersistenceVersions
    status: TaskStatus
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    evaluation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    lease_owner: str | None = Field(default=None, min_length=1, max_length=200)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    cv_profile: CVMatchProfile = Field(exclude=True, repr=False)
    position_profile: PositionMatchProfile = Field(exclude=True, repr=False)
    target_type: str = "standard_position"

    @computed_field
    @property
    def tenant_ref(self) -> str:
        parts = self.access_scope.split(":")
        return parts[1] if len(parts) >= 2 else self.access_scope

    @computed_field
    @property
    def source_version(self) -> str:
        return (
            f"cv={self.cv_profile.source_version}|"
            f"position={self.position_profile.source_version}"
        )

    @computed_field
    @property
    def cv_profile_id(self) -> str:
        return self.cv_profile.profile_id or self.cv_profile.cv_id

    @computed_field
    @property
    def position_profile_id(self) -> str:
        return self.position_profile.profile_id or self.position_profile.position_id

    @computed_field
    @property
    def taxonomy_version(self) -> str:
        return (
            f"cv={self.cv_profile.taxonomy_version}|"
            f"position={self.position_profile.taxonomy_version}"
        )

    @computed_field
    @property
    def graph_version(self) -> str:
        return self.position_profile.graph_version

    @computed_field
    @property
    def algorithm_version(self) -> str:
        return self.versions.evaluation_algorithm_version

    @computed_field
    @property
    def provider(self) -> str:
        return "matching-service"

    @computed_field
    @property
    def execution_mode(self) -> str:
        return "asynchronous_remote"

    @computed_field
    @property
    def rule_based(self) -> bool:
        return self.versions.embedding_version == "embedding.disabled"

    @computed_field
    @property
    def use_enterprise_weights(self) -> bool:
        return self.versions.use_enterprise_weights

    @computed_field
    @property
    def generate_learning_path(self) -> bool:
        return self.versions.generate_learning_path


class PersistedEvaluation(ImmutableDTO):
    schema_version: Literal["matching-evaluation-result.v1"] = (
        "matching-evaluation-result.v1"
    )
    evaluation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    access_scope: str = Field(min_length=1, exclude=True, repr=False)
    versions: PersistenceVersions
    evaluation: MatchEvaluation
    gap_analysis: GapAnalysis
    stale: bool = False
    stale_reason_codes: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def tenant_ref(self) -> str:
        parts = self.access_scope.split(":")
        return parts[1] if len(parts) >= 2 else self.access_scope

    @computed_field
    @property
    def target_type(self) -> str:
        return self.versions.target_type

    @computed_field
    @property
    def algorithm_version(self) -> str:
        return self.versions.evaluation_algorithm_version

    @computed_field
    @property
    def provider(self) -> str:
        return "matching-service"

    @computed_field
    @property
    def execution_mode(self) -> str:
        return "asynchronous_remote"

    @computed_field
    @property
    def rule_based(self) -> bool:
        return self.versions.embedding_version == "embedding.disabled"

    @computed_field
    @property
    def use_enterprise_weights(self) -> bool:
        return self.versions.use_enterprise_weights

    @computed_field
    @property
    def generate_learning_path(self) -> bool:
        return self.versions.generate_learning_path

    @computed_field
    @property
    def report_metadata(self) -> dict[str, object]:
        semantic = self.evaluation.semantic_status == "available"
        responsibility_semantic_verified = any(
            item.ce_score is not None
            or item.retrieval_score is not None
            or bool(item.top_candidates)
            for item in self.evaluation.responsibility_results
        )
        return {
            "provider": "matching-service",
            "method": "hybrid_semantic" if semantic else "deterministic_explainable",
            "rule_based": not semantic,
            # Product-level matching mode. The old ``method``/``rule_based``
            # fields describe the separate semantic-shadow retrieval channel
            # and are not a reliable indicator of the responsibility CE path.
            "matching_method": (
                "semantic_verified"
                if responsibility_semantic_verified
                else "rule"
            ),
            "degraded": False,
            "target_type": self.versions.target_type,
            "use_enterprise_weights": self.versions.use_enterprise_weights,
            "generate_learning_path": self.versions.generate_learning_path,
            "algorithm_versions": {
                "evaluation": self.versions.evaluation_algorithm_version,
                "scoring": self.versions.scoring_algorithm_version,
                "scoring_config": self.versions.scoring_config_version,
                "gap": self.versions.gap_algorithm_version,
                "gap_config": self.versions.gap_config_version,
                "semantic": self.versions.semantic_algorithm_version,
            },
            "data_versions": {
                "cv_source": self.versions.cv_source_version,
                "position_source": self.versions.position_source_version,
                "cv_taxonomy": self.versions.cv_taxonomy_version,
                "position_taxonomy": self.versions.position_taxonomy_version,
                "graph": self.versions.position_graph_version,
                "embedding": self.versions.embedding_version,
            },
        }

    @computed_field
    @property
    def radar_dimensions(self) -> tuple[dict[str, object], ...]:
        final = self.evaluation.final_match_result
        if final is None:
            return ()
        dimensions = (
            "required_skills",
            "responsibilities",
            "projects",
            "capability_level",
            "hard_conditions",
            "business_scenarios",
        )
        scores = {item.dimension: item for item in final.dimension_scores}
        rows: list[dict[str, object]] = []
        for name in dimensions:
            score = scores.get(name)
            contributions = tuple(
                item for item in final.score_contributions if item.dimension == name
            )
            candidate_evidence = tuple(
                evidence.model_dump(mode="json")
                for item in contributions
                for evidence in item.candidate_evidence
            )
            position_evidence = tuple(
                evidence.model_dump(mode="json")
                for item in contributions
                for evidence in item.position_evidence
            )
            gaps = tuple(
                item.message for item in final.gaps if item.dimension == name
            )
            measured = score is not None and score.score is not None
            rows.append(
                {
                    "dimension": name,
                    "candidate_score": score.score if measured else None,
                    "target_score": 100.0 if score is not None and score.applicable_count else None,
                    "measurement_status": "measured" if measured else "unavailable",
                    "applicable_count": score.applicable_count if score is not None else 0,
                    "scored_count": score.scored_count if score is not None else 0,
                    "result_ids": tuple(item.result_id for item in contributions),
                    "candidate_evidence": candidate_evidence,
                    "position_evidence": position_evidence,
                    "gap_explanations": gaps,
                }
            )
        return tuple(rows)


EvaluationResult = PersistedEvaluation


class AuditRecord(ImmutableDTO):
    audit_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    access_scope: str = Field(min_length=1, exclude=True, repr=False)
    event_type: Literal[
        "task_created",
        "task_started",
        "task_succeeded",
        "task_failed",
        "task_retried",
        "task_abandoned",
        "evaluation_stale",
        "evaluation_current",
    ]
    from_status: TaskStatus | None = None
    to_status: TaskStatus | None = None
    attempt: int = Field(ge=0)
    reason_code: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    algorithm_version: str = Field(min_length=1)
    occurred_at: datetime

    @model_validator(mode="after")
    def _safe_audit_values(self) -> AuditRecord:
        validate_audit_record(self)
        return self


class AuditSecurityError(ValueError):
    """Stable boundary error; unsafe input is deliberately not included."""

    code = "AUDIT_VALUE_UNSAFE"

    def __init__(self) -> None:
        super().__init__(self.code)


_SAFE_AUDIT_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,699}$")
_SAFE_AUDIT_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_SAFE_VERSION_SIGNATURE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:=+|/-]{0,1999}$")


def validate_audit_record(record: AuditRecord) -> None:
    """Application/model check and repository last-line guard share one policy."""
    safe_references = (record.audit_id, record.task_id, record.access_scope)
    if any(not _SAFE_AUDIT_REFERENCE.fullmatch(value) for value in safe_references):
        raise AuditSecurityError()
    if record.reason_code is not None and not _SAFE_AUDIT_CODE.fullmatch(
        record.reason_code
    ):
        raise AuditSecurityError()
    if not _SAFE_VERSION_SIGNATURE.fullmatch(record.algorithm_version):
        raise AuditSecurityError()
    # event_type/status are Literals; this catches model_construct and adapter bypasses.
    if record.event_type not in {
        "task_created", "task_started", "task_succeeded", "task_failed",
        "task_retried", "task_abandoned", "evaluation_stale",
        "evaluation_current",
    }:
        raise AuditSecurityError()
    if record.from_status not in {None, "pending", "running", "succeeded", "failed"}:
        raise AuditSecurityError()
    if record.to_status not in {None, "pending", "running", "succeeded", "failed"}:
        raise AuditSecurityError()
    if find_pii(record.model_dump(mode="python")):
        raise AuditSecurityError()


class TaskSubmissionResult(ImmutableDTO):
    task: EvaluationTask | None = None
    created: bool
    error_code: str | None = None
    error_message: str | None = None


class TaskQueryResult(ImmutableDTO):
    task: EvaluationTask | None = None
    error_code: str | None = None
    error_message: str | None = None


class EvaluationQueryResult(ImmutableDTO):
    result: PersistedEvaluation | None = None
    error_code: str | None = None
    error_message: str | None = None
