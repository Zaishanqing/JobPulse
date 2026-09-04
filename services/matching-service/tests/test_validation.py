from __future__ import annotations

from app.application.validation import ProfileValidationService
from app.domain.privacy import find_pii


def test_approved_complete_cv_is_ready(cv_payload):
    result = ProfileValidationService().validate_cv(cv_payload)

    assert result.profile_status == "ready"
    assert result.profile_version is not None
    assert result.validation_errors == ()


def test_unresolved_is_review_required_not_missing(cv_payload):
    cv_payload["skills"][0].update(
        {
            "skill_id": None,
            "canonical_name": None,
            "resolution_status": "unresolved",
            "verification_status": "unresolved",
        }
    )
    cv_payload["unresolved_items"] = [
        {
            "item_id": "skill_raw_1",
            "item_type": "skill",
            "raw_value": "内部平台技术",
            "reason": "taxonomy_no_match",
            "evidence_refs": [],
        }
    ]

    result = ProfileValidationService().validate_cv(cv_payload)

    assert result.profile_status == "review_required"
    assert result.validation_errors == ()
    assert result.unresolved_items[0].reason == "taxonomy_no_match"
    assert result.profile_version is not None


def test_missing_required_field_is_invalid_not_unresolved(cv_payload):
    del cv_payload["skills"]

    result = ProfileValidationService().validate_cv(cv_payload)

    assert result.profile_status == "invalid"
    assert result.unresolved_items == ()
    assert any(error.error_type == "missing" for error in result.validation_errors)


def test_pii_field_and_pii_value_are_rejected(cv_payload, clone):
    with_field = clone(cv_payload)
    with_field["phone"] = "opaque"
    field_result = ProfileValidationService().validate_cv(with_field)

    with_value = clone(cv_payload)
    with_value["evidence_refs"][0]["quote"] = "contact candidate@example.com"
    value_result = ProfileValidationService().validate_cv(with_value)

    assert field_result.profile_status == "invalid"
    assert field_result.validation_errors[0].error_type == "pii_forbidden"
    assert value_result.profile_status == "invalid"
    assert value_result.validation_errors[0].error_type == "pii_forbidden"


def test_position_quality_gate(position_payload):
    position_payload["quality_context"]["status"] = "review_required"

    result = ProfileValidationService().validate_position(position_payload)

    assert result.profile_status == "review_required"
    assert result.validation_errors == ()


def test_source_version_is_authoritative(cv_payload):
    cv_payload["source_version"] = "cv-source.v2"

    result = ProfileValidationService().validate_cv(cv_payload)

    assert result.profile_status == "ready"
    assert result.profile_version == "cv-source.v2"
    assert result.validation_errors == ()


# ── structured-identifier PII regression tests ──────────────────────────

def test_structured_document_id_not_flagged_as_phone():
    """Versioned document_id values must not be falsely flagged as phone-like."""
    payload = {"document_id": "cv-extraction-2026-0728-001"}
    assert find_pii(payload) == ()


def test_uuid_and_namespaced_ids_pass_pii_check():
    """UUID-format and namespace-prefixed IDs are valid structured identifiers."""
    payload = {
        "document_id": "550e8400-e29b-41d4-a716-446655440000",
        "profile_id": "capability:python",
        "snapshot_id": "verify_20260727_001",
    }
    assert find_pii(payload) == ()


def test_uuid_digit_groups_inside_version_metadata_are_not_phone_pii():
    """UUID numeric groups inside source/profile versions must not make CI flaky."""
    version = "snapshot=550e8400-1234-5678-9012-446655440000:1:"
    assert find_pii({"source_version": version, "profile_version": version}) == ()


def test_bare_phone_number_as_document_id_rejected():
    """A pure digit phone number masquerading as document_id is still rejected."""
    payload = {"document_id": "13800138000"}
    violations = find_pii(payload)
    assert len(violations) == 1
    assert violations[0].reason == "phone-like value is not allowed"


def test_phone_prefix_wrapped_id_rejected():
    """Semantic contact prefixes (phone_, contact-, …) are rejected even in ID fields."""
    for value in ("phone_13800138000", "contact-4155552671", "mobile_8613800138000"):
        violations = find_pii({"document_id": value})
        assert violations, f"Expected rejection for {value!r}"
        assert "contact" in violations[0].reason or "phone" in violations[0].reason


def test_email_in_id_field_rejected():
    """An email address inside a document_id field must still be rejected."""
    violations = find_pii({"document_id": "alice@example.com"})
    assert violations
    assert any("email" in item.reason for item in violations)


def test_free_text_phone_still_rejected():
    """Free-text fields must still undergo full PII detection — no weakening."""
    payload = {"evidence": {"quote": "call me at 13800138000"}}
    violations = find_pii(payload)
    assert violations
    assert any("phone" in item.reason for item in violations)


def test_existing_pii_detections_not_regressed():
    """Existing PII guardrails (email, phone, address, name) must remain intact."""
    unsafe = [
        "alice@example.com",
        "+1 415 555 2671",
        "地址：上海市浦东新区世纪大道100号",
        "姓名：张三",
        "Contact person: John Smith",
    ]
    for text in unsafe:
        violations = find_pii(text)
        assert violations, f"Expected PII violation for {text!r} but got none"


def test_id_without_expected_format_is_rejected():
    """A value that is not a recognized ID format triggers a format violation."""
    # Random gibberish that isn't a UUID, prefixed, or letter-containing ID
    violations = find_pii({"document_id": "!!!not-a-valid-id!!!"})
    assert violations
    assert any("format" in item.reason for item in violations)
