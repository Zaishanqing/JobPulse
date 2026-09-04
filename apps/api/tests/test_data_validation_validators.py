from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.contexts.data_validation import (
    BundleLineageValidator,
    CatalogReferenceValidator,
    ContractValidator,
    EvidenceAlignmentValidator,
    ExactDuplicateValidator,
    Finding,
    FindingSeverity,
    RequiredFieldValidator,
    SourceIdentityValidator,
    TaxonomyProjectionValidator,
    ValidateBundleUseCase,
    ValidationConclusion,
    ValidationContext,
    ValidatorSet,
    bundle_identity,
    default_validator_set,
)
from app.contexts.data_validation.fakes import (
    FakeCrossSourceDuplicatePort,
    FakeSkillCatalogResolutionPort,
)
from app.contexts.data_validation.ports import (
    SkillCatalogResolution,
)
from app.domain.jd_skill_catalog import CatalogClassification
RAW_TEXT = "Backend Engineer uses Python daily"
DOCUMENT_ID = "jdv1_document"


def _evidence(quote: str = "Python") -> dict:
    start = RAW_TEXT.index(quote)
    return {
        "source_id": DOCUMENT_ID,
        "quote": quote,
        "start": start,
        "end": start + len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


def _bundle() -> dict:
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc).isoformat()
    return {
        "schema_version": "extracted-jd-bundle-v1",
        "source_platform": "boss",
        "source_record_id": "job-1",
        "source_version": "1",
        "cleaned_text": RAW_TEXT,
        "extraction_result": {
            "schema_version": "v2",
            "document_id": DOCUMENT_ID,
            "job_title": {
                "text": "Backend Engineer",
                "evidence": _evidence("Backend Engineer"),
            },
            "responsibilities": [
                {
                    "requirement_id": "task-1",
                    "text": "uses Python",
                    "evidence": _evidence("uses Python"),
                }
            ],
            "requirements": [
                {
                    "requirement_id": "skill-1",
                    "kind": "skill",
                    "modality": "required",
                    "items": [{"name": "Python", "item_type": "language"}],
                    "evidence": _evidence(),
                }
            ],
            "company_facts": [],
            "employment_facts": [],
        },
        "normalized_result": {
            "schema_version": "v2",
            "document_id": DOCUMENT_ID,
            "job_classification": {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "source_title": "Backend Engineer",
                "candidate_positions": [],
                "technology_focus_codes": [],
                "industry_context_codes": [],
                "observed_skill_domain_codes": [],
                "classification_status": "catalog_gap",
                "review_reason_codes": ["CLASSIFICATION_NOT_RUN"],
                "evidence_refs": ["skill-1"],
                "classification_policy_version": "position-classifier.v3.0",
            },
            "normalized_requirements": [
                {
                    "requirement_id": "skill-1",
                    "kind": "skill",
                    "normalized_skills": [
                        {
                            "source_name": "Python",
                            "skill_id": "skill-python",
                            "canonical_name": "Python",
                            "resolution_status": "resolved",
                            "resolution_source": "same_id",
                        }
                    ],
                }
            ],
            "unresolved_items": [],
        },
        "review_flags": [],
        "extraction_provider": "fake",
        "model_version": "test-v1",
        "extraction_run_id": "run-1",
        "extraction_started_at": now,
        "extraction_finished_at": now,
    }


def _v2_bundle() -> dict:
    bundle = _bundle()
    bundle["schema_version"] = "extracted-jd-bundle-v2"
    bundle["skill_taxonomy"] = {
        "schema_version": "skill-taxonomy-projection.v1",
        "taxonomy_version": "skill-taxonomy-snapshot.v1",
        "skills": [
            {
                "skill_id": "skill-python",
                "canonical_name": "Python",
                "classifications": [
                    {
                        "facet": "concept_class",
                        "code": "technology",
                        "name_zh": "技术实体",
                        "name_en": "Technology",
                        "is_primary": True,
                    },
                    {
                        "facet": "technology_kind",
                        "code": "language",
                        "name_zh": "编程语言",
                        "name_en": "Language",
                        "is_primary": True,
                    },
                ],
            }
        ],
    }
    return bundle


