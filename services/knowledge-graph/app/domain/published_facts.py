"""Authoritative published-fact versioning and import decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.domain.decisions import DomainRejection
from app.domain.lineage import PublishedFactLineage, decide_lineage
from app.domain.structured_facts import (
    ExtractionFacts,
    NormalizationFacts,
    PublishedJDFact,
)


@dataclass(frozen=True)
class PublishedFactVersion:
    raw: str
    kind: Literal["sequence", "timestamp"]
    sequence: int | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PublishedFactIdentity:
    source_system: str
    source_fact_id: str


@dataclass(frozen=True)
class PublishedFactRecord:
    document_id: str
    identity: PublishedFactIdentity
    version: str
    source_version: str
    lineage_lineage_version: str | None = None


@dataclass(frozen=True)
class PublishedFactValidationFacts:
    incoming: PublishedJDFact
    same_version: PublishedFactRecord | None
    current: PublishedFactRecord | None
    lineage: PublishedFactLineage = PublishedFactLineage()


@dataclass(frozen=True)
class PublishedFactImportPlan:
    fact: PublishedJDFact
    identity: PublishedFactIdentity
    version: PublishedFactVersion
    source_version: str
    lineage: PublishedFactLineage = PublishedFactLineage()


@dataclass(frozen=True)
class PublishedFactDecision:
    accepted: bool
    plan: PublishedFactImportPlan | None = None
    existing: PublishedFactRecord | None = None
    idempotent: bool = False
    stale: bool = False
    rejection: DomainRejection | None = None


def _evidence(value) -> dict[str, object]:
    return {
        "source_id": value.source_id,
        "quote": value.quote,
        "start": value.start,
        "end": value.end,
        "alignment": value.alignment,
        "occurrence_index": value.occurrence_index,
    }


def _extraction(value: ExtractionFacts) -> dict[str, object]:
    requirements: list[dict[str, object]] = []
    for item in value.requirements:
        requirement: dict[str, object] = {
            "requirement_id": item.requirement_id,
            "kind": item.kind,
            "modality": item.modality,
            "evidence": _evidence(item.evidence),
        }
        if item.kind == "skill":
            requirement["items"] = [
                {"name": skill.name, "item_type": skill.item_type}
                for skill in item.items
            ]
            requirement["proficiency"] = item.proficiency
        elif item.kind == "education":
            requirement.update(
                {
                    "text": item.text,
                    "minimum_degree": item.minimum_degree,
                    "majors": list(item.majors),
                    "school_constraints": list(item.school_constraints),
                    "admission_type": item.admission_type,
                    "graduation_year": item.graduation_year,
                    "student_cohort": item.student_cohort,
                }
            )
        elif item.kind == "experience":
            requirement.update(
                {
                    "text": item.text,
                    "minimum_years": item.minimum_years,
                    "maximum_years": item.maximum_years,
                    "domain": item.domain,
                    "role": item.role,
                    "duration_text": item.duration_text,
                    "experience_unlimited": item.experience_unlimited,
                }
            )
        elif item.kind == "certificate":
            requirement.update(
                {
                    "text": item.text,
                    "certificates": list(item.certificates),
                }
            )
        elif item.kind == "soft_skill":
            requirement.update(
                {"text": item.text, "skills": list(item.skills)}
            )
        else:
            requirement["text"] = item.text or ""
        requirements.append(requirement)
    return {
        "schema_version": value.schema_version,
        "document_id": value.document_id,
        "job_title": (
            {
                "text": value.job_title.text,
                "evidence": _evidence(value.job_title.evidence),
            }
            if value.job_title
            else None
        ),
        "responsibilities": [
            {
                "requirement_id": item.requirement_id,
                "text": item.text,
                "evidence": _evidence(item.evidence),
            }
            for item in value.responsibilities
        ],
        "requirements": requirements,
        "company_facts": [
            {"fact_id": item.fact_id, "text": item.text, "evidence": _evidence(item.evidence)}
            for item in value.company_facts
        ],
        "employment_facts": [
            {
                "fact_id": item.fact_id,
                "fact_type": item.fact_type,
                "text": item.text,
                "evidence": _evidence(item.evidence),
            }
            for item in value.employment_facts
        ],
    }


def _normalized_requirement(value) -> dict[str, object]:
    return {
        "requirement_id": value.requirement_id,
        "kind": value.kind,
        "normalized_skills": [
            {
                "source_name": skill.source_name,
                "skill_id": skill.skill_id,
                "canonical_name": skill.canonical_name,
                "category_code": skill.category_code,
                "subcategory_code": skill.subcategory_code,
                "resolution_status": skill.resolution_status,
                "resolution_source": skill.resolution_source,
            }
            for skill in value.normalized_skills
        ],
    }


def _normalization(value: NormalizationFacts) -> dict[str, object]:
    classification = value.job_classification
    return {
        "schema_version": value.schema_version,
        "document_id": value.document_id,
        "job_classification": {
            "schema_version": classification.schema_version,
            "taxonomy_version": classification.taxonomy_version,
            "source_title": classification.source_title,
            "position_id": classification.position_id,
            "position_code": classification.position_code,
            "position_name": classification.position_name,
            "family_code": classification.family_code,
            "family_name": classification.family_name,
            "candidate_positions": list(classification.candidate_positions),
            "career_level": classification.career_level,
            "leadership_scope": classification.leadership_scope,
            "technology_focus_codes": list(classification.technology_focus_codes),
            "industry_context_codes": list(classification.industry_context_codes),
            "observed_skill_domain_codes": list(
                classification.observed_skill_domain_codes
            ),
            "confidence": classification.confidence,
            "classification_status": classification.classification_status,
            "review_reason_codes": list(classification.review_reason_codes),
            "evidence_refs": list(classification.evidence_refs),
            "classification_policy_version": (
                classification.classification_policy_version
            ),
        },
        "normalized_requirements": [
            _normalized_requirement(item) for item in value.normalized_requirements
        ],
        "salary": (
            {
                "currency": value.salary.currency,
                "minimum": value.salary.minimum,
                "maximum": value.salary.maximum,
                "period": value.salary.period,
            }
            if value.salary
            else None
        ),
        "unresolved_items": [
            {
                "source_name": item.source_name,
                "item_type": item.item_type,
                "reason": item.reason,
            }
            for item in value.unresolved_items
        ],
    }


def _canonical_payload(fact: PublishedJDFact) -> dict[str, object]:
    return {
        "contract_version": fact.contract_version,
        "schema_version": fact.schema_version,
        "source_system": fact.source_system,
        "source_jd_id": fact.source_jd_id,
        "source_fact_id": fact.source_fact_id,
        "source_fact_version": fact.source_fact_version,
        "review_status": fact.review_status,
        "published_at": fact.published_at,
        "position_fact": {
            "position_id": fact.position_fact.position_id,
            **dict(fact.position_fact.extensions),
        },
        "skill_facts": [
            {"skill_id": item.skill_id, **dict(item.extensions)}
            for item in fact.skill_facts
        ],
        "requirement_facts": [
            {"requirement_id": item.requirement_id, **dict(item.extensions)}
            for item in fact.requirement_facts
        ],
        "education_fact": fact.education_fact,
        "experience_fact": fact.experience_fact,
        "industry_fact": fact.industry_fact,
        "company_facts": [dict(item) for item in fact.company_facts],
        "employment_facts": [dict(item) for item in fact.employment_facts],
        "evidence": [
            {**_evidence(item), **dict(item.extensions)} for item in fact.evidence
        ],
        "extraction_fact": _extraction(fact.extraction_fact),
        "normalized_fact": _normalization(fact.normalized_fact),
        "trace_metadata": dict(fact.trace_metadata),
    }


def parse_published_fact_version(raw: str) -> PublishedFactVersion | DomainRejection:
    if raw.isdecimal():
        return PublishedFactVersion(raw, "sequence", sequence=int(raw))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return DomainRejection(
            "validation",
            "source_fact_version must be an integer sequence or UTC timestamp",
            "INVALID_PUBLISHED_FACT_VERSION",
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return DomainRejection(
            "validation",
            "source_fact_version timestamp must be timezone-aware",
            "INVALID_PUBLISHED_FACT_VERSION",
        )
    return PublishedFactVersion(
        raw, "timestamp", timestamp=parsed.astimezone(timezone.utc)
    )


def _compare(
    incoming: PublishedFactVersion, current: PublishedFactVersion
) -> int | DomainRejection:
    if incoming.kind != current.kind:
        return DomainRejection(
            "validation",
            "source_fact_version formats cannot be mixed",
            "INCOMPATIBLE_PUBLISHED_FACT_VERSION",
        )
    left = incoming.sequence if incoming.kind == "sequence" else incoming.timestamp
    right = current.sequence if current.kind == "sequence" else current.timestamp
    assert left is not None and right is not None
    return (left > right) - (left < right)


def decide_published_fact_import(
    facts: PublishedFactValidationFacts,
) -> PublishedFactDecision:
    incoming = facts.incoming
    lineage_decision = decide_lineage(facts.lineage)
    if not lineage_decision.accepted:
        return PublishedFactDecision(False, rejection=lineage_decision.rejection)
    supplied_source_version = incoming.source_version
    version = parse_published_fact_version(incoming.source_fact_version)
    if isinstance(version, DomainRejection):
        return PublishedFactDecision(False, rejection=version)
    if facts.same_version:
        if facts.same_version.source_version != supplied_source_version:
            return PublishedFactDecision(
                False,
                rejection=DomainRejection(
                    "conflict",
                    "published JD fact version already exists with a different source version",
                    "PUBLISHED_FACT_CONTENT_CONFLICT",
                ),
            )
        if (
            lineage_decision.lineage_version is not None
            and facts.same_version.lineage_lineage_version
            != lineage_decision.lineage_version
        ):
            return PublishedFactDecision(
                False,
                rejection=DomainRejection(
                    "conflict",
                    "published JD fact version already exists with different lineage",
                    "PUBLISHED_FACT_LINEAGE_CONFLICT",
                ),
            )
        return PublishedFactDecision(
            True, existing=facts.same_version, idempotent=True
        )
    if facts.current:
        current_version = parse_published_fact_version(facts.current.version)
        if isinstance(current_version, DomainRejection):
            return PublishedFactDecision(False, rejection=current_version)
        comparison = _compare(version, current_version)
        if isinstance(comparison, DomainRejection):
            return PublishedFactDecision(False, rejection=comparison)
        if comparison < 0:
            return PublishedFactDecision(
                True, existing=facts.current, idempotent=True, stale=True
            )
        if comparison == 0:
            return PublishedFactDecision(
                False,
                rejection=DomainRejection(
                    "conflict",
                    "published JD fact version already exists with different content",
                    "PUBLISHED_FACT_CONTENT_CONFLICT",
                ),
            )
    if (
        incoming.extraction_fact.document_id != incoming.source_jd_id
        or incoming.normalized_fact.document_id != incoming.source_jd_id
    ):
        return PublishedFactDecision(
            False,
            rejection=DomainRejection(
                "validation",
                "source_jd_id does not match structured fact document_id",
                "PUBLISHED_FACT_DOCUMENT_MISMATCH",
            ),
        )
    identity = PublishedFactIdentity(
        incoming.source_system, incoming.source_fact_id
    )
    return PublishedFactDecision(
        True,
        plan=PublishedFactImportPlan(
            incoming, identity, version, supplied_source_version, facts.lineage
        ),
    )
