from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
import unicodedata

from .field_contract import (
    EDUCATION_FIELD_EVIDENCE_NAME_VALUES,
    PERSONAL_FIELD_EVIDENCE_NAME_VALUES,
    PROJECT_FIELD_EVIDENCE_NAME_VALUES,
    WORK_FIELD_EVIDENCE_NAME_VALUES,
)


COLLECTIONS = (
    "education",
    "work_experience",
    "project_experience",
    "skills",
    "languages",
    "certificates",
    "awards",
    "publications",
    "patents",
    "research_outputs",
    "self_evaluation",
)
SINGLETONS = ("personal_info",)

FIELD_EVIDENCE_FIELDS = {
    "personal_info": PERSONAL_FIELD_EVIDENCE_NAME_VALUES,
    "education": EDUCATION_FIELD_EVIDENCE_NAME_VALUES,
    "work_experience": WORK_FIELD_EVIDENCE_NAME_VALUES,
    "project_experience": PROJECT_FIELD_EVIDENCE_NAME_VALUES,
}
DERIVED_EVIDENCE_FIELDS = {"degree", "date", "work_type", "work_status"}


def _normalized_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _close_replacement_field_evidence(
    collection: str,
    original: dict[str, Any],
    replacement: dict[str, Any],
) -> dict[str, Any]:
    """Keep a replacement's field evidence structurally closed over its values.

    A repair response replaces a whole object. Evidence for an unchanged or
    deterministically derived field must not disappear merely because the model
    omitted it from the replacement object.
    """
    field_names = FIELD_EVIDENCE_FIELDS.get(collection)
    if field_names is None:
        return replacement
    closed = deepcopy(replacement)
    incoming = closed.get("field_evidence")
    bindings = incoming if isinstance(incoming, list) else []
    expected = {
        field_name
        for field_name in field_names
        if closed.get(field_name) is not None
        and not (
            field_name in {"degree", "work_type", "work_status"}
            and closed.get(field_name) == "unknown"
        )
    }
    unique: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        field_name = binding.get("field_name")
        if isinstance(field_name, str) and field_name in expected and field_name not in unique:
            unique[field_name] = deepcopy(binding)

    original_bindings = {
        binding.get("field_name"): binding
        for binding in original.get("field_evidence", [])
        if isinstance(binding, dict) and isinstance(binding.get("field_name"), str)
    }
    for field_name in sorted(expected.difference(unique)):
        binding = original_bindings.get(field_name)
        if not isinstance(binding, dict):
            continue
        evidence = binding.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        value = closed.get(field_name)
        supported = field_name in DERIVED_EVIDENCE_FIELDS
        if not supported and isinstance(value, str) and isinstance(quote, str):
            supported = _normalized_text(value) in _normalized_text(quote)
        if supported:
            unique[field_name] = deepcopy(binding)
    closed["field_evidence"] = [unique[name] for name in field_names if name in unique]
    return closed


def materialize_collection_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """Materialize the list defaults declared by CVExtractionResult.

    A missing collection and an explicitly empty collection are the same valid
    schema state. Present non-list values remain hard errors.
    """
    materialized = deepcopy(payload)
    for collection in COLLECTIONS:
        if collection not in materialized:
            materialized[collection] = []
        elif not isinstance(materialized[collection], list):
            raise ValueError(f"Extraction payload has invalid {collection} collection.")
    return materialized


@dataclass(frozen=True)
class RepairTarget:
    collection: str
    index: int | None = None


@dataclass(frozen=True)
class LocalRepairPlan:
    targets: tuple[RepairTarget, ...]
    source_blocks: tuple[dict[str, Any], ...]
    append_collections: tuple[str, ...] = ()
    required_append_counts: tuple[tuple[str, int], ...] = ()


def _target_from_path(path: str) -> RepairTarget | None:
    if path == "personal_info" or path.startswith("personal_info."):
        return RepairTarget("personal_info")
    for collection in COLLECTIONS:
        prefix = f"{collection}["
        if path.startswith(prefix):
            closing = path.find("]", len(prefix))
            index_text = path[len(prefix):closing] if closing >= 0 else ""
            if index_text.isdigit():
                return RepairTarget(collection, int(index_text))
    return None