def _v2_catalog() -> FakeSkillCatalogResolutionPort:
    return FakeSkillCatalogResolutionPort(
        taxonomy_version="skill-taxonomy-snapshot.v1",
        classification_sets={
            "skill-python": (
                "Python",
                (
                    CatalogClassification("concept_class", "technology", True),
                    CatalogClassification("technology_kind", "language", True),
                ),
            )
        },
    )


def _context(bundle: dict | None = None, **updates) -> ValidationContext:
    payload = bundle or _bundle()
    values = {
        "bundle": payload,
        "raw_text": RAW_TEXT,
        "cleaned_text": RAW_TEXT,
        "source_jd_id": "source-1",
        "source_jd_version_id": "version-1",
        "extraction_task_id": "task-1",
        "source_platform": "boss",
        "source_record_id": "job-1",
        "bundle_id": bundle_identity(payload),
    }
    values.update(updates)
    return ValidationContext(**values)


@pytest.mark.parametrize(
    "validator",
    [
        ContractValidator(),
        SourceIdentityValidator(),
        EvidenceAlignmentValidator(),
        RequiredFieldValidator(),
        CatalogReferenceValidator(),
        BundleLineageValidator(),
        ExactDuplicateValidator(),
    ],
)
def test_each_validator_accepts_valid_bundle(validator):
    assert validator.validate(_context()) == ()


def test_taxonomy_projection_requires_exact_authoritative_content():
    bundle = _v2_bundle()
    validator = TaxonomyProjectionValidator()
    assert validator.validate(_context(bundle, catalog=_v2_catalog())) == ()

    bundle["skill_taxonomy"]["skills"][0]["classifications"][0]["code"] = "practice"
    finding = validator.validate(_context(bundle, catalog=_v2_catalog()))[0]
    assert (finding.code, finding.severity.value) == (
        "taxonomy_projection_content_mismatch",
        "block",
    )


def test_taxonomy_projection_rejects_duplicate_skill_entries():
    bundle = _v2_bundle()
    bundle["skill_taxonomy"]["skills"].append(
        deepcopy(bundle["skill_taxonomy"]["skills"][0])
    )
    finding = TaxonomyProjectionValidator().validate(
        _context(bundle, catalog=_v2_catalog())
    )[0]
    assert finding.code == "taxonomy_projection_coverage_mismatch"


@pytest.mark.parametrize(
    ("mutate", "validator", "code", "path"),
    [
        (lambda b: b.pop("schema_version"), ContractValidator(), "missing_contract_version", "$.schema_version"),
        (lambda b: b.update(schema_version="v99"), ContractValidator(), "unsupported_contract_version", "$.schema_version"),
        (lambda b: b.update(source_record_id=" "), SourceIdentityValidator(), "invalid_source_identity", "$.source_record_id"),
        (lambda b: b.pop("source_platform"), SourceIdentityValidator(), "missing_source_identity", "$.source_platform"),
        (lambda b: b.update(source_platform="unknown"), SourceIdentityValidator(), "invalid_source_identity", "$.source_platform"),
        (lambda b: b.update(source_record_id="job-other"), SourceIdentityValidator(), "conflicting_source_identity", "$.source_record_id"),
        (lambda b: b.pop("extraction_provider"), RequiredFieldValidator(), "required_field_missing", "$.extraction_provider"),
        (lambda b: b.update(extraction_provider="  "), RequiredFieldValidator(), "required_value_blank", "$.extraction_provider"),
    ],
)
def test_stable_block_findings(mutate, validator, code, path):
    bundle = _bundle()
    mutate(bundle)
    finding = validator.validate(_context(bundle))[0]
    assert (finding.code, finding.severity.value, finding.path) == (code, "block", path)


