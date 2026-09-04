from sqlalchemy import select

from app.infrastructure.sqlalchemy.query_base import QuerySession, evidence_projection
from app.models import (
    ExtractedCandidateRequirement,
    ExtractionEvidence,
    JDDocument,
    NormalizedSkillRecord,
    PositionRequirementAggregateDraft,
    PositionSkillRelationDraft,
    PositionSkillSupport,
    PositionTaskAggregateDraft,
)


class EvidenceQueryMixin(QuerySession):
    def relation_evidence(self, relation_id: int) -> list[dict] | None:
        relation = self.session.get(PositionSkillRelationDraft, relation_id)
        if relation is None:
            return None
        supports = self.session.scalars(
            select(PositionSkillSupport).where(
                PositionSkillSupport.build_run_id == relation.build_run_id,
                PositionSkillSupport.skill_id == relation.skill_id,
            )
        ).all()
        result = []
        for support in supports:
            evidence = self.session.get(ExtractionEvidence, support.evidence_id)
            source = self.session.get(ExtractedCandidateRequirement, support.source_requirement_id)
            normalized = self.session.get(NormalizedSkillRecord, support.normalized_skill_id)
            result.append(
                {
                    "support_id": support.id,
                    "document_id": support.document_id,
                    "requirement_id": support.requirement_id,
                    "modality": support.modality,
                    "evidence": evidence_projection(evidence),
                    "original_requirement": source.payload,
                    "normalized_skill": {
                        "id": normalized.id,
                        "skill_id": normalized.skill_id,
                        "canonical_name": normalized.canonical_name,
                        "source_name": normalized.source_name,
                        "resolution_status": normalized.resolution_status,
                        "resolution_source": normalized.resolution_source,
                    },
                }
            )
        return result

    def aggregate_evidence(self, aggregate_id: int, kind: str) -> list[dict] | None:
        model = PositionRequirementAggregateDraft if kind == "requirement" else PositionTaskAggregateDraft
        row = self.session.get(model, aggregate_id)
        if row is None:
            return None
        result: list[dict] = []
        for value in row.payload.get("evidence_ids", []):
            evidence = self.session.get(ExtractionEvidence, value)
            if evidence is None:
                continue
            document = self.session.scalar(
                select(JDDocument).where(
                    JDDocument.document_id == evidence.document_id
                )
            )
            result.append({
                "evidence_id": value,
                "evidence": evidence_projection(evidence),
                "source": {
                    "document_id": evidence.document_id,
                    "raw_text": document.raw_text if document is not None else "",
                },
            })
        return result

    def document_evidence(self, document_id: str) -> list[dict]:
        rows = self.session.scalars(
            select(ExtractionEvidence).where(ExtractionEvidence.document_id == document_id)
        ).all()
        return [evidence_projection(row) for row in rows]