def _target_from_id(payload: dict[str, Any], object_id: str) -> RepairTarget | None:
    if object_id == "personal_info":
        return RepairTarget("personal_info")
    for collection in COLLECTIONS:
        for index, item in enumerate(payload.get(collection, [])):
            if not isinstance(item, dict):
                continue
            if item.get("entry_id") == object_id or item.get("item_id") == object_id:
                return RepairTarget(collection, index)
            if collection in {"work_experience", "project_experience"} and any(
                isinstance(skill, dict) and skill.get("item_id") == object_id
                for skill in item.get("tech_stack", [])
            ):
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
        if not isinstance(location, (list, tuple)) or not location:
            continue
        if location[0] == "personal_info":
            targets.add(RepairTarget("personal_info"))
            continue
        if len(location) < 2:
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
    canonicalized = materialize_collection_defaults(canonicalized)
    if error_type == "CandidateValidationError":
        if not isinstance(error_details, list) or not error_details:
            return None
        plans: list[LocalRepairPlan] = []
        for issue in error_details:
            if not isinstance(issue, dict):
                return None
            issue_type = issue.get("error_type")
            if not isinstance(issue_type, str):
                return None
            plan = plan_local_repair(
                canonicalized,
                issue_type,
                issue.get("error_details"),
                source_blocks,
            )
            if plan is None:
                return None
            plans.append(plan)
        targets = {target for plan in plans for target in plan.targets}
        append_collections = {
            collection for plan in plans for collection in plan.append_collections
        }
        required_counts: dict[str, int] = {}
        for plan in plans:
            for collection, count in plan.required_append_counts:
                required_counts[collection] = required_counts.get(collection, 0) + count
        block_ids = {
            str(block.get("source_id"))
            for plan in plans
            for block in plan.source_blocks
        }
        return LocalRepairPlan(
            targets=tuple(sorted(targets, key=lambda item: (item.collection, -1 if item.index is None else item.index))),
            source_blocks=tuple(
                deepcopy(block)
                for block in source_blocks
                if str(block.get("source_id")) in block_ids
            ),
            append_collections=tuple(sorted(append_collections)),
            required_append_counts=tuple(sorted(required_counts.items())),
        )
    targets: set[RepairTarget] = set()
    append_collections: set[str] = set()
    required_append_counts: dict[str, int] = {}
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
            if detail.get("code") in {
                "credential_award_misclassified",
                "role_title_as_project",
                "research_project_as_work",
            }:
                entry_id = detail.get("entry_id")
                expected_collection = detail.get("expected_collection")
                if not isinstance(entry_id, str) or expected_collection not in COLLECTIONS:
                    return None
                target = _target_from_id(canonicalized, entry_id)
                allowed_moves = {
                    "credential_award_misclassified": {
                        ("certificates", "awards"),
                        ("awards", "certificates"),
                    },
                    "role_title_as_project": {
                        ("project_experience", "work_experience")
                    },
                    "research_project_as_work": {
                        ("work_experience", "project_experience")
                    },
                }
                if target is None or (
                    target.collection, expected_collection
                ) not in allowed_moves[str(detail["code"])]:
                    return None
                targets.add(target)
                append_collections.add(expected_collection)
                required_append_counts[expected_collection] = (
                    required_append_counts.get(expected_collection, 0) + 1
                )
                continue
            if detail.get("code") == "taxonomy_skill_uncovered":
                entry_id = detail.get("entry_id")
                if isinstance(entry_id, str):
                    target = _target_from_id(canonicalized, entry_id)
                    if target is None:
                        return None
                    targets.add(target)
                elif detail.get("target_collection") == "skills":
                    append_collections.add("skills")
                    required_append_counts["skills"] = (
                        required_append_counts.get("skills", 0) + 1
                    )
                else:
                    return None
                continue
            if detail.get("code") == "explicit_language_uncovered":
                if detail.get("suggested_append_collection") != "languages":
                    return None
                append_collections.add("languages")
                required_append_counts["languages"] = (
                    required_append_counts.get("languages", 0) + 1
                )
                continue
            if detail.get("code") in {
                "source_section_uncovered",
                "source_section_misclassified",
            } and not isinstance(detail.get("entry_id"), str):
                candidate_owners = detail.get("candidate_owners")
                if isinstance(candidate_owners, list) and candidate_owners:
                    candidate_targets = {
                        target
                        for candidate in candidate_owners
                        if isinstance(candidate, dict)
                        and isinstance(candidate.get("entry_id"), str)
                        for target in [_target_from_id(canonicalized, candidate["entry_id"])]
                        if target is not None
                    }
                    if not candidate_targets:
                        return None
                    targets.update(candidate_targets)
                    continue
                append_collection = detail.get("suggested_append_collection")
                if not isinstance(append_collection, str) or append_collection not in COLLECTIONS:
                    return None
                append_collections.add(append_collection)
                required_append_counts[append_collection] = (
                    required_append_counts.get(append_collection, 0) + 1
                )
                continue
            for key in ("entry_id", "item_id"):
                value = detail.get(key)
                if isinstance(value, str):
                    target = _target_from_id(canonicalized, value)
                    if target is not None:
                        targets.add(target)
                        if (
                            detail.get("code") == "composite_skill_item"
                            and target.collection == "skills"
                            and isinstance(detail.get("parts"), list)
                            and len(detail["parts"]) >= 2
                        ):
                            append_collections.add("skills")
                            required_append_counts["skills"] = (
                                required_append_counts.get("skills", 0)
                                + len(detail["parts"])
                                - 1
                            )

    if not targets and not append_collections:
        return None
    if any(
        target.index is not None
        and target.index >= len(canonicalized.get(target.collection, []))
        for target in targets
    ):
        return None

    def collect_anchor_source_ids(value: Any) -> set[str]:
        found: set[str] = set()
        if not isinstance(value, dict):
            return found
        evidence = value.get("evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("source_id"), str):
            found.add(evidence["source_id"])
        bindings = value.get("field_evidence")
        for binding in bindings if isinstance(bindings, list) else []:
            binding_evidence = binding.get("evidence") if isinstance(binding, dict) else None
            if (
                isinstance(binding_evidence, dict)
                and isinstance(binding_evidence.get("source_id"), str)
            ):
                found.add(binding_evidence["source_id"])
        return found

    referenced_item_ids = {
        detail.get("item_id")
        for detail in details
        if isinstance(detail, dict) and isinstance(detail.get("item_id"), str)
    }

    def collect_referenced_item_sources(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            if value.get("item_id") in referenced_item_ids:
                found.update(collect_anchor_source_ids(value))
            for child in value.values():
                found.update(collect_referenced_item_sources(child))
        elif isinstance(value, list):
            for child in value:
                found.update(collect_referenced_item_sources(child))
        return found

    def collect_detail_source_ids(value: Any, key: str | None = None) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for child_key, child in value.items():
                found.update(collect_detail_source_ids(child, child_key))
        elif isinstance(value, list):
            if key is not None and key.endswith("source_ids"):
                found.update(item for item in value if isinstance(item, str))
            else:
                for child in value:
                    found.update(collect_detail_source_ids(child, key))
        elif isinstance(value, str) and key == "source_id":
            found.add(value)
        return found

    source_ids: set[str] = set()
    for target in targets:
        target_value = (
            canonicalized.get(target.collection)
            if target.index is None
            else canonicalized[target.collection][target.index]
        )
        source_ids.update(collect_anchor_source_ids(target_value))
    source_ids.update(collect_referenced_item_sources(canonicalized))
    source_ids.update(collect_detail_source_ids(details))
    relevant_blocks = tuple(
        deepcopy(block)
        for block in source_blocks
        if block.get("source_id") in source_ids
    )
    return LocalRepairPlan(
        targets=tuple(
            sorted(
                targets,
                key=lambda item: (
                    item.collection,
                    -1 if item.index is None else item.index,
                ),
            )
        ),
        source_blocks=relevant_blocks,
        append_collections=tuple(sorted(append_collections)),
        required_append_counts=tuple(sorted(required_append_counts.items())),
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
    authorized_collections = set(plan.append_collections)
    repaired = materialize_collection_defaults(payload)
    removals: dict[str, list[int]] = {}
    replacements: dict[tuple[str, int], dict[str, Any]] = {}
    singleton_removals: set[str] = set()
    singleton_replacements: dict[str, dict[str, Any]] = {}
    appends: dict[str, list[dict[str, Any]]] = {}
    operated_targets: set[tuple[str, int]] = set()

    def validate_value(value: dict[str, Any]) -> None:
        forbidden_root_fields = {"document_id", "entry_id"}
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
        singleton = target.get("singleton")
        collection = target.get("collection")
        if singleton is not None:
            if set(target) != {"singleton"} or singleton not in SINGLETONS:
                raise ValueError("Local repair operation targets an unknown singleton.")
            collection = singleton
        elif collection not in COLLECTIONS:
            raise ValueError("Local repair operation targets an unknown collection.")

        if op in ("replace", "remove"):
            if singleton is not None:
                index = None
            else:
                if set(target) != {"collection", "index"} or not isinstance(target.get("index"), int):
                    raise ValueError("replace/remove collection targets must contain collection and integer index.")
                index = target["index"]
            if (collection, index) not in authorized:
                raise ValueError("Local repair operation targets an object outside the authorized error scope.")
            if (collection, index) in operated_targets:
                raise ValueError("Local repair response cannot operate on the same target more than once.")
            operated_targets.add((collection, index))
            if op == "replace":
                if set(operation) != {"op", "target", "value"} or not isinstance(operation["value"], dict):
                    raise ValueError("replace must contain exactly an object value.")
                original_value = (
                    repaired.get(collection)
                    if singleton is not None
                    else repaired[collection][index]
                )
                replacement_value = _close_replacement_field_evidence(
                    collection,
                    original_value if isinstance(original_value, dict) else {},
                    operation["value"],
                )
                validate_value(replacement_value)
                if singleton is not None:
                    singleton_replacements[collection] = replacement_value
                else:
                    replacements[(collection, index)] = replacement_value
            elif set(operation) != {"op", "target"}:
                raise ValueError("remove must contain only op and target.")
            else:
                if singleton is not None:
                    singleton_removals.add(collection)
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

    for singleton in SINGLETONS:
        if singleton in singleton_removals:
            repaired[singleton] = None
        elif singleton in singleton_replacements:
            repaired[singleton] = singleton_replacements[singleton]

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
    for collection, required_count in plan.required_append_counts:
        actual_count = len(appends.get(collection, []))
        if actual_count != required_count:
            raise ValueError(
                f"Local repair requires exactly {required_count} append operation(s) "
                f"for {collection}, got {actual_count}."
            )
    return repaired
