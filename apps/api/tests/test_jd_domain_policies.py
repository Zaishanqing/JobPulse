from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.jd_error_mapping import jd_http_exception
from app.application.jd import JDApplicationError
from app.compatibility.jd import LEGACY_PARSE_FIELDS, parse_result_payload
from app.contexts.jd_lifecycle._applications.jd_support import (
    inflation_facts_from_result,
    _parse_facts_from_bundle,
)
from app.domain.jd_policies import (
    DuplicateCandidateFacts,
    DuplicateFacts,
    InflationFacts,
    JDParseEditCommand,
    JDPolicyViolation,
    ParseFacts,
    ParseReviewFacts,
    duplicate_action,
    evaluate_duplicate,
    evaluate_inflation,
    evaluate_parse,
    evaluate_parse_edit,
    inflation_action,
    is_similar_duplicate,
    requires_parse_review,
    validate_jd_raw_text,
)
from app.ports.jd_repository import JDParseResultDTO


def test_jd_raw_text_policy_rejects_text_over_shared_limit():
    from app.domain.input_limits import MAX_JD_TEXT_CHARS

    validate_jd_raw_text("x" * MAX_JD_TEXT_CHARS)
    with pytest.raises(JDPolicyViolation, match="must not exceed"):
        validate_jd_raw_text("x" * (MAX_JD_TEXT_CHARS + 1))


def test_near_copy_without_legacy_skill_keywords_is_high_duplicate_risk():
    source = "负责用户增长策略设计，分析漏斗数据，联动产品与运营团队推动实验落地。"
    candidate = "负责用户增长策略设计，分析漏斗数据，联动产品与运营团队推动实验执行。"

    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd", source, (DuplicateCandidateFacts("other", candidate, "source"),)
        )
    )

    assert decision.copy_risk_score >= 0.7
    assert decision.recommended_action == "downweight"
    assert len(decision.similar_jds) == 1
    assert decision.similar_jds[0].text_overlap > 0.8
    assert decision.similar_jds[0].skill_overlap == 0.0


def test_equal_length_unrelated_content_is_low_duplicate_risk():
    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd",
            "策划品牌活动并跟踪市场投放效果",
            (
                DuplicateCandidateFacts(
                    "other", "维护财务凭证并完成月度税务申报", "source"
                ),
            ),
        )
    )

    assert decision.copy_risk_score < 0.5
    assert decision.similar_jds == ()


def test_shared_skills_with_different_responsibilities_are_not_high_risk():
    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd",
            "使用 Python Docker 开发订单服务并优化数据库事务",
            (
                DuplicateCandidateFacts(
                    "other",
                    "使用 Python Docker 设计课程体系并指导学员实战",
                    "source",
                    required_skill_ids=("skill_python", "skill_docker"),
                ),
            ),
            required_skill_ids=("skill_python", "skill_docker"),
        )
    )

    assert decision.copy_risk_score < 0.7
    assert decision.recommended_action == "keep"


def test_no_candidate_has_zero_duplicate_risk_even_for_template_like_text():
    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd", "岗位职责：完成上级交办任务。任职要求：具备良好沟通能力。", ()
        )
    )

    assert decision.copy_risk_score == 0.0
    assert decision.similar_jds == ()
    assert decision.recommended_action == "keep"
    assert "mock" not in decision.reason
    assert "模板" not in decision.reason


def test_duplicate_text_normalization_handles_width_case_punctuation_and_space():
    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd",
            "ＰＹＴＨＯＮ，数据   分析！",
            (
                DuplicateCandidateFacts(
                    "other",
                    "python 数据分析",
                    "source",
                    required_skill_ids=("skill_python",),
                ),
            ),
            required_skill_ids=("skill_python",),
        )
    )

    assert decision.copy_risk_score == 1.0
    assert decision.similar_jds[0].text_overlap == 1.0
    assert decision.similar_jds[0].skill_overlap == 1.0
    assert decision.similar_jds[0].length_similarity == 1.0


