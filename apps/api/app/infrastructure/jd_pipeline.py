"""Application pipeline for extracting and validating versioned JD data."""

from pathlib import Path
import re

import yaml
from app.contracts.jd.extraction_model_output import (
    ModelCandidateRequirement,
    ModelEvidence,
    ModelExtractionOutput,
    ModelFact,
    ModelResponsibility,
    ModelSourcedText,
)
from app.contracts.jd.extraction_v2 import JDExtractionResult
from app.contracts.jd.normalization_v2 import JDNormalizedResult
from app.domain.jd import Document, NormalizationResult, NormalizedItem, ReviewFlag
from app.infrastructure.jd_extraction_mapper import extraction_to_domain
from app.infrastructure.jd_normalization_mapper import domain_to_normalization

from app.infrastructure.jd_extraction_postprocessor import model_output_to_v2_contract


NORMALIZATION_MAP_PATH = Path(__file__).resolve().parents[2] / "config" / "normalization_map.yaml"
SKILL_TYPES = {
    "Python": "programming_language",
    "Java": "programming_language",
    "Spring Boot": "framework",
    "RAG": "methodology",
    "Docker": "tool",
    "Kubernetes": "platform",
    "多智能体": "methodology",
    "FastAPI": "framework",
}


def _segments(raw_text: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"[。；;\n\r]+", raw_text) if segment.strip()]


def extract_jd(document_id: str, raw_text: str, fallback_title: str) -> JDExtractionResult:
    """Produce model-shaped semantics, then let Python post-processing own Evidence/IDs."""

    def model_evidence(quote: str) -> ModelEvidence:
        return ModelEvidence(source_id=document_id, quote=quote)

    title_match = re.search(r"(?:职位|岗位名称|招聘岗位)[:：]\s*([^\n。；;]+)", raw_text)
    title_quote = title_match.group(1).strip() if title_match else None
    job_title = None
    if title_quote:
        job_title = ModelSourcedText(value=title_quote, evidence=model_evidence(title_quote))
    elif fallback_title and fallback_title in raw_text:
        job_title = ModelSourcedText(value=fallback_title, evidence=model_evidence(fallback_title))

    responsibilities = []
    for segment in _segments(raw_text):
        cleaned = re.sub(r"^(岗位职责|工作职责|职责)[:：]\s*", "", segment).strip()
        if any(marker in cleaned for marker in ("负责", "参与", "承担")):
            quote = cleaned if cleaned in raw_text else segment
            responsibilities.append(
                ModelResponsibility(
                    modality="required", evidence=model_evidence(quote), action=cleaned
                )
            )

    requirements = []
    lowered = raw_text.lower()
    for name, item_type in SKILL_TYPES.items():
        start = lowered.find(name.lower())
        if start < 0:
            continue
        quote = raw_text[start : start + len(name)]
        prefix = raw_text[max(0, start - 8) : start]
        modality = "bonus" if any(word in prefix for word in ("优先", "加分")) else "required"
        requirements.append(
            ModelCandidateRequirement(
                kind="skill",
                modality=modality,
                evidence=model_evidence(quote),
                payload={"items": [{"name": quote, "item_type": item_type}]},
            )
        )

    degree_match = re.search(r"(博士|硕士|本科|大专|专科)(?:及以上|以上)?", raw_text)
    if degree_match:
        quote = degree_match.group(0)
        requirements.append(
            ModelCandidateRequirement(
                kind="education",
                modality="preferred"
                if "优先" in raw_text[degree_match.end() : degree_match.end() + 4]
                else "required",
                evidence=model_evidence(quote),
                payload={"minimum_degree": degree_match.group(1)},
            )
        )

    experience_match = re.search(
        r"(\d+)\s*[-~至]\s*(\d+)\s*年|(?:至少|不少于)?\s*(\d+)\s*年(?:以上)?", raw_text
    )
    if experience_match:
        quote = experience_match.group(0)
        minimum = experience_match.group(1) or experience_match.group(3)
        maximum = experience_match.group(2)
        requirements.append(
            ModelCandidateRequirement(
                kind="experience",
                modality="required",
                evidence=model_evidence(quote),
                payload={
                    "minimum_years": float(minimum),
                    "maximum_years": float(maximum) if maximum else None,
                    "duration_text": quote,
                },
            )
        )

    company_facts = []
    for kind, pattern in (
        ("industry", r"(?:所属行业|行业)[:：]\s*([^，。；;\n]+)"),
        ("company_size", r"(?:公司规模|规模)[:：]\s*([^，。；;\n]+)"),
        ("business", r"(?:业务场景|应用场景)[:：]\s*([^，。；;\n]+)"),
    ):
        match = re.search(pattern, raw_text)
        if match:
            quote = match.group(1).strip()
            company_facts.append(
                ModelFact(
                    scope="company",
                    kind=kind,
                    value=quote,
                    evidence=model_evidence(quote),
                )
            )

    employment_facts = []
    employment_patterns = (
        (
            "salary",
            r"(?:薪资|月薪)[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*[kK千万](?:\s*[-~至]\s*[0-9]+(?:\.[0-9]+)?\s*[kK千万])?)",
        ),
        ("work_location", r"(?:工作地点|工作地址)[:：]\s*([^，。；;\n]+)"),
        ("work_schedule", r"(双休|大小周|弹性工作)"),
        ("benefit", r"(五险一金|带薪年假|餐补|交通补贴)"),
        ("training", r"(带薪培训|入职培训|导师制)"),
    )
    for kind, pattern in employment_patterns:
        for match in re.finditer(pattern, raw_text):
            quote = (match.group(1) if match.lastindex else match.group(0)).strip()
            employment_facts.append(
                ModelFact(
                    scope="employment",
                    kind=kind,
                    value=quote,
                    evidence=model_evidence(quote),
                )
            )

    model_output = ModelExtractionOutput(
        document_id=document_id,
        job_title=job_title,
        responsibilities=responsibilities,
        requirements=requirements,
        facts=[*company_facts, *employment_facts],
    )
    return model_output_to_v2_contract(model_output, raw_text)


