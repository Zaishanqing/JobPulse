from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from pydantic import ValidationError

from app.contexts.data_validation.domain import (
    Finding,
    FindingSeverity,
    ValidationConclusion,
)
from app.contexts.data_validation.ports import (
    CrossSourceDuplicatePort,
    SkillCatalogReference,
    SkillCatalogResolutionPort,
)
from app.domain.json_types import (
    FrozenJsonObject,
    JsonInputValue,
    freeze_json_object,
    thaw_json_object,
)
from jobgraph_contracts.extraction_bundle import (
    ExtractedJDBundleV1,
    parse_extracted_jd_bundle,
)


def _payload(bundle: ExtractedJDBundleV1 | FrozenJsonObject) -> dict[str, Any]:
    if isinstance(bundle, ExtractedJDBundleV1):
        return bundle.model_dump(mode="json")
    return thaw_json_object(bundle)


def _duplicate_value(item: Any) -> JsonInputValue:
    """Remove per-occurrence identity while preserving the extracted fact value."""
    if not isinstance(item, Mapping):
        return item
    ignored = {"evidence", "requirement_id", "fact_id"}
    return {str(key): value for key, value in item.items() if key not in ignored}


def _duplicate_material(fact_type: str, item: Any) -> JsonInputValue:
    return {
        "fact_type": fact_type,
        "value": _duplicate_value(item),
    }


def canonical_fact_key(fact_type: str, item: JsonInputValue) -> str:
    """Deterministic fact identity used for cross-source duplicate lookup."""
    return json.dumps(
        _duplicate_material(fact_type, item),
        sort_keys=True,
        ensure_ascii=False,
    )


def _path(parts: Sequence[str | int]) -> str:
    result = "$"
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result


@dataclass(frozen=True)
class ValidationContext:
    bundle: ExtractedJDBundleV1 | JsonInputValue
    raw_text: str
    cleaned_text: str
    source_jd_id: str
    source_jd_version_id: str
    extraction_task_id: str
    source_platform: str
    source_record_id: str
    bundle_id: str
    declared_source_jd_id: str | None = None
    declared_source_jd_version_id: str | None = None
    declared_extraction_task_id: str | None = None
    catalog: SkillCatalogResolutionPort | None = None
    cross_source_duplicates: CrossSourceDuplicatePort | None = None

    def __post_init__(self) -> None:
        payload = (
            self.bundle.model_dump(mode="json")
            if isinstance(self.bundle, ExtractedJDBundleV1)
            else self.bundle
        )
        object.__setattr__(
            self, "bundle", freeze_json_object(payload, field="validation_context.bundle")
        )


class Validator(Protocol):
    name: str

    def validate(self, context: ValidationContext) -> Sequence[Finding]: ...


class _BaseValidator:
    name = "base"

    def finding(
        self,
        code: str,
        severity: FindingSeverity,
        path: str,
        message: str,
        *,
        evidence: JsonInputValue = None,
        details: JsonInputValue = None,
    ) -> Finding:
        return Finding(code, severity, path, message, self.name, evidence, details)


