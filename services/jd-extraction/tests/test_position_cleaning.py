from __future__ import annotations

from collections import Counter

from src.position_cleaning import (
    CleaningDecision,
    decide_record,
    promote_classification,
    read_jsonl,
)


CATALOG = {
    "catalog_version": "position-taxonomy.v3.0.0",
    "_position_by_code": {
        "BACKEND_ENGINEER": {
            "code": "BACKEND_ENGINEER",
            "name": "后端开发工程师",
            "family_code": "SOFTWARE_ENGINEERING",
        }
    },
    "_family_by_code": {
        "SOFTWARE_ENGINEERING": {
            "code": "SOFTWARE_ENGINEERING",
            "name": "软件研发",
        }
    },
}
POLICY = {
    "policy_version": "position-cleaning.v1",
    "near_threshold": {
        "minimum_top_score": 0.7,
        "minimum_margin": 0.05,
        "minimum_evidence_refs": 1,
    },
    "technical_title_patterns": ["开发", "工程师", "Java"],
    "obvious_out_of_scope_title_patterns": ["销售顾问", "门店销售"],
}


def _normalized(
    *,
    status: str,
    title: str,
    candidates: list[dict[str, object]],
    evidence_refs: list[str],
) -> dict[str, object]:
    return {
        "document_id": "jd-1",
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": title,
            "candidate_positions": candidates,
            "confidence": 0.72,
            "classification_status": status,
            "review_reason_codes": ["ambiguous_candidates"],
            "evidence_refs": evidence_refs,
        },
        "normalized_requirements": [],
        "unresolved_items": [],
    }


def test_near_threshold_ambiguous_is_promoted() -> None:
    normalized = _normalized(
        status="ambiguous",
        title="Java开发工程师",
        candidates=[
            {"position_code": "BACKEND_ENGINEER", "score": 0.72},
            {"position_code": "FULLSTACK_ENGINEER", "score": 0.65},
        ],
        evidence_refs=["req_001"],
    )

    decision = decide_record(
        {"job_title": {"value": "Java开发工程师"}},
        normalized,
        policy=POLICY,
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "promote",
        "near_threshold_with_evidence",
        "BACKEND_ENGINEER",
    )
    promoted = promote_classification(
        normalized,
        decision=decision,
        catalog=CATALOG,
        policy=POLICY,
    )
    classification = promoted["job_classification"]
    assert classification["classification_status"] == "manually_confirmed"
    assert classification["position_code"] == "BACKEND_ENGINEER"
    assert classification["family_code"] == "SOFTWARE_ENGINEERING"


def test_close_candidates_are_not_promoted() -> None:
    normalized = _normalized(
        status="ambiguous",
        title="Java/C++开发",
        candidates=[
            {"position_code": "BACKEND_ENGINEER", "score": 0.74},
            {"position_code": "FULLSTACK_ENGINEER", "score": 0.72},
        ],
        evidence_refs=["req_001"],
    )

    decision = decide_record(
        {"job_title": {"value": "Java/C++开发"}},
        normalized,
        policy=POLICY,
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "quarantine",
        "unresolved_ambiguous",
    )


def test_obvious_nontechnical_out_of_scope_is_deleted() -> None:
    normalized = _normalized(
        status="out_of_scope",
        title="门店销售顾问",
        candidates=[],
        evidence_refs=["req_001"],
    )

    decision = decide_record(
        {"job_title": {"value": "门店销售顾问"}},
        normalized,
        policy=POLICY,
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "delete",
        "obvious_non_technical_title",
    )


def test_technical_out_of_scope_is_quarantined() -> None:
    normalized = _normalized(
        status="out_of_scope",
        title="Java开发模块负责人",
        candidates=[],
        evidence_refs=["req_001"],
    )

    decision = decide_record(
        {"job_title": {"value": "Java开发模块负责人"}},
        normalized,
        policy=POLICY,
        alias_codes={},
        resolved_titles={"java开发模块负责人": Counter()},
    )

    assert decision == CleaningDecision(
        "quarantine",
        "possible_technical_out_of_scope",
    )


