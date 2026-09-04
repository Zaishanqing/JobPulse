"""Application policy for Python-owned IDs and exact Evidence alignment."""

from uuid import NAMESPACE_URL, uuid5

from app.contracts.jd.evidence import Evidence
from app.contracts.jd.extraction_model_output import ModelExtractionOutput
from app.contracts.jd.extraction_v2 import JDExtractionResult


def _stable_id(document_id: str, namespace: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{document_id}:{namespace}:{index}"))


def align_evidence(source_id: str, source_text: str, quote: str) -> Evidence:
    start = source_text.find(quote)
    if start < 0:
        return Evidence(
            source_id=source_id,
            quote=quote,
            start=None,
            end=None,
            alignment="unresolved",
            occurrence_index=None,
        )
    return Evidence(
        source_id=source_id,
        quote=quote,
        start=start,
        end=start + len(quote),
        alignment="exact",
        occurrence_index=source_text[:start].count(quote),
    )


def model_output_to_v2_contract(
    output: ModelExtractionOutput, source_text: str
) -> JDExtractionResult:
    """Map model output to V2, discarding unresolved atoms from the formal result."""
    title = None
    if output.job_title:
        evidence = align_evidence(
            output.job_title.evidence.source_id,
            source_text,
            output.job_title.evidence.quote,
        )
        if evidence.alignment == "exact":
            title = {"value": output.job_title.value, "evidence": evidence.model_dump()}

    responsibilities = []
    for index, item in enumerate(output.responsibilities):
        evidence = align_evidence(item.evidence.source_id, source_text, item.evidence.quote)
        if evidence.alignment == "exact":
            responsibilities.append(
                {
                    "requirement_id": _stable_id(output.document_id, "task", index),
                    "kind": "task",
                    "modality": item.modality,
                    "evidence": evidence.model_dump(),
                    "action": item.action,
                }
            )

    requirements = []
    for index, item in enumerate(output.requirements):
        evidence = align_evidence(item.evidence.source_id, source_text, item.evidence.quote)
        if evidence.alignment == "exact":
            requirements.append(
                {
                    "requirement_id": _stable_id(output.document_id, "requirement", index),
                    "kind": item.kind,
                    "modality": item.modality,
                    "evidence": evidence.model_dump(),
                    **item.payload,
                }
            )

    facts = {"company": [], "employment": []}
    for index, item in enumerate(output.facts):
        evidence = align_evidence(item.evidence.source_id, source_text, item.evidence.quote)
        if evidence.alignment == "exact":
            facts[item.scope].append(
                {
                    "fact_id": _stable_id(output.document_id, f"{item.scope}_fact", index),
                    "kind": item.kind,
                    "value": item.value,
                    "evidence": evidence.model_dump(),
                }
            )
    return JDExtractionResult.model_validate(
        {
            "schema_version": "v2",
            "document_id": output.document_id,
            "job_title": title,
            "responsibilities": responsibilities,
            "requirements": requirements,
            "company_facts": facts["company"],
            "employment_facts": facts["employment"],
        }
    )