class ContractValidator(_BaseValidator):
    name = "contract"
    supported_versions = frozenset(
        {"extracted-jd-bundle-v1", "extracted-jd-bundle-v2"}
    )

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        payload = _payload(context.bundle)
        version = payload.get("schema_version")
        if version is None:
            return (
                self.finding(
                    "missing_contract_version",
                    FindingSeverity.BLOCK,
                    "$.schema_version",
                    "Bundle schema version is required.",
                ),
            )
        if version not in self.supported_versions:
            return (
                self.finding(
                    "unsupported_contract_version",
                    FindingSeverity.BLOCK,
                    "$.schema_version",
                    "Bundle schema version is not supported.",
                    details={"version": version},
                ),
            )
        try:
            parse_extracted_jd_bundle(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            locations: list[str] = []
            if isinstance(exc, ValidationError):
                locations = sorted({_path(error["loc"]) for error in exc.errors()})
            return (
                self.finding(
                    "invalid_contract_structure",
                    FindingSeverity.BLOCK,
                    locations[0] if locations else "$",
                    "Bundle structure does not conform to its declared contract.",
                    details={"invalid_paths": locations},
                ),
            )
        return ()


class SourceIdentityValidator(_BaseValidator):
    name = "source_identity"
    placeholders = frozenset({"unknown", "n/a", "none", "null", "placeholder"})

    @classmethod
    def _invalid(cls, value: Any) -> bool:
        return (
            not isinstance(value, str)
            or not value.strip()
            or value.strip().lower() in cls.placeholders
        )

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        payload = _payload(context.bundle)
        values = {
            "$.source_platform": payload.get("source_platform"),
            "$.source_record_id": payload.get("source_record_id"),
            "$context.source_jd_id": context.source_jd_id,
        }
        findings = [
            self.finding(
                "missing_source_identity",
                FindingSeverity.BLOCK,
                path,
                "Source identity is required.",
            )
            for path, value in values.items()
            if value is None
        ]
        findings.extend(
            self.finding(
                "invalid_source_identity",
                FindingSeverity.BLOCK,
                path,
                "Source identity must be a non-placeholder value.",
            )
            for path, value in values.items()
            if value is not None and self._invalid(value)
        )
        comparisons = (
            ("$.source_platform", payload.get("source_platform"), context.source_platform),
            ("$.source_record_id", payload.get("source_record_id"), context.source_record_id),
            (
                "$context.declared_source_jd_id",
                context.declared_source_jd_id,
                context.source_jd_id,
            ),
        )
        findings.extend(
            self.finding(
                "conflicting_source_identity",
                FindingSeverity.BLOCK,
                path,
                "Bundle and validation context identify different sources.",
            )
            for path, actual, expected in comparisons
            if actual is not None and actual != expected
        )
        return tuple(findings)


def _evidence_nodes(value: Any, parts: tuple[str | int, ...] = ()):
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = value[key]
            child_parts = (*parts, str(key))
            if key == "evidence":
                yield child_parts, child, value
            yield from _evidence_nodes(child, child_parts)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _evidence_nodes(child, (*parts, index))


class EvidenceAlignmentValidator(_BaseValidator):
    name = "evidence_alignment"

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        payload = _payload(context.bundle)
        findings: list[Finding] = []
        document_id = payload.get("extraction_result", {}).get("document_id")
        evidence_nodes = list(_evidence_nodes(payload.get("extraction_result", {})))
        target_collections = (
            "responsibilities",
            "requirements",
            "company_facts",
            "employment_facts",
        )
        extraction = payload.get("extraction_result", {})
        job_title = extraction.get("job_title")
        if isinstance(job_title, Mapping) and "evidence" not in job_title:
            findings.append(
                self.finding(
                    "missing_evidence",
                    FindingSeverity.BLOCK,
                    "$.extraction_result.job_title.evidence",
                    "Extracted job title requires supporting evidence.",
                )
            )
        for collection in target_collections:
            for index, item in enumerate(extraction.get(collection, []) or []):
                if not isinstance(item, Mapping) or "evidence" not in item:
                    findings.append(
                        self.finding(
                            "missing_evidence",
                            FindingSeverity.BLOCK,
                            f"$.extraction_result.{collection}[{index}].evidence",
                            "Extracted fact requires supporting evidence.",
                        )
                    )
        for parts, evidence, target in evidence_nodes:
            path = _path(("extraction_result", *parts))
            if not isinstance(evidence, Mapping):
                findings.append(
                    self.finding(
                        "missing_evidence", FindingSeverity.BLOCK, path, "Evidence is required."
                    )
                )
                continue
            start, end, quote = evidence.get("start"), evidence.get("end"), evidence.get("quote")
            if evidence.get("source_id") != document_id:
                findings.append(
                    self.finding(
                        "orphan_evidence",
                        FindingSeverity.WARN,
                        f"{path}.source_id",
                        "Evidence source does not match the extraction document.",
                    )
                )
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end < start
                or end > len(context.cleaned_text)
            ):
                findings.append(
                    self.finding(
                        "invalid_evidence_span",
                        FindingSeverity.BLOCK,
                        path,
                        "Evidence span is missing, reversed, or outside the source text.",
                    )
                )
                continue
            if (
                evidence.get("alignment") == "exact"
                and context.cleaned_text[start:end] != quote
            ):
                findings.append(
                    self.finding(
                        "evidence_text_mismatch",
                        FindingSeverity.BLOCK,
                        f"{path}.quote",
                        "Exact evidence text does not match its source span.",
                    )
                )
            if not self._has_business_target(target):
                findings.append(
                    self.finding(
                        "evidence_target_missing",
                        FindingSeverity.BLOCK,
                        _path(("extraction_result", *parts[:-1])),
                        "Evidence is not associated with a populated target field.",
                    )
                )
        return tuple(findings)

    @staticmethod
    def _has_business_target(target: Mapping[str, Any]) -> bool:
        if target.get("kind") == "skill":
            items = target.get("items")
            return isinstance(items, list) and any(
                isinstance(item, Mapping)
                and isinstance(item.get("name"), str)
                and bool(item["name"].strip())
                for item in items
            )
        text = target.get("text")
        return isinstance(text, str) and bool(text.strip())


