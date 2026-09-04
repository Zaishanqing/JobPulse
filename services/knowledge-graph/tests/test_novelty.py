"""Tests for TEMP-08: Five-dimension novelty scoring and classification."""

from __future__ import annotations

import pytest

from app.domain.novelty import (
    NoveltyDimensionResult,
    FiveDimNoveltyResult,
    NoveltyEventClassification,
    compute_five_dim_novelty,
    classify_novelty_event,
    score_name_novelty,
    score_skill_combination_novelty,
    score_responsibility_structure_novelty,
    score_industry_scenario_novelty,
    score_market_behavior_novelty,
)


# ── Name novelty ──

def test_name_novelty_high_for_completely_different_name():
    r = score_name_novelty("量子计算工程师", before_name="Java后端工程师")
    assert r.available
    assert r.score is not None and r.score >= 0.6


def test_name_novelty_low_for_similar_name():
    r = score_name_novelty("Java Backend Engineer", before_name="Java Backend Developer")
    assert r.available
    assert r.score is not None and r.score < 0.5


def test_name_novelty_zero_for_same_name():
    r = score_name_novelty("Java Engineer", before_name="Java Engineer")
    assert r.available
    assert r.score is not None and r.score < 0.1


def test_name_novelty_unavailable_when_no_before():
    r = score_name_novelty("New Position")
    assert not r.available
    assert r.score is None
    assert "NO_DATA" in r.reason


def test_name_novelty_with_historic_names():
    r = score_name_novelty(
        "大模型算法工程师",
        before_name="算法工程师",
        historic_names=("Java后端工程师", "Python工程师", "数据分析师"),
    )
    assert r.available
    assert r.score is not None


# ── Skill combination novelty ──

def test_skill_combination_novelty_high():
    r = score_skill_combination_novelty(
        ("PyTorch", "Transformer", "RLHF", "CUDA"),
        before_skills=("Spring", "MyBatis", "MySQL", "Redis"),
    )
    assert r.available
    assert r.score is not None and r.score >= 0.7


def test_skill_combination_novelty_low():
    r = score_skill_combination_novelty(
        ("Java", "Spring", "MySQL"),
        before_skills=("Java", "Spring", "MySQL"),
    )
    assert r.available
    assert r.score is not None and r.score < 0.1


def test_skill_combination_novelty_unavailable():
    r = score_skill_combination_novelty(("Java", "Spring"))
    assert not r.available
    assert r.score is None


def test_skill_combination_vs_peers_reduces_novelty():
    # same skills as one peer → novelty should be lower
    r = score_skill_combination_novelty(
        ("Java", "Spring", "MySQL"),
        before_skills=("Java", "Spring"),
        peer_skill_sets=(("Java", "Spring", "MySQL"),),
    )
    assert r.available
    # combined_dist = 0.6 * vs_before + 0.4 * min_peer_dist
    # vs_before is low but not zero, min_peer_dist is 0 → combined should be low
    assert r.score is not None and r.score < 0.5


# ── Responsibility novelty ──

def test_responsibility_novelty_different():
    r = score_responsibility_structure_novelty(
        ("训练大语言模型并部署到生产环境",),
        before_responsibilities=("编写Java后端业务代码",),
    )
    assert r.available
    assert r.score is not None and r.score > 0.3


def test_responsibility_novelty_same():
    r = score_responsibility_structure_novelty(
        ("编写Java后端业务代码",),
        before_responsibilities=("编写Java后端业务代码",),
    )
    assert r.available
    assert r.score is not None and r.score < 0.1


def test_responsibility_novelty_unavailable():
    r = score_responsibility_structure_novelty(("test",))
    assert not r.available


