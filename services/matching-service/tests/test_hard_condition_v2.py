"""Unit tests for Hard Condition Matching v2 semantics."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from app.domain.degree_levels import (
    DEGREE_LEVELS,
    degree_rank,
    normalize_degree,
)
from app.domain.matching import MatchingAlgorithmConfig, evaluate_hard_constraints
from app.domain.profiles import CVMatchProfile, PositionMatchProfile


def _evidence(source_id: str, quote: str) -> dict:
    return {
        "source_id": source_id,
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


def _cv(cv_payload: dict, **updates) -> CVMatchProfile:
    payload = copy.deepcopy(cv_payload)
    payload.update(updates)
    return CVMatchProfile.model_validate(payload)


def _position(position_payload: dict, hard_conditions: list[dict]) -> PositionMatchProfile:
    payload = copy.deepcopy(position_payload)
    payload["hard_conditions"] = hard_conditions
    return PositionMatchProfile.model_validate(payload)


def _hard(
    condition_id: str,
    condition_type: str,
    operator: str,
    value: str,
) -> dict:
    return {
        "condition_id": condition_id,
        "condition_type": condition_type,
        "operator": operator,
        "value": value,
        "resolution_status": "resolved",
        "evidence_refs": [],
    }


def _result(results, requirement_id: str):
    return next(item for item in results if item.requirement_id == requirement_id)


def test_degree_normalization_aliases() -> None:
    assert normalize_degree("associate") == "associate"
    assert normalize_degree("大专") == "associate"
    assert normalize_degree("专科") == "associate"
    assert normalize_degree("bachelor") == "bachelor"
    assert normalize_degree("本科") == "bachelor"
    assert normalize_degree("学士") == "bachelor"
    assert normalize_degree("master") == "master"
    assert normalize_degree("硕士") == "master"
    assert normalize_degree("研究生") == "master"
    assert normalize_degree("doctor") == "doctor"
    assert normalize_degree("phd") == "doctor"
    assert normalize_degree("博士") == "doctor"
    assert normalize_degree("postdoc") == "postdoc"
    assert normalize_degree("博士后") == "postdoc"
    assert degree_rank("本科") < degree_rank("硕士") < degree_rank("博士")
    assert DEGREE_LEVELS == (
        "high_school",
        "associate",
        "bachelor",
        "master",
        "doctor",
        "postdoc",
    )


def test_education_alias_business_pass_and_single_grounding(
    cv_payload, position_payload
) -> None:
    cv = _cv(
        cv_payload,
        education=[
            {
                "education_id": "edu:1",
                "degree_level": "bachelor",
                "field_of_study": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("edu:1", "本科")],
            },
            {
                "education_id": "edu:2",
                "degree_level": "master",
                "field_of_study": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("edu:2", "硕士")],
            },
        ],
    )
    position = _position(
        position_payload,
        [_hard("cond:edu", "education", "at_least", "bachelor")],
    )
    results = evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig())
    result = _result(results, "cond:edu")
    assert result.status == "pass"
    assert result.candidate_value == "master"
    assert result.confidence == 0.7
    # Business semantics do not depend on the number of supporting evidences.
    assert len(result.candidate_evidence) == 1
    assert result.candidate_evidence[0].source_id == "edu:2"


def test_education_associate_and_dazhuan(cv_payload, position_payload) -> None:
    cv = _cv(
        cv_payload,
        education=[
            {
                "education_id": "edu:1",
                "degree_level": "associate",
                "field_of_study": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("edu:1", "大专")],
            }
        ],
    )
    position_pass = _position(
        position_payload,
        [_hard("cond:edu", "education", "at_least", "associate")],
    )
    position_fail = _position(
        position_payload,
        [_hard("cond:edu", "education", "at_least", "bachelor")],
    )
    assert _result(
        evaluate_hard_constraints(cv, position_pass, MatchingAlgorithmConfig()),
        "cond:edu",
    ).status == "pass"
    assert _result(
        evaluate_hard_constraints(cv, position_fail, MatchingAlgorithmConfig()),
        "cond:edu",
    ).status == "fail"


def test_experience_dates_drive_pass_fail_unknown(cv_payload, position_payload) -> None:
    cv = _cv(
        cv_payload,
        work_experiences=[
            {
                "experience_id": "exp:1",
                "kind": "work",
                "role": None,
                "responsibilities": [],
                "business_scenarios": [],
                "tool_skill_ids": [],
                "start_date": date(2021, 1, 1),
                "end_date": date(2023, 2, 1),
                "evidence_refs": [_evidence("exp:1", "work evidence 1")],
            }
        ],
    )
    position_pass = _position(
        position_payload,
        [_hard("cond:exp", "experience", "at_least", "2 years")],
    )
    position_fail = _position(
        position_payload,
        [_hard("cond:exp", "experience", "at_least", "3 years")],
    )
    assert _result(
        evaluate_hard_constraints(cv, position_pass, MatchingAlgorithmConfig()),
        "cond:exp",
    ).status == "pass"
    assert _result(
        evaluate_hard_constraints(cv, position_fail, MatchingAlgorithmConfig()),
        "cond:exp",
    ).status == "fail"

    open_ended = _cv(
        cv_payload,
        work_experiences=[
            {
                "experience_id": "exp:1",
                "kind": "work",
                "role": None,
                "responsibilities": [],
                "business_scenarios": [],
                "tool_skill_ids": [],
                "start_date": date(2021, 1, 1),
                "end_date": None,
                "evidence_refs": [_evidence("exp:1", "2021-01 至今")],
            }
        ],
    )
    assert _result(
        evaluate_hard_constraints(open_ended, position_pass, MatchingAlgorithmConfig()),
        "cond:exp",
    ).status == "unknown"


def test_certificate_one_of_with_grounding(cv_payload, position_payload) -> None:
    cv = _cv(
        cv_payload,
        certificates=[
            {
                "credential_id": "cert:1",
                "name": "PMP",
                "level": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("cert:1", "PMP")],
            }
        ],
    )
    position = _position(
        position_payload,
        [_hard("cond:cert", "certificate", "one_of", "PMP|CPA")],
    )
    result = _result(
        evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig()),
        "cond:cert",
    )
    assert result.status == "pass"
    assert len(result.candidate_evidence) == 1
    assert result.candidate_evidence[0].source_id == "cert:1"


def test_location_one_of_with_grounding(cv_payload, position_payload) -> None:
    feature = {
        "feature_id": "loc:1",
        "document_id": cv_payload["cv_id"],
        "side": "cv",
        "feature_type": "location",
        "source_object_id": "loc:1",
        "source_scope": "location",
        "canonical_id": None,
        "canonical_name": "北京",
        "raw_text": "北京",
        "vector_text": "北京",
        "requirement_modality": None,
        "candidate_level": None,
        "structured_values": {},
        "resolution_status": "resolved",
        "evidence_refs": [_evidence("loc:1", "北京")],
        "taxonomy_version": cv_payload["taxonomy_version"],
        "derivation_version": "test.v1",
    }
    cv = _cv(
        cv_payload,
        match_features=list(cv_payload.get("match_features", [])) + [feature],
    )
    position = _position(
        position_payload,
        [_hard("cond:loc", "location", "one_of", "北京|上海")],
    )
    result = _result(
        evaluate_hard_constraints(cv, position, MatchingAlgorithmConfig()),
        "cond:loc",
    )
    assert result.status == "pass"
    assert len(result.candidate_evidence) == 1
    assert result.candidate_evidence[0].source_id == "loc:1"


def test_education_business_status_ignores_evidence_count(
    cv_payload, position_payload
) -> None:
    position = _position(
        position_payload,
        [_hard("cond:edu", "education", "at_least", "bachelor")],
    )
    one_evidence = _cv(
        cv_payload,
        education=[
            {
                "education_id": "edu:1",
                "degree_level": "bachelor",
                "field_of_study": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("edu:1", "本科")],
            }
        ],
    )
    two_evidence = _cv(
        cv_payload,
        education=[
            {
                "education_id": "edu:1",
                "degree_level": "bachelor",
                "field_of_study": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("edu:1", "本科")],
            },
            {
                "education_id": "edu:2",
                "degree_level": "master",
                "field_of_study": None,
                "resolution_status": "resolved",
                "evidence_refs": [_evidence("edu:2", "硕士")],
            },
        ],
    )
    result_one = _result(
        evaluate_hard_constraints(one_evidence, position, MatchingAlgorithmConfig()),
        "cond:edu",
    )
    result_two = _result(
        evaluate_hard_constraints(two_evidence, position, MatchingAlgorithmConfig()),
        "cond:edu",
    )
    assert result_one.status == result_two.status == "pass"
    assert result_one.confidence == result_two.confidence == 0.7
    assert len(result_one.candidate_evidence) == 1
    assert len(result_two.candidate_evidence) == 1