def test_duplicate_skill_overlap_uses_normalized_ids_not_hardcoded_keywords():
    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd",
            "负责算法模型训练与前端页面开发，持续优化性能",
            (
                DuplicateCandidateFacts(
                    "other",
                    "负责算法模型训练与前端页面开发，持续优化质量",
                    "source",
                    required_skill_ids=("skill_pytorch", "skill_react"),
                ),
            ),
            required_skill_ids=("skill_pytorch", "skill_react"),
        )
    )

    assert decision.similar_jds[0].skill_overlap == 1.0


def test_duplicate_skill_overlap_consumes_required_and_bonus_skill_ids():
    decision = evaluate_duplicate(
        DuplicateFacts(
            "jd",
            "负责数据平台开发",
            (
                DuplicateCandidateFacts(
                    "other",
                    "负责数据平台开发",
                    "source",
                    bonus_skill_ids=("skill_kafka",),
                ),
            ),
            required_skill_ids=("skill_kafka",),
        )
    )

    assert decision.similar_jds[0].skill_overlap == 1.0


@pytest.mark.parametrize(
    ("score", "included"),
    [(0.49, False), (0.5, True)],
)
def test_duplicate_similarity_threshold_boundary(score: float, included: bool):
    assert is_similar_duplicate(score) is included


@pytest.mark.parametrize(
    ("score", "action"),
    [(0.69, "keep"), (0.7, "downweight")],
)
def test_duplicate_action_threshold_boundary(score: float, action: str):
    assert duplicate_action(score) == action


def test_junior_with_many_ordinary_skills_without_senior_scope_is_low_risk():
    decision = evaluate_inflation(
        InflationFacts(
            title="初级工程师",
            required_skill_names=tuple(f"skill-{index}" for index in range(10)),
            career_level="junior",
            min_experience_years=1,
            responsibilities=("完成功能开发、单元测试和日常缺陷修复",),
        )
    )

    assert decision.inflation_score == 0.1
    assert decision.recommended_action == "keep"
    assert decision.abnormal_skills == ()
    assert decision.mismatch_reasons == (
        "skill_breadth: 必备技能范围较广，仅作为弱信号",
    )


def test_low_seniority_and_experience_with_senior_scope_is_high_risk():
    decision = evaluate_inflation(
        InflationFacts(
            title="初级工程师",
            required_skill_names=("Python", "SQL", "Docker"),
            career_level="junior",
            min_experience_years=1,
            responsibilities=(
                "主导大型系统的整体架构和技术选型，带领团队完成交付",
            ),
        )
    )

    assert decision.inflation_score == 0.9
    assert decision.recommended_action == "manual_review"
    assert tuple(reason.split(":", 1)[0] for reason in decision.mismatch_reasons) == (
        "seniority_mismatch",
        "experience_mismatch",
        "ownership_mismatch",
        "leadership_mismatch",
    )


def test_senior_with_broad_skills_and_architecture_scope_is_low_risk():
    decision = evaluate_inflation(
        InflationFacts(
            title="高级架构师",
            required_skill_names=tuple(f"skill-{index}" for index in range(12)),
            career_level="senior",
            min_experience_years=8,
            responsibilities=("主导整体架构设计、技术选型和大型系统交付",),
            leadership_signals=("leadership_scope:technical_lead",),
        )
    )

    assert decision.inflation_score == 0.1
    assert decision.recommended_action == "keep"
    assert not any(
        reason.startswith(("seniority_mismatch", "experience_mismatch"))
        for reason in decision.mismatch_reasons
    )


def test_junior_with_legacy_advanced_skill_keyword_alone_is_not_inflation():
    decision = evaluate_inflation(
        InflationFacts("初级工程师", ("RAG",), career_level="junior")
    )

    assert decision.inflation_score == 0.0
    assert decision.recommended_action == "keep"
    assert decision.mismatch_reasons == ()


def test_inflation_facts_reuse_normalization_and_extraction_signals():
    facts = inflation_facts_from_result(
        SimpleNamespace(title="岗位标题"),
        SimpleNamespace(
            position_title="解析标题",
            normalized_result={
                "job_classification": {
                    "career_level": "junior",
                    "leadership_scope": "team",
                }
            },
            extraction_result={
                "requirements": [
                    {"kind": "experience", "minimum_years": 1.0}
                ]
            },
            experience="1 年以上",
            required_skills=[SimpleNamespace(raw_skill="Python")],
            responsibilities=("主导技术选型并带领团队",),
        ),
    )

    assert facts.title == "岗位标题"
    assert facts.career_level == "junior"
    assert facts.min_experience_years == 1.0
    assert facts.required_skill_names == ("Python",)
    assert facts.responsibilities == ("主导技术选型并带领团队",)
    assert facts.leadership_signals == ("leadership_scope:team",)


