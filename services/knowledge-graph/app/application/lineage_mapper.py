"""Strict anti-corruption mapping for optional upstream lineage DTOs."""

from __future__ import annotations

from collections.abc import Mapping

from app.application.contracts import CatalogSnapshotRefInput, ValidationLineageInput
from app.application.errors import ValidationError
from app.domain.lineage import (
    CatalogSnapshotRef,
    PublishedFactLineage,
    ValidationLineage,
)


def _strict_strings(
    payload: Mapping[str, object], expected: tuple[str, ...], label: str
) -> dict[str, str]:
    actual = set(payload)
    required = set(expected)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        raise ValidationError(
            f"invalid {label} fields; missing={missing}, unknown={unknown}",
            error_code="INVALID_LINEAGE_FIELDS",
        )
    values: dict[str, str] = {}
    for name in expected:
        value = payload[name]
        if not isinstance(value, str):
            raise ValidationError(
                f"{label}.{name} must be a string",
                error_code="INVALID_LINEAGE_FIELD_TYPE",
            )
        values[name] = value
    return values


def map_published_fact_lineage(
    *,
    validation: ValidationLineageInput | None = None,
    catalog: CatalogSnapshotRefInput | None = None,
) -> PublishedFactLineage:
    """Map an explicitly supplied internal DTO; absence remains real absence."""
    validation_value = None
    if validation is not None:
        snapshot_id = validation.get("validated_bundle_snapshot_id")
        validation = dict(validation)
        validation.setdefault("bundle_lineage_version", snapshot_id)
        values = _strict_strings(
            {
                name: value
                for name, value in validation.items()
                if name != "validated_bundle_snapshot_id"
            },
            (
                "data_validation_task_id",
                "validation_report_id",
                "validation_policy_version",
                "validation_conclusion",
                "bundle_lineage_version",
            ),
            "validation_lineage",
        )
        if snapshot_id is not None and not isinstance(snapshot_id, str):
            raise ValidationError(
                "validation_lineage.validated_bundle_snapshot_id must be a string or null",
                error_code="INVALID_LINEAGE_FIELD_TYPE",
            )
        if "validated_bundle_snapshot_id" not in validation:
            raise ValidationError(
                "invalid validation_lineage fields; missing=['validated_bundle_snapshot_id'], unknown=[]",
                error_code="INVALID_LINEAGE_FIELDS",
            )
        validation_value = ValidationLineage(
            validated_bundle_snapshot_id=snapshot_id,
            **values,
        )
    catalog_value = None
    if catalog is not None:
        catalog = dict(catalog)
        values = _strict_strings(
            {
                name: value
                for name, value in catalog.items()
                if name != "source_version"
            },
            (
                "source",
                "catalog_version",
                "content_hash",
                "effective_at",
                "status",
            ),
            "catalog_snapshot_ref",
        )
        catalog_value = CatalogSnapshotRef(
            source=values["source"],
            catalog_version=values["catalog_version"],
            source_version=values["content_hash"],
            effective_at=values["effective_at"],
            status=values["status"],
        )
    return PublishedFactLineage(validation_value, catalog_value)