def test_invalid_contract_structure_has_stable_code_and_path():
    bundle = _bundle()
    bundle["normalized_result"]["document_id"] = "different-document"
    finding = ContractValidator().validate(_context(bundle))[0]
    assert (finding.code, finding.severity.value, finding.path) == (
        "invalid_contract_structure",
        "block",
        "$",
    )


def test_conflicting_source_identity_has_stable_code_path_and_severity():
    finding = SourceIdentityValidator().validate(
        _context(source_record_id="different-source")
    )[0]
    assert (finding.code, finding.severity.value, finding.path) == (
        "conflicting_source_identity",
        "block",
        "$.source_record_id",
    )


def test_required_skill_items_collection_must_be_non_empty():
    bundle = _bundle()
    bundle["extraction_result"]["requirements"][0]["items"] = []
    findings = RequiredFieldValidator().validate(_context(bundle))
    assert [
        (finding.code, finding.severity.value, finding.path)
        for finding in findings
    ] == [
        (
            "required_collection_empty",
            "block",
            "$.extraction_result.requirements[0].items",
        )
    ]


def test_required_nested_fields_missing_null_blank_and_whitespace():
    bundle = _bundle()
    responsibility = bundle["extraction_result"]["responsibilities"][0]
    del responsibility["requirement_id"]
    responsibility["text"] = "  "
    skill = bundle["extraction_result"]["requirements"][0]["items"][0]
    skill["name"] = None
    findings = RequiredFieldValidator().validate(_context(bundle))
    assert [
        (finding.code, finding.path)
        for finding in findings
    ] == [
        (
            "required_field_missing",
            "$.extraction_result.responsibilities[0].requirement_id",
        ),
        (
            "required_value_blank",
            "$.extraction_result.responsibilities[0].text",
        ),
        (
            "required_field_missing",
            "$.extraction_result.requirements[0].items[0].name",
        ),
    ]


def test_required_collections_non_empty_and_optional_collections_empty_are_valid():
    bundle = _bundle()
    bundle["extraction_result"]["company_facts"] = []
    bundle["extraction_result"]["employment_facts"] = []
    assert RequiredFieldValidator().validate(_context(bundle)) == ()


@pytest.mark.parametrize("items_value", [None])
def test_required_skill_items_null_is_missing(items_value):
    bundle = _bundle()
    bundle["extraction_result"]["requirements"][0]["items"] = items_value
    finding = RequiredFieldValidator().validate(_context(bundle))[0]
    assert (finding.code, finding.path) == (
        "required_field_missing",
        "$.extraction_result.requirements[0].items",
    )


def test_required_skill_items_missing_and_required_string_empty():
    bundle = _bundle()
    del bundle["extraction_result"]["requirements"][0]["items"]
    bundle["extraction_result"]["job_title"]["text"] = ""
    findings = RequiredFieldValidator().validate(_context(bundle))
    assert [(finding.code, finding.path) for finding in findings] == [
        ("required_value_blank", "$.extraction_result.job_title.text"),
        (
            "required_field_missing",
            "$.extraction_result.requirements[0].items",
        ),
    ]


def test_evidence_exact_span_out_of_bounds_mismatch_target_and_orphan():
    bundle = _bundle()
    evidence = bundle["extraction_result"]["requirements"][0]["evidence"]
    evidence["end"] = len(RAW_TEXT) + 1
    assert EvidenceAlignmentValidator().validate(_context(bundle))[0].code == "invalid_evidence_span"

    bundle = _bundle()
    bundle["extraction_result"]["requirements"][0]["evidence"]["quote"] = "Java"
    assert any(item.code == "evidence_text_mismatch" for item in EvidenceAlignmentValidator().validate(_context(bundle)))

    bundle = _bundle()
    item = bundle["extraction_result"]["responsibilities"][0]
    item["text"] = ""
    item["requirement_id"] = ""
    assert any(item.code == "evidence_target_missing" for item in EvidenceAlignmentValidator().validate(_context(bundle)))

    bundle = _bundle()
    bundle["extraction_result"]["requirements"][0]["evidence"]["source_id"] = "other"
    finding = EvidenceAlignmentValidator().validate(_context(bundle))[0]
    assert (finding.code, finding.severity.value) == ("orphan_evidence", "warn")

    bundle = _bundle()
    del bundle["extraction_result"]["requirements"][0]["evidence"]
    finding = EvidenceAlignmentValidator().validate(_context(bundle))[0]
    assert finding.code == "missing_evidence"


