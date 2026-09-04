from __future__ import annotations

from app.domain.candidate_admission import (
    AdmissionEvidence,
    assess_candidate_admission,
    load_candidate_admission_policy,
)


def _policy():
    return load_candidate_admission_policy()


def test_policy_weights_and_thresholds_are_config_file_driven():
    policy = _policy()
    assert policy["policy_version"] == "candidate-admission-policy.v2"
    assert policy["weights"] == {
        "title": 0.30,
        "responsibility": 0.50,
        "skill": 0.20,
    }
    assert policy["thresholds"]["admit_combined"] == 0.60


def test_skill_only_ai_signal_is_rejected_off_target():
    policy = _policy()
    cases = [
        (
            "Retail Marketing Manager-UK",
            ("Offline experience activity planning",),
            ("RAG",),
        ),
        (
            "Kuwait Experience Operation （Grocery)",
            ("负责闪购业务履约全流程体验管理",),
            ("LLM",),
        ),
        (
            "Fulfillment Capacity Strategy Expert运力运营专家",
            ("Be responsible for fulfillment capacity levels",),
            ("LLM", "RAG"),
        ),
    ]
    for title, responsibilities, skills in cases:
        decision = assess_candidate_admission(
            AdmissionEvidence((title,), responsibilities, skills),
            policy,
        )
        assert decision.decision == "REJECT_OFF_TARGET"
        assert decision.title_score == 0.0
        assert decision.responsibility_score == 0.0
        assert decision.skill_score == 1.0
        assert decision.skill_only_weak is True


def test_title_related_but_responsibility_unrelated_stays_review_required():
    decision = assess_candidate_admission(
        AdmissionEvidence(
            ("AI Agent产品经理",),
            ("负责线下门店活动策划与社区运营",),
            ("LLM",),
        ),
        _policy(),
    )
    assert decision.title_score == 1.0
    assert decision.responsibility_score == 0.0
    assert decision.skill_score == 1.0
    assert decision.decision == "REVIEW_REQUIRED"


def test_responsibility_strongly_related_is_admitted():
    decision = assess_candidate_admission(
        AdmissionEvidence(
            ("运营专员",),
            ("负责大模型数据构建、指令微调与模型评测",),
            ("LLM", "RAG"),
        ),
        _policy(),
    )
    assert decision.responsibility_score == 1.0
    assert decision.combined_score >= 0.60
    assert decision.decision == "ADMIT"


def test_genuine_emerging_candidate_is_not_falsely_filtered():
    decision = assess_candidate_admission(
        AdmissionEvidence(
            ("大模型训练框架研发工程师",),
            ("负责大规模分布式训练框架的设计、优化与迭代",),
            ("Python", "PyTorch", "大模型", "深度学习"),
        ),
        _policy(),
    )
    assert decision.title_score == 1.0
    assert decision.responsibility_score == 1.0
    assert decision.decision == "ADMIT"


def test_admission_decision_carries_traceable_certificate_fields():
    decision = assess_candidate_admission(
        AdmissionEvidence(("Retail Marketing Manager-UK",), (), ("RAG",)),
        _policy(),
    )
    certificate = decision.certificate()
    assert certificate["decision"] == "REJECT_OFF_TARGET"
    assert certificate["policy_version"] == "candidate-admission-policy.v2"
    assert certificate["title_score"] == 0.0
    assert certificate["responsibility_score"] == 0.0
    assert certificate["skill_score"] == 1.0
    assert certificate["combined_score"] == 0.2
    assert certificate["decision_reason"]