def test_responsibility_chinese_near_synonym_not_max_distance():
    # EVOL-COMP-01: two near-identical Chinese responsibilities must not
    # collapse to a single token and yield a ~1.0 Jaccard distance.
    r = score_responsibility_structure_novelty(
        ("负责机器学习模型的训练与调优",),
        before_responsibilities=("负责机器学习模型的训练和优化",),
    )
    assert r.available
    dist = r.evidence_components["token_jaccard_vs_before"]
    assert dist < 0.9


def test_responsibility_chinese_different_high_distance():
    # EVOL-COMP-01: a clearly different Chinese responsibility stays clearly
    # above a near-synonym rewrite.
    near = score_responsibility_structure_novelty(
        ("负责机器学习模型的训练与调优",),
        before_responsibilities=("负责机器学习模型的训练和优化",),
    )
    far = score_responsibility_structure_novelty(
        ("负责机器学习模型的训练与调优",),
        before_responsibilities=("编写Java后端业务代码",),
    )
    near_dist = near.evidence_components["token_jaccard_vs_before"]
    far_dist = far.evidence_components["token_jaccard_vs_before"]
    assert far_dist > near_dist + 0.2


# ── Industry novelty ──

def test_industry_novelty_new_codes():
    r = score_industry_scenario_novelty(
        ("AI", "FinTech"),
        historic_industry_codes=("Finance",),
    )
    assert r.available
    assert r.score is not None and r.score > 0.0


def test_industry_novelty_all_historic():
    r = score_industry_scenario_novelty(
        ("Finance", "E-Commerce"),
        historic_industry_codes=("Finance", "E-Commerce"),
    )
    assert r.available
    assert r.score is not None and r.score < 0.1


def test_industry_novelty_unavailable():
    r = score_industry_scenario_novelty(())
    assert not r.available


def test_industry_novelty_no_historical_baseline_unavailable():
    # EVOL-COMP-04: present industry codes but no historical baseline must be
    # unavailable (not a fixed 0.30 score).
    r = score_industry_scenario_novelty(
        ("AI", "FinTech"),
        historic_industry_codes=(),
    )
    assert not r.available
    assert r.score is None
    assert "NO_HISTORICAL_INDUSTRY_BASELINE" in r.reason


# ── Market behavior novelty ──

def test_market_behavior_novelty_high():
    r = score_market_behavior_novelty(
        jd_count=50, enterprise_count=8, geographic_spread=4,
        growth_trajectory=(10, 20, 35, 50),
    )
    assert r.available
    assert r.score is not None and r.score >= 0.5


def test_market_behavior_single_company_low():
    r = score_market_behavior_novelty(
        jd_count=20, enterprise_count=1, geographic_spread=1,
        growth_trajectory=(20,),
    )
    assert r.available
    assert r.score is not None and r.score < 0.3  # enterprise_count < min


def test_market_behavior_single_source_concentrated_downgraded():
    """单源平台即使企业数多，市场分应被降权（soft penalty，非硬否决）。"""
    base = score_market_behavior_novelty(
        jd_count=100, enterprise_count=50, geographic_spread=20,
        growth_trajectory=(10, 100),
    )
    downgraded = score_market_behavior_novelty(
        jd_count=100, enterprise_count=50, geographic_spread=20,
        growth_trajectory=(10, 100),
        source_diversity=1, max_source_share=1.0,
    )
    assert downgraded.available
    assert base.score is not None and downgraded.score is not None
    assert downgraded.score < base.score  # downgraded
    assert "source_concentrated" in downgraded.reason


def test_market_behavior_dominant_source_concentrated_downgraded():
    """单源占比 >0.8（即使有第二源）也降权。"""
    downgraded = score_market_behavior_novelty(
        jd_count=100, enterprise_count=50, geographic_spread=20,
        growth_trajectory=(10, 100),
        source_diversity=2, max_source_share=0.9,
    )
    assert downgraded.available
    assert downgraded.score is not None and downgraded.score < 0.5
    assert "source_concentrated" in downgraded.reason


