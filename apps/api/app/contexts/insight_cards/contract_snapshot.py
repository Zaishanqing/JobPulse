"""Canonical InsightCard contract snapshot.

This module derives a JSON contract description directly from the existing
domain dataclasses.  It intentionally does not introduce a second DTO system:
``app.contexts.insight_cards.contracts.InsightCard`` remains the single domain
shape, ``app.api.v1.insights`` serializes it with ``dataclasses.asdict``, and
the frontend mirrors the same committed snapshot as a TypeScript contract.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, fields, is_dataclass
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from app.contexts.insight_cards.contracts import InsightCard
from app.domain.json_types import MutableJsonObject

SNAPSHOT_VERSION = "insight-card-contract-snapshot.v1"
BFF_CONTRACT_VERSION = "insight-card.v1"

_CORE_ROLES = {
    "evidence_refs": "evidence_refs",
    "counter_evidence_refs": "evidence_refs",
    "used_evidence_ids": "evidence_refs",
    "uncertainty_state": "uncertainty",
    "uncertainty_reasons": "uncertainty",
    "algorithm_version": "algorithm_config_version",
    "algorithm_config_version": "algorithm_config_version",
    "algorithm_config_hash": "algorithm_config_version",
    "evidence_algorithm_version": "algorithm_config_version",
    "evidence_config_hash": "algorithm_config_version",
    "authority_state": "review_state",
    "human_decision": "review_state",
    "next_action": "result_status",
    "coverage_status": "result_status",
    "limitations": "result_status",
}


def _type_repr(annotation: Any) -> str:
    if annotation is type(None):
        return "None"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _strip_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        if len(args) != len(get_args(annotation)):
            return (args[0] if len(args) == 1 else Union[args], True)
    return annotation, False


def _kind(annotation: Any) -> str:
    annotation, _ = _strip_optional(annotation)
    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    origin = get_origin(annotation)
    if origin in (tuple, list, set, frozenset):
        return "array"
    if origin is Literal:
        return "string"
    if is_dataclass(annotation):
        return "object"
    raise TypeError(f"unsupported contract annotation: {annotation!r}")


def _item_spec(annotation: Any, definitions: dict[str, Any]) -> dict[str, Any]:
    annotation, nullable = _strip_optional(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return {
            "kind": "string",
            "nullable": nullable,
            "allowed_values": [str(item) for item in get_args(annotation)],
        }
    if is_dataclass(annotation):
        definition = annotation.__name__
        definitions[definition] = _fields_spec(annotation, definitions)
        return {"kind": "object", "nullable": nullable, "definition": definition}
    if annotation is str:
        return {"kind": "string", "nullable": nullable}
    if annotation is int:
        return {"kind": "integer", "nullable": nullable}
    if annotation is float:
        return {"kind": "number", "nullable": nullable}
    if annotation is bool:
        return {"kind": "boolean", "nullable": nullable}
    raise TypeError(f"unsupported item annotation: {annotation!r}")


def _field_spec(
    dataclass_type: type, field_name: str, definitions: dict[str, Any]
) -> dict[str, Any]:
    annotation = get_type_hints(dataclass_type)[field_name]
    annotation, nullable = _strip_optional(annotation)
    origin = get_origin(annotation)
    spec: dict[str, Any] = {
        "json_name": field_name,
        "kind": _kind(annotation),
        "python_type": _type_repr(annotation),
        "nullable": nullable,
        "required": _field_required(dataclass_type, field_name),
    }
    if spec["kind"] == "array":
        args = get_args(annotation)
        if args and args[-1] is Ellipsis:
            spec["item"] = _item_spec(args[0], definitions)
        elif args:
            spec["item"] = _item_spec(args[0], definitions)
    if origin is Literal:
        spec["allowed_values"] = [str(item) for item in get_args(annotation)]
    elif is_dataclass(annotation):
        definition = annotation.__name__
        definitions[definition] = _fields_spec(annotation, definitions)
        spec["definition"] = definition
    if field_name in _CORE_ROLES:
        spec["contract_role"] = _CORE_ROLES[field_name]
    return spec


def _field_required(dataclass_type: type, field_name: str) -> bool:
    field = next(field for field in fields(dataclass_type) if field.name == field_name)
    if field.default is not MISSING:
        return False
    return field.default_factory is MISSING


def _fields_spec(
    dataclass_type: type, definitions: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields(dataclass_type):
        result[field.name] = _field_spec(dataclass_type, field.name, definitions)
    return result


def contract_snapshot() -> MutableJsonObject:
    """Return the committed JSON snapshot, derived from the domain dataclasses."""
    definitions: dict[str, Any] = {}
    root_fields = _fields_spec(InsightCard, definitions)
    return {
        "schema_version": SNAPSHOT_VERSION,
        "bff_contract_version": BFF_CONTRACT_VERSION,
        "domain_source": {
            "module": "app.contexts.insight_cards.contracts",
            "root_type": "InsightCard",
            "bff_serializer": (
                "app.api.v1.insights returns success_response("
                "data=asdict(assemble_insight_card(source)))"
            ),
        },
        "fields": root_fields,
        "definitions": definitions,
        "core_roles": {
            "evidence_refs": [
                "evidence_refs",
                "counter_evidence_refs",
                "used_evidence_ids",
            ],
            "uncertainty": ["uncertainty_state", "uncertainty_reasons"],
            "algorithm_config_version": [
                "algorithm_version",
                "algorithm_config_version",
                "algorithm_config_hash",
                "evidence_algorithm_version",
                "evidence_config_hash",
            ],
            "review_state": ["authority_state", "human_decision"],
            "result_status": ["next_action", "coverage_status", "limitations"],
        },
        "invariants": {
            "evidence_ref_identity_non_empty": (
                "EvidenceRef.evidence_id/source_object_type/source_object_id/"
                "source_document_id are validated non-empty by __post_init__"
            ),
            "evidence_location_pair": (
                "EvidenceRef.location_start and location_end are both null "
                "or both integers with end >= start"
            ),
            "used_evidence_subset": (
                "used_evidence_ids must be a subset of visible evidence_refs"
            ),
            "algorithm_version_non_empty": (
                "insight_id/subject_ref/claim/algorithm_version are validated "
                "non-empty before BFF serialization"
            ),
        },
    }


def snapshot_json(snapshot: MutableJsonObject | None = None) -> str:
    payload = snapshot or contract_snapshot()
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_bff_payload(
    payload: MutableJsonObject, snapshot: MutableJsonObject | None = None
) -> list[str]:
    """Validate an actual BFF card payload against the canonical snapshot.

    This is intentionally strict: unknown fields, missing fields, wrong enum
    values and wrong nullability all fail.
    """
    snapshot = snapshot or contract_snapshot()
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]
    problems: list[str] = []
    expected = set(snapshot["fields"])
    actual = set(payload)
    for field in sorted(expected - actual):
        problems.append(f"BFF field missing: {field}")
    for field in sorted(actual - expected):
        problems.append(f"BFF field renamed/dropped/unexpected: {field}")
    if problems:
        return problems
    problems.extend(_validate_object_fields(payload, snapshot["fields"], snapshot))
    return problems


def _validate_object_fields(
    payload: dict[str, Any],
    fields_spec: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[str]:
    problems: list[str] = []
    for name, spec in fields_spec.items():
        value = payload.get(name)
        problems.extend(_validate_value(name, value, spec, snapshot))
    return problems


def _validate_value(
    path: str, value: Any, spec: dict[str, Any], snapshot: dict[str, Any]
) -> list[str]:
    problems: list[str] = []
    if value is None:
        if not spec["nullable"]:
            problems.append(f"{path} is null but contract requires {spec['kind']}")
        return problems
    kind = spec["kind"]
    if kind == "string":
        if not isinstance(value, str):
            problems.append(f"{path} expected string, got {type(value).__name__}")
        elif "allowed_values" in spec and value not in spec["allowed_values"]:
            problems.append(
                f"{path}={value!r} not in contract enum {spec['allowed_values']}"
            )
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"{path} expected integer, got {type(value).__name__}")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{path} expected number, got {type(value).__name__}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            problems.append(f"{path} expected boolean, got {type(value).__name__}")
    elif kind == "array":
        if not isinstance(value, (list, tuple)):
            problems.append(f"{path} expected array, got {type(value).__name__}")
        elif "item" in spec:
            for index, item in enumerate(value):
                problems.extend(
                    _validate_value(f"{path}[{index}]", item, spec["item"], snapshot)
                )
    elif kind == "object":
        if not isinstance(value, dict):
            problems.append(f"{path} expected object, got {type(value).__name__}")
        elif "definition" in spec:
            problems.extend(
                _validate_object_fields(
                    value, snapshot["definitions"][spec["definition"]], snapshot
                )
            )
    return problems


__all__ = [
    "BFF_CONTRACT_VERSION",
    "SNAPSHOT_VERSION",
    "contract_snapshot",
    "snapshot_json",
    "validate_bff_payload",
]
