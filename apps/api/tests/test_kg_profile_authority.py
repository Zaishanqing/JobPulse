from types import SimpleNamespace
from copy import deepcopy

import pytest

from app.contexts.catalog import ManagePositions
from app.contexts.matching_learning.contracts_service import (
    NO_FORMAL_REQUIREMENTS,
    NO_VALID_SPECIALTY_ROUTE,
    ROUTE_SUPPORT_UNAVAILABLE,
    StandardPositionProfileInsufficient,
)
from app.contexts.catalog._ports.positions import (
    PositionChanges,
    PositionDraft,
    PositionRecord,
)
from app.domain.accounts import AccountActor
from app.domain.positions import PositionRuleViolation
from app.infrastructure.matching_contracts import (
    KnowledgeGraphMatchingContractReader,
    _enterprise_job_match_profile,
    _kg_position_match_profile,
    _remap_requirement_graph_reference_ids,
    _scrub_profile_pii,
    _standard_position_match_profile,
    _snapshot_evidence,
)
from jobgraph_contracts.privacy import find_pii
from app.infrastructure.jd_pipeline import extract_jd
from app.infrastructure.position_profiles import KnowledgeGraphPositionProfileAdapter
from app.infrastructure.trend_analysis import SqlAlchemyTrendAnalysisUnitOfWork
from app.models.trend_report import TrendReport
from jobgraph_contracts.evidence import Evidence
from jobgraph_contracts.requirement_graph import (
    RequirementGraph,
    RequirementGraphChild,
    RequirementGraphGroup,
)


PROFILE = {
    "contract_version": "position-profile.v3",
    "position_id": "POS_BACKEND",
    "position_code": "POS_BACKEND",
    "position_name": "后端工程师",
    "graph_version": "v3",
    "graph_version_id": 3,
    "profile_state": "published",
    "taxonomy_version": "position-taxonomy.v3.0.0",
    "classification_status": "resolved",
    "sample_support_status": "sufficient",
    "responsibilities": [{"text": "负责服务开发"}],
    "requirements": [],
    "skill_relations": [
        {
            "skill_id": "SKILL_PYTHON",
            "skill_name": "Python",
            "category_code": "LANG",
            "weight": 0.8,
            "confidence": 0.9,
            "importance_level": "core",
            "evidence_count": 1,
        }
    ],
    "evidence_summary": [
        {
            "evidence_id": 7,
            "document_id": "JD1",
            "requirement_id": "R1",
            "skill_id": "SKILL_PYTHON",
            "quote": "熟悉 Python",
            "alignment": "exact",
        }
    ],
}


class FakeKGClient:
    references = []

    def position_profile(self, position_id, *, graph_version_id=None):
        assert position_id == "POS_BACKEND"
        assert graph_version_id in (None, 3)
        return SimpleNamespace(data=PROFILE)

    def skill_relations_batch(self, skill_ids):
        assert skill_ids == ("SKILL_PYTHON",)
        return SimpleNamespace(
            data={
                "graph_version": "kg-published-current",
                "relations": [],
            }
        )

    def register_dependency_reference(self, **payload):
        self.references.append(payload)
        return SimpleNamespace(data={"dependency_reference_id": len(self.references)})


