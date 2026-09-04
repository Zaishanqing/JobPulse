from datetime import datetime, timedelta, timezone

from app.domain.jd import CandidateRequirement, Document, Evidence, NormalizationResult
from app.contexts.extraction_tasks.drafts import map_bundle_to_framework_draft
from app.infrastructure.jd_contract_mapper import to_legacy_dto
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1
from jobgraph_contracts.extraction_v2 import (
    Evidence as ExtractionEvidence,
    JDExtractionResult,
    SkillItem,
    SkillRequirement,
)
from jobgraph_contracts.normalization_v2 import JDNormalizedResult, JobClassification


def _evidence() -> Evidence:
    return Evidence(
        source_id="JD1",
        quote="熟悉 Python",
        start=0,
        end=8,
        alignment="exact",
        occurrence_index=0,
    )


def _skill_requirement(requirement_id: str, modality: str, name: str):
    return CandidateRequirement(
        requirement_id=requirement_id,
        kind="skill",
        modality=modality,
        evidence=_evidence(),
        raw_payload={"items": [{"name": name}]},
    )


def test_legacy_mapper_keeps_required_preferred_bonus_orthogonal():
    document = Document(
        document_id="JD1",
        contract_version="v2",
        title="后端工程师",
        requirements=(
            _skill_requirement("r1", "required", "Python"),
            _skill_requirement("r2", "preferred", "SQL"),
            _skill_requirement("r3", "bonus", "Docker"),
            _skill_requirement("r4", "unknown", "Rare"),
        ),
    )
    normalization = NormalizationResult(document_id="JD1", contract_version="v2")
    legacy = to_legacy_dto(document, normalization, fallback_title="后端工程师")

    assert [item.raw_skill for item in legacy.required_skills] == ["Python"]
    assert [item.raw_skill for item in legacy.bonus_skills] == ["SQL", "Docker"]
    assert "Rare" not in {item.raw_skill for item in legacy.required_skills}
    assert "Rare" not in {item.raw_skill for item in legacy.bonus_skills}


def _exact(raw_text: str, quote: str, document_id: str) -> ExtractionEvidence:
    start = raw_text.index(quote)
    return ExtractionEvidence(
        source_id=document_id,
        quote=quote,
        start=start,
        end=start + len(quote),
        alignment="exact",
        occurrence_index=0,
    )


def test_framework_draft_keeps_preferred_and_bonus_out_of_required():
    raw_text = "Python SQL Docker Rare"
    document_id = "doc-1"
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    bundle = ExtractedJDBundleV1(
        source_platform="test",
        source_record_id="rec-1",
        source_version="1",
        cleaned_text=raw_text,
        extraction_result=JDExtractionResult(
            document_id=document_id,
            requirements=[
                SkillRequirement(
                    requirement_id="r1",
                    kind="skill",
                    modality="required",
                    items=[SkillItem(name="Python", item_type="language")],
                    evidence=_exact(raw_text, "Python", document_id),
                ),
                SkillRequirement(
                    requirement_id="r2",
                    kind="skill",
                    modality="preferred",
                    items=[SkillItem(name="SQL", item_type="language")],
                    evidence=_exact(raw_text, "SQL", document_id),
                ),
                SkillRequirement(
                    requirement_id="r3",
                    kind="skill",
                    modality="bonus",
                    items=[SkillItem(name="Docker", item_type="language")],
                    evidence=_exact(raw_text, "Docker", document_id),
                ),
                SkillRequirement(
                    requirement_id="r4",
                    kind="skill",
                    modality="unknown",
                    items=[SkillItem(name="Rare", item_type="language")],
                    evidence=_exact(raw_text, "Rare", document_id),
                ),
            ],
        ),
        normalized_result=JDNormalizedResult(
            document_id=document_id,
            job_classification=JobClassification(
                classification_status="catalog_gap",
                review_reason_codes=["CLASSIFICATION_NOT_RUN"],
            ),
        ),
        extraction_provider="test",
        model_version="v1",
        extraction_run_id="run-1",
        extraction_started_at=now,
        extraction_finished_at=now + timedelta(seconds=1),
    )
    material = map_bundle_to_framework_draft(
        bundle,
        framework_jd_id="jd-1",
        raw_text=raw_text,
        fallback_title="Backend",
    )
    assert [item["raw_skill"] for item in material.required_skills] == ["Python"]
    assert [item["raw_skill"] for item in material.bonus_skills] == [
        "SQL",
        "Docker",
    ]
    assert all(
        item["raw_skill"] != "Rare"
        for item in material.required_skills + material.bonus_skills
    )