class RequiredFieldValidator(_BaseValidator):
    name = "required_field"
    required = (
        ("schema_version",),
        ("source_platform",),
        ("source_record_id",),
        ("extraction_result",),
        ("extraction_result", "document_id"),
        ("normalized_result",),
        ("normalized_result", "document_id"),
        ("normalized_result", "job_classification"),
        ("extraction_provider",),
        ("model_version",),
        ("extraction_run_id",),
        ("extraction_started_at",),
        ("extraction_finished_at",),
    )
    extracted_object_rules = {
        "responsibilities": ("requirement_id", "text"),
        "company_facts": ("fact_id", "text"),
        "employment_facts": ("fact_id", "fact_type", "text"),
    }

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        payload = _payload(context.bundle)
        findings: list[Finding] = []
        for parts in self.required:
            self._check_required_value(payload, parts, findings)
        extraction = payload.get("extraction_result")
        if isinstance(extraction, Mapping):
            job_title = extraction.get("job_title")
            if isinstance(job_title, Mapping):
                self._check_required_value(
                    payload, ("extraction_result", "job_title", "text"), findings
                )
            for collection, fields in self.extracted_object_rules.items():
                self._check_object_collection(payload, collection, fields, findings)
            self._check_requirements(payload, findings)
        return tuple(findings)

    def _check_object_collection(
        self,
        payload: Mapping[str, Any],
        collection: str,
        fields: Sequence[str],
        findings: list[Finding],
    ) -> None:
        extraction = payload.get("extraction_result")
        if not isinstance(extraction, Mapping):
            return
        items = extraction.get(collection)
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            for field in fields:
                self._check_required_value(
                    payload,
                    ("extraction_result", collection, index, field),
                    findings,
                )

    def _check_requirements(
        self,
        payload: Mapping[str, Any],
        findings: list[Finding],
    ) -> None:
        extraction = payload.get("extraction_result")
        if not isinstance(extraction, Mapping):
            return
        requirements = extraction.get("requirements")
        if not isinstance(requirements, list):
            return
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, Mapping):
                continue
            base = ("extraction_result", "requirements", index)
            for field in ("requirement_id", "kind"):
                self._check_required_value(payload, (*base, field), findings)
            if requirement.get("kind") == "skill":
                items_path = (*base, "items")
                items = requirement.get("items")
                if items is None:
                    self._append_missing(items_path, findings)
                elif isinstance(items, list) and not items:
                    findings.append(
                        self.finding(
                            "required_collection_empty",
                            FindingSeverity.BLOCK,
                            _path(items_path),
                            "Required collection must not be empty.",
                        )
                    )
                elif isinstance(items, list):
                    for item_index, item in enumerate(items):
                        if isinstance(item, Mapping):
                            self._check_required_value(
                                payload,
                                (*items_path, item_index, "name"),
                                findings,
                            )
            elif requirement.get("kind") == "tool":
                tools_path = (*base, "tools")
                tools = requirement.get("tools")
                if tools is None:
                    self._append_missing(tools_path, findings)
                elif isinstance(tools, list) and not tools:
                    findings.append(
                        self.finding(
                            "required_collection_empty",
                            FindingSeverity.BLOCK,
                            _path(tools_path),
                            "Required collection must not be empty.",
                        )
                    )
            else:
                self._check_required_value(payload, (*base, "text"), findings)

    def _check_required_value(
        self,
        payload: Mapping[str, Any],
        parts: Sequence[str | int],
        findings: list[Finding],
    ) -> None:
        current: Any = payload
        for part in parts:
            if isinstance(part, int):
                if not isinstance(current, list) or part >= len(current):
                    self._append_missing(parts, findings)
                    return
                current = current[part]
            else:
                if not isinstance(current, Mapping) or part not in current:
                    self._append_missing(parts, findings)
                    return
                current = current[part]
        if current is None:
            self._append_missing(parts, findings)
        elif isinstance(current, str) and not current.strip():
            findings.append(
                self.finding(
                    "required_value_blank",
                    FindingSeverity.BLOCK,
                    _path(parts),
                    "Required value must not be blank.",
                )
            )

    def _append_missing(
        self,
        parts: Sequence[str | int],
        findings: list[Finding],
    ) -> None:
        findings.append(
            self.finding(
                "required_field_missing",
                FindingSeverity.BLOCK,
                _path(parts),
                "Required field is missing.",
            )
        )