def test_matching_and_trend_read_the_published_kg_profile():
    client = FakeKGClient()
    matching = KnowledgeGraphPositionProfileAdapter(client).get("POS_BACKEND")
    assert matching is not None
    assert matching.source_version == "3"
    assert matching.skills[0].skill_id == "SKILL_PYTHON"
    assert client.references[-1]["consumer_system"] == "matching"

    graph = SqlAlchemyTrendAnalysisUnitOfWork._kg_graph(PROFILE)
    assert graph is not None
    assert graph.graph_version == "3"
    assert graph.skills[0].evidence_references == ("7",)

    trend = SqlAlchemyTrendAnalysisUnitOfWork(lambda: None, client)
    trend._session = SimpleNamespace(
        scalar=lambda statement: SimpleNamespace(
            knowledge_graph_id="POS_BACKEND", sync_status="synced"
        )
    )
    trend_graph = trend.get_position_graph("MAIN_POSITION_ID")
    assert trend_graph is not None
    assert trend_graph.position_id == "MAIN_POSITION_ID"
    assert client.references[-1]["reference_id"] == "MAIN_POSITION_ID"

    local = SimpleNamespace(
        cv_profile=lambda value: {"cv_id": value},
        position_reference=lambda value: {
            "position_id": value,
            "position_code": value,
            "position_name": "后端工程师",
        },
        enterprise_job_reference=lambda job_id: {
            "job_id": job_id,
            "title": "后端工程师",
            "standard_position_id": "POS_BACKEND",
            "weights": (
                {"skill_id": "SKILL_PYTHON", "weight": 0.8, "is_required": True, "is_bonus": False},
            ),
            "requirement_graph": {
                "graph_version": "requirement-graph.v1",
                "status": "complete",
                "groups": [],
                "unresolved_items": [],
            },
        },
    )
    contracts = KnowledgeGraphMatchingContractReader(local, client)
    with pytest.raises(StandardPositionProfileInsufficient) as insufficient:
        contracts.position_profile("POS_BACKEND")
    assert insufficient.value.reason_code == NO_VALID_SPECIALTY_ROUTE
    profile = _kg_position_match_profile(
        PROFILE,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    assert profile is not None
    assert profile["contract_version"] == "position-match-profile.v1"
    assert profile["position_id"] == "POS_BACKEND"
    assert profile["graph_version"] == "3"
    assert profile["graph_mode"] == "enabled"
    assert all(
        profile[field] == {
            "values": (),
            "evidence_refs": (),
            "availability": "unavailable",
        }
        for field in ("tools", "industries", "business_scenarios")
    )
    assert profile["unresolved_items"] == ()
    enterprise_profile = contracts.enterprise_job_profile("ENTERPRISE_JOB_1")
    assert enterprise_profile is not None
    assert enterprise_profile["position_id"] == "enterprise_job:ENTERPRISE_JOB_1"
    assert enterprise_profile["graph_version"] == "3"
    assert enterprise_profile["requirement_graph"]["graph_version"] == (
        "requirement-graph.v1"
    )
    assert {
        matching.source_version,
        graph.graph_version,
        profile["graph_version"],
        enterprise_profile["graph_version"],
    } == {"3"}
    assert contracts.skill_relations(("SKILL_PYTHON",))["graph_version"] == ("kg-published-current")
    assert contracts.cv_profile("CV1") == {"cv_id": "CV1"}


def test_kg_profile_consumes_representative_responsibilities_and_text_requirements():
    raw = deepcopy(PROFILE)
    raw["responsibilities"] = [
        {
            "topic": "训练与对齐",
            "representative_text": "负责大模型后训练与对齐优化",
            "text": "负责大模型后训练与对齐优化",
            "source_texts": ["负责 SFT", "负责 DPO"],
            "source_items": [{"text": "负责 SFT"}, {"text": "负责 DPO"}],
            "support_count": 2,
            "skill_ids": ["SKILL_LLM"],
            "evidence": [
                {"evidence_id": 11, "document_id": "JD1", "quote": "负责 SFT"}
            ],
        }
    ]
    raw["requirements"] = [
        {
            "kind": "education",
            "text": "硕士及以上学历",
            "support_document_count": 2,
            "evidence": [{"evidence_id": 21, "quote": "硕士及以上学历"}],
        },
        {
            "kind": "experience",
            "text": "3年以上相关经验",
            "support_document_count": 3,
            "evidence": [{"evidence_id": 22, "quote": "3年以上相关经验"}],
        },
        {
            "kind": "certificate",
            "text": "算法竞赛获奖",
            "support_document_count": 1,
            "evidence": [{"evidence_id": 23, "quote": "必须具备算法竞赛获奖经历"}],
        },
    ]
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    assert profile is not None
    assert profile["core_responsibilities"] == ("负责大模型后训练与对齐优化",)
    assert len(profile["responsibility_requirements"]) == 1
    assert profile["responsibility_requirements"][0]["text"] == "负责大模型后训练与对齐优化"
    assert profile["responsibility_requirements"][0]["skill_ids"] == ("SKILL_LLM",)
    assert {
        (item["condition_type"], item["value"])
        for item in profile["hard_conditions"]
    } == {
        ("education", "master"),
        ("experience", "3 years"),
        ("certificate", "算法竞赛获奖"),
    }


def test_kg_profile_keeps_preferred_awards_out_of_hard_gate():
    raw = deepcopy(PROFILE)
    raw["requirements"] = [
        {
            "kind": "certificate",
            "text": "ACM 竞赛获奖",
            "support_document_count": 3,
            "evidence": [
                {"evidence_id": 31, "quote": "ACM 竞赛获奖者优先"},
                {"evidence_id": 32, "quote": "有顶会论文发表经历者更佳"},
            ],
        }
    ]
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    assert profile is not None
    assert profile["hard_conditions"] == ()


def test_matching_rejects_non_published_kg_profiles():
    draft = {**PROFILE, "profile_state": "draft"}
    client = SimpleNamespace(
        position_profile=lambda *args, **kwargs: SimpleNamespace(data=draft),
        register_dependency_reference=lambda **kwargs: None,
    )
    assert KnowledgeGraphPositionProfileAdapter(client).get("POS_BACKEND") is None

    local = SimpleNamespace(
        position_reference=lambda value: {
            "position_id": value,
            "position_code": value,
            "position_name": "后端工程师",
        },
        enterprise_job_reference=lambda job_id: {
            "job_id": job_id,
            "title": "后端工程师",
            "standard_position_id": "POS_BACKEND",
            "weights": (),
        }
    )
    contracts = KnowledgeGraphMatchingContractReader(local, client)
    assert contracts.position_profile("POS_BACKEND") is None
    assert contracts.enterprise_job_profile("ENTERPRISE_JOB_1") is None


def _standard_reader_for(raw: dict[str, object]) -> KnowledgeGraphMatchingContractReader:
    local = SimpleNamespace(
        position_reference=lambda value: {
            "position_id": value,
            "position_code": value,
            "position_name": "后端工程师",
        }
    )
    client = SimpleNamespace(
        position_profile=lambda *_args, **_kwargs: SimpleNamespace(data=raw)
    )
    return KnowledgeGraphMatchingContractReader(local, client)


def test_standard_position_no_route_is_explicitly_classified():
    no_formal_requirements = deepcopy(PROFILE)
    no_formal_requirements["skill_relations"] = []
    no_formal_requirements["evidence_summary"] = []

    preferred_only = deepcopy(PROFILE)
    preferred_only["skill_relations"] = [
        {
            **preferred_only["skill_relations"][0],
            "modality": "preferred",
            "requirement_market_status": "market_supported",
        }
    ]

    formal_without_membership = deepcopy(PROFILE)
    formal_without_membership["skill_relations"] = [
        {
            **formal_without_membership["skill_relations"][0],
            "modality": "required",
            "requirement_market_status": "market_supported",
            "required_supporting_jd_count": 1,
            "required_prevalence": 1.0,
            "required_purity": 1.0,
        }
    ]
    formal_without_membership["evidence_summary"] = []

    cases = (
        (no_formal_requirements, NO_FORMAL_REQUIREMENTS),
        (preferred_only, NO_VALID_SPECIALTY_ROUTE),
        (formal_without_membership, ROUTE_SUPPORT_UNAVAILABLE),
    )
    for raw, reason_code in cases:
        with pytest.raises(StandardPositionProfileInsufficient) as insufficient:
            _standard_reader_for(raw).position_profile("POS_BACKEND")
        assert insufficient.value.code == "STANDARD_POSITION_PROFILE_INSUFFICIENT"
        assert insufficient.value.reason_code == reason_code
        assert str(insufficient.value) == (
            "standard position does not have sufficient evidence to build a reliable "
            "matching route"
        )


def test_enterprise_job_jd_facts_overlay_standard_position_profile():
    job = {
        "job_id": "ENTERPRISE_JOB_1",
        "title": "反欺诈平台工程师",
        "standard_position_id": "POS_BACKEND",
        "jd_text": (
            "岗位职责：负责银行反欺诈规则引擎。"
            "业务场景：银行反欺诈。"
            "工作地点：上海张江。"
            "要求硕士及以上，至少 5 年以上经验，熟悉 Python，优先熟悉 Docker。"
        ),
        "requirement_graph": {
            "graph_version": "requirement-graph.v1",
            "status": "complete",
            "groups": [],
            "unresolved_items": [],
        },
        "updated_at": "2026-08-17T09:00:00+08:00",
        "weights": (),
    }
    local = SimpleNamespace(
        cv_profile=lambda value: {"cv_id": value},
        position_reference=lambda value: {
            "position_id": value,
            "position_code": value,
            "position_name": "后端工程师",
        },
        enterprise_job_reference=lambda _job_id: job,
    )
    raw_profile = deepcopy(PROFILE)
    raw_profile.update(
        {
            "requirements": [
                {
                    "requirement_id": "kg-education",
                    "kind": "education",
                    "minimum_degree": "本科",
                    "evidence": {"source_id": "kg-jd", "quote": "本科"},
                }
            ],
            "skill_relations": [
                {
                    **PROFILE["skill_relations"][0],
                    "modality": "required",
                    "requirement_market_status": "market_supported",
                    "required_supporting_jd_count": 1,
                    "required_prevalence": 1.0,
                    "required_purity": 1.0,
                }
            ],
            "evidence_summary": [
                {**PROFILE["evidence_summary"][0], "jd_modality": "required"}
            ],
        }
    )
    client = SimpleNamespace(
        position_profile=lambda *_args, **_kwargs: SimpleNamespace(data=raw_profile)
    )
    contracts = KnowledgeGraphMatchingContractReader(local, client)

    standard = contracts.position_profile("POS_BACKEND")
    first = contracts.enterprise_job_profile("ENTERPRISE_JOB_1")
    repeated = contracts.enterprise_job_profile("ENTERPRISE_JOB_1")

    assert standard is not None
    assert first is not None
    assert repeated is not None
    assert standard["core_responsibilities"] == ("负责服务开发",)
    assert standard["hard_conditions"][0]["value"] == "本科"
    assert first["core_responsibilities"][0] == "负责银行反欺诈规则引擎"
    assert {item["canonical_name"] for item in first["required_skills"]} >= {"Python"}
    assert {item["canonical_name"] for item in first["preferred_skills"]} >= {"Docker"}
    assert {
        (item["condition_type"], item["value"])
        for item in first["hard_conditions"]
    } >= {
        ("education", "硕士"),
        ("experience", "5 years"),
        ("location", "上海张江"),
    }
    assert ("education", "本科") not in {
        (item["condition_type"], item["value"])
        for item in first["hard_conditions"]
    }
    assert first["business_scenarios"]["values"] == ("银行反欺诈",)
    assert first["requirement_graph"] == job["requirement_graph"]
    assert first["profile_id"] == "enterprise-job:ENTERPRISE_JOB_1"
    assert first["profile_version"] == first["source_version"]
    assert first["profile_version"] == repeated["profile_version"]
    assert all(
        item["source_id"] == "enterprise-job:ENTERPRISE_JOB_1:jd"
        and item["alignment"] == "exact"
        for item in first["evidence_refs"]
        if item["source_id"] == "enterprise-job:ENTERPRISE_JOB_1:jd"
    )

    job["jd_text"] += "\n岗位职责：负责实时决策链路。"
    updated = contracts.enterprise_job_profile("ENTERPRISE_JOB_1")
    assert updated is not None
    assert updated["profile_version"] != first["profile_version"]
    assert "负责实时决策链路" in updated["core_responsibilities"]


def test_enterprise_job_graph_responsibility_ref_is_remapped_for_matching():
    source_id = "enterprise-job:ENTERPRISE_JOB_1:jd"
    jd_text = "岗位职责：负责银行反欺诈规则引擎。"
    extracted = extract_jd(
        source_id,
        jd_text,
        "反欺诈平台工程师",
    ).model_dump(mode="python")
    responsibility_id = str(extracted["responsibilities"][0]["requirement_id"])
    base = _kg_position_match_profile(
        PROFILE,
        position_id="ENTERPRISE_JOB_1",
        canonical_position_id="POS_BACKEND",
        canonical_title="反欺诈平台工程师",
    )
    assert base is not None
    reference = {
        "job_id": "ENTERPRISE_JOB_1",
        "title": "反欺诈平台工程师",
        "standard_position_id": "POS_BACKEND",
        "jd_text": jd_text,
        "requirement_graph": {
            "graph_version": "requirement-graph.v1",
            "status": "complete",
            "groups": [
                {
                    "requirement_group_id": "group-responsibility",
                    "group_type": "must",
                    "priority": "required",
                    "children": [
                        {
                            "node_type": "requirement_ref",
                            "ref_id": responsibility_id,
                        }
                    ],
                    "evidence": {
                        "source_id": source_id,
                        "quote": "负责银行反欺诈规则引擎",
                    },
                    "confidence": 0.9,
                }
            ],
            "unresolved_items": [],
        },
        "updated_at": "2026-08-17T09:00:00+08:00",
        "weights": (),
    }

    profile = _enterprise_job_match_profile(base, reference)
    graph = profile["requirement_graph"]
    assert isinstance(graph, dict)
    assert graph["groups"][0]["children"][0]["ref_id"] == "responsibility:1"
    assert profile["core_responsibilities"][0] == "负责银行反欺诈规则引擎"


def test_requirement_graph_reference_ids_remap_accepts_requirement_graph_model():
    graph = RequirementGraph(
        status="complete",
        groups=(
            RequirementGraphGroup(
                requirement_group_id="group-a",
                group_type="must",
                priority="required",
                children=(
                    RequirementGraphChild(
                        node_type="requirement_ref",
                        ref_id="responsibility:legacy-id",
                    ),
                ),
                evidence=Evidence(source_id="enterprise-job:JOB:jd", quote="负责服务"),
            ),
        ),
        unresolved_items=(),
    )
    remapped = _remap_requirement_graph_reference_ids(
        graph, {"responsibility:legacy-id": "responsibility:1"}
    )
    assert isinstance(remapped, RequirementGraph)
    assert remapped.groups[0].children[0].ref_id == "responsibility:1"


def test_snapshot_evidence_does_not_keep_zero_length_span_exact():
    projected = _snapshot_evidence(
        {
            "source_id": "src-1",
            "quote": "Python",
            "start": 3,
            "end": 3,
            "alignment": "exact",
            "occurrence_index": 0,
        },
        "src-1",
        "Python",
    )
    assert projected["alignment"] == "unresolved"
    assert projected["start"] is None
    assert projected["end"] is None


def test_matching_required_skills_use_prevalence_and_purity_gates():
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.4,
                    "min_required_prevalence_jd_count": 2,
                    "min_required_purity": 0.8,
                }
            }
        },
        "skill_relations": [
            {
                **PROFILE["skill_relations"][0],
                "modality": "required",
                "profile_tier": "market_core",
                "supporting_jd_count": 4,
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.75,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_SQL",
                "skill_name": "SQL",
                "modality": "required",
                "profile_tier": "specialty",
                "supporting_jd_count": 2,
                "required_supporting_jd_count": 1,
                "required_prevalence": 0.25,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_DOCKER",
                "skill_name": "Docker",
                "modality": "preferred",
                "profile_tier": "observed",
                "supporting_jd_count": 2,
                "required_supporting_jd_count": 0,
                "required_prevalence": 0.0,
                "required_purity": 0.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_UNKNOWN",
                "skill_name": "Unknown",
                "modality": "unknown",
                "profile_tier": "observed",
                "supporting_jd_count": 2,
                "required_supporting_jd_count": 0,
                "required_prevalence": 0.0,
                "required_purity": 0.0,
            },
        ],
    }
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    assert [item["skill_id"] for item in profile["required_skills"]] == [
        "SKILL_PYTHON"
    ]
    assert [item["skill_id"] for item in profile["preferred_skills"]] == [
        "SKILL_SQL",
        "SKILL_DOCKER",
    ]
    assert all(
        item["skill_id"] != "SKILL_UNKNOWN"
        for item in profile["required_skills"] + profile["preferred_skills"]
    )
    # Frequency metadata governs projection only. The strict Matching v1
    # requirement contract must not receive KG-internal statistics.
    allowed_requirement_fields = {
        "skill_id",
        "canonical_name",
        "required_level",
        "importance",
        "resolution_status",
        "evidence_refs",
    }
    assert all(
        set(item) <= allowed_requirement_fields
        for item in profile["required_skills"] + profile["preferred_skills"]
    )


