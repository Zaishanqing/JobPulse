from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, Sequence

from app.domain.jd_skill_catalog import (
    CatalogClassification,
    CatalogResolution as SkillCatalogResolution,
)
from app.domain.jd_skill_catalog import (
    CatalogResolutionStatus as SkillCatalogResolutionStatus,
)
from app.contexts.data_validation.domain import (
    DataValidationTask,
    ValidatedBundleSnapshot,
    ValidationReport,
)
from app.domain.json_types import FrozenJsonObject

__all__ = [
    "CrossSourceDuplicatePort",
    "DataValidationTaskRepository",
    "DataValidationUnitOfWork",
    "DataValidationUoWFactory",
    "ValidationInput",
    "ValidationGovernancePort",
    "ValidationGovernanceTaskReference",
    "ValidationInputReaderPort",
    "ValidationPortFactory",
    "SkillCatalogReference",
    "SkillCatalogResolution",
    "SkillCatalogResolutionPort",
    "SkillCatalogResolutionStatus",
    "ValidatedBundleSnapshotRepository",
    "ValidationReportRepository",
]


@dataclass(frozen=True)
class ValidationGovernanceTaskReference:
    task_id: str
    validation_report_id: str
    conclusion: str
    status: str
    created: bool


class ValidationGovernancePort(Protocol):
    def ensure_for_report(
        self,
        *,
        validation_report_id: str,
        data_validation_task_id: str,
        extraction_task_id: str,
        source_jd_version_id: str,
        conclusion: str,
    ) -> ValidationGovernanceTaskReference: ...


class DataValidationTaskRepository(Protocol):
    def get(self, task_id: str) -> DataValidationTask | None: ...

    def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> DataValidationTask | None: ...

    def list_by_extraction_and_policy(
        self,
        extraction_task_id: str,
        source_jd_version_id: str,
        policy_version: str,
    ) -> tuple[DataValidationTask, ...]: ...

    def add(self, task: DataValidationTask) -> DataValidationTask: ...

    def save(self, task: DataValidationTask) -> DataValidationTask: ...

    def claim_next_pending(self) -> DataValidationTask | None: ...


class ValidationReportRepository(Protocol):
    def get(self, report_id: str) -> ValidationReport | None: ...

    def get_by_task(self, task_id: str) -> ValidationReport | None: ...

    def add(self, report: ValidationReport) -> ValidationReport: ...


class ValidatedBundleSnapshotRepository(Protocol):
    def get(self, snapshot_id: str) -> ValidatedBundleSnapshot | None: ...

    def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> ValidatedBundleSnapshot | None: ...

    def add(
        self, snapshot: ValidatedBundleSnapshot
    ) -> ValidatedBundleSnapshot: ...


class DataValidationUnitOfWork(Protocol):
    tasks: DataValidationTaskRepository
    reports: ValidationReportRepository
    snapshots: ValidatedBundleSnapshotRepository
    governance: ValidationGovernancePort

    def __enter__(self) -> DataValidationUnitOfWork: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class DataValidationUoWFactory(Protocol):
    def __call__(
        self,
    ) -> AbstractContextManager[DataValidationUnitOfWork]: ...


@dataclass(frozen=True)
class SkillCatalogReference:
    source_name: str
    claimed_skill_id: str | None = None
    claimed_canonical_name: str | None = None

class SkillCatalogResolutionPort(Protocol):
    taxonomy_version: str

    def resolve(self, reference: SkillCatalogReference) -> SkillCatalogResolution: ...

    def classification_set(
        self, catalog_code: str
    ) -> tuple[str, tuple[CatalogClassification, ...]] | None: ...


class CrossSourceDuplicatePort(Protocol):
    def find_sources(self, canonical_hash: str) -> Sequence[str]: ...


@dataclass(frozen=True)
class ValidationInput:
    extraction_task_id: str
    source_jd_version_id: str
    source_jd_id: str
    source_platform: str
    source_record_id: str
    raw_text: str
    cleaned_text: str
    source_version: str
    bundle_id: str
    bundle: FrozenJsonObject
    ruleset_version: str
    catalog_snapshot_version: str
    policy_binding_version: str


class ValidationInputReaderPort(Protocol):
    def load(self, task: DataValidationTask) -> ValidationInput: ...


class ValidationPortFactory(Protocol):
    def catalog(
        self, snapshot_version: str
    ) -> SkillCatalogResolutionPort: ...

    def cross_source_duplicates(
        self, validation_input: ValidationInput
    ) -> CrossSourceDuplicatePort: ...