@pytest.mark.parametrize(
    ("score", "action"),
    [(0.69, "keep"), (0.7, "manual_review")],
)
def test_inflation_action_threshold_boundary(score: float, action: str):
    assert inflation_action(score) == action


def _parse_bundle(
    *,
    exact_evidence: bool = True,
    complete: bool = True,
    scenarios: tuple[str, ...] = (),
    scenario_evidence: bool = False,
):
    evidence = SimpleNamespace(alignment="exact" if exact_evidence else "inferred")
    skill = SimpleNamespace(name="Python")
    requirement = SimpleNamespace(
        kind="skill", items=(skill,), evidence=evidence
    )
    classification = SimpleNamespace(
        industry_context_codes=scenarios,
        evidence_refs=("evidence:scenario",) if scenario_evidence else (),
    )
    return SimpleNamespace(
        schema_version="extracted-jd-bundle-v2",
        cleaned_text="负责后端服务与数据处理",
        extraction_provider="test-provider",
        need_review=False,
        review_flags=[],
        extraction_result=SimpleNamespace(
            schema_version="v2",
            job_title=SimpleNamespace(text="Python 工程师", evidence=evidence),
            responsibilities=(SimpleNamespace(evidence=evidence),) if complete else (),
            requirements=(requirement,) if complete else (),
            company_facts=(),
            employment_facts=(),
        ),
        normalized_result=SimpleNamespace(
            schema_version="v2",
            normalized_requirements=(
                SimpleNamespace(
                    normalized_skills=(
                        SimpleNamespace(
                            source_name="Python", resolution_status="resolved"
                        ),
                    )
                ),
            )
            if complete
            else (),
            unresolved_items=() if complete else (SimpleNamespace(),),
            job_classification=classification,
        ),
    )


def test_high_quality_bundle_is_not_forced_into_review():
    decision = evaluate_parse(_parse_facts_from_bundle(_parse_bundle()))

    assert decision.parse_quality_score == 1.0
    assert decision.parse_confidence == 1.0
    assert decision.quality_level == "high"
    assert decision.need_review is False
    assert decision.review_priority is None


def test_poor_evidence_and_required_coverage_routes_to_priority_review():
    decision = evaluate_parse(
        _parse_facts_from_bundle(
            _parse_bundle(exact_evidence=False, complete=False)
        )
    )

    assert decision.parse_quality_score < 0.6
    assert decision.quality_level == "low"
    assert decision.need_review is True
    assert decision.review_priority == "high"


def test_backend_text_does_not_inject_business_scenario():
    facts = _parse_facts_from_bundle(_parse_bundle())
    assert "后端" in facts.raw_text

    assert evaluate_parse(facts).business_scenarios == ()


def test_evidence_supported_normalized_scenario_is_preserved():
    facts = _parse_facts_from_bundle(
        _parse_bundle(
            scenarios=("E_COMMERCE",),
            scenario_evidence=True,
        )
    )

    assert evaluate_parse(facts).business_scenarios == ("E_COMMERCE",)


def test_persisted_normalization_outcome_overrides_provider_skill_claim():
    facts = _parse_facts_from_bundle(
        _parse_bundle(),
        {
            "normalized_requirements": [
                {
                    "source_name": "Python",
                    "resolution_status": "unresolved",
                }
            ],
            "unresolved_items": [
                {
                    "severity": "blocking",
                    "source_value": "Python",
                }
            ],
            "job_classification": {
                "industry_context_codes": [],
                "evidence_refs": [],
            },
        },
    )
    decision = evaluate_parse(facts)

    assert facts.normalization_coverage == 0.0
    assert facts.provider_requires_review is True
    assert decision.need_review is True