def test_standard_position_core_gate_keeps_market_core_only_and_demotes_others():
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.2,
                    "min_required_prevalence_jd_count": 1,
                    "min_required_purity": 0.5,
                }
            }
        },
        "skill_relations": [
            {
                **PROFILE["skill_relations"][0],
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.6,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_SQL",
                "skill_name": "SQL",
                "modality": "required",
                "profile_tier": "specialty",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.7,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_DOCKER",
                "skill_name": "Docker",
                "modality": "required",
                "profile_tier": "observed",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.7,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_REDIS",
                "skill_name": "Redis",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 2,
                "required_prevalence": 0.3,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_KAFKA",
                "skill_name": "Kafka",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "enterprise_specific",
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.7,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_AWS",
                "skill_name": "AWS",
                "modality": "preferred",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.7,
                "required_purity": 1.0,
            },
        ],
    }

    shared = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    standard = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )

    assert shared is not None
    assert standard is not None
    assert {item["skill_id"] for item in shared["required_skills"]} == {
        "SKILL_PYTHON",
        "SKILL_SQL",
        "SKILL_DOCKER",
        "SKILL_REDIS",
        "SKILL_KAFKA",
    }
    assert [item["skill_id"] for item in standard["required_skills"]] == [
        "SKILL_PYTHON",
        "SKILL_SQL",
        "SKILL_DOCKER",
        "SKILL_REDIS",
        "SKILL_KAFKA",
    ]
    assert [item["skill_id"] for item in standard["preferred_skills"]] == [
        "SKILL_AWS",
    ]
    assert len(raw["skill_relations"]) == 6
    allowed_requirement_fields = {
        "requirement_id",
        "skill_id",
        "canonical_name",
        "required_level",
        "importance",
        "resolution_status",
        "evidence_refs",
    }
    assert all(
        set(item) <= allowed_requirement_fields
        for item in standard["required_skills"] + standard["preferred_skills"]
    )


