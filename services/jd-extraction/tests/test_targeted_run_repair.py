from copy import deepcopy

import pytest

from src.targeted_run_repair import apply_replacement, redact_extraction_attempts


def _annotation(value: str) -> dict:
    return {
        "document_id": "jd_000001",
        "responsibilities": [
            {
                "requirement_id": "resp_001",
                "description": value,
            }
        ],
    }


def test_remove_inserted_boss_is_exact_and_does_not_mutate_decision():
    annotation = _annotation("负责数boss据平台建设")
    decision = {
        "document_id": "jd_000001",
        "collection": "responsibilities",
        "object_id": "resp_001",
        "field_path": "description",
        "operation": "remove_inserted_boss",
    }
    original_decision = deepcopy(decision)

    revised = apply_replacement(annotation, decision)

    assert revised["responsibilities"][0]["description"] == "负责数据平台建设"
    assert annotation["responsibilities"][0]["description"] == "负责数boss据平台建设"
    assert decision == original_decision


def test_remove_inserted_boss_fails_when_selected_value_has_no_artifact():
    decision = {
        "document_id": "jd_000001",
        "collection": "responsibilities",
        "object_id": "resp_001",
        "field_path": "description",
        "operation": "remove_inserted_boss",
    }

    with pytest.raises(ValueError, match="does not contain"):
        apply_replacement(_annotation("负责数据平台建设"), decision)


def test_redact_extraction_attempts_removes_raw_response_content():
    attempts = [{"attempt": 1, "status": "passed", "raw_response": '{"ok":true}'}]

    redacted = redact_extraction_attempts(attempts)

    assert redacted == [{"attempt": 1, "status": "passed", "raw_response_present": True}]
    assert "raw_response" in attempts[0]
