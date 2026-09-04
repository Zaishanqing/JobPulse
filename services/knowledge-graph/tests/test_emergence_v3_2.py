"""Targeted tests for EXP-EMERGE-01 v3.2 Stage2 occupation-cluster policy."""

from __future__ import annotations

from app.emergence.emergence_v2 import SkillInfo
from app.emergence.emergence_v3_1 import (
    ReferenceProfile,
    build_cluster_temporal_layers,
    retrieve_top_k,
)
from app.emergence.emergence_v3_2 import (
    cluster_stage2_decision,
    cluster_stage2_with_ablations,
    retrieve_top_k_cached,
)


class FakeSkillIndex:
    def __init__(self, mapping: dict[str, SkillInfo]):
        self._mapping = dict(mapping)

    @property
    def raw_map(self) -> dict[str, SkillInfo]:
        return dict(self._mapping)

    def resolve(self, raw: str) -> SkillInfo:
        if raw in self._mapping:
            return self._mapping[raw]
        return SkillInfo(
            raw=raw,
            skill_id=f"UNRES:{raw}",
            canonical_name=raw,
            category_code=None,
            subcategory_code=None,
            domains=frozenset(),
            resolved=False,
        )

    def resolve_many(self, raws):
        return tuple(self.resolve(raw) for raw in raws)

    def domain_keywords(self) -> dict[str, frozenset[str]]:
        return {}


def _skill(raw: str, skill_id: str) -> SkillInfo:
    return SkillInfo(
        raw=raw,
        skill_id=skill_id,
        canonical_name=raw,
        category_code="domain_knowledge",
        subcategory_code="AI",
        domains=frozenset({"ai_intelligent_systems"}),
    )


SKILL_INDEX = FakeSkillIndex(
    {
        "Python": _skill("Python", "LANG_PYTHON"),
        "Java": _skill("Java", "LANG_JAVA"),
        "PyTorch": _skill("PyTorch", "FRAMEWORK_PYTORCH"),
        "大模型": _skill("大模型", "AI_LLM"),
        "机器学习": _skill("机器学习", "KNOWLEDGE_ML"),
        "深度学习": _skill("深度学习", "KNOWLEDGE_DL"),
    }
)


def _obs(date: str, identity: str, hashv: str, company: str, platform: str):
    return {
        "date": date,
        "source_record_id": identity,
        "content_hash": hashv,
        "company": company,
        "platform": platform,
        "bundle_id": f"bundle-{date}-x",
        "region": "上海",
    }


def _layers(members):
    return build_cluster_temporal_layers(
        cluster_key="测试算法", members=members
    ).to_dict()


_REAL_UNEXPLAINED = {
    "reference_family": "ROBOTICS",
    "reference_core_skills_non_empty": True,
    "reference_core_inherited": True,
    "reference_core_domains": ["ai_intelligent_systems"],
    "candidate_skill_domains": ["ai_intelligent_systems"],
    "explanation_combined": 0.30,
}


def test_repeated_same_jd_hash_never_emerging():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-07-28", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-1", "h1", "A", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="specialization",
        layers=_layers(members),
    )
    assert decision["state"] != "emerging"
    assert decision["gates"]["independent_posting_persistence"] is False
    assert decision["counts"]["independent_postings"] == 1


def test_family_multi_enterprise_does_not_auto_emerge():
    # synthetic layers: many enterprises but only one independent posting
    layers = {
        "re_observation_persistence": {
            "dates": ["2026-07-27", "2026-07-28"],
            "distinct_dates": 2,
            "observation_count": 2,
            "same_content_hash": True,
        },
        "independent_posting_persistence": {
            "independent_posting_count": 1,
            "distinct_dates": 2,
            "evidence_refs": ["jd-1"],
            "note": "",
        },
        "enterprise_diffusion": {
            "independent_enterprise_count": 5,
            "evidence_refs": ["A", "B", "C", "D", "E"],
            "note": "family-level context only",
        },
        "source_diffusion": {
            "independent_source_count": 2,
            "evidence_refs": ["feishu", "playwright"],
            "note": "",
        },
        "structural_evolution": {
            "content_hash_count": 1,
            "changed": False,
            "content_hashes": ["h1"],
        },
        "market_growth": {
            "available": False,
            "reason": "single independent posting",
            "distinct_independent_postings_per_window": [],
        },
        "evidence_refs": ["jd-1"],
    }
    decision = cluster_stage2_decision(
        cluster_relation="specialization",
        layers=layers,
    )
    assert decision["state"] != "emerging"
    assert decision["gates"]["independent_posting_persistence"] is False


def test_single_enterprise_single_posting_not_emerging():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="unexplained_structural_novelty",
        layers=_layers(members),
        structural_evidence=_REAL_UNEXPLAINED,
    )
    assert decision["state"] in {"insufficient_evidence", "weak_emerging_signal"}
    assert decision["state"] != "emerging"


def test_multi_jd_cross_enterprise_temporal_can_emerge():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="specialization",
        layers=_layers(members),
    )
    assert decision["state"] == "emerging"
    assert decision["evidence_level"] == "short_window"
    assert decision["gates"]["independent_posting_persistence"] is True
    assert decision["gates"]["enterprise_diffusion"] is True
    assert decision["gates"]["diffusion"] is True