def test_standard_position_projection_does_not_truncate_profile_skills():
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.1,
                    "min_required_prevalence_jd_count": 1,
                    "min_required_purity": 0.5,
                }
            }
        },
        "skill_relations": [
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_A",
                "skill_name": "Alpha",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 9,
                "required_prevalence": 0.9,
                "required_purity": 1.0,
                "support_ratio": 0.9,
                "supporting_jd_count": 90,
                "weight": 0.9,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_B",
                "skill_name": "Beta",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 8,
                "required_prevalence": 0.8,
                "required_purity": 1.0,
                "support_ratio": 0.8,
                "supporting_jd_count": 80,
                "weight": 0.8,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_C",
                "skill_name": "Gamma",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 7,
                "required_prevalence": 0.7,
                "required_purity": 1.0,
                "support_ratio": 0.7,
                "supporting_jd_count": 70,
                "weight": 0.7,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_D",
                "skill_name": "Delta",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 6,
                "required_prevalence": 0.6,
                "required_purity": 1.0,
                "support_ratio": 0.6,
                "supporting_jd_count": 60,
                "weight": 0.6,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_E",
                "skill_name": "Epsilon",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 5,
                "required_prevalence": 0.5,
                "required_purity": 1.0,
                "support_ratio": 0.5,
                "supporting_jd_count": 50,
                "weight": 0.5,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_F",
                "skill_name": "Zeta",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 4,
                "required_prevalence": 0.4,
                "required_purity": 1.0,
                "support_ratio": 0.4,
                "supporting_jd_count": 40,
                "weight": 0.4,
            },
        ],
    }
    before = deepcopy(raw)
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )

    assert profile is not None
    assert [item["skill_id"] for item in profile["required_skills"]] == [
        "SKILL_A",
        "SKILL_B",
        "SKILL_C",
        "SKILL_D",
        "SKILL_E",
        "SKILL_F",
    ]
    assert profile["preferred_skills"] == ()
    assert len(profile["required_skills"]) + len(profile["preferred_skills"]) == 6
    assert raw == before
    allowed_requirement_fields = {
        "requirement_id",
        "skill_id",
        "canonical_name",
        "required_level",
        "importance",
        "resolution_status",
        "evidence_refs",
    }
    assert all(
        set(item) <= allowed_requirement_fields
        for item in profile["required_skills"] + profile["preferred_skills"]
    )