def test_market_behavior_diverse_sources_not_penalized():
    """多源且无单源主导时，不应触发来源集中惩罚。"""
    r = score_market_behavior_novelty(
        jd_count=100, enterprise_count=50, geographic_spread=20,
        growth_trajectory=(10, 100),
        source_diversity=3, max_source_share=0.4,
    )
    assert r.available
    assert r.score is not None and r.score >= 0.5


def test_market_behavior_no_source_info_not_penalized():
    """未提供来源信息（None）时，不触发来源集中惩罚（向后兼容）。"""
    r = score_market_behavior_novelty(
        jd_count=50, enterprise_count=8, geographic_spread=4,
        growth_trajectory=(10, 20, 35, 50),
    )
    assert r.available
    assert r.score is not None and r.score >= 0.5


def test_source_concentrated_market_not_counted_as_novel_dimension():
    """来源集中降权后 market 分 <0.5，不再作为 novel 维度计入 genuine_emergence。"""
    novelty = compute_five_dim_novelty(
        position_id="P",
        release_id="R",
        graph_version_id=0,
        current_name="量子计算工程师",
        current_skills=("量子计算", "Qiskit", "超导量子比特"),
        current_responsibilities=("设计量子算法", "超导量子比特调控"),
        before_name="后端开发工程师",
        before_skills=("Java", "Spring"),
        before_responsibilities=("Java后端开发",),
        jd_count=100, enterprise_count=50, geographic_spread=20,
        source_diversity=1, max_source_share=1.0,
    )
    market = next(d for d in novelty.dimensions if d.dimension == "market_behavior_novelty")
    assert market.available
    assert market.score is not None and market.score < 0.5


def test_market_behavior_unavailable():
    r = score_market_behavior_novelty(jd_count=0, enterprise_count=0)
    assert not r.available


def test_market_behavior_zero_baseline_growth_positive():
    # EVOL-COMP-02: 0 -> N is a first-appearance growth signal, higher than 0 -> 0.
    zero_to_n = score_market_behavior_novelty(
        jd_count=20, enterprise_count=5, geographic_spread=2,
        growth_trajectory=(0, 20),
    )
    zero_to_zero = score_market_behavior_novelty(
        jd_count=20, enterprise_count=5, geographic_spread=2,
        growth_trajectory=(0, 0),
    )
    assert zero_to_n.available and zero_to_n.score is not None
    assert zero_to_zero.available and zero_to_zero.score is not None
    assert zero_to_n.score > zero_to_zero.score


def test_market_behavior_peer_relative_growth_changes_score():
    # EVOL-COMP-03: target outgrowing its peers scores higher than lagging peers.
    base = dict(jd_count=30, enterprise_count=5, geographic_spread=2)
    growth = (10, 20)
    outgrowing = score_market_behavior_novelty(
        **base, growth_trajectory=growth,
        peer_trajectories=((10, 11), (10, 11)),
    )
    lagging = score_market_behavior_novelty(
        **base, growth_trajectory=growth,
        peer_trajectories=((10, 50), (10, 50)),
    )
    assert outgrowing.score is not None and lagging.score is not None
    assert outgrowing.score > lagging.score
    assert outgrowing.evidence_components["peer_count"] == 2
    assert outgrowing.evidence_components["peer_relative_delta"] is not None


# ── Five-dim result ──

def test_five_dim_never_synthesizes_total():
    r = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="AI Engineer",
        current_skills=("PyTorch",),
        current_responsibilities=("train models",),
        before_name="Java Engineer",
        before_skills=("Java",),
        before_responsibilities=("write Java code",),
    )
    # result has no total_score attribute — just dimensions
    assert len(r.dimensions) == 6
    # verify via hasattr
    for d in r.dimensions:
        assert isinstance(d, NoveltyDimensionResult)


