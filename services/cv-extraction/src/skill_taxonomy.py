from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from jobgraph_contracts.skill_taxonomy import (
    SkillClassificationSetV1,
)

from .exceptions import InputFormatError


SNAPSHOT_SCHEMA = "skill-taxonomy-snapshot.v1"
FACET_CODES = {
    "concept_class": {
        "technology",
        "knowledge",
        "practice",
        "transversal_skill",
    },
    "technology_kind": {
        "language",
        "framework",
        "library_sdk",
        "database_storage",
        "middleware_runtime",
        "platform_service",
        "tool",
        "protocol_standard",
        "algorithm_model",
        "hardware_system",
    },
    "domain": {
        "software_engineering",
        "data_engineering",
        "ai_intelligent_systems",
        "cloud_distributed",
        "cybersecurity_privacy",
        "network_communications",
        "computing_hardware",
        "embedded_iot_edge",
        "robotics_autonomy",
        "hci_graphics_xr",
        "blockchain_web3",
        "quantum_computing",
        "digital_governance",
    },
}


def load_skill_taxonomy_snapshot(path: str | Path) -> dict[str, Any]:
    snapshot_path = Path(path)
    if not snapshot_path.is_file():
        raise InputFormatError(
            f"Skill taxonomy snapshot does not exist: {snapshot_path}"
        )
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InputFormatError(
            f"Skill taxonomy snapshot is not valid JSON: {snapshot_path}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema") != SNAPSHOT_SCHEMA:
        raise InputFormatError(
            f"Skill taxonomy snapshot must use schema {SNAPSHOT_SCHEMA}"
        )
    nodes = payload.get("nodes")
    skills = payload.get("skills")
    if not isinstance(nodes, list) or not isinstance(skills, dict):
        raise InputFormatError(
            "Skill taxonomy snapshot must contain nodes and skills"
        )

    active_nodes: set[tuple[str, str]] = set()
    known_nodes: set[tuple[str, str]] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise InputFormatError("Skill taxonomy nodes must be objects")
        facet = node.get("facet")
        code = node.get("code")
        status = node.get("status")
        if facet not in FACET_CODES or code not in FACET_CODES[facet]:
            raise InputFormatError(f"Invalid skill taxonomy node: {node!r}")
        key = (facet, code)
        if key in known_nodes:
            raise InputFormatError(f"Duplicate taxonomy node {facet}:{code}")
        if status not in {"active", "inactive"}:
            raise InputFormatError(
                f"Taxonomy node {facet}:{code} has invalid status {status!r}"
            )
        known_nodes.add(key)
        if status == "active":
            active_nodes.add(key)

    for skill_id, entry in skills.items():
        if not isinstance(skill_id, str) or not skill_id or not isinstance(entry, dict):
            raise InputFormatError("Snapshot skills must map skill IDs to objects")
        if not isinstance(entry.get("canonical_name"), str):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} has no canonical_name"
            )
        relations = entry.get("classifications")
        if not isinstance(relations, list):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} classifications must be a list"
            )
        review = entry.get("review")
        if not isinstance(review, dict) or review.get("status") != "approved":
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} is not explicitly approved"
            )
        if review.get("review_basis") != "canonical_identity":
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} must be reviewed by canonical identity"
            )
        has_domain = any(item.get("facet") == "domain" for item in relations)
        expected = "classified" if has_domain else "not_applicable"
        if review.get("domain_decision") != expected:
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} has no final domain decision"
            )
        _validate_relations(skill_id, relations, active_nodes)
    return payload


def validate_snapshot_against_normalization_map(
    snapshot: dict[str, Any], normalization_map: dict[str, Any]
) -> None:
    catalog: dict[str, str] = {}
    for mapping in normalization_map["skills"].values():
        previous = catalog.setdefault(
            mapping["skill_id"], mapping["canonical_name"]
        )
        if previous != mapping["canonical_name"]:
            raise InputFormatError(
                f"Normalization skill {mapping['skill_id']!r} has inconsistent canonical names"
            )
    for skill_id, entry in snapshot["skills"].items():
        if skill_id not in catalog:
            raise InputFormatError(
                f"Skill taxonomy snapshot contains unknown skill_id {skill_id!r}"
            )
        if entry["canonical_name"] != catalog[skill_id]:
            raise InputFormatError(
                f"Skill taxonomy snapshot canonical name conflicts for {skill_id!r}"
            )
    missing = sorted(set(catalog) - set(snapshot["skills"]))
    if missing:
        raise InputFormatError(
            "Skill taxonomy snapshot is missing canonical skills: "
            + ", ".join(missing[:10])
        )


def taxonomy_snapshot_version(snapshot: dict[str, Any]) -> str:
    del snapshot
    return "skill-taxonomy-snapshot.v1"


def build_taxonomy_projection(
    skill_ids: Iterable[str], snapshot: dict[str, Any]
) -> dict[str, Any]:
    names = {
        (node["facet"], node["code"]): (
            node["name_zh"],
            node.get("name_en"),
        )
        for node in snapshot["nodes"]
    }
    skills = []
    for skill_id in sorted(set(skill_ids)):
        entry = snapshot["skills"].get(skill_id)
        if entry is None:
            raise InputFormatError(
                f"Resolved skill {skill_id!r} has no approved classification"
            )
        classifications = []
        for relation in entry["classifications"]:
            name_zh, name_en = names[(relation["facet"], relation["code"])]
            classifications.append(
                {**relation, "name_zh": name_zh, "name_en": name_en}
            )
        skills.append(
            {
                "skill_id": skill_id,
                "canonical_name": entry["canonical_name"],
                "classifications": classifications,
            }
        )
    return {
        "schema_version": "skill-taxonomy-projection.v1",
        "taxonomy_version": taxonomy_snapshot_version(snapshot),
        "skills": skills,
    }