def test_empty_skill_items_cannot_be_hidden_by_requirement_metadata():
    bundle = _bundle()
    bundle["extraction_result"]["requirements"][0]["items"] = []
    finding = EvidenceAlignmentValidator().validate(_context(bundle))[0]
    assert (finding.code, finding.severity.value, finding.path) == (
        "evidence_target_missing",
        "block",
        "$.extraction_result.requirements[0]",
    )


def test_non_empty_skill_items_have_a_valid_evidence_target():
    assert EvidenceAlignmentValidator().validate(_context()) == ()


@pytest.mark.parametrize(
    ("status", "error_code", "code", "severity"),
    [
        ("unresolved", None, "catalog_reference_unresolved", "warn"),
        ("conflict", None, "catalog_reference_conflict", "block"),
        ("conflict", "skill_catalog_snapshot_missing", "catalog_snapshot_missing", "block"),
    ],
)
def test_catalog_fake_port_statuses(status, error_code, code, severity):
    port = FakeSkillCatalogResolutionPort(
        responses={
            "Python": SkillCatalogResolution(
                status, None, None, None, "fake", error_code
            )
        }
    )
    findings = CatalogReferenceValidator().validate(_context(catalog=port))
    assert [(item.code, item.severity.value) for item in findings] == [(code, severity)]


def test_catalog_fake_resolved_has_no_finding():
    assert CatalogReferenceValidator().validate(
        _context(catalog=FakeSkillCatalogResolutionPort())
    ) == ()


@pytest.mark.parametrize(
    ("updates", "code", "path"),
    [
        ({"declared_extraction_task_id": "other"}, "extraction_task_mismatch", "$context.declared_extraction_task_id"),
        ({"declared_source_jd_version_id": "other"}, "source_version_mismatch", "$context.declared_source_jd_version_id"),
        ({"source_jd_version_id": ""}, "missing_extraction_lineage", "$context.source_jd_version_id"),
        ({"extraction_task_id": ""}, "missing_extraction_lineage", "$context.extraction_task_id"),
    ],
)
def test_lineage_findings(updates, code, path):
    findings = BundleLineageValidator().validate(_context(**updates))
    assert any((item.code, item.path) == (code, path) for item in findings)


def test_duplicate_with_dict_order_changes_is_block_and_legal_skill_values_are_ignored():
    bundle = _bundle()
    first = bundle["extraction_result"]["responsibilities"][0]
    duplicate = dict(reversed(list(first.items())))
    duplicate["requirement_id"] = "task-2"
    bundle["extraction_result"]["responsibilities"].append(duplicate)
    findings = ExactDuplicateValidator().validate(_context(bundle))
    assert findings[0].code == "exact_duplicate_item"
    assert findings[0].severity is FindingSeverity.BLOCK

    bundle = _bundle()
    bundle["extraction_result"]["requirements"][0]["items"].append(
        {"name": "Python", "item_type": "language"}
    )
    assert ExactDuplicateValidator().validate(_context(bundle)) == ()


class _AnyCrossSourceDuplicate(FakeCrossSourceDuplicatePort):
    def find_sources(self, canonical_hash: str) -> tuple[str, ...]:
        return ("source-z", "source-a")


def test_cross_source_exact_duplicate_is_warn_and_sources_are_sorted():
    finding = ExactDuplicateValidator().validate(
        _context(cross_source_duplicates=_AnyCrossSourceDuplicate())
    )[0]
    assert (finding.code, finding.severity.value) == ("cross_source_exact_duplicate", "warn")
    assert finding.as_dict()["details"]["source_ids"] == ["source-a", "source-z"]