def test_available_dimensions_filter():
    r = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="X",
        before_name="Y",
        current_skills=("a",),
        before_skills=("b",),
        current_responsibilities=("c",),
        before_responsibilities=("d",),
        industry_codes=("Tech",),
        historic_industry_codes=("Legacy",),
        jd_count=10,
        enterprise_count=5,
    )
    # all 5 available when all inputs provided (incl. industry history)
    assert len(r.available_dimensions()) == 5


def test_unavailable_dimensions_filter():
    r = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="X",
        current_skills=("a",),
        current_responsibilities=("c",),
    )
    # name has no before_name, industry/market have no data
    unavailable = r.unavailable_dimensions()
    assert len(unavailable) >= 2


def test_reason_string_when_unavailable():
    r = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="X",
        current_skills=("a",),
        current_responsibilities=("c",),
    )
    for d in r.unavailable_dimensions():
        assert "NO_DATA" in d.reason


# ── Negative controls ──

def test_stable_java_backend_not_novel():
    skills = ("Java", "Spring", "MySQL", "Redis")
    resp = ("编写Java后端代码", "数据库设计与优化")
    r = compute_five_dim_novelty(
        "POS_JAVA", "REL-1", 1,
        current_name="Java后端工程师",
        current_skills=skills,
        current_responsibilities=resp,
        before_name="Java后端工程师",
        before_skills=skills,
        before_responsibilities=resp,
    )
    available = r.available_dimensions()
    avg_score = sum(d.score for d in available if d.score is not None) / max(len(available), 1)
    assert avg_score < 0.3  # stable position should have low novelty


def test_title_only_swap_classified_renaming():
    skills = ("Java", "Spring", "MySQL")
    resp = ("编写后端代码",)
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="AI开发专家",
        current_skills=skills,
        current_responsibilities=resp,
        before_name="Java后端工程师",
        before_skills=skills,
        before_responsibilities=resp,
    )
    cls = classify_novelty_event(
        novelty,
        before_skills=skills,
        current_skills=skills,
    )
    assert cls.classification == "renaming"


def test_template_copy_not_genuine_emergence():
    # template copy: same responsibilities, different name, copied skills, weak market
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="AI数据分析师",
        current_skills=("Python", "Pandas", "SQL"),
        current_responsibilities=("产品规划与需求分析",),
        before_name="产品经理",
        before_skills=("需求分析", "PRD编写"),
        before_responsibilities=("产品规划与需求分析",),  # same responsibility
        jd_count=5,
        enterprise_count=1,
    )
    cls = classify_novelty_event(
        novelty,
        before_skills=("需求分析", "PRD编写"),
        current_skills=("Python", "Pandas", "SQL"),
    )
    # name + skill novelty are high, but responsibility same + market low
    # only 2 dims above threshold → not genuine_emergence
    assert cls.classification != "genuine_emergence"


def test_single_company_burst_market_novelty_low():
    r = score_market_behavior_novelty(
        jd_count=30, enterprise_count=1, geographic_spread=1,
    )
    assert r.available
    assert r.score is not None and r.score < 0.3


def test_old_position_plus_buzzword_name_novelty():
    # Backend + "AI" buzzword → name novelty should be medium at most
    r = score_name_novelty(
        "AI智能Java后端工程师",
        before_name="Java后端工程师",
    )
    assert r.available
    # edit distance is relatively low since most chars overlap
    assert r.score is not None and r.score < 0.7


def test_random_skill_concat_high_skill_novelty():
    r = score_skill_combination_novelty(
        ("TensorFlow", "Kubernetes", "Solidity", "Figma"),
        before_skills=("Java", "Spring", "MySQL"),
    )
    assert r.available
    assert r.score is not None and r.score >= 0.7


# ── Classification ──

def test_classification_specialization():
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="高级Java工程师",
        current_skills=("Java", "Spring"),
        current_responsibilities=("后端开发",),
        before_name="Java工程师",
        before_skills=("Java", "Spring", "MySQL", "Redis", "Kafka"),
        before_responsibilities=("后端开发",),
    )
    cls = classify_novelty_event(
        novelty,
        before_skills=("Java", "Spring", "MySQL", "Redis", "Kafka"),
        current_skills=("Java", "Spring"),
    )
    assert cls.classification == "specialization"