class CatalogReferenceValidator(_BaseValidator):
    name = "catalog_reference"

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        if context.catalog is None:
            return ()
        normalized = _payload(context.bundle).get("normalized_result", {})
        references: list[tuple[str, Mapping[str, Any]]] = []
        for req_index, requirement in enumerate(
            normalized.get("normalized_requirements", []) or []
        ):
            for skill_index, skill in enumerate(requirement.get("normalized_skills", []) or []):
                references.append(
                    (
                        f"$.normalized_result.normalized_requirements[{req_index}].normalized_skills[{skill_index}]",
                        skill,
                    )
                )
        findings: list[Finding] = []
        for path, item in references:
            result = context.catalog.resolve(
                SkillCatalogReference(
                    source_name=str(item.get("source_name", "")),
                    claimed_skill_id=item.get("skill_id"),
                    claimed_canonical_name=item.get("canonical_name"),
                )
            )
            if result.error_code == "skill_catalog_snapshot_missing":
                code, severity = "catalog_snapshot_missing", FindingSeverity.BLOCK
            elif result.status == "unresolved":
                code, severity = "catalog_reference_unresolved", FindingSeverity.WARN
            elif result.status == "conflict":
                code, severity = "catalog_reference_conflict", FindingSeverity.BLOCK
            else:
                continue
            if result.status != "resolved":
                findings.append(
                    self.finding(
                        code,
                        severity,
                        path,
                        "Skill catalog reference could not be resolved uniquely.",
                        details={
                            "status": (
                                "snapshot_missing"
                                if result.error_code == "skill_catalog_snapshot_missing"
                                else result.status
                            )
                        },
                    )
                )
        return tuple(findings)


class TaxonomyProjectionValidator(_BaseValidator):
    name = "taxonomy_projection"

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        payload = _payload(context.bundle)
        if payload.get("schema_version") != "extracted-jd-bundle-v2":
            return ()
        projection = payload.get("skill_taxonomy") or {}
        if context.catalog is None:
            return (
                self.finding(
                    "taxonomy_catalog_missing",
                    FindingSeverity.BLOCK,
                    "$.skill_taxonomy",
                    "V2 taxonomy projection requires an authoritative catalog.",
                ),
            )
        if projection.get("taxonomy_version") != context.catalog.taxonomy_version:
            return (
                self.finding(
                    "taxonomy_version_mismatch",
                    FindingSeverity.BLOCK,
                    "$.skill_taxonomy.taxonomy_version",
                    "Extraction and main-system taxonomy versions differ.",
                ),
            )
        normalized_ids = {
            skill.get("skill_id")
            for requirement in (
                payload.get("normalized_result", {}).get(
                    "normalized_requirements", []
                )
                or []
            )
            for skill in requirement.get("normalized_skills", []) or []
            if skill.get("resolution_status") == "resolved"
            and isinstance(skill.get("skill_id"), str)
        }
        projected_skills = projection.get("skills", []) or []
        projected_ids = {item.get("skill_id") for item in projected_skills}
        if (
            normalized_ids != projected_ids
            or len(projected_skills) != len(projected_ids)
        ):
            return (
                self.finding(
                    "taxonomy_projection_coverage_mismatch",
                    FindingSeverity.BLOCK,
                    "$.skill_taxonomy.skills",
                    "V2 taxonomy projection must cover every resolved skill exactly.",
                ),
            )
        for item in projected_skills:
            skill_id = item.get("skill_id")
            authoritative = context.catalog.classification_set(skill_id)
            if authoritative is None:
                return (
                    self.finding(
                        "taxonomy_projection_skill_missing",
                        FindingSeverity.BLOCK,
                        "$.skill_taxonomy.skills",
                        "Projected skill is absent from the authoritative catalog.",
                    ),
                )
            canonical_name, classifications = authoritative
            projected_relations = {
                (
                    relation.get("facet"),
                    relation.get("code"),
                    relation.get("is_primary"),
                )
                for relation in item.get("classifications", []) or []
            }
            authoritative_relations = {
                (relation.facet, relation.code, relation.is_primary)
                for relation in classifications
            }
            if (
                item.get("canonical_name") != canonical_name
                or projected_relations != authoritative_relations
                or len(item.get("classifications", []) or [])
                != len(projected_relations)
            ):
                return (
                    self.finding(
                        "taxonomy_projection_content_mismatch",
                        FindingSeverity.BLOCK,
                        "$.skill_taxonomy.skills",
                        "Projected classification differs from the authoritative catalog.",
                    ),
                )
        return ()