def build_classification_records(
    occurrences: Iterable[dict[str, Any]], snapshot: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    resolved = missing = identity_unresolved = 0
    for occurrence in occurrences:
        skill_id = occurrence.get("skill_id")
        identity_status = occurrence.get("identity_resolution_status")
        entry = snapshot["skills"].get(skill_id) if isinstance(skill_id, str) else None
        record = dict(occurrence)
        if identity_status != "resolved" or not isinstance(skill_id, str):
            record.update(
                classification_resolution_status="unresolved",
                classification_unresolved_reason="identity_unresolved",
                classifications=[],
            )
            identity_unresolved += 1
        elif entry is None or not entry["classifications"]:
            record.update(
                classification_resolution_status="unresolved",
                classification_unresolved_reason="classification_missing",
                classifications=[],
            )
            missing += 1
        else:
            record.update(
                classification_resolution_status="resolved",
                classifications=entry["classifications"],
            )
            resolved += 1
        records.append(record)
    return records, {
        "classification_occurrence_count": len(records),
        "classification_resolved_count": resolved,
        "classification_missing_count": missing,
        "classification_identity_unresolved_count": identity_unresolved,
    }


def build_unified_normalized_payloads(
    normalized_results: Iterable[Any],
    classification_records: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    records = iter(classification_records)
    payloads: list[dict[str, Any]] = []
    for result in normalized_results:
        payload = result.model_dump(exclude_none=True)
        payload["skill_taxonomy_version"] = taxonomy_snapshot_version(snapshot)
        for occurrence_index, skill in enumerate(payload["normalized_skills"]):
            try:
                classification = next(records)
            except StopIteration as error:
                raise ValueError(
                    "Classification records ended before normalized CV skills"
                ) from error
            expected = (
                payload["document_id"],
                skill["source_scope"],
                skill["source_item_id"],
                occurrence_index,
                skill["source_name"],
            )
            actual = (
                classification.get("document_id"),
                classification.get("source_scope"),
                classification.get("source_item_id"),
                classification.get("occurrence_index"),
                classification.get("source_name"),
            )
            if actual != expected:
                raise ValueError(
                    f"CV classification occurrence does not match normalized skill: "
                    f"expected {expected!r}, got {actual!r}"
                )
            skill.pop("category_code")
            skill.pop("subcategory_code", None)
            skill["identity_resolution_status"] = skill.pop("resolution_status")
            skill["classification_resolution_status"] = classification[
                "classification_resolution_status"
            ]
            reason = classification.get("classification_unresolved_reason")
            if reason is not None:
                skill["classification_unresolved_reason"] = reason
            skill["classifications"] = classification["classifications"]
        payloads.append(payload)
    try:
        extra = next(records)
    except StopIteration:
        return payloads
    raise ValueError(f"Unused CV classification occurrence: {extra!r}")


def write_unified_normalized_artifacts(
    normalized_results: Iterable[Any],
    classification_records: list[dict[str, Any]],
    snapshot: dict[str, Any],
    final_dir: str | Path,
) -> list[dict[str, Any]]:
    output_dir = Path(final_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = build_unified_normalized_payloads(
        normalized_results, classification_records, snapshot
    )
    (output_dir / "normalized_annotations.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in payloads),
        encoding="utf-8",
    )
    (output_dir / "normalized_annotations.json").write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payloads


def iter_cv_skill_occurrences(
    normalized_results: Iterable[Any],
) -> Iterable[dict[str, Any]]:
    for result in normalized_results:
        for occurrence_index, skill in enumerate(result.normalized_skills):
            yield {
                "document_id": result.document_id,
                "source_scope": skill.source_scope,
                "source_item_id": skill.source_item_id,
                "occurrence_index": occurrence_index,
                "source_name": skill.source_name,
                "skill_id": skill.skill_id,
                "canonical_name": skill.canonical_name,
                "identity_resolution_status": skill.resolution_status,
            }


def _validate_relations(
    skill_id: str,
    relations: list[Any],
    active_nodes: set[tuple[str, str]],
) -> None:
    seen: set[tuple[str, str]] = set()
    by_facet: dict[str, list[dict[str, Any]]] = {
        facet: [] for facet in FACET_CODES
    }
    for relation in relations:
        if not isinstance(relation, dict):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} classifications must be objects"
            )
        facet = relation.get("facet")
        code = relation.get("code")
        key = (facet, code)
        if key not in active_nodes or key in seen:
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} has invalid or duplicate classification {key!r}"
            )
        if not isinstance(relation.get("is_primary"), bool):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} classification primary flag must be boolean"
            )
        seen.add(key)
        by_facet[facet].append(relation)

    concepts = by_facet["concept_class"]
    kinds = by_facet["technology_kind"]
    if len(concepts) != 1 or not concepts[0]["is_primary"]:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} must have one primary concept_class"
        )
    if len(kinds) > 1 or (concepts[0]["code"] == "technology") != (len(kinds) == 1):
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} technology_kind conflicts with concept_class"
        )
    if kinds and not kinds[0]["is_primary"]:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} technology_kind must be primary"
        )
    if sum(relation["is_primary"] for relation in by_facet["domain"]) > 1:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} has multiple primary domains"
        )