def test_standard_position_projection_does_not_rank_skills_into_required():
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.1,
                    "min_required_prevalence_jd_count": 1,
                    "min_required_purity": 0.5,
                }
            }
        },
        "skill_relations": [
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_A",
                "skill_name": "Alpha",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 8,
                "required_prevalence": 0.8,
                "required_purity": 1.0,
                "support_ratio": 0.8,
                "supporting_jd_count": 80,
                "weight": 0.8,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_B",
                "skill_name": "Beta",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 9,
                "required_prevalence": 0.8,
                "required_purity": 1.0,
                "support_ratio": 0.7,
                "supporting_jd_count": 90,
                "weight": 0.9,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_C",
                "skill_name": "Gamma",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 9,
                "required_prevalence": 0.8,
                "required_purity": 1.0,
                "support_ratio": 0.7,
                "supporting_jd_count": 90,
                "weight": 0.4,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_D",
                "skill_name": "Delta",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 9,
                "required_prevalence": 0.8,
                "required_purity": 1.0,
                "support_ratio": 0.7,
                "supporting_jd_count": 90,
                "weight": 0.4,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_E",
                "skill_name": "Epsilon",
                "modality": "required",
                "profile_tier": "market_core",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 1,
                "required_prevalence": 0.15,
                "required_purity": 1.0,
                "support_ratio": 0.15,
                "supporting_jd_count": 15,
                "weight": 0.7,
            },
        ],
    }
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )

    assert profile is not None
    assert [item["skill_id"] for item in profile["required_skills"]] == [
        "SKILL_A",
        "SKILL_B",
        "SKILL_C",
        "SKILL_D",
        "SKILL_E",
    ]
    assert profile["preferred_skills"] == ()


