"""Version-dispatched normalization Contract ↔ Domain mapping."""

from collections.abc import Callable
from pydantic import BaseModel

from app.contracts.jd.normalization_registry import validate_normalization
from app.contracts.jd.normalization_v2 import JDNormalizedResult
from app.domain.jd import NormalizationResult, NormalizedItem, ReviewFlag
from app.domain.json_types import JsonObject

ToDomain = Callable[[BaseModel], NormalizationResult]
FromDomain = Callable[[NormalizationResult], BaseModel]
_to_domain: dict[str, ToDomain] = {}
_from_domain: dict[str, FromDomain] = {}


def register_normalization_mapper(
    version: str, *, to_domain: ToDomain, from_domain: FromDomain
) -> None:
    _to_domain[version] = to_domain
    _from_domain[version] = from_domain


def _v2_to_domain(contract: BaseModel) -> NormalizationResult:
    value = JDNormalizedResult.model_validate(contract.model_dump(mode="json"))
    return NormalizationResult(
        document_id=value.document_id,
        contract_version=value.schema_version,
        items=tuple(
            NormalizedItem(
                source_value=item.source_name,
                item_type="skill",
                resolution_status=item.resolution_status,
                skill_id=item.skill_id,
                canonical_name=item.canonical_name,
                category_code=item.category_code,
                subcategory_code=item.subcategory_code,
                raw_payload=item.model_dump(
                    mode="json",
                    exclude={
                        "source_name", "resolution_status", "skill_id", "canonical_name",
                        "category_code", "subcategory_code",
                    },
                ),
            )
            for item in value.normalized_requirements
        ),
        review_flags=tuple(
            ReviewFlag(
                flag_type=item.item_type,
                source_value=item.source_value,
                reason=item.reason,
                severity=item.severity,
                source=item.source,
                code=item.code,
                raw_payload=item.model_dump(
                    mode="json",
                    exclude={
                        "item_type", "source_value", "reason", "severity", "source", "code"
                    },
                ),
            )
            for item in value.unresolved_items
        ),
        job_classification=(
            value.job_classification.model_dump(mode="json")
            if value.job_classification else None
        ),
        salary=value.salary.model_dump(mode="json") if value.salary else None,
    )


def _v2_from_domain(result: NormalizationResult) -> BaseModel:
    return JDNormalizedResult.model_validate(
        {
            "schema_version": "v2",
            "document_id": result.document_id,
            "job_classification": result.job_classification,
            "normalized_requirements": [
                {
                    "source_name": item.source_value,
                    "resolution_status": item.resolution_status,
                    "skill_id": item.skill_id,
                    "canonical_name": item.canonical_name,
                    "category_code": item.category_code,
                    "subcategory_code": item.subcategory_code,
                    **item.raw_payload,
                }
                for item in result.items if item.item_type == "skill"
            ],
            "salary": result.salary,
            "unresolved_items": [
                {
                    "item_type": item.flag_type,
                    "source_value": item.source_value,
                    "reason": item.reason,
                    "severity": item.severity,
                    "source": item.source,
                    "code": item.code,
                    **item.raw_payload,
                }
                for item in result.review_flags
            ],
        }
    )


register_normalization_mapper(
    "v2", to_domain=_v2_to_domain, from_domain=_v2_from_domain
)


def normalization_to_domain(
    payload: JsonObject, version: str | None = None
) -> NormalizationResult:
    contract = validate_normalization(payload, version)
    resolved_version = version or getattr(contract, "schema_version", None) or "v2"
    try:
        return _to_domain[resolved_version](contract)
    except KeyError as exc:
        raise ValueError(
            f"No normalization-to-domain mapper for {resolved_version}"
        ) from exc


def domain_to_normalization(
    result: NormalizationResult, version: str = "v2"
) -> BaseModel:
    try:
        return _from_domain[version](result)
    except KeyError as exc:
        raise ValueError(
            f"No domain-to-normalization mapper for {version}"
        ) from exc