def _load_normalization_map() -> dict:
    with NORMALIZATION_MAP_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize_document(document: Document) -> NormalizationResult:
    """Normalize stable domain concepts; no extraction contract is mutated."""
    mapping = _load_normalization_map()
    skill_map = {key.casefold(): value for key, value in mapping.get("skills", {}).items()}
    normalized: list[NormalizedItem] = []
    unresolved: list[ReviewFlag] = []
    for requirement in document.requirements:
        if requirement.kind != "skill":
            continue
        for item in requirement.raw_payload.get("items", []):
            source_name = item["name"]
            target = skill_map.get(source_name.casefold())
            if target:
                normalized.append(
                    NormalizedItem(
                        source_value=source_name,
                        item_type="skill",
                        resolution_status="resolved",
                        skill_id=target.get("skill_id"),
                        canonical_name=target.get("canonical_name"),
                        category_code=target.get("category_code"),
                        subcategory_code=target.get("subcategory_code"),
                        raw_payload={
                            "requirement_id": requirement.requirement_id,
                            "requirement_kind": requirement.kind,
                        },
                    )
                )
            else:
                normalized.append(
                    NormalizedItem(
                        source_value=source_name,
                        item_type="skill",
                        resolution_status="unresolved",
                        raw_payload={
                            "requirement_id": requirement.requirement_id,
                            "requirement_kind": requirement.kind,
                        },
                    )
                )
                unresolved.append(
                    ReviewFlag(
                        flag_type="skill",
                        source_value=source_name,
                        reason="not_found_in_normalization_map",
                        raw_payload={
                            "details": {
                                "requirement_id": requirement.requirement_id,
                                "requirement_kind": requirement.kind,
                            }
                        },
                    )
                )

    classification = None
    if document.title:
        classification = {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": document.title,
            "position_id": None,
            "position_code": None,
            "position_name": None,
            "family_code": None,
            "family_name": None,
            "candidate_positions": [],
            "career_level": None,
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": [],
            "confidence": None,
            "classification_status": "catalog_gap",
            "review_reason_codes": ["CLASSIFICATION_NOT_RUN"],
            "evidence_refs": [],
            "classification_policy_version": "position-classifier.v3.0",
        }
        unresolved.append(
            ReviewFlag(
                flag_type="job_title",
                source_value=document.title,
                reason="position_catalog_review_required",
            )
        )

    salary_fact = next(
        (fact for fact in document.facts if fact.scope == "employment" and fact.kind == "salary"),
        None,
    )
    return NormalizationResult(
        document_id=document.document_id,
        contract_version="v2",
        items=tuple(normalized),
        review_flags=tuple(unresolved),
        job_classification=classification,
        salary={"raw_value": salary_fact.value} if salary_fact else None,
    )


def normalize_jd(extraction: JDExtractionResult) -> JDNormalizedResult:
    document = extraction_to_domain(extraction.model_dump(mode="json"))
    return domain_to_normalization(normalize_document(document))


def validate_document_publishable(document: Document) -> None:
    evidence_items = []
    if document.title_evidence:
        evidence_items.append(document.title_evidence)
    evidence_items.extend(item.evidence for item in document.responsibilities)
    evidence_items.extend(item.evidence for item in document.requirements)
    evidence_items.extend(item.evidence for item in document.facts)
    if any(
        item.alignment != "exact"
        or item.start is None
        or item.end is None
        or item.occurrence_index is None
        for item in evidence_items
    ):
        raise ValueError("Only exact evidence can enter a formal or published result")


def validate_publishable(extraction: JDExtractionResult) -> None:
    validate_document_publishable(extraction_to_domain(extraction.model_dump(mode="json")))