def test_standard_position_routes_recover_source_jd_membership_and_dedupe():
    route_a = ("SKILL_CUDA", "SKILL_VLLM", "SKILL_SGLANG")
    route_b = ("SKILL_MEGATRON", "SKILL_RLHF", "SKILL_PYTORCH")
    relations = [
        {
            **PROFILE["skill_relations"][0],
            "skill_id": skill_id,
            "skill_name": skill_id.removeprefix("SKILL_").replace("_", " "),
            "modality": "required",
            "profile_tier": "specialty",
            "requirement_market_status": "market_supported",
            "required_supporting_jd_count": 2,
            "required_prevalence": 0.5,
            "required_purity": 1.0,
            "support_ratio": 0.5,
            "supporting_jd_count": 4,
        }
        for skill_id in (*route_a, *route_b)
    ]
    evidence_summary = [
        {
            "evidence_id": index,
            "document_id": document_id,
            "requirement_id": f"{document_id}:R{index}",
            "skill_id": skill_id,
            "quote": skill_id,
            "alignment": "exact",
        }
        for index, (document_id, skills) in enumerate(
            (
                ("JD_A", route_a),
                ("JD_B", route_b),
                ("JD_A_DUPLICATE_SOURCE", route_a),
            ),
            10,
        )
        for skill_id in skills
    ]
    evidence_summary.append(
        {
            "evidence_id": 0,
            "document_id": "",
            "requirement_id": "",
            "skill_id": "SKILL_CUDA",
            "quote": "ignored",
            "alignment": "exact",
        }
    )
    evidence_summary.append(
        {
            "evidence_id": 99,
            "document_id": "JD_B",
            "requirement_id": "JD_B:PREFERRED_CUDA",
            "skill_id": "SKILL_CUDA",
            "jd_modality": "preferred",
            "quote": "preferred CUDA",
            "alignment": "exact",
        }
    )
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.1,
                    "min_required_prevalence_jd_count": 1,
                    "min_required_purity": 0.5,
                }
            }
        },
        "skill_relations": relations,
        "evidence_summary": evidence_summary,
        "requirement_inflation": {
            "jd_diagnostics": [
                {
                    "document_id": document_id,
                    "requirements": [
                        {
                            "evidence_id": item["evidence_id"],
                            "requirement_id": item["requirement_id"],
                            "skill_id": item["skill_id"],
                            "jd_modality": "required",
                            "market_status": "market_supported",
                        }
                        for item in evidence_summary
                        if item.get("document_id") == document_id
                        and item.get("evidence_id", 0) > 0
                        and item.get("jd_modality") != "preferred"
                    ],
                }
                for document_id in ("JD_A", "JD_B", "JD_A_DUPLICATE_SOURCE")
            ]
        },
    }
    before = deepcopy(raw)

    profile = _standard_position_match_profile(
        raw,
        position_id="POS_AI_INFERENCE",
        canonical_position_id="POS_AI_INFERENCE",
        canonical_title="AI 推理优化工程师",
    )

    assert profile is not None
    graph = RequirementGraph.model_validate(profile["requirement_graph"])
    route_groups = [
        item
        for item in graph.groups
        if item.requirement_group_id.startswith("standard-route:")
    ]
    root = next(
        item
        for item in graph.groups
        if item.requirement_group_id.startswith("standard-route-root:")
    )
    assert root.group_type == "one_of"
    assert len(route_groups) == 2
    assert len(root.children) == 2
    groups_by_id = {item.requirement_group_id: item for item in graph.groups}

    def atomic_skill_ids(group):
        return tuple(
            sorted(
                child.ref_id.removeprefix("standard-position:skill:")
                if child.node_type == "requirement_ref"
                else skill_id
                for child in group.children
                for skill_id in (
                    (child.ref_id.removeprefix("standard-position:skill:"),)
                    if child.node_type == "requirement_ref"
                    else atomic_skill_ids(groups_by_id[child.ref_id])
                )
            )
        )

    assert {
        atomic_skill_ids(group)
        for group in route_groups
    } == {tuple(sorted(route_a)), tuple(sorted(route_b))}
    assert all(group.children for group in route_groups)
    assert {
        item["skill_id"]
        for item in profile["required_skills"] + profile["preferred_skills"]
    } == set(route_a + route_b)
    assert raw == before