def test_source_only_diffusion_with_independent_postings_can_emerge():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "A", "playwright"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="hybridization",
        layers=_layers(members),
    )
    assert decision["state"] == "emerging"
    assert decision["gates"]["enterprise_diffusion"] is False
    assert decision["gates"]["source_diffusion"] is True
    assert decision["gates"]["diffusion"] is True


def test_stable_relation_not_emerging_despite_diffusion():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "playwright"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="renaming",
        layers=_layers(members),
    )
    assert decision["state"] == "not_emerging"


def test_insufficient_stage1_stays_insufficient():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="insufficient_evidence",
        layers=_layers(members),
    )
    assert decision["state"] == "insufficient_evidence"


def test_unexplained_vs_empty_other_reference_is_not_structural():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="unexplained_structural_novelty",
        layers=_layers(members),
        structural_evidence={
            **_REAL_UNEXPLAINED,
            "reference_family": "OTHER",
            "reference_core_skills_non_empty": False,
        },
    )
    assert decision["state"] == "not_emerging"
    assert decision["gates"]["structural_signal"] is False


def test_unexplained_domain_mismatch_is_not_structural():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="unexplained_structural_novelty",
        layers=_layers(members),
        structural_evidence={
            **_REAL_UNEXPLAINED,
            "candidate_skill_domains": ["design_graphics"],
        },
    )
    assert decision["state"] == "not_emerging"
    assert decision["gates"]["structural_signal"] is False


def test_unexplained_with_strong_reference_match_is_not_structural():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="unexplained_structural_novelty",
        layers=_layers(members),
        structural_evidence={
            **_REAL_UNEXPLAINED,
            "explanation_combined": 0.55,
        },
    )
    assert decision["state"] == "not_emerging"
    assert decision["gates"]["structural_signal"] is False


def test_unexplained_without_core_inheritance_is_not_structural():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="unexplained_structural_novelty",
        layers=_layers(members),
        structural_evidence={
            **_REAL_UNEXPLAINED,
            "reference_core_inherited": False,
        },
    )
    assert decision["state"] == "not_emerging"
    assert decision["gates"]["structural_signal"] is False


def test_unexplained_weak_same_domain_real_family_is_structural():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    decision = cluster_stage2_decision(
        cluster_relation="unexplained_structural_novelty",
        layers=_layers(members),
        structural_evidence=_REAL_UNEXPLAINED,
    )
    assert decision["state"] == "emerging"
    assert decision["gates"]["structural_signal"] is True


def test_ablations_gate_emerging():
    members = [
        _obs("2026-07-27", "jd-1", "h1", "A", "feishu"),
        _obs("2026-08-01", "jd-2", "h2", "B", "feishu"),
    ]
    layers = _layers(members)
    all_decisions = cluster_stage2_with_ablations(
        cluster_relation="specialization",
        layers=layers,
    )
    assert all_decisions["baseline"]["state"] == "emerging"
    assert all_decisions["no_temporal"]["state"] != "emerging"
    assert all_decisions["no_enterprise_diffusion"]["state"] != "emerging"
    # evolution is not a standalone driver here, so removing it keeps the
    # persistence + enterprise path intact
    assert all_decisions["no_structural_evolution"]["state"] == "emerging"


def _bank() -> list[ReferenceProfile]:
    return [
        ReferenceProfile(
            family_id="SEARCH",
            canonical_title="搜索算法工程师",
            titles=("搜索算法工程师",),
            skills=("Java", "Python", "PyTorch", "大模型", "深度学习"),
            responsibilities=("搜索召回与排序算法研发",),
            member_document_ids=("doc-search-1",),
            source="frozen-bundle-jds",
        ),
        ReferenceProfile(
            family_id="AD",
            canonical_title="广告算法工程师",
            titles=("广告算法工程师",),
            skills=("Java", "Python", "机器学习", "深度学习"),
            responsibilities=("广告竞价与投放算法优化",),
            member_document_ids=("doc-ad-1",),
            source="frozen-bundle-jds",
        ),
    ]


class _VecEncoder:
    def __call__(self, text: str):
        if "搜索" in text:
            return (1.0, 0.0, 0.0)
        if "广告" in text:
            return (0.0, 1.0, 0.0)
        return (0.5, 0.5, 0.5)


def test_cached_retrieval_equals_v31_retrieval():
    bank = _bank()
    encoder = _VecEncoder()
    candidate = {
        "candidate_title": "地图搜索算法工程师",
        "candidate_skills": ("Java", "Python", "PyTorch", "大模型", "深度学习"),
        "candidate_responsibilities": ("负责地图搜索算法研发",),
        "bank": bank,
        "skill_index": SKILL_INDEX,
        "encoder": encoder,
        "exclude_document_ids": (),
        "k": 3,
    }
    original = retrieve_top_k(**candidate)
    cached = retrieve_top_k_cached(**candidate)
    assert original == cached
    assert cached[0]["family_id"] == "SEARCH"
