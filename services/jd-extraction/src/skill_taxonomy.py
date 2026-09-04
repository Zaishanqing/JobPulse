from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .exceptions import InputFormatError


SNAPSHOT_SCHEMA = "skill-taxonomy-snapshot.v1"
REVIEW_STATUS = "approved"
FACETS = {"concept_class", "technology_kind", "domain"}
CONCEPT_CLASSES = {
    "technology",
    "knowledge",
    "practice",
    "transversal_skill",
}
TECHNOLOGY_KINDS = {
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
}
DOMAINS = {
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

    node_status: dict[tuple[str, str], str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise InputFormatError("Skill taxonomy nodes must be objects")
        facet = node.get("facet")
        code = node.get("code")
        status = node.get("status")
        if facet not in FACETS or not isinstance(code, str) or not code:
            raise InputFormatError(f"Invalid skill taxonomy node: {node!r}")
        allowed_codes = _allowed_codes(facet)
        if code not in allowed_codes:
            raise InputFormatError(
                f"Unknown {facet} taxonomy code {code!r}"
            )
        if status not in {"active", "inactive"}:
            raise InputFormatError(
                f"Taxonomy node {facet}:{code} has invalid status {status!r}"
            )
        key = (facet, code)
        if key in node_status:
            raise InputFormatError(f"Duplicate taxonomy node {facet}:{code}")
        node_status[key] = status

    for skill_id, entry in skills.items():
        if not isinstance(skill_id, str) or not skill_id or not isinstance(entry, dict):
            raise InputFormatError("Snapshot skills must map skill IDs to objects")
        canonical_name = entry.get("canonical_name")
        relations = entry.get("classifications")
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} has no canonical_name"
            )
        if not isinstance(relations, list):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} classifications must be a list"
            )
        review = entry.get("review")
        if not isinstance(review, dict) or review.get("status") != REVIEW_STATUS:
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} is not explicitly approved"
            )
        if review.get("review_basis") != "canonical_identity":
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} must be reviewed by canonical identity"
            )
        if review.get("domain_decision") not in {
            "classified",
            "not_applicable",
        }:
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} has no final domain decision"
            )
        _validate_relations(skill_id, relations, node_status)
        has_domain = any(
            relation.get("facet") == "domain" for relation in relations
        )
        if has_domain != (review["domain_decision"] == "classified"):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} domain decision conflicts with relations"
            )
    return payload


