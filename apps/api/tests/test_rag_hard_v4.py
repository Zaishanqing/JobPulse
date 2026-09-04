"""Focused regression tests for RAG-QA-HARD-01 v4 hardening."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_EVAL = _load(
    "run_rag_hard_hybrid_evaluation",
    _SCRIPTS / "run_rag_hard_hybrid_evaluation.py",
)
_BUILDER = _load(
    "build_rag_hard_qa_v3",
    _SCRIPTS / "build_rag_hard_qa_v3.py",
)


def test_query_intent_parser_has_no_benchmark_vocabulary_import() -> None:
    source = (
        _SCRIPTS / "run_rag_hard_hybrid_evaluation.py"
    ).read_text(encoding="utf-8")
    assert "from build_rag_hard_qa_v3 import TECH_TERMS" not in source
    intent = _EVAL._query_intent("该 JD 是否要求 Python 并有足够证据？")
    assert intent["intent_type"] == "REQUIREMENT_CONJUNCTION"
    assert intent["min_evidence_count"] == 2


def test_query_intent_maps_open_vocabulary_alias_independently() -> None:
    intent = _EVAL._query_intent("该 JD 是否要求大模型运维？")
    assert "llmops" in intent["required_concepts"]
    intent = _EVAL._query_intent("该 JD 是否要求 K8s 容器编排？")
    assert "kubernetes" in intent["required_concepts"]


def test_broad_query_never_reaches_resolver() -> None:
    called = False

    class Resolver:
        @staticmethod
        def resolve(_query_text: str):
            nonlocal called
            called = True
            return ()

    intent = _EVAL._query_intent("这个岗位要求什么？", Resolver())
    assert intent["insufficient_query_specificity"] is True
    assert intent["specificity"] == "broad"
    assert called is False


def test_implicit_capability_queries_are_specific() -> None:
    queries = (
        "该 JD 是否要求具备容器化部署能力？",
        "该 JD 是否要求能管理大规模容器集群？",
        "该 JD 是否要求能做模型训练与效果调优？",
        "该 JD 是否要求具备大模型应用落地经验？",
        "该 JD 是否要求熟悉高速缓存场景？",
    )
    for query in queries:
        assert _EVAL._classify_query_specificity(query) == "specific"


def test_broad_question_stays_broad_and_conflict_question_is_not_broad() -> None:
    assert _EVAL._classify_query_specificity("该 JD 的必备技能是什么？") == "broad"
    assert (
        _EVAL._classify_query_specificity("该 JD 是否存在同概念冲突证据？")
        == "specific"
    )


def test_conflict_query_is_refused() -> None:
    case = {
        "query_text": "该 JD 对 Java 的要求是否存在冲突？",
        "visible_evidence": [],
    }
    intent = {
        "insufficient_query_specificity": False,
        "resolver_status": "accepted",
        "required_concepts": ("java",),
        "resolved_concepts": (),
        "min_evidence_count": 1,
    }
    result = _EVAL._system_answerability([{"quote": "Java"}], case, intent)
    assert result["answerable"] is False
    assert result["reason"] == "conflict_detected"


def test_stale_visible_quote_propagates_to_pool_copy() -> None:
    case = {
        "query_text": "该 JD 当前版本是否要求 Redis？",
        "visible_evidence": [
            {"quote": "需要 Redis", "source_version": "2020-01-01 00:00:00"}
        ],
    }
    intent = {
        "insufficient_query_specificity": False,
        "resolver_status": "accepted",
        "required_concepts": ("redis",),
        "resolved_concepts": (),
        "min_evidence_count": 1,
    }
    result = _EVAL._system_answerability(
        [{"quote": "需要 Redis", "source_version": "2026-07-30 00:00:00"}],
        case,
        intent,
    )
    assert result["answerable"] is False
    assert result["reason"] == "no_fresh_evidence"


def test_semantic_support_threshold_controls_loose_matches() -> None:
    class Item:
        skill_id = "python"
        name = "Python"
        aliases = ()
        confidence = 1.0
        category = None
        lexical_score = 0.0
        semantic_score = 0.0
        margin = 0.0

    class Resolver:
        @staticmethod
        def support_score(_item, _quote: str, **kwargs) -> float:
            return 0.6

    case = {
        "query_text": "该 JD 是否要求 Python？",
        "visible_evidence": [],
    }
    intent = {
        "insufficient_query_specificity": False,
        "resolver_status": "accepted",
        "required_concepts": ("python",),
        "resolved_concepts": (Item(),),
        "min_evidence_count": 1,
    }
    result = _EVAL._system_answerability(
        [{"quote": "Java", "source_version": "2026-01-01 00:00:00"}],
        case,
        intent,
        Resolver(),
        resolver_support_threshold=0.65,
    )
    assert result["answerable"] is False
    assert result["reason"] == "concepts_not_covered"


def test_shared_evidence_pool_keeps_visible_overrides_case_local() -> None:
    snapshot_pool = {
        "ev-1": {
            "evidence_id": "ev-1",
            "document_id": "doc-1",
            "source_document_id": "doc-1",
            "source_version": "2026-01-01",
            "quote": "snapshot text",
            "tenant_ref": "jobgraph-platform-public",
            "permission_scope": "platform:public",
        }
    }
    case = {
        "query_text": "case text",
        "requested_identity": {
            "business_object": {"object_id": "doc-1"},
            "tenant_ref": "jobgraph-platform-public",
            "permission_scope": "platform:public",
        },
        "visible_evidence": [
            {
                "evidence_id": "ev-1",
                "source_document_id": "doc-1",
                "source_version": "2026-01-01",
                "quote": "case text",
                "tenant_ref": "jobgraph-platform-public",
                "permission_scope": "platform:public",
            }
        ],
    }
    hits = _EVAL._retrieve(case, snapshot_pool, "bm25_only")
    assert hits and hits[0]["evidence_id"] == "ev-1"
    assert hits[0]["quote"] == "case text"


def test_evidence_pool_does_not_embed_visible_evidence_globally() -> None:
    pool = _EVAL._evidence_pool(
        [{"visible_evidence": [{"evidence_id": "case-only"}]}],
        Path("missing-snapshot.db"),
    )
    assert "case-only" not in pool


def _evidence(evidence_id: str, document_id: str, quote: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "owner_type": "jd",
        "owner_ref": f"owner-{evidence_id}",
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
        "source_version": "2026-01-01 00:00:00",
        "graph_version_id": 1,
    }


def test_v4_manifest_contains_open_vocabulary_and_implicit_cases() -> None:
    evidence = [
        _evidence("e1", "doc-1", "Python 开发工程师"),
        _evidence("e2", "doc-1", "Java 后端工程师"),
        _evidence("e3", "doc-2", "MySQL 数据库运维"),
        _evidence("e4", "doc-3", "LLMOps 平台建设"),
        _evidence("e5", "doc-4", "Kubernetes 容器编排"),
        _evidence("e6", "doc-5", "PyTorch 模型训练"),
        _evidence("e7", "doc-6", "Redis 缓存优化"),
        _evidence("e8", "doc-7", "微服务架构设计"),
        _evidence("e9", "doc-8", "Docker 容器化部署"),
        _evidence("e10", "doc-9", "机器学习模型调优"),
        _evidence("e11", "doc-10", "分布式系统设计"),
        _evidence("e12", "doc-11", "大模型应用落地"),
        _evidence("e13", "doc-12", "常规行政岗位描述"),
        _evidence("e14", "doc-13", "通用文档与流程管理"),
        _evidence("e15", "doc-14", "无关键词的普通说明"),
    ]
    cases = _BUILDER._freeze_hard_qa(evidence, seed=20260816)
    assert len(cases) == sum(_BUILDER.SCENARIO_TARGETS.values())
    assert any(case["scenario"] == "open_vocabulary" for case in cases)
    assert any(case["scenario"] == "abbreviation_query" for case in cases)
    assert any(case["scenario"] == "implicit_skill" for case in cases)
    sufficient = [
        case for case in cases if case["scenario"] == "sufficient_evidence"
    ]
    insufficient = [
        case for case in cases if case["scenario"] == "insufficient_evidence"
    ]
    assert sufficient and "足够证据" in sufficient[0]["query_text"]
    assert insufficient and insufficient[0]["suggestion"]["answerable"] is False
    assert any(case["scenario"] == "conflict_pending" for case in cases)
