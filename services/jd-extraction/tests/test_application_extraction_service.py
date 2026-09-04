from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import ExtractedJDBundleV1

from src.application import (
    ExtractionErrorCode,
    JDExtractionApplicationError,
    JDExtractionApplicationService,
)
from src.application.identity import build_document_id
from src.application.text_mapping import NFKCTextMap
from src.deepseek_client import DeepSeekResult
from src.exceptions import BusinessValidationError, InvalidJSONError
from src.models import Evidence
from src.preprocess import build_source_blocks, normalize_jd_text
from src.text_cleaning import clean_jd_text

from application_fakes import (
    FakeClient,
    FakePositionClassifier,
    RaisingClient,
    RecordingClient,
    TimeoutClient,
    valid_payload,
)


NORMALIZATION_PATH = str(Path("config/normalization_map.yaml"))


def envelope(raw_text: str = "熟练使用 Python", **changes) -> CrawlerJDEnvelopeV1:
    values = {
        "source_record_id": "job-1",
        "source_platform": "boss_zhipin",
        "crawl_time": datetime.now(timezone.utc),
        "raw_text": raw_text,
        "raw_payload": {"source": "test"},
        "text_canonicalization_version": "v1",
        "source_version": "1",
    }
    values.update(changes)
    return CrawlerJDEnvelopeV1(**values)


def service(client, *, retries: int = 0) -> JDExtractionApplicationService:
    return JDExtractionApplicationService(
        model="fake-model",
        normalization_path=NORMALIZATION_PATH,
        client=client,
        position_classifier=FakePositionClassifier(),
        extraction_provider="fake",
        extraction_run_id="test-run",
        semantic_retry_attempts=retries,
    )


def classified_envelope_and_payload():
    raw_text = "Python开发工程师；熟练使用 Python"
    payload = valid_payload()
    payload["job_title"] = {
        "value": "Python开发工程师",
        "evidence": {
            "source_id": "src_0001",
            "quote": "Python开发工程师",
        },
    }
    payload["requirements"][0]["evidence"] = {
        "source_id": "src_0002",
        "quote": "熟练使用 Python",
    }
    return envelope(raw_text), payload


def all_evidence(bundle: ExtractedJDBundleV1):
    result = bundle.extraction_result
    if result.job_title is not None:
        yield result.job_title.evidence
    for collection in (
        result.responsibilities,
        result.requirements,
        result.company_facts,
        result.employment_facts,
    ):
        for item in collection:
            yield item.evidence


def assert_all_evidence_is_exact(bundle: ExtractedJDBundleV1, raw_text: str) -> None:
    evidence_items = list(all_evidence(bundle))
    assert evidence_items
    canonical = bundle.cleaned_text
    for evidence in evidence_items:
        assert evidence.alignment == "exact"
        assert evidence.start is not None
        assert evidence.end is not None
        assert 0 <= evidence.start <= evidence.end <= len(canonical)
        assert canonical[evidence.start : evidence.end] == evidence.quote


def test_valid_envelope_returns_shared_bundle_with_matching_identity():
    source = envelope()
    bundle = service(FakeClient()).extract_one(source)
    assert isinstance(bundle, ExtractedJDBundleV1)
    assert (bundle.source_platform, bundle.source_record_id, bundle.source_version) == (
        source.source_platform,
        source.source_record_id,
        source.source_version,
    )


def test_v2_classifies_before_returning_bundle():
    source, payload = classified_envelope_and_payload()
    classifier = FakePositionClassifier()
    extractor = JDExtractionApplicationService(
        model="fake-model",
        normalization_path=NORMALIZATION_PATH,
        client=FakeClient(payload),
        position_classifier=classifier,
        extraction_provider="fake",
        extraction_run_id="test-run",
        semantic_retry_attempts=0,
    )

    bundle = extractor.extract_one_v2(source)

    classification = bundle.normalized_result.job_classification
    assert classification is not None
    assert classification.classification_status == "resolved"
    assert classification.position_code == "BACKEND_ENGINEER"
    assert classifier.profiles[0]["available_evidence_refs"] == ["src_0001"]