def validate_snapshot_against_normalization_map(
    snapshot: dict[str, Any], normalization_map: dict[str, Any]
) -> None:
    catalog: dict[str, str] = {}
    for mapping in normalization_map["skills"].values():
        skill_id = mapping["skill_id"]
        canonical_name = mapping["canonical_name"]
        previous = catalog.setdefault(skill_id, canonical_name)
        if previous != canonical_name:
            raise InputFormatError(
                f"Normalization skill {skill_id!r} has inconsistent canonical names"
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


def build_classification_records(
    occurrences: Iterable[dict[str, Any]], snapshot: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    resolved = 0
    missing = 0
    identity_unresolved = 0
    for occurrence in occurrences:
        skill_id = occurrence.get("skill_id")
        identity_status = occurrence.get("identity_resolution_status")
        entry = snapshot["skills"].get(skill_id) if isinstance(skill_id, str) else None
        record = dict(occurrence)
        if identity_status != "resolved" or not isinstance(skill_id, str):
            record.update(
                {
                    "classification_resolution_status": "unresolved",
                    "classification_unresolved_reason": "identity_unresolved",
                    "classifications": [],
                }
            )
            identity_unresolved += 1
        elif entry is None or not entry["classifications"]:
            record.update(
                {
                    "classification_resolution_status": "unresolved",
                    "classification_unresolved_reason": "classification_missing",
                    "classifications": [],
                }
            )
            missing += 1
        else:
            record.update(
                {
                    "classification_resolution_status": "resolved",
                    "classifications": entry["classifications"],
                }
            )
            resolved += 1
        records.append(record)
    return records, {
        "classification_occurrence_count": len(records),
        "classification_resolved_count": resolved,
        "classification_missing_count": missing,
        "classification_identity_unresolved_count": identity_unresolved,
    }


def taxonomy_snapshot_version(snapshot: dict[str, Any]) -> str:
    del snapshot
    return "skill-taxonomy-snapshot.v1"


def build_taxonomy_projection(
    skill_ids: Iterable[str], snapshot: dict[str, Any]
) -> dict[str, Any]:
    node_names = {
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
            name_zh, name_en = node_names[
                (relation["facet"], relation["code"])
            ]
            classifications.append(
                {
                    **relation,
                    "name_zh": name_zh,
                    "name_en": name_en,
                }
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
        for requirement in payload["normalized_requirements"]:
            scope = f"requirement:{requirement['requirement_id']}"
            for occurrence_index, skill in enumerate(requirement["skills"]):
                try:
                    classification = next(records)
                except StopIteration as error:
                    raise ValueError(
                        "Classification records ended before normalized JD skills"
                    ) from error
                expected = (
                    payload["document_id"],
                    scope,
                    occurrence_index,
                    skill["source_name"],
                )
                actual = (
                    classification.get("document_id"),
                    classification.get("source_scope"),
                    classification.get("occurrence_index"),
                    classification.get("source_name"),
                )
                if actual != expected:
                    raise ValueError(
                        f"JD classification occurrence does not match normalized skill: "
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
    raise ValueError(f"Unused JD classification occurrence: {extra!r}")


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


def iter_jd_skill_occurrences(
    normalized_results: Iterable[Any],
) -> Iterable[dict[str, Any]]:
    for result in normalized_results:
        for requirement in result.normalized_requirements:
            for occurrence_index, skill in enumerate(requirement.skills):
                yield {
                    "document_id": result.document_id,
                    "source_scope": f"requirement:{requirement.requirement_id}",
                    "occurrence_index": occurrence_index,
                    "source_name": skill.source_name,
                    "skill_id": skill.skill_id,
                    "canonical_name": skill.canonical_name,
                    "identity_resolution_status": skill.resolution_status,
                }


def _allowed_codes(facet: str) -> set[str]:
    if facet == "concept_class":
        return CONCEPT_CLASSES
    if facet == "technology_kind":
        return TECHNOLOGY_KINDS
    return DOMAINS


def _validate_relations(
    skill_id: str,
    relations: list[Any],
    node_status: dict[tuple[str, str], str],
) -> None:
    seen: set[tuple[str, str]] = set()
    by_facet: dict[str, list[dict[str, Any]]] = {facet: [] for facet in FACETS}
    for relation in relations:
        if not isinstance(relation, dict):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} classifications must be objects"
            )
        facet = relation.get("facet")
        code = relation.get("code")
        is_primary = relation.get("is_primary")
        if facet not in FACETS or not isinstance(code, str):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} has an invalid classification"
            )
        key = (facet, code)
        if key in seen:
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} repeats {facet}:{code}"
            )
        if node_status.get(key) != "active":
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} references inactive or missing {facet}:{code}"
            )
        if not isinstance(is_primary, bool):
            raise InputFormatError(
                f"Snapshot skill {skill_id!r} classification primary flag must be boolean"
            )
        seen.add(key)
        by_facet[facet].append(relation)

    concept_relations = by_facet["concept_class"]
    kind_relations = by_facet["technology_kind"]
    if len(concept_relations) != 1 or not concept_relations[0]["is_primary"]:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} must have one primary concept_class"
        )
    if len(kind_relations) > 1:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} has multiple technology_kind values"
        )
    is_technology = concept_relations[0]["code"] == "technology"
    if is_technology != (len(kind_relations) == 1):
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} technology_kind conflicts with concept_class"
        )
    if kind_relations and not kind_relations[0]["is_primary"]:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} technology_kind must be primary"
        )
    if sum(relation["is_primary"] for relation in by_facet["domain"]) > 1:
        raise InputFormatError(
            f"Snapshot skill {skill_id!r} has multiple primary domains"
        )