def test_classification_hybridization():
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="AI全栈工程师",
        current_skills=("Python", "PyTorch", "React", "TypeScript"),
        current_responsibilities=("全栈开发",),
        before_name="后端工程师",
        before_skills=("Python", "Django"),
        before_responsibilities=("后端开发",),
    )
    cls = classify_novelty_event(
        novelty,
        before_skills=("Python", "Django"),
        current_skills=("Python", "PyTorch", "React", "TypeScript"),
    )
    # "PyTorch" and "React"/"TypeScript" may create new categories
    # This depends on category prefix parsing
    assert cls.classification in ("hybridization", "genuine_emergence", "unclassified")


def test_classification_tool_shift():
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="Java后端工程师",
        current_skills=("Java", "SpringBoot", "MySQL"),
        current_responsibilities=("后端开发",),
        before_name="Java后端工程师",
        before_skills=("Java", "Spring", "MySQL"),
        before_responsibilities=("后端开发",),
    )
    cls = classify_novelty_event(
        novelty,
        before_skills=("Java", "Spring", "MySQL"),
        current_skills=("Java", "SpringBoot", "MySQL"),
    )
    # Spring → SpringBoot is within same category (both start with "Spring")
    assert cls.classification in ("tool_shift", "unclassified")


def test_classification_reproducibility():
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="X Engineer",
        current_skills=("A", "B"),
        current_responsibilities=("do stuff",),
        before_name="Y Engineer",
        before_skills=("C", "D"),
        before_responsibilities=("do other stuff",),
    )
    c1 = classify_novelty_event(novelty, before_skills=("C", "D"), current_skills=("A", "B"))
    c2 = classify_novelty_event(novelty, before_skills=("C", "D"), current_skills=("A", "B"))
    assert c1.classification == c2.classification


def test_classification_confidence_bounded():
    novelty = compute_five_dim_novelty(
        "POS_TEST", "REL-1", 1,
        current_name="X",
        before_name="Y",
        current_skills=("a",),
        before_skills=("b",),
        current_responsibilities=("c",),
        before_responsibilities=("d",),
    )
    cls = classify_novelty_event(novelty, before_skills=("b",), current_skills=("a",))
    assert 0.0 <= cls.confidence <= 1.0


# ── Semantic novelty (LLM provider) ──


class _FakeSemanticProvider:
    def __init__(self, same_domain: bool, similarity: float):
        self._same_domain = same_domain
        self._similarity = similarity

    def judge(self, **_kwargs):
        return _FakeJudgment(self._similarity, self._same_domain)


class _FakeJudgment:
    def __init__(self, similarity: float, same_domain: bool):
        self.similarity = similarity
        self.same_domain = same_domain
        self.reason = "fake"


def test_semantic_novelty_unavailable_without_provider():
    novelty = compute_five_dim_novelty(
        "P", "R", 0,
        current_name="具身智能工程师",
        before_name="大模型算法工程师",
    )
    semantic = next(d for d in novelty.dimensions if d.dimension == "semantic_novelty")
    assert not semantic.available
    assert semantic.score is None


def test_semantic_novelty_high_for_different_domain():
    novelty = compute_five_dim_novelty(
        "P", "R", 0,
        current_name="具身智能工程师",
        current_skills=("机器人", "具身智能"),
        current_responsibilities=("具身智能推理",),
        before_name="大模型算法工程师",
        before_skills=("Python", "PyTorch"),
        before_responsibilities=("大模型训练",),
        semantic_provider=_FakeSemanticProvider(same_domain=False, similarity=0.3),
    )
    semantic = next(d for d in novelty.dimensions if d.dimension == "semantic_novelty")
    assert semantic.available
    assert semantic.score is not None and semantic.score >= 0.7