def test_catalog_scope_product_role_is_not_deleted() -> None:
    normalized = _normalized(
        status="out_of_scope",
        title="高级产品经理",
        candidates=[],
        evidence_refs=["req_001"],
    )

    decision = decide_record(
        {"job_title": {"value": "高级产品经理"}},
        normalized,
        policy={**POLICY, "technical_title_patterns": ["产品"]},
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "quarantine",
        "possible_technical_out_of_scope",
    )


def test_strong_title_promotes_out_of_scope_with_title_evidence() -> None:
    normalized = _normalized(
        status="out_of_scope",
        title="高级Java开发工程师",
        candidates=[],
        evidence_refs=[],
    )
    policy = {
        **POLICY,
        "strong_title_rules": [
            {
                "position_code": "BACKEND_ENGINEER",
                "required_any": ["java开发"],
                "forbidden_any": [],
            }
        ],
    }

    decision = decide_record(
        {
            "job_title": {
                "value": "高级Java开发工程师",
                "evidence": {"source_id": "src_title"},
            }
        },
        normalized,
        policy=policy,
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "promote",
        "strong_catalog_title_with_annotation_evidence",
        "BACKEND_ENGINEER",
    )


def test_strong_title_must_match_ambiguous_candidate() -> None:
    normalized = _normalized(
        status="ambiguous",
        title="Java开发工程师",
        candidates=[
            {"position_code": "FULLSTACK_ENGINEER", "score": 0.68},
            {"position_code": "FRONTEND_ENGINEER", "score": 0.64},
        ],
        evidence_refs=["req_001"],
    )
    policy = {
        **POLICY,
        "strong_title_rules": [
            {
                "position_code": "BACKEND_ENGINEER",
                "required_any": ["java开发"],
                "forbidden_any": [],
            }
        ],
    }

    decision = decide_record(
        {
            "job_title": {
                "value": "Java开发工程师",
                "evidence": {"source_id": "src_title"},
            }
        },
        normalized,
        policy=policy,
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "quarantine",
        "unresolved_ambiguous",
    )


def test_candidate_title_consensus_promotes_matching_top_candidate() -> None:
    normalized = _normalized(
        status="ambiguous",
        title="Java",
        candidates=[
            {"position_code": "BACKEND_ENGINEER", "score": 0.66},
            {"position_code": "FULLSTACK_ENGINEER", "score": 0.64},
        ],
        evidence_refs=["req_001"],
    )

    decision = decide_record(
        {"job_title": {"value": "Java"}},
        normalized,
        policy=POLICY,
        alias_codes={},
        resolved_titles={},
        candidate_title_consensus={"java": "BACKEND_ENGINEER"},
    )

    assert decision == CleaningDecision(
        "promote",
        "candidate_title_consensus_matches_top_candidate",
        "BACKEND_ENGINEER",
    )


def test_single_candidate_catalog_gap_with_evidence_is_promoted() -> None:
    normalized = _normalized(
        status="catalog_gap",
        title="机器人仿真工程师",
        candidates=[
            {"position_code": "ROBOTICS_ENGINEER", "score": 0.62},
        ],
        evidence_refs=["req_001"],
    )
    policy = {
        **POLICY,
        "single_candidate_catalog_gap": {
            "minimum_score": 0.4,
            "minimum_evidence_refs": 1,
        },
    }

    decision = decide_record(
        {"job_title": {"value": "机器人仿真工程师"}},
        normalized,
        policy=policy,
        alias_codes={},
        resolved_titles={},
    )

    assert decision == CleaningDecision(
        "promote",
        "single_candidate_catalog_gap_with_evidence",
        "ROBOTICS_ENGINEER",
    )


def test_read_jsonl_preserves_unicode_line_separator(
    tmp_path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(
        '{"document_id":"jd-1","raw_text":"第一段\u2028第二段"}\n',
        encoding="utf-8",
    )

    assert read_jsonl(path) == [
        {"document_id": "jd-1", "raw_text": "第一段\u2028第二段"}
    ]