class BundleLineageValidator(_BaseValidator):
    name = "bundle_lineage"

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        findings: list[Finding] = []
        for path, value in (
            ("$context.extraction_task_id", context.extraction_task_id),
            ("$context.source_jd_version_id", context.source_jd_version_id),
        ):
            if not isinstance(value, str) or not value.strip():
                findings.append(
                    self.finding(
                        "missing_extraction_lineage",
                        FindingSeverity.BLOCK,
                        path,
                        "Extraction lineage is required.",
                    )
                )
        if (
            context.declared_extraction_task_id is not None
            and context.declared_extraction_task_id != context.extraction_task_id
        ):
            findings.append(
                self.finding(
                    "extraction_task_mismatch",
                    FindingSeverity.BLOCK,
                    "$context.declared_extraction_task_id",
                    "Extraction task lineage is inconsistent.",
                )
            )
        if (
            context.declared_source_jd_version_id is not None
            and context.declared_source_jd_version_id != context.source_jd_version_id
        ):
            findings.append(
                self.finding(
                    "source_version_mismatch",
                    FindingSeverity.BLOCK,
                    "$context.declared_source_jd_version_id",
                    "Source version lineage is inconsistent.",
                )
            )
        return tuple(findings)


class ExactDuplicateValidator(_BaseValidator):
    name = "exact_duplicate"
    collections = {
        "responsibilities": "responsibility",
        "requirements": "requirement",
        "company_facts": "company_fact",
        "employment_facts": "employment_fact",
    }

    def validate(self, context: ValidationContext) -> Sequence[Finding]:
        extraction = _payload(context.bundle).get("extraction_result", {})
        findings: list[Finding] = []
        for collection, fact_type in self.collections.items():
            seen: list[tuple[JsonInputValue, int]] = []
            for index, item in enumerate(extraction.get(collection, []) or []):
                value = _duplicate_value(item)
                canonical_key = canonical_fact_key(fact_type, item)
                previous = next((entry_index for entry, entry_index in seen if entry == value), None)
                if previous is not None:
                    findings.append(
                        self.finding(
                            "exact_duplicate_item",
                            FindingSeverity.BLOCK,
                            f"$.extraction_result.{collection}[{index}]",
                            "Extracted fact exactly duplicates an earlier item.",
                            details={"first_index": previous},
                        )
                    )
                else:
                    seen.append((value, index))
                if context.cross_source_duplicates is not None:
                    source_ids = tuple(
                        sorted(context.cross_source_duplicates.find_sources(canonical_key))
                    )
                    if source_ids:
                        findings.append(
                            self.finding(
                                "cross_source_exact_duplicate",
                                FindingSeverity.WARN,
                                f"$.extraction_result.{collection}[{index}]",
                                "Extracted fact exactly matches a fact from another source.",
                                details={"source_ids": list(source_ids)},
                            )
                        )
        return tuple(findings)


class ValidatorSet:
    def __init__(self, validators: Sequence[Validator]) -> None:
        self.validators = tuple(validators)
        names = [validator.name for validator in self.validators]
        if len(names) != len(set(names)):
            raise ValueError("Validator names must be unique")

    def validate(self, context: ValidationContext) -> tuple[Finding, ...]:
        findings: list[tuple[int, Finding]] = []
        for index, validator in enumerate(self.validators):
            findings.extend((index, item) for item in validator.validate(context))
        severity_order = {FindingSeverity.BLOCK: 0, FindingSeverity.WARN: 1}
        findings.sort(
            key=lambda item: (
                item[0],
                severity_order[item[1].severity],
                item[1].code,
                item[1].path,
                item[1].message,
            )
        )
        return tuple(item for _, item in findings)

    @staticmethod
    def conclusion(findings: Sequence[Finding]) -> ValidationConclusion:
        if any(item.severity is FindingSeverity.BLOCK for item in findings):
            return ValidationConclusion.BLOCK
        if findings:
            return ValidationConclusion.WARN
        return ValidationConclusion.PASS


def default_validator_set() -> ValidatorSet:
    return ValidatorSet(
        (
            ContractValidator(),
            SourceIdentityValidator(),
            EvidenceAlignmentValidator(),
            RequiredFieldValidator(),
            CatalogReferenceValidator(),
            TaxonomyProjectionValidator(),
            BundleLineageValidator(),
            ExactDuplicateValidator(),
        )
    )