def test_v2_unresolved_classification_is_blocking_review():
    source, payload = classified_envelope_and_payload()
    extractor = JDExtractionApplicationService(
        model="fake-model",
        normalization_path=NORMALIZATION_PATH,
        client=FakeClient(payload),
        position_classifier=FakePositionClassifier(status="catalog_gap"),
        extraction_provider="fake",
        extraction_run_id="test-run",
        semantic_retry_attempts=0,
    )

    bundle = extractor.extract_one_v2(source)

    classification = bundle.normalized_result.job_classification
    assert classification is not None
    assert classification.classification_status == "catalog_gap"
    assert any(
        flag["issue_type"] == "job_classification_not_resolved"
        and flag["severity"] == "blocking"
        for flag in bundle.review_flags
    )


def test_result_ids_match_and_repeat_exactly():
    source = envelope()
    extractor = service(FakeClient())
    first = extractor.extract_one(source)
    second = extractor.extract_one(source)
    expected = build_document_id(source.source_platform, source.source_record_id, source.source_version)
    assert first.extraction_result.document_id == expected
    assert second.extraction_result.document_id == expected
    assert first.normalized_result.document_id == expected


@pytest.mark.parametrize(
    "token",
    ["ＡＩ", "ﬁ", "①", "㍿", "e\u0301", "é", "，全角："],
)
def test_nfkc_model_evidence_maps_back_to_exact_cleaned_text(token: str):
    raw_text = f"熟练使用 {token} Python"
    model_quote = normalize_jd_text(raw_text)
    bundle = service(FakeClient(valid_payload(model_quote))).extract_one(envelope(raw_text))
    evidence = bundle.extraction_result.requirements[0].evidence
    assert evidence.quote == clean_jd_text(raw_text)
    assert_all_evidence_is_exact(bundle, raw_text)


def test_model_receives_cleaned_source_blocks_while_raw_text_is_preserved():
    raw_text = "熟练使用 ﬁ、㍿、ＡＩ 与 Python"
    cleaned = clean_jd_text(raw_text)
    fake = RecordingClient(valid_payload(normalize_jd_text(cleaned)))
    source = envelope(raw_text)
    bundle = service(fake).extract_one(source)
    assert len(fake.user_prompts) == 1
    assert normalize_jd_text(cleaned) in fake.user_prompts[0]
    assert "ﬁ" not in fake.user_prompts[0]
    assert bundle.source_version == source.source_version
    assert_all_evidence_is_exact(bundle, raw_text)


def test_partial_nfkc_quote_is_exact_against_cleaned_text():
    payload = {
        "job_title": {
            "value": "f",
            "evidence": {"source_id": "src_0001", "quote": "f"},
        },
        "responsibilities": [],
        "requirements": [],
        "company_facts": [],
        "employment_facts": [],
    }
    bundle = service(FakeClient(payload)).extract_one(envelope("ﬁ"))
    assert bundle.cleaned_text == "fi"
    assert_all_evidence_is_exact(bundle, "ﬁ")


def test_every_extraction_evidence_collection_is_remapped_and_checked():
    raw_text = "ＡＩ工程师；负责ﬁ开发；熟悉㍿ Python；示例e\u0301公司；上海，浦东"
    model_text = normalize_jd_text(raw_text)
    blocks = [block["text"] for block in build_source_blocks(model_text)]
    payload = {
        "job_title": {"value": "AI工程师", "evidence": {"source_id": "src_0001", "quote": blocks[0]}},
        "responsibilities": [
            {"kind": "task", "modality": "required", "action": "负责fi开发", "evidence": {"source_id": "src_0002", "quote": blocks[1]}}
        ],
        "requirements": [
            {"kind": "skill", "modality": "required", "items": [{"name": "Python", "item_type": "programming_language"}], "evidence": {"source_id": "src_0003", "quote": blocks[2]}}
        ],
        "company_facts": [
            {"kind": "company_name", "value": "示例é公司", "evidence": {"source_id": "src_0004", "quote": blocks[3]}}
        ],
        "employment_facts": [
            {"kind": "location", "value": "上海,浦东", "evidence": {"source_id": "src_0005", "quote": blocks[4]}}
        ],
    }
    bundle = service(FakeClient(payload)).extract_one(envelope(raw_text))
    assert len(list(all_evidence(bundle))) == 5
    assert_all_evidence_is_exact(bundle, raw_text)


def test_text_map_rejects_non_exact_or_out_of_bounds_model_evidence():
    mapping = NFKCTextMap("ＡＩ")
    evidence = Evidence(source_id="src_0001", quote="AI", start=1, end=2, alignment="exact")
    with pytest.raises(ValueError):
        mapping.remap_evidence(evidence)