class _RecordingCrossSourceDuplicate(FakeCrossSourceDuplicatePort):
    def __init__(self, duplicate_call: int | None = None):
        super().__init__()
        self.calls: list[str] = []
        self.duplicate_call = duplicate_call

    def find_sources(self, canonical_hash: str) -> tuple[str, ...]:
        self.calls.append(canonical_hash)
        if self.duplicate_call == len(self.calls):
            return ("source-other",)
        return ()


def test_duplicate_canonical_key_includes_fact_type():
    bundle = _bundle()
    bundle["extraction_result"]["responsibilities"][0] = {
        "requirement_id": "task-python",
        "text": "Python",
        "evidence": _evidence(),
    }
    bundle["extraction_result"]["company_facts"] = [
        {
            "fact_id": "company-python",
            "text": "Python",
            "evidence": _evidence(),
        }
    ]
    port = _RecordingCrossSourceDuplicate()
    ExactDuplicateValidator().validate(
        _context(bundle, cross_source_duplicates=port)
    )
    responsibility_key = port.calls[0]
    company_fact_key = port.calls[2]
    assert responsibility_key != company_fact_key


def test_cross_source_same_fact_type_duplicate_remains_warn():
    port = _RecordingCrossSourceDuplicate(duplicate_call=1)
    finding = ExactDuplicateValidator().validate(
        _context(cross_source_duplicates=port)
    )[0]
    assert (finding.code, finding.severity.value) == (
        "cross_source_exact_duplicate",
        "warn",
    )


class _WarnValidator:
    name = "warn"

    def validate(self, context):
        return (Finding("z_warn", "warn", "$.z", "warn", self.name),)


class _BlockValidator:
    name = "block"

    def validate(self, context):
        return (
            Finding("b_block", "block", "$.b", "block", self.name),
            Finding("a_block", "block", "$.a", "block", self.name),
        )


def test_validator_set_aggregation_order_determinism_and_input_immutability():
    bundle = _bundle()
    before = deepcopy(bundle)
    context = _context(bundle)
    validators = ValidatorSet((_WarnValidator(), _BlockValidator()))
    first = validators.validate(context)
    second = validators.validate(context)
    assert first == second
    assert [item.code for item in first] == ["z_warn", "a_block", "b_block"]
    assert validators.conclusion(first) is ValidationConclusion.BLOCK
    assert ValidatorSet((_WarnValidator(),)).conclusion(
        _WarnValidator().validate(context)
    ) is ValidationConclusion.WARN
    assert ValidatorSet(()).conclusion(()) is ValidationConclusion.PASS
    assert bundle == before


def test_validation_context_deeply_freezes_lists_dicts_and_evidence():
    bundle = _bundle()
    before = deepcopy(bundle)
    context = _context(bundle)

    with pytest.raises(TypeError):
        context.bundle["source_record_id"] = "changed"
    requirements = context.bundle["extraction_result"]["requirements"]
    with pytest.raises(AttributeError):
        requirements.append({})
    evidence = requirements[0]["evidence"]
    with pytest.raises(TypeError):
        evidence["quote"] = "changed"

    default_validator_set().validate(context)
    assert bundle == before


def test_synchronous_use_case_returns_report_compatible_payload_without_side_effects():
    result = ValidateBundleUseCase(default_validator_set()).execute(_context())
    assert result.conclusion is ValidationConclusion.PASS
    assert result.findings == ()
    assert result.report_payload == {"conclusion": "pass", "findings": []}


def test_validation_layers_do_not_import_runtime_frameworks_or_orm():
    forbidden = ("fastapi", "sqlalchemy", "app.models", "app.infrastructure")
    for module in ("domain.py", "ports.py", "validators.py", "application.py"):
        source = (
            __import__("pathlib").Path(__file__).parents[1]
            / "app"
            / "contexts"
            / "data_validation"
            / module
        ).read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden)
