from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.errors import StructuredFactsIncompleteError
from app.application.mappers import ExtractionMapper
from app.config import settings
from app.models import (
    ExtractedCandidateRequirement, ExtractedCompanyFact, ExtractedEmploymentFact,
    ExtractedJobTitle, ExtractedTaskRequirement, ExtractionEvidence,
    JDExtractionRecord, JDNormalizedRecord, NormalizedJobClassification,
    NormalizedRequirementRecord, NormalizedSkillRecord,
    UnresolvedNormalizationItem,
)
from app.schemas.extraction import JDExtractionResult
from app.schemas.normalization import JDNormalizedResult


def _payload(item) -> dict:
    value = item.model_dump(mode="json")
    value.pop("evidence", None)
    return value


def _evidence(document_id: str, owner_type: str, owner_ref: str, item) -> ExtractionEvidence:
    value = item.evidence.model_dump(mode="json")
    value.pop("source_id", None)
    return ExtractionEvidence(
        document_id=document_id, owner_type=owner_type, owner_ref=owner_ref, **value
    )


def persist_extracted(session: Session, result: JDExtractionResult) -> None:
    """Atomically replace the complete structured projection; audit payloads are untouched."""
    for model in (
        ExtractedJobTitle, ExtractedTaskRequirement, ExtractedCandidateRequirement,
        ExtractedCompanyFact, ExtractedEmploymentFact, ExtractionEvidence,
    ):
        session.query(model).filter_by(document_id=result.document_id).delete()
    session.add(ExtractedJobTitle(
        document_id=result.document_id,
        text=result.job_title.text if result.job_title else None,
    ))
    if result.job_title:
        session.add(_evidence(result.document_id, "job_title", "job_title", result.job_title))
    for item in result.responsibilities:
        session.add(ExtractedTaskRequirement(document_id=result.document_id,
            requirement_id=item.requirement_id, payload=_payload(item)))
        session.add(_evidence(result.document_id, "task", item.requirement_id, item))
    for item in result.requirements:
        session.add(ExtractedCandidateRequirement(document_id=result.document_id,
            requirement_id=item.requirement_id, kind=item.kind, modality=item.modality,
            payload=_payload(item)))
        session.add(_evidence(result.document_id, "requirement", item.requirement_id, item))
    for item in result.company_facts:
        session.add(ExtractedCompanyFact(document_id=result.document_id,
            fact_id=item.fact_id, payload=_payload(item)))
        session.add(_evidence(result.document_id, "company_fact", item.fact_id, item))
    for item in result.employment_facts:
        session.add(ExtractedEmploymentFact(document_id=result.document_id,
            fact_id=item.fact_id, payload=_payload(item)))
        session.add(_evidence(result.document_id, "employment_fact", item.fact_id, item))
    session.flush()


def load_structured_extraction(session: Session, document_id: str) -> JDExtractionResult:
    """Load the only business-readable extraction projection; never fall back to audit JSON."""
    title = session.scalar(select(ExtractedJobTitle).where(
        ExtractedJobTitle.document_id == document_id
    ))
    if title is None:
        raise StructuredFactsIncompleteError(
            f"structured extraction facts missing for document {document_id}"
        )

    def rows(model):
        return session.scalars(select(model).where(
            model.document_id == document_id
        ).order_by(model.id)).all()

    evidence_rows = rows(ExtractionEvidence)
    value = ExtractionMapper.from_structured_facts(
        document_id=document_id,
        job_title=None if title.text is None else {"text": title.text},
        responsibilities=[row.payload for row in rows(ExtractedTaskRequirement)],
        requirements=[row.payload for row in rows(ExtractedCandidateRequirement)],
        company_facts=[row.payload for row in rows(ExtractedCompanyFact)],
        employment_facts=[row.payload for row in rows(ExtractedEmploymentFact)],
        evidence_rows=[{
            "owner_type": row.owner_type, "owner_ref": row.owner_ref,
            "quote": row.quote, "start": row.start, "end": row.end,
            "alignment": row.alignment, "occurrence_index": row.occurrence_index,
        } for row in evidence_rows],
    )
    return JDExtractionResult.model_validate(value)


def persist_normalized(session: Session, result: JDNormalizedResult) -> JDNormalizedRecord:
    record = JDNormalizedRecord(document_id=result.document_id,
        payload=result.model_dump(mode="json"), map_version=settings.normalization_map_version)
    session.add(record); session.flush()
    classification = result.job_classification
    session.add(NormalizedJobClassification(normalized_record_id=record.id,
        position_id=classification.position_code,
        source_title=classification.source_title,
        resolution_status=classification.classification_status))
    for requirement in result.normalized_requirements:
        row = NormalizedRequirementRecord(normalized_record_id=record.id,
            requirement_id=requirement.requirement_id, kind=requirement.kind)
        session.add(row); session.flush()
        for skill in requirement.normalized_skills:
            session.add(NormalizedSkillRecord(
                normalized_requirement_id=row.id, **skill.model_dump()))
    for item in result.unresolved_items:
        session.add(UnresolvedNormalizationItem(document_id=result.document_id,
            source_name=item.source_name, item_type=item.item_type, reason=item.reason))
    session.flush()
    return record


def latest_record(session: Session, model, document_id: str):
    return session.scalar(select(model).where(
        model.document_id == document_id).order_by(model.id.desc()))