def test_standard_position_route_preserves_alternatives_inside_one_source_requirement():
    skills = ("SKILL_PYTHON", "SKILL_CPP", "SKILL_PYTORCH", "SKILL_MEGATRON")
    relations = [
        {
            **PROFILE["skill_relations"][0],
            "skill_id": skill_id,
            "skill_name": skill_id.removeprefix("SKILL_"),
            "modality": "required",
            "profile_tier": "specialty",
            "requirement_market_status": "market_supported",
            "required_supporting_jd_count": 2,
            "required_prevalence": 0.5,
            "required_purity": 1.0,
            "support_ratio": 0.5,
            "supporting_jd_count": 4,
        }
        for skill_id in skills
    ]
    requirements = (
        ("R_LANG", ("SKILL_PYTHON", "SKILL_CPP"), "熟练 Python 或 C++"),
        ("R_TRAIN", ("SKILL_PYTORCH", "SKILL_MEGATRON"), "熟悉 PyTorch 和 Megatron"),
    )
    evidence_summary = [
        {
            "evidence_id": index,
            "document_id": "JD_AI",
            "requirement_id": requirement_id,
            "skill_id": skill_id,
            "jd_modality": "required",
            "quote": quote,
            "alignment": "exact",
        }
        for index, (requirement_id, requirement_skills, quote) in enumerate(requirements, 10)
        for skill_id in requirement_skills
    ]
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.1,
                    "min_required_prevalence_jd_count": 1,
                    "min_required_purity": 0.5,
                }
            }
        },
        "skill_relations": relations,
        "evidence_summary": evidence_summary,
    }

    profile = _standard_position_match_profile(
        raw,
        position_id="POS_AI",
        canonical_position_id="POS_AI",
        canonical_title="大模型算法工程师",
    )

    assert profile is not None
    graph = RequirementGraph.model_validate(profile["requirement_graph"])
    clause_groups = {
        group.group_type: {
            child.ref_id.removeprefix("standard-position:skill:")
            for child in group.children
        }
        for group in graph.groups
        if group.requirement_group_id.startswith("standard-clause:")
    }
    assert clause_groups["one_of"] == {"SKILL_PYTHON", "SKILL_CPP"}
    assert clause_groups["and"] == {"SKILL_PYTORCH", "SKILL_MEGATRON"}
    route = next(
        group
        for group in graph.groups
        if group.requirement_group_id.startswith("standard-route:")
    )
    assert route.group_type == "and"
    assert all(child.node_type == "group_ref" for child in route.children)


def test_enterprise_job_projection_does_not_apply_specialty_route_graph():
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.2,
                    "min_required_prevalence_jd_count": 1,
                    "min_required_purity": 0.5,
                }
            }
        },
        "skill_relations": [
            {
                **PROFILE["skill_relations"][0],
                "modality": "required",
                "profile_tier": "observed",
                "requirement_market_status": "market_supported",
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.7,
                "required_purity": 1.0,
            }
        ],
        "requirement_inflation": {
            "jd_diagnostics": [
                {
                    "document_id": "JD1",
                    "requirements": [
                        {
                            "evidence_id": 7,
                            "requirement_id": "R1",
                            "skill_id": "SKILL_PYTHON",
                            "jd_modality": "required",
                            "market_status": "market_supported",
                        }
                    ],
                }
            ]
        },
    }
    local = SimpleNamespace(
        position_reference=lambda value: {
            "position_id": value,
            "position_code": value,
            "position_name": "后端工程师",
        },
        enterprise_job_reference=lambda job_id: {
            "job_id": job_id,
            "title": "后端工程师",
            "standard_position_id": "POS_BACKEND",
            "jd_text": "",
            "requirement_graph": None,
            "updated_at": "2026-08-17T09:00:00+08:00",
            "weights": (),
        },
    )
    client = SimpleNamespace(
        position_profile=lambda *_args, **_kwargs: SimpleNamespace(data=raw)
    )
    contracts = KnowledgeGraphMatchingContractReader(local, client)

    standard = contracts.position_profile("POS_BACKEND")
    enterprise = contracts.enterprise_job_profile("ENTERPRISE_JOB_1")

    assert standard is not None
    assert enterprise is not None
    assert standard["required_skills"]
    assert standard["requirement_graph"] is not None
    assert enterprise["requirement_graph"] is None
    assert "SKILL_PYTHON" in {
        item["skill_id"] for item in enterprise["required_skills"]
    }