@pytest.mark.parametrize(
    ("confidence", "need_review"),
    [(0.849999, True), (0.85, False)],
)
def test_parse_review_threshold_boundary(confidence: float, need_review: bool):
    assert requires_parse_review(confidence) is need_review


@pytest.mark.parametrize(
    ("raw_score", "display_score", "need_review", "quality_level"),
    [
        (0.844, 0.84, True, "medium"),
        (0.846, 0.85, True, "medium"),
        (0.8499, 0.85, True, "medium"),
        (0.8500, 0.85, False, "high"),
        (0.8501, 0.85, False, "high"),
    ],
)
def test_parse_review_gate_uses_unrounded_score(
    raw_score: float,
    display_score: float,
    need_review: bool,
    quality_level: str,
):
    decision = evaluate_parse(
        ParseFacts(
            required_field_coverage=(raw_score - 0.7) / 0.3,
            exact_evidence_ratio=1.0,
            unresolved_ratio=0.0,
            normalization_coverage=1.0,
            schema_provider_valid=True,
        )
    )

    assert decision.parse_quality_score == display_score
    assert decision.need_review is need_review
    assert decision.quality_level == quality_level


def test_typed_edit_command_drives_review_decision():
    confidence = JDParseEditCommand(
        changed_fields=frozenset({"parse_confidence"}),
        parse_confidence=0.9,
    )
    versioned = JDParseEditCommand(
        changed_fields=frozenset({"extraction_result"}),
        extraction_result={"schema_version": "v2"},
    )

    assert evaluate_parse_edit(ParseReviewFacts(True, "draft"), confidence).need_review is False
    decision = evaluate_parse_edit(ParseReviewFacts(False, "published"), versioned)
    assert decision.need_review is True
    assert decision.workflow_status == "draft"

    explicit = JDParseEditCommand(
        changed_fields=frozenset({"parse_confidence", "need_review"}),
        parse_confidence=0.1,
        need_review=False,
    )
    assert evaluate_parse_edit(ParseReviewFacts(True, "draft"), explicit).need_review is False


@pytest.mark.parametrize(
    "command",
    [
        JDParseEditCommand(changed_fields=frozenset({"position_title"})),
        JDParseEditCommand(changed_fields=frozenset({"responsibilities"})),
    ],
)
def test_typed_edit_command_rejects_legacy_fields(command: JDParseEditCommand):
    with pytest.raises(JDPolicyViolation, match="Legacy compatibility fields are read-only"):
        evaluate_parse_edit(ParseReviewFacts(True, "draft"), command)


def test_typed_edit_command_rejects_unknown_and_invalid_confidence_fields():
    with pytest.raises(JDPolicyViolation, match="Unsupported JD parse edit fields"):
        JDParseEditCommand(changed_fields=frozenset({"unknown"}))
    with pytest.raises(JDPolicyViolation, match="parse_confidence must be between 0 and 1"):
        JDParseEditCommand(
            changed_fields=frozenset({"parse_confidence"}),
            parse_confidence=1.1,
        )


@pytest.mark.parametrize(
    ("error_code", "status_code"),
    [
        ("forbidden", 403),
        ("not_found", 404),
        ("conflict", 409),
        ("invalid", 422),
    ],
)
def test_http_error_mapper_preserves_status_and_message(
    error_code: str, status_code: int
):
    error = jd_http_exception(JDApplicationError(error_code, "stable message"))

    assert error.status_code == status_code
    assert error.detail == "stable message"


def test_legacy_compatibility_mapper_preserves_response_fields():
    now = datetime.now(timezone.utc)
    result = JDParseResultDTO(
        "parse-id", "jd-id", "岗位", ["职责"], [], [], None, None, None,
        [], [], 0.85, True, {"schema_version": "v2"},
        {"schema_version": "v2"}, None, "v2", "v2", "draft", now, now,
    )

    payload = parse_result_payload(result)

    assert payload["position_title"] == "岗位"
    assert payload["responsibilities"] == ["职责"]
    assert payload["compatibility"] == {
        "legacy_fields": list(LEGACY_PARSE_FIELDS),
        "source": "versioned_domain_adapter",
    }
    assert payload["extraction_status"] == "available"
    assert payload["normalization_status"] == "available"
