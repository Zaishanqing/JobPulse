from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


COLLECTIONS = ("responsibilities", "requirements", "company_facts", "employment_facts")


@dataclass(frozen=True)
class RepairTarget:
    collection: str
    index: int


@dataclass(frozen=True)
class LocalRepairPlan:
    targets: tuple[RepairTarget, ...]
    source_blocks: tuple[dict[str, Any], ...]
    append_collections: tuple[str, ...] = ()


def _target_from_path(path: str) -> RepairTarget | None:
    for collection in COLLECTIONS:
        prefix = f"{collection}["
        if path.startswith(prefix) and path.endswith("]"):
            index_text = path[len(prefix):-1]
            if index_text.isdigit():
                return RepairTarget(collection, int(index_text))
    return None


def _target_from_id(payload: dict[str, Any], object_id: str) -> RepairTarget | None:
    for collection in COLLECTIONS:
        id_field = "requirement_id" if collection in ("responsibilities", "requirements") else "fact_id"
        for index, item in enumerate(payload.get(collection, [])):
            if isinstance(item, dict) and item.get(id_field) == object_id:
                return RepairTarget(collection, index)
    return None


def _targets_from_schema_errors(errors: object) -> set[RepairTarget]:
    if not isinstance(errors, list):
        return set()
    targets: set[RepairTarget] = set()
    for error in errors:
        if not isinstance(error, dict):
            continue
        location = error.get("loc")
        if not isinstance(location, (list, tuple)) or len(location) < 2:
            continue
        collection, index = location[0], location[1]
        if collection in COLLECTIONS and isinstance(index, int):
            targets.add(RepairTarget(collection, index))
    return targets


def plan_local_repair(
    canonicalized: dict[str, Any],
    error_type: str,
    error_details: object,
    source_blocks: list[dict[str, Any]],
) -> LocalRepairPlan | None:
    """Return the exact existing objects a deterministic error authorizes us to repair.

    Returning None is deliberate: top-level or otherwise unaddressable structural
    failures must use the existing full re-extraction request.
    """
    targets: set[RepairTarget] = set()
    append_collections: set[str] = set()
    details = error_details if isinstance(error_details, list) else [error_details]

    if error_type == "SchemaValidationError":
        targets.update(_targets_from_schema_errors(error_details))
    elif error_type == "SourceBindingError":
        for detail in details:
            if isinstance(detail, dict) and isinstance(detail.get("object_path"), str):
                target = _target_from_path(detail["object_path"])
                if target is not None:
                    targets.add(target)
    elif error_type == "SemanticValidationError":
        for detail in details:
            if not isinstance(detail, dict):
                continue
            for key in ("requirement_id", "fact_id", "related_fact_id", "related_requirement_id"):
                value = detail.get(key)
                if isinstance(value, str):
                    target = _target_from_id(canonicalized, value)
                    if target is not None:
                        targets.add(target)
            for value in detail.get("requirement_ids", []):
                if isinstance(value, str):
                    target = _target_from_id(canonicalized, value)
                    if target is not None:
                        targets.add(target)
            if detail.get("code") == "candidate_requirement_in_responsibility":
                append_collections.add("requirements")

    if not targets:
        return None
    if any(target.index >= len(canonicalized.get(target.collection, [])) for target in targets):
        return None

    source_ids = {
        item.get("evidence", {}).get("source_id")
        for target in targets
        for item in [canonicalized[target.collection][target.index]]
        if isinstance(item, dict) and isinstance(item.get("evidence"), dict)
    }
    relevant_blocks = tuple(
        deepcopy(block)
        for block in source_blocks
        if block.get("source_id") in source_ids
    )
    return LocalRepairPlan(
        targets=tuple(sorted(targets, key=lambda item: (item.collection, item.index))),
        source_blocks=relevant_blocks,
        append_collections=tuple(sorted(append_collections)),
    )


def apply_local_repair(
    payload: dict[str, Any],
    response: dict[str, Any],
    plan: LocalRepairPlan,
) -> dict[str, Any]:
    """Apply only explicitly authorized object-level operations to a raw model payload."""
    if set(response) != {"operations"} or not isinstance(response["operations"], list):
        raise ValueError("Local repair response must contain exactly one operations list.")
    if not response["operations"]:
        raise ValueError("Local repair response operations must not be empty.")

    authorized = {(target.collection, target.index) for target in plan.targets}
    authorized_collections = {
        *(target.collection for target in plan.targets),
        *plan.append_collections,
    }
    repaired = deepcopy(payload)
    removals: dict[str, list[int]] = {}
    replacements: dict[tuple[str, int], dict[str, Any]] = {}
    appends: dict[str, list[dict[str, Any]]] = {}
    operated_targets: set[tuple[str, int]] = set()

    def validate_value(value: dict[str, Any]) -> None:
        forbidden_root_fields = {"document_id", "requirement_id", "fact_id"}
        if forbidden_root_fields.intersection(value):
            raise ValueError("Local repair value must not contain Python-generated identifier fields.")
        evidence = value.get("evidence")
        if isinstance(evidence, dict) and {"start", "end", "alignment", "occurrence_index"}.intersection(evidence):
            raise ValueError("Local repair value must not contain Python-generated evidence fields.")

    for operation in response["operations"]:
        if not isinstance(operation, dict):
            raise ValueError("Each local repair operation must be an object.")
        op = operation.get("op")
        target = operation.get("target")
        if not isinstance(target, dict):
            raise ValueError("Each local repair operation must declare a target object.")
        collection = target.get("collection")
        if collection not in COLLECTIONS:
            raise ValueError("Local repair operation targets an unknown collection.")

        if op in ("replace", "remove"):
            if set(target) != {"collection", "index"} or not isinstance(target.get("index"), int):
                raise ValueError("replace/remove targets must contain collection and integer index.")
            index = target["index"]
            if (collection, index) not in authorized:
                raise ValueError("Local repair operation targets an object outside the authorized error scope.")
            if (collection, index) in operated_targets:
                raise ValueError("Local repair response cannot operate on the same target more than once.")
            operated_targets.add((collection, index))
            if op == "replace":
                if set(operation) != {"op", "target", "value"} or not isinstance(operation["value"], dict):
                    raise ValueError("replace must contain exactly an object value.")
                validate_value(operation["value"])
                replacements[(collection, index)] = deepcopy(operation["value"])
            elif set(operation) != {"op", "target"}:
                raise ValueError("remove must contain only op and target.")
            else:
                removals.setdefault(collection, []).append(index)
        elif op == "append":
            if set(target) != {"collection"} or collection not in authorized_collections:
                raise ValueError("append is allowed only in an authorized target collection.")
            if set(operation) != {"op", "target", "value"} or not isinstance(operation["value"], dict):
                raise ValueError("append must contain exactly an object value.")
            validate_value(operation["value"])
            appends.setdefault(collection, []).append(deepcopy(operation["value"]))
        else:
            raise ValueError("Local repair operation op must be replace, remove, or append.")

    for collection in COLLECTIONS:
        items = repaired.get(collection)
        if not isinstance(items, list):
            raise ValueError(f"Local repair base payload has invalid {collection} collection.")
        for index in removals.get(collection, []):
            if (collection, index) in replacements:
                raise ValueError("The same local repair target cannot be both removed and replaced.")
        updated: list[Any] = []
        for index, item in enumerate(items):
            if index in removals.get(collection, []):
                continue
            updated.append(replacements.get((collection, index), item))
        updated.extend(appends.get(collection, []))
        repaired[collection] = updated
    return repaired
