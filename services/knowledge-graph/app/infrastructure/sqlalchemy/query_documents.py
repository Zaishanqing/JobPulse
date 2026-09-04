from copy import deepcopy

from sqlalchemy import select

from app.infrastructure.sqlalchemy.fact_mappers import load_structured_extraction
from app.infrastructure.sqlalchemy.query_base import QuerySession, evidence_projection
from app.models import (
    ExtractionEvidence,
    JDDocument,
    JDNormalizedRecord,
    NormalizedRequirementRecord,
    NormalizedSkillRecord,
    UnresolvedNormalizationItem,
)


class DocumentQueryMixin(QuerySession):
    def list_documents(self) -> list[dict]:
        return [
            {
                "document_id": row.document_id,
                "source_type": row.source_type,
                "published_at": row.published_at,
            }
            for row in self.session.scalars(select(JDDocument)).all()
        ]

    def document(self, document_id: str) -> dict | None:
        row = self.session.scalar(select(JDDocument).where(JDDocument.document_id == document_id))
        if row is None:
            return None
        return {
            "document_id": row.document_id,
            "raw_text": row.raw_text,
            "source_type": row.source_type,
            "is_synthetic": row.is_synthetic,
        }

    def extraction(self, document_id: str) -> dict | None:
        document = self.session.scalar(
            select(JDDocument).where(JDDocument.document_id == document_id)
        )
        if document is None:
            return None
        return load_structured_extraction(self.session, document_id).model_dump(mode="json")

    def normalization(self, document_id: str) -> dict | None:
        row = self.latest(JDNormalizedRecord, document_id)
        if row is None:
            return None
        result = deepcopy(row.payload)
        requirements = self.session.scalars(
            select(NormalizedRequirementRecord).where(
                NormalizedRequirementRecord.normalized_record_id == row.id
            )
        ).all()
        structured: dict[str, list[dict]] = {}
        for requirement in requirements:
            skills = self.session.scalars(
                select(NormalizedSkillRecord).where(
                    NormalizedSkillRecord.normalized_requirement_id == requirement.id
                )
            ).all()
            structured[requirement.requirement_id] = [
                {
                    "source_name": skill.source_name,
                    "skill_id": skill.skill_id,
                    "canonical_name": skill.canonical_name,
                    "category_code": skill.category_code,
                    "subcategory_code": skill.subcategory_code,
                    "resolution_status": skill.resolution_status,
                    "resolution_source": skill.resolution_source,
                }
                for skill in skills
            ]
        for requirement in result.get("normalized_requirements", []):
            requirement["normalized_skills"] = structured.get(
                requirement.get("requirement_id"), []
            )
        return result

    def unresolved_items(self, status: str) -> list[dict]:
        rows = self.session.scalars(
            select(UnresolvedNormalizationItem).where(
                UnresolvedNormalizationItem.status == status
            )
        ).all()
        result = []
        for row in rows:
            document = self.session.scalar(
                select(JDDocument).where(JDDocument.document_id == row.document_id)
            )
            evidence_rows = self.session.scalars(
                select(ExtractionEvidence).where(
                    ExtractionEvidence.document_id == row.document_id
                )
            ).all()
            result.append({
                "id": row.id,
                "document_id": row.document_id,
                "source_name": row.source_name,
                "item_type": row.item_type,
                "status": row.status,
                "reason": row.reason,
                "evidence": [
                    evidence_projection(item)
                    for item in evidence_rows
                    if row.source_name.casefold() in (item.quote or "").casefold()
                ],
                "source": {
                    "document_id": row.document_id,
                    "raw_text": document.raw_text if document is not None else None,
                    "source_type": document.source_type if document is not None else "unknown",
                    "is_synthetic": document.is_synthetic if document is not None else False,
                },
            })
        return result
