from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from pydantic import ValidationError

from app.domain.json_types import FrozenJsonObject, freeze_json_object, thaw_json_object
from app.contexts.data_validation.ports import SkillCatalogResolutionPort
from jobgraph_contracts.cv_extraction_http import (
    CVExtractionResponseV2,
    CVExtractionResponseV3,
    parse_cv_extraction_response,
)


@dataclass(frozen=True)
class CVValidationPolicy:
    version: str = "cv-validation-policy.v2"
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    prompt_version: str = "cv-prompt.v1"
    schema_version: str = "2.4"
    normalization_version: str = "2.0"


@dataclass(frozen=True)
class CVValidationResult:
    decision: str
    report: FrozenJsonObject
    extraction: FrozenJsonObject | None
    normalized: FrozenJsonObject | None
    review_flags: tuple[FrozenJsonObject, ...]


class CVValidatorSet:
    def __init__(
        self,
        policy: CVValidationPolicy,
        catalog_provider: Callable[[], SkillCatalogResolutionPort],
    ) -> None:
        self.policy = policy
        self._catalog_provider = catalog_provider

    def validate(
        self, payload: FrozenJsonObject, *, source_cv_version_id: str
    ) -> CVValidationResult:
        data = thaw_json_object(payload)
        findings: list[dict[str, str]] = []
        try:
            contract = parse_cv_extraction_response(data)
        except (ValidationError, TypeError, ValueError):
            findings.append(
                {"code": "cv_contract_structure_invalid", "severity": "block"}
            )
            contract = None
        extraction = (
            contract.extraction_result.model_dump(mode="json")
            if contract is not None
            else data.get("extraction_result")
        )
        normalized = (
            contract.normalized_result.model_dump(mode="json")
            if contract is not None
            else data.get("normalized_result")
        )
        if contract is not None and contract.document_id != source_cv_version_id:
            findings.append(
                {"code": "cv_document_lineage_invalid", "severity": "block"}
            )
        if (
            not isinstance(extraction, Mapping)
            or extraction.get("document_id") != source_cv_version_id
        ):
            findings.append(
                {"code": "cv_extraction_lineage_invalid", "severity": "block"}
            )
        if (
            not isinstance(normalized, Mapping)
            or normalized.get("document_id") != source_cv_version_id
        ):
            findings.append(
                {"code": "cv_normalization_lineage_invalid", "severity": "block"}
            )
        if contract is not None:
            execution = contract.execution
            if execution.schema_version != self.policy.schema_version:
                findings.append(
                    {"code": "cv_schema_version_mismatch", "severity": "block"}
                )
            expected = {
                "provider": self.policy.provider,
                "model": self.policy.model,
                "prompt_version": self.policy.prompt_version,
                "normalization_version": self.policy.normalization_version,
            }
            for field, expected_value in expected.items():
                if getattr(execution, field) != expected_value:
                    findings.append(
                        {
                            "code": f"cv_execution_{field}_mismatch",
                            "severity": "review",
                        }
                    )
        if isinstance(contract, (CVExtractionResponseV2, CVExtractionResponseV3)):
            findings.extend(self._validate_taxonomy_projection(contract))
        if isinstance(contract, CVExtractionResponseV3):
            findings.extend(self._validate_position_classifications(contract))
        raw_flags = (
            [
                item.model_dump(mode="json")
                for item in contract.review_flags
            ]
            if contract is not None
            else data.get("review_flags")
        )
        if not isinstance(raw_flags, list) or any(
            not isinstance(item, Mapping) for item in raw_flags
        ):
            findings.append(
                {"code": "cv_review_flags_invalid", "severity": "block"}
            )
            flags: tuple[dict, ...] = ()
        else:
            flags = tuple(dict(item) for item in raw_flags)
        empty_content = bool(
            contract is not None
            and not any(
                (
                    contract.extraction_result.skills,
                    contract.extraction_result.education,
                    contract.extraction_result.project_experience,
                    contract.extraction_result.work_experience,
                    contract.extraction_result.certificates,
                    contract.extraction_result.awards,
                )
            )
        )
        if empty_content:
            findings.append(
                {"code": "cv_extraction_content_empty", "severity": "review"}
            )
        blocking = any(
            str(item.get("severity", "")).lower() in {"block", "blocking"}
            for item in (*findings, *flags)
        )
        if blocking:
            decision = "block"
        elif findings or flags or (
            isinstance(normalized, Mapping) and normalized.get("unresolved_items")
        ):
            decision = "review"
        else:
            decision = "allow"
        report = freeze_json_object(
            {
                "policy_version": self.policy.version,
                "source_cv_version_id": source_cv_version_id,
                "decision": decision,
                "findings": findings,
                "review_flags": list(flags),
            },
            field="cv_validation_report",
        )
        return CVValidationResult(
            decision=decision,
            report=report,
            extraction=(
                freeze_json_object(extraction, field="cv_extraction_payload")
                if isinstance(extraction, Mapping)
                else None
            ),
            normalized=(
                freeze_json_object(normalized, field="cv_normalized_payload")
                if isinstance(normalized, Mapping)
                else None
            ),
            review_flags=tuple(
                freeze_json_object(item, field="cv_review_flag") for item in flags
            ),
        )

    def _validate_taxonomy_projection(
        self, contract: CVExtractionResponseV2 | CVExtractionResponseV3
    ) -> list[dict[str, str]]:
        catalog = self._catalog_provider()
        projection = contract.skill_taxonomy
        if projection.taxonomy_version != catalog.taxonomy_version:
            return [{"code": "cv_taxonomy_version_mismatch", "severity": "block"}]
        normalized_ids = {
            item.skill_id
            for item in contract.normalized_result.normalized_skills
            if item.resolution_status == "resolved" and item.skill_id is not None
        }
        projected_ids = {item.skill_id for item in projection.skills}
        if normalized_ids != projected_ids or len(projection.skills) != len(projected_ids):
            return [
                {
                    "code": "cv_taxonomy_projection_coverage_mismatch",
                    "severity": "block",
                }
            ]
        for item in projection.skills:
            authoritative = catalog.classification_set(item.skill_id)
            if authoritative is None:
                return [
                    {
                        "code": "cv_taxonomy_projection_skill_missing",
                        "severity": "block",
                    }
                ]
            canonical_name, classifications = authoritative
            projected_relations = {
                (relation.facet, relation.code, relation.is_primary)
                for relation in item.classifications
            }
            authoritative_relations = {
                (relation.facet, relation.code, relation.is_primary)
                for relation in classifications
            }
            if (
                item.canonical_name != canonical_name
                or projected_relations != authoritative_relations
                or len(item.classifications) != len(projected_relations)
            ):
                return [
                    {
                        "code": "cv_taxonomy_projection_content_mismatch",
                        "severity": "block",
                    }
                ]
        return []

    @staticmethod
    def _validate_position_classifications(
        contract: CVExtractionResponseV3,
    ) -> list[dict[str, str]]:
        findings: list[dict[str, str]] = []
        actual_identities = [
            (
                item.feature_id,
                item.source_object_id,
                item.source_scope,
                item.role_kind,
            )
            for item in contract.normalized_result.position_classifications
        ]
        if len(actual_identities) != len(set(actual_identities)):
            findings.append(
                {
                    "code": "cv_position_classification_identity_duplicate",
                    "severity": "block",
                }
            )
        expected_titles: dict[tuple[str, str, str, str], str] = {}
        personal = contract.extraction_result.personal_info
        if personal is not None and personal.expected_position is not None:
            expected_titles[
                (
                    "role_personal_info_expected_position",
                    "personal_info",
                    "personal_info.expected_position",
                    "expected",
                )
            ] = personal.expected_position
        for entry in contract.extraction_result.work_experience:
            if entry.position is None:
                continue
            expected_titles[
                (
                    f"role_{entry.entry_id}_position",
                    entry.entry_id,
                    "work_experience.position",
                    "historical",
                )
            ] = entry.position
        if set(actual_identities) != set(expected_titles):
            findings.append(
                {
                    "code": "cv_position_classification_coverage_mismatch",
                    "severity": "block",
                }
            )
        elif any(
            item.job_classification.source_title
            != expected_titles[
                (
                    item.feature_id,
                    item.source_object_id,
                    item.source_scope,
                    item.role_kind,
                )
            ]
            for item in contract.normalized_result.position_classifications
        ):
            findings.append(
                {
                    "code": "cv_position_classification_source_mismatch",
                    "severity": "block",
                }
            )
        if any(
            item.job_classification.classification_status
            not in {"resolved", "manually_confirmed"}
            for item in contract.normalized_result.position_classifications
        ):
            findings.append(
                {
                    "code": "cv_position_classification_unresolved",
                    "severity": "review",
                }
            )
        return findings


__all__ = ["CVValidationPolicy", "CVValidationResult", "CVValidatorSet"]
