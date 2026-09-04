from __future__ import annotations

from typing import Any, Mapping


PUBLICATION_CONTENT_FIELDS = (
    "parse_result_id",
    "jd_id",
    "source_jd_id",
    "source_jd_version_id",
    "extraction_task_id",
    "document_id",
    "source_version",
    "source_content_hash",
    "schema_version",
    "normalization_schema_version",
    "extraction_result",
    "normalized_result",
    "legacy",
)

PUBLICATION_V2_LINEAGE_FIELDS = (
    "validation_lineage",
    "skill_catalog_snapshot",
    "position_catalog_snapshot",
)


def publication_content_basis(payload: Mapping[str, Any]) -> dict[str, Any]:
    contract_version = payload.get("contract_version", "jd-publication-snapshot.v1")
    fields = PUBLICATION_CONTENT_FIELDS
    if contract_version == "jd-publication-snapshot.v3":
        fields += PUBLICATION_V2_LINEAGE_FIELDS
    elif contract_version != "jd-publication-snapshot.v1":
        raise ValueError("jd_publication_snapshot_contract_unsupported")
    try:
        return {field: payload[field] for field in fields}
    except KeyError as exc:
        raise ValueError("jd_publication_snapshot_invalid") from exc


def publication_idempotency_key(payload: Mapping[str, Any]) -> str:
    parse_result_id = str(payload.get("parse_result_id") or "").strip()
    if not parse_result_id:
        raise ValueError("jd_publication_parse_result_required")
    source_version = str(payload.get("source_version") or "").strip()
    if not source_version:
        raise ValueError("jd_publication_source_version_required")
    return f"jd-publication:{parse_result_id}:{source_version}"