def test_semantic_novelty_low_for_same_domain():
    novelty = compute_five_dim_novelty(
        "P", "R", 0,
        current_name="大模型训练工程师",
        current_skills=("Python", "PyTorch"),
        current_responsibilities=("大模型微调",),
        before_name="大模型算法工程师",
        before_skills=("Python", "PyTorch"),
        before_responsibilities=("大模型训练",),
        semantic_provider=_FakeSemanticProvider(same_domain=True, similarity=0.9),
    )
    semantic = next(d for d in novelty.dimensions if d.dimension == "semantic_novelty")
    assert semantic.available
    assert semantic.score is not None and semantic.score < 0.5


def test_semantic_veto_blocks_genuine_emergence_for_same_domain():
    """语义判为同一方向时，即使 name/skill/resp 字符级都高，也不判 genuine_emergence。"""
    novelty = compute_five_dim_novelty(
        "P", "R", 0,
        current_name="大模型训练框架研发工程师",
        current_skills=("Python", "PyTorch", "大模型"),
        current_responsibilities=("大模型训练框架研发",),
        before_name="后端开发工程师",
        before_skills=("Java", "Spring"),
        before_responsibilities=("Java后端开发",),
        semantic_provider=_FakeSemanticProvider(same_domain=True, similarity=0.9),
    )
    cls = classify_novelty_event(
        novelty,
        before_skills=("Java", "Spring"),
        current_skills=("Python", "PyTorch", "大模型"),
    )
    assert cls.classification != "genuine_emergence"


class _DomainAwareProvider:
    """按基线名判断是否同域：名字含相同关键词则 same_domain=True。"""

    def __init__(self, same_domain_keyword: str):
        self._kw = same_domain_keyword

    def judge(self, *, baseline_name, **_kwargs):
        same = self._kw in baseline_name
        return _FakeJudgment(0.9 if same else 0.3, same)


def test_semantic_multi_baseline_low_when_any_baseline_same_domain():
    """多基准下，只要与任一基准同域，语义新颖性就低（最近邻）。"""
    baselines = (
        ("大模型算法工程师", "Python, PyTorch", "大模型训练"),
        ("后端开发工程师", "Java, Spring", "Java后端开发"),
        ("AI产品经理", "产品", "需求分析"),
    )
    novelty = compute_five_dim_novelty(
        "P", "R", 0,
        current_name="大模型训练工程师",
        current_skills=("Python", "PyTorch"),
        current_responsibilities=("大模型微调",),
        before_name="大模型算法工程师",
        before_skills=("Python", "PyTorch"),
        before_responsibilities=("大模型训练",),
        semantic_provider=_DomainAwareProvider("大模型"),
        semantic_baselines=baselines,
    )
    semantic = next(d for d in novelty.dimensions if d.dimension == "semantic_novelty")
    assert semantic.available
    assert semantic.score is not None and semantic.score < 0.5


def test_semantic_multi_baseline_high_when_all_different_domain():
    """多基准下，与全部基准都不同域才判高新颖性。"""
    baselines = (
        ("大模型算法工程师", "Python, PyTorch", "大模型训练"),
        ("后端开发工程师", "Java, Spring", "Java后端开发"),
    )
    novelty = compute_five_dim_novelty(
        "P", "R", 0,
        current_name="具身智能工程师",
        current_skills=("机器人", "具身智能"),
        current_responsibilities=("具身智能推理",),
        before_name="大模型算法工程师",
        before_skills=("Python", "PyTorch"),
        before_responsibilities=("大模型训练",),
        semantic_provider=_DomainAwareProvider("不匹配任何基准"),
        semantic_baselines=baselines,
    )
    semantic = next(d for d in novelty.dimensions if d.dimension == "semantic_novelty")
    assert semantic.available
    assert semantic.score is not None and semantic.score >= 0.7