def _exact_model_evidence(raw_text: str, quote: str, occurrence: int) -> Evidence:
    model_text = normalize_jd_text(raw_text)
    model_quote = normalize_jd_text(quote)
    starts: list[int] = []
    offset = 0
    while True:
        start = model_text.find(model_quote, offset)
        if start < 0:
            break
        starts.append(start)
        offset = start + len(model_quote)
    start = starts[occurrence]
    return Evidence(
        source_id="src_0001",
        quote=model_quote,
        start=start,
        end=start + len(model_quote),
        alignment="exact",
        occurrence_index=99,
    )


def test_remap_recomputes_second_non_overlapping_occurrence():
    mapping = NFKCTextMap("重复内容；重复内容")
    evidence = _exact_model_evidence(mapping.raw_text, "重复内容", 1)

    mapping.remap_evidence(evidence)

    assert evidence.start == mapping.raw_text.rfind("重复内容")
    assert evidence.occurrence_index == 1


@pytest.mark.parametrize("occurrence", [0, 1, 2])
def test_remap_numbers_three_occurrences_from_left_to_right(occurrence: int):
    mapping = NFKCTextMap("目标|目标|目标")
    evidence = _exact_model_evidence(mapping.raw_text, "目标", occurrence)

    mapping.remap_evidence(evidence)

    assert evidence.occurrence_index == occurrence


@pytest.mark.parametrize("token", ["ＡＩ", "ﬁ", "e\u0301"])
def test_remap_recomputes_later_occurrence_after_nfkc_length_change(token: str):
    mapping = NFKCTextMap(f"{token}|{token}")
    evidence = _exact_model_evidence(mapping.raw_text, token, 1)

    mapping.remap_evidence(evidence)

    assert evidence.quote == token
    assert evidence.start == mapping.raw_text.rfind(token)
    assert evidence.occurrence_index == 1


def test_remap_rejects_exact_overlapping_span_outside_non_overlapping_search():
    mapping = NFKCTextMap("aaa")
    evidence = Evidence(
        source_id="src_0001",
        quote="aa",
        start=1,
        end=3,
        alignment="exact",
        occurrence_index=1,
    )

    with pytest.raises(ValueError, match="non-overlapping occurrence"):
        mapping.remap_evidence(evidence)


def test_source_version_is_preserved_at_the_application_boundary():
    source = envelope()
    values = source.model_dump()
    values["source_version"] = "2"
    changed = CrawlerJDEnvelopeV1(**values)
    assert service(FakeClient()).extract_one(changed).source_version == "2"


def test_invalid_schema_uses_stable_error_and_keeps_cause():
    payload = valid_payload()
    payload["requirements"] = "not-a-list"
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(FakeClient(payload)).extract_one(envelope())
    assert exc_info.value.code == ExtractionErrorCode.SCHEMA_VALIDATION_FAILED
    assert exc_info.value.__cause__ is not None


def test_hallucinated_evidence_uses_stable_evidence_error():
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(FakeClient(valid_payload("原文中不存在"))).extract_one(envelope())
    assert exc_info.value.code == ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidJSONError("private response", raw_response="private response"), ExtractionErrorCode.MODEL_INVALID_RESPONSE),
        (TimeoutError("private endpoint"), ExtractionErrorCode.MODEL_TIMEOUT),
        (ConnectionError("private endpoint"), ExtractionErrorCode.MODEL_UNAVAILABLE),
        (BusinessValidationError("private business data"), ExtractionErrorCode.BUSINESS_VALIDATION_FAILED),
        (RuntimeError("private unknown data"), ExtractionErrorCode.INTERNAL_ERROR),
    ],
)
def test_typed_failures_have_stable_safe_errors(error: BaseException, expected: ExtractionErrorCode):
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(RaisingClient(error)).extract_one(envelope())
    assert exc_info.value.code == expected
    assert exc_info.value.__cause__ is error
    assert "private" not in str(exc_info.value)


def test_timeout_client_uses_stable_error():
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(TimeoutClient()).extract_one(envelope())
    assert exc_info.value.code == ExtractionErrorCode.MODEL_TIMEOUT