def test_enterprise_weights_do_not_bypass_required_gates():
    raw = {
        **PROFILE,
        "dependencies": {
            "position_profile_thresholds": {
                "required_skill_gate": {
                    "min_required_prevalence": 0.4,
                    "min_required_prevalence_jd_count": 2,
                    "min_required_purity": 0.8,
                }
            }
        },
        "skill_relations": [
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_PYTHON",
                "modality": "required",
                "profile_tier": "market_core",
                "supporting_jd_count": 4,
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.75,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_SQL",
                "skill_name": "SQL",
                "modality": "required",
                "profile_tier": "specialty",
                "supporting_jd_count": 2,
                "required_supporting_jd_count": 1,
                "required_prevalence": 0.25,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_DOCKER",
                "skill_name": "Docker",
                "modality": "required",
                "profile_tier": "observed",
                "supporting_jd_count": 3,
                "required_supporting_jd_count": 3,
                "required_prevalence": 0.75,
                "required_purity": 1.0,
            },
            {
                **PROFILE["skill_relations"][0],
                "skill_id": "SKILL_REDIS",
                "skill_name": "Redis",
                "modality": "preferred",
                "profile_tier": "observed",
                "supporting_jd_count": 3,
                "required_supporting_jd_count": 0,
                "required_prevalence": 0.0,
                "required_purity": 0.0,
            },
        ],
    }
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
        weights=(
            {"skill_id": "SKILL_PYTHON", "weight": 0.8, "is_required": True, "is_bonus": False},
            {"skill_id": "SKILL_SQL", "weight": 0.7, "is_required": True, "is_bonus": False},
            {"skill_id": "SKILL_DOCKER", "weight": 0.6, "is_required": False, "is_bonus": False},
            {"skill_id": "SKILL_REDIS", "weight": 0.5, "is_required": True, "is_bonus": False},
        ),
    )
    assert [item["skill_id"] for item in profile["required_skills"]] == [
        "SKILL_PYTHON",
        "SKILL_DOCKER",
    ]
    assert [item["skill_id"] for item in profile["preferred_skills"]] == [
        "SKILL_SQL",
        "SKILL_REDIS",
    ]


def test_matching_keeps_published_bonus_optional_and_does_not_use_modality_as_level():
    profile = {
        **PROFILE,
        "skill_relations": [
            {
                "skill_id": "SKILL_PYTHON",
                "skill_name": "Python",
                "weight": 0.8,
                "importance_level": "core",
                "modality": "bonus",
            }
        ],
    }
    mapped = _kg_position_match_profile(
        profile,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )

    assert mapped is not None
    assert mapped["required_skills"] == ()
    assert len(mapped["preferred_skills"]) == 1
    assert mapped["preferred_skills"][0]["required_level"] is None


def test_main_system_formal_graph_writes_are_closed():
    positions = ManagePositions(lambda: None)
    for name in ("build", "publish_version", "rollback", "delete_skill"):
        assert not hasattr(positions, name)


def _catalog_position_record() -> PositionRecord:
    return PositionRecord(
        position_id="main-position-id",
        position_name="后端开发工程师",
        source_emerging_position_id=None,
        core_responsibilities=(),
        required_skills=(),
        bonus_skills=(),
        industry_scenarios=(),
        status="catalog",
        created_at=None,
        updated_at=None,
        position_code="BACKEND_ENGINEER",
        taxonomy_version="position-taxonomy.v3.0.0",
    )


class _CatalogPositionRepository:
    def get(self, position_id):
        assert position_id == "main-position-id"
        return _catalog_position_record()


class _CatalogPositionUoW:
    positions = _CatalogPositionRepository()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


def test_authoritative_position_catalog_cannot_be_created_updated_or_deleted_by_crud():
    service = ManagePositions(_CatalogPositionUoW)
    admin = AccountActor("catalog-admin", "admin")
    draft = PositionDraft(
        position_name="后端开发工程师",
        source_emerging_position_id=None,
        core_responsibilities=(),
        required_skills=(),
        bonus_skills=(),
        industry_scenarios=(),
        status="catalog",
        position_code="BACKEND_ENGINEER",
    )

    with pytest.raises(PositionRuleViolation, match="catalog synchronization"):
        service.create(admin, draft)
    with pytest.raises(PositionRuleViolation, match="immutable"):
        service.update(
            admin,
            "main-position-id",
            PositionChanges(
                changed_fields=frozenset({"position_name"}),
                position_name="新名称",
            ),
        )
    with pytest.raises(PositionRuleViolation, match="immutable"):
        service.delete(admin, "main-position-id")


def test_trend_report_graph_version_is_an_external_kg_reference():
    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in TrendReport.__table__.c.graph_version_id.foreign_keys
    }
    assert foreign_keys == set()


def test_profile_pii_is_scrubbed_before_matching_boundary():
    """Quotes lifted from raw JDs may carry contact info; the boundary must scrub."""
    raw = deepcopy(PROFILE)
    raw["evidence_summary"][0]["quote"] = (
        "熟悉 Python，简历请发邮箱 hr@example.com 或联系电话 13800138000"
    )
    profile = _kg_position_match_profile(
        raw,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    assert profile is not None
    # The unscrubbed profile would be rejected by the matching-service gate.
    assert find_pii(profile)

    scrubbed = _scrub_profile_pii(profile)

    # Same rules the matching service enforces: scrubbed payload is accepted.
    assert find_pii(scrubbed) == ()
    quotes = [
        item.get("quote")
        for item in scrubbed.get("evidence_refs", ())
        if isinstance(item, dict)
    ]
    assert "[已脱敏]" in quotes
    assert all("13800138000" not in str(quote) for quote in quotes)


def test_profile_without_pii_is_left_untouched():
    profile = _kg_position_match_profile(
        PROFILE,
        position_id="POS_BACKEND",
        canonical_position_id="POS_BACKEND",
        canonical_title="后端工程师",
    )
    assert profile is not None
    assert not find_pii(profile)
    assert _scrub_profile_pii(profile) == profile
