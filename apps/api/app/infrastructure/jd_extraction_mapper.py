"""Version dispatch for extraction contract/domain conversion."""

from collections.abc import Callable
from pydantic import BaseModel

from app.contracts.jd.extraction_registry import validate_extraction
from app.contracts.jd.extraction_v2 import JDExtractionResult
from app.domain.jd import CandidateRequirement, Document, Evidence, Fact, Responsibility
from app.domain.json_types import JsonObject

ToDomain = Callable[[BaseModel], Document]
FromDomain = Callable[[Document], BaseModel]
_to_domain: dict[str, ToDomain] = {}
_from_domain: dict[str, FromDomain] = {}


def register_extraction_mapper(
    version: str, *, to_domain: ToDomain, from_domain: FromDomain
) -> None:
    _to_domain[version] = to_domain
    _from_domain[version] = from_domain


def _evidence_to_domain(value: BaseModel) -> Evidence:
    payload = value.model_dump(mode="json")
    return Evidence(**payload)


def _evidence_to_dict(value: Evidence) -> JsonObject:
    return {
        "source_id": value.source_id, "quote": value.quote, "start": value.start,
        "end": value.end, "alignment": value.alignment,
        "occurrence_index": value.occurrence_index,
    }


def _v2_to_domain(contract: BaseModel) -> Document:
    value = JDExtractionResult.model_validate(contract.model_dump(mode="json"))
    responsibilities = tuple(
        Responsibility(
            responsibility_id=item.requirement_id,
            modality=item.modality,
            evidence=_evidence_to_domain(item.evidence),
            raw_payload={"action": item.action},
        )
        for item in value.responsibilities
    )
    requirements = []
    for item in value.requirements:
        payload = item.model_dump(mode="json", exclude={"requirement_id", "kind", "modality", "evidence"})
        requirements.append(
            CandidateRequirement(
                requirement_id=item.requirement_id,
                kind=item.kind,
                modality=item.modality,
                evidence=_evidence_to_domain(item.evidence),
                raw_payload=payload,
            )
        )
    facts = tuple(
        Fact(
            fact_id=item.fact_id,
            scope=scope,
            kind=item.kind,
            value=item.value,
            evidence=_evidence_to_domain(item.evidence),
        )
        for scope, values in (
            ("company", value.company_facts), ("employment", value.employment_facts)
        )
        for item in values
    )
    return Document(
        document_id=value.document_id,
        contract_version="v2",
        title=value.job_title.value if value.job_title else None,
        title_evidence=_evidence_to_domain(value.job_title.evidence) if value.job_title else None,
        responsibilities=responsibilities,
        requirements=tuple(requirements),
        facts=facts,
    )


def _v2_from_domain(document: Document) -> BaseModel:
    payload = {
        "schema_version": "v2",
        "document_id": document.document_id,
        "job_title": (
            {"value": document.title, "evidence": _evidence_to_dict(document.title_evidence)}
            if document.title is not None and document.title_evidence is not None else None
        ),
        "responsibilities": [
            {
                "requirement_id": item.responsibility_id, "kind": "task",
                "modality": item.modality, "evidence": _evidence_to_dict(item.evidence),
                **item.raw_payload,
            }
            for item in document.responsibilities
        ],
        "requirements": [
            {
                "requirement_id": item.requirement_id, "kind": item.kind,
                "modality": item.modality, "evidence": _evidence_to_dict(item.evidence),
                **item.raw_payload,
            }
            for item in document.requirements
        ],
        "company_facts": [
            {
                "fact_id": item.fact_id, "kind": item.kind, "value": item.value,
                "evidence": _evidence_to_dict(item.evidence), **item.raw_payload,
            }
            for item in document.facts if item.scope == "company"
        ],
        "employment_facts": [
            {
                "fact_id": item.fact_id, "kind": item.kind, "value": item.value,
                "evidence": _evidence_to_dict(item.evidence), **item.raw_payload,
            }
            for item in document.facts if item.scope == "employment"
        ],
    }
    return JDExtractionResult.model_validate(payload)


register_extraction_mapper("v2", to_domain=_v2_to_domain, from_domain=_v2_from_domain)


def extraction_to_domain(payload: JsonObject, version: str | None = None) -> Document:
    contract = validate_extraction(payload, version)
    resolved_version = version or getattr(contract, "schema_version", None) or "v2"
    try:
        return _to_domain[resolved_version](contract)
    except KeyError as exc:
        raise ValueError(f"No extraction-to-domain mapper for {resolved_version}") from exc


def domain_to_extraction(document: Document, version: str = "v2") -> BaseModel:
    try:
        return _from_domain[version](document)
    except KeyError as exc:
        raise ValueError(f"No domain-to-extraction mapper for {version}") from exc