def test_semantic_failure_has_a_distinct_stable_error():
    payload = valid_payload("熟练使用 Python和")
    payload["requirements"][0]["items"][0]["name"] = "Python和"
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(FakeClient(payload)).extract_one(envelope("熟练使用 Python和"))
    assert exc_info.value.code == ExtractionErrorCode.SEMANTIC_VALIDATION_FAILED


def test_normalization_failure_has_a_distinct_stable_error(monkeypatch):
    def fail_normalization(*args, **kwargs):
        raise RuntimeError("private normalization details")

    monkeypatch.setattr("src.application.extraction_service.normalize_extraction", fail_normalization)
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(FakeClient()).extract_one(envelope())
    assert exc_info.value.code == ExtractionErrorCode.NORMALIZATION_FAILED
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_contract_failure_has_a_distinct_stable_error(monkeypatch):
    def fail_contract(*args, **kwargs):
        raise ValueError("private contract details")

    monkeypatch.setattr("src.application.extraction_service.to_contract_extraction", fail_contract)
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(FakeClient()).extract_one(envelope())
    assert exc_info.value.code == ExtractionErrorCode.CONTRACT_VALIDATION_FAILED
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_non_envelope_input_is_rejected_at_the_application_boundary():
    with pytest.raises(JDExtractionApplicationError) as exc_info:
        service(FakeClient()).extract_one({"raw_text": "not validated"})  # type: ignore[arg-type]
    assert exc_info.value.code == ExtractionErrorCode.INVALID_ENVELOPE


def test_review_flags_are_carried_into_bundle():
    bundle = service(FakeClient()).extract_one(envelope())
    issue_types = {flag["issue_type"] for flag in bundle.review_flags}
    assert {"missing_job_title", "missing_company_fact", "missing_company_name"} <= issue_types
    assert all(flag["jd_id"] == bundle.extraction_result.document_id for flag in bundle.review_flags)


def test_injected_fake_is_the_only_model_client_used(monkeypatch):
    def forbid_real_client(*args, **kwargs):
        raise AssertionError("real client must not be constructed")

    monkeypatch.setattr("src.pipeline.DeepSeekClient", forbid_real_client)
    fake = FakeClient()
    service(fake).extract_one(envelope())
    assert fake.calls == 1


def test_retry_uses_the_same_injected_client_and_full_validation_chain():
    class InvalidThenValidClient:
        def __init__(self):
            self.calls = 0

        def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
            self.calls += 1
            payload = valid_payload() if self.calls > 1 else {**valid_payload(), "requirements": "invalid"}
            return DeepSeekResult(data=payload, raw_response="{}")

    fake = InvalidThenValidClient()
    bundle = service(fake, retries=1).extract_one(envelope())
    assert fake.calls == 2
    assert_all_evidence_is_exact(bundle, envelope().raw_text)


def test_explicit_responsibility_section_omission_triggers_existing_retry():
    raw_text = "岗位职责：负责开发服务。\n任职要求：熟悉 Python。"
    missing = valid_payload("任职要求：熟悉 Python。")
    missing["requirements"][0]["evidence"]["source_id"] = "src_0002"
    corrected = deepcopy(missing)
    corrected["responsibilities"] = [
        {
            "kind": "task",
            "modality": "required",
            "action": "负责开发服务",
            "evidence": {
                "source_id": "src_0001",
                "quote": "岗位职责：负责开发服务。",
            },
        }
    ]

    class MissingThenCorrectedClient:
        def __init__(self):
            self.calls = 0
            self.prompts: list[str] = []

        def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
            self.calls += 1
            self.prompts.append(user_prompt)
            payload = missing if self.calls == 1 else corrected
            return DeepSeekResult(
                data=deepcopy(payload),
                raw_response=json.dumps(payload, ensure_ascii=False),
            )

    fake = MissingThenCorrectedClient()
    bundle = service(fake, retries=1).extract_one(envelope(raw_text))

    assert fake.calls == 2
    assert len(bundle.extraction_result.responsibilities) == 1
    assert "missing_explicit_responsibilities" in fake.prompts[1]


def test_no_responsibility_is_required_without_an_explicit_section():
    fake = FakeClient(valid_payload("熟悉 Python"))

    bundle = service(fake, retries=1).extract_one(envelope("熟悉 Python"))

    assert fake.calls == 1
    assert bundle.extraction_result.responsibilities == []
