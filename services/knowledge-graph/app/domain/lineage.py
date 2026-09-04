"""Validation and catalog lineage attached to authoritative fact imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.domain.decisions import DomainRejection
from app.domain.value_types import AuditSnapshot


@dataclass(frozen=True)
class ValidationLineage:
    data_validation_task_id: str
    validation_report_id: str
    validated_bundle_snapshot_id: str | None
    validation_policy_version: str
    validation_conclusion: str
    bundle_lineage_version: str


@dataclass(frozen=True)
class CatalogSnapshotRef:
    source: str
    catalog_version: str
    source_version: str
    effective_at: str
    status: str


@dataclass(frozen=True)
class PublishedFactLineage:
    validation: ValidationLineage | None = None
    catalog: CatalogSnapshotRef | None = None

    @property
    def present(self) -> bool:
        return self.validation is not None or self.catalog is not None


@dataclass(frozen=True)
class LineageDecision:
    accepted: bool
    lineage_version: str | None = None
    rejection: DomainRejection | None = None


def lineage_snapshot(lineage: PublishedFactLineage) -> AuditSnapshot:
    value: dict[str, object] = {}
    if lineage.validation is not None:
        value["validation"] = asdict(lineage.validation)
    if lineage.catalog is not None:
        value["catalog"] = asdict(lineage.catalog)
    return value


def lineage_lineage_version(lineage: PublishedFactLineage) -> str | None:
    if lineage.validation is not None:
        catalog_version = (
            lineage.catalog.catalog_version if lineage.catalog else "none"
        )
        catalog_token = catalog_version.split(":")[-1]
        return f"{lineage.validation.validation_report_id}:{catalog_token}"
    if lineage.catalog is not None:
        return lineage.catalog.catalog_version
    return None


def decide_lineage(lineage: PublishedFactLineage) -> LineageDecision:
    validation = lineage.validation
    if validation is not None:
        required = {
            "data_validation_task_id": validation.data_validation_task_id,
            "validation_report_id": validation.validation_report_id,
            "validation_policy_version": validation.validation_policy_version,
            "bundle_lineage_version": validation.bundle_lineage_version,
        }
        empty = sorted(name for name, value in required.items() if not value.strip())
        if empty:
            return LineageDecision(
                False,
                rejection=DomainRejection(
                    "validation",
                    f"validation lineage fields cannot be empty: {', '.join(empty)}",
                    "INVALID_VALIDATION_LINEAGE",
                ),
            )
        if validation.validation_conclusion not in {"pass", "warn", "block"}:
            return LineageDecision(
                False,
                rejection=DomainRejection(
                    "validation",
                    "validation_conclusion must be pass, warn, or block",
                    "INVALID_VALIDATION_CONCLUSION",
                ),
            )
        if validation.validation_conclusion == "block":
            return LineageDecision(
                False,
                rejection=DomainRejection(
                    "validation",
                    "blocked validation lineage cannot be imported into the graph",
                    "VALIDATION_BLOCKED",
                ),
            )
        if not validation.validated_bundle_snapshot_id:
            return LineageDecision(
                False,
                rejection=DomainRejection(
                    "validation",
                    "pass or warn validation lineage requires a bundle snapshot",
                    "VALIDATED_BUNDLE_SNAPSHOT_REQUIRED",
                ),
            )
    catalog = lineage.catalog
    if catalog is not None:
        required = {
            "source": catalog.source,
            "catalog_version": catalog.catalog_version,
            "source_version": catalog.source_version,
            "effective_at": catalog.effective_at,
        }
        empty = sorted(name for name, value in required.items() if not value.strip())
        if empty:
            return LineageDecision(
                False,
                rejection=DomainRejection(
                    "validation",
                    "catalog snapshot reference is incomplete",
                    "INVALID_CATALOG_SNAPSHOT_REF",
                ),
            )
        if catalog.status not in {"active", "inactive"}:
            return LineageDecision(
                False,
                rejection=DomainRejection(
                    "validation",
                    "catalog snapshot status must be active or inactive",
                    "INVALID_CATALOG_SNAPSHOT_STATUS",
                ),
            )
    return LineageDecision(True, lineage_lineage_version(lineage))
