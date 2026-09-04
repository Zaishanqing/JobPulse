from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.api.contracts import DiscoveryRunRequest
from app.domain.discovery import JDStructuredData, JDSnapshot, SkillReference
from app.domain.values import FrozenDict
from app.infrastructure.multi_view import discover_multi_view
from app.infrastructure.semantic_embeddings import (
    LocalChineseSemanticEmbeddingProvider,
    SemanticProviderUnavailable,
)


class DemoSemanticEncoder:
    def encode(self, documents, **_kwargs):
        return np.asarray(
            [
                [
                    float("rag" in value.casefold() or "检索增强" in value),
                    float("客服" in value),
                    float("供应链" in value),
                ]
                for value in documents
            ],
            dtype=float,
        )


def _snapshot(index: int, month: int, title: str, responsibility: str, skills: tuple[str, ...]):
    return JDSnapshot(
        jd_id=f"real-jd-{index}",
        schema_version="v2",
        review_status="published",
        title=title,
        source_name=f"platform-{index % 2}",
        publish_date=date(2026, month, 10),
        structured_data=JDStructuredData(
            responsibilities=(responsibility,),
            required_skills=tuple(SkillReference(raw_skill=value) for value in skills),
            bonus_skills=(),
            business_scenarios=("智能客服",),
        ),
        source_fact_id=f"fact-{index}",
        source_fact_version=f"version-{index}",
        window_id=f"2026-{month:02d}",
        consumption_path="published",
    )


def _real_snapshots() -> tuple[JDSnapshot, ...]:
    return (
        _snapshot(1, 1, "RAG 应用工程师", "构建检索增强生成客服系统", ("Python", "RAG")),
        _snapshot(2, 2, "知识库问答工程师", "开发企业知识检索与问答服务", ("Python", "RAG")),
        _snapshot(3, 3, "大模型客服工程师", "优化智能客服检索生成链路", ("Python", "RAG")),
    )


def _provider():
    return LocalChineseSemanticEmbeddingProvider(encoder=DemoSemanticEncoder())


def test_three_view_candidates_merge_into_evidence_backed_cluster():
    result = discover_multi_view(_real_snapshots(), _provider(), FrozenDict())
    cluster = result.clusters[0]
    assert {item.jd_id for item in cluster.members} == {
        "real-jd-1",
        "real-jd-2",
        "real-jd-3",
    }
    assert {"text_semantic", "skill_set", "responsibility_expression"} <= set(
        cluster.algorithm_sources
    )
    assert cluster.core_skills == ("python", "rag")
    assert cluster.core_responsibilities
    assert cluster.semantic_centroid
    assert cluster.merge_basis["accepted_edges"]


def test_embedding_similarity_alone_never_creates_a_formal_cluster():
    snapshots = (
        _snapshot(1, 1, "RAG 工程师", "构建模型服务", ("Python",)),
        _snapshot(2, 2, "知识问答开发", "维护数据管道", ("Java",)),
    )
    result = discover_multi_view(
        snapshots,
        _provider(),
        FrozenDict(
            {
                "semantic_candidate_threshold": 0.5,
                "skill_cooccurrence_threshold": 0.9,
                "responsibility_similarity_threshold": 0.9,
                "supporting_view_threshold": 0.9,
            }
        ),
    )
    assert result.clusters == ()
    assert result.metadata["outcome"] == "no_supported_cluster"


def test_semantic_unavailability_is_explicit_or_fails_by_configuration():
    unavailable = LocalChineseSemanticEmbeddingProvider(model_path="missing-model")
    marked = discover_multi_view(
        _real_snapshots(),
        unavailable,
        FrozenDict({"semantic_failure_mode": "mark_unavailable"}),
    )
    assert marked.metadata["semantic_status"] == "unavailable"
    assert marked.clusters[0].merge_basis["semantic_status"] == "unavailable"
    assert marked.clusters[0].semantic_centroid == ()
    with pytest.raises(SemanticProviderUnavailable):
        discover_multi_view(
            _real_snapshots(),
            unavailable,
            FrozenDict({"semantic_failure_mode": "fail"}),
        )


def test_multiview_result_is_reproducible_for_same_snapshots_and_config():
    first = discover_multi_view(_real_snapshots(), _provider(), FrozenDict())
    second = discover_multi_view(_real_snapshots(), _provider(), FrozenDict())
    assert first == second


def test_real_input_contract_requires_three_continuous_windows_and_valid_config():
    payload = {
        "contract_version": "discovery.v2",
        "request_id": "real-history",
        "algorithm": "emerge_v3_2",
        "time_windows": [
            {"window_id": "w1", "start": "2026-01-01", "end": "2026-01-31"},
            {"window_id": "w2", "start": "2026-02-01", "end": "2026-02-28"},
            {"window_id": "w3", "start": "2026-03-01", "end": "2026-03-31"},
        ],
        "snapshots": [
            {
                "source_fact_id": item.source_fact_id,
                "source_fact_version": item.source_fact_version,
                "jd_id": item.jd_id,
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": item.title,
                "source_name": item.source_name,
                "publish_date": item.publish_date.isoformat(),
                "structured_data": {
                    "responsibilities": list(item.structured_data.responsibilities),
                    "required_skills": [
                        {"raw_skill": skill.raw_skill}
                        for skill in item.structured_data.required_skills
                    ],
                    "bonus_skills": [],
                    "business_scenarios": ["智能客服"],
                },
            }
            for item in _real_snapshots()
        ],
        "position_references": [
            {
                "position_id": "standard-backend",
                "graph_version_id": "graph-2026-03",
                "required_skills": [{"raw_skill": "Java"}],
            }
        ],
        "config": {"semantic_failure_mode": "mark_unavailable"},
    }
    assert DiscoveryRunRequest.model_validate(payload).time_windows[2].window_id == "w3"
    with pytest.raises(ValueError):
        DiscoveryRunRequest.model_validate({**payload, "snapshots": []})
    with pytest.raises(ValueError, match="continuous"):
        broken = {**payload, "time_windows": [*payload["time_windows"]]}
        broken["time_windows"][1] = {
            "window_id": "w2",
            "start": "2026-02-02",
            "end": "2026-02-28",
        }
        DiscoveryRunRequest.model_validate(broken)
    with pytest.raises(ValueError, match="semantic_candidate_threshold"):
        DiscoveryRunRequest.model_validate(
            {**payload, "config": {"semantic_candidate_threshold": 1.1}}
        )
