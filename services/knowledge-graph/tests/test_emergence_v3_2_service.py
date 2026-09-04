"""Targeted tests for the formal EMERGE v3.2 production policy service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder

from app.api.contracts import EmergenceV32EvaluateInput
from app.api import router as router_module
from app.emergence.emergence_v2 import (
    EmbeddingSemanticEncoder,
    SkillInfo,
)
from app.emergence.emergence_v3_1 import ReferenceProfile
from app.emergence.policy import EmergenceV32Policy, _CachedEmbedder
from app.infrastructure.providers.embedding import (
    BgeEmbeddingClient,
    EmbeddingContractViolation,
)


class _SkillIndex:
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


SKILL_INDEX = _SkillIndex(
    {
        "Java": _skill("Java", "LANG_JAVA"),
        "Python": _skill("Python", "LANG_PYTHON"),
        "PyTorch": _skill("PyTorch", "FRAMEWORK_PYTORCH"),
        "大模型": _skill("大模型", "AI_LLM"),
        "深度学习": _skill("深度学习", "KNOWLEDGE_DL"),
    }
)

BANK = [
    ReferenceProfile(
        family_id="SEARCH",
        canonical_title="搜索算法工程师",
        titles=("搜索算法工程师",),
        skills=("Java", "Python", "PyTorch", "大模型", "深度学习"),
        responsibilities=("搜索召回与排序算法研发",),
        member_document_ids=("doc-search-1",),
        source="formal-position-reference",
    )
]


class _VecEncoder:
    def __call__(self, text: str):
        if "搜索" in text:
            return (1.0, 0.0, 0.0)
        return (0.5, 0.5, 0.5)


def _policy() -> EmergenceV32Policy:
    encoder = EmbeddingSemanticEncoder(_VecEncoder())
    return EmergenceV32Policy(
        skill_index=SKILL_INDEX,
        bank=BANK,
        domain_keywords={},
        encoder=encoder,
        embedder=_VecEncoder(),
    )


def test_from_frozen_assets_requires_embedding_endpoint():
    with pytest.raises(ValueError, match="EMBEDDING_ENDPOINT"):
        EmergenceV32Policy.from_frozen_assets(
            config_dir=None,
            env={"KG_EMBEDDING_ENDPOINT": ""},
        )


def test_service_runs_stage1_stage2_pipeline():
    policy = _policy()
    explanation = policy.explain_candidate(
        title="地图搜索算法工程师",
        skills=("Java", "Python", "PyTorch", "大模型", "深度学习"),
        responsibilities=("负责搜索召回算法研发",),
    )
    assert explanation["top_k"][0]["family_id"] == "SEARCH"
    assert explanation["relation"] != "unexplained_structural_novelty"
    assert explanation["reference_core_skills_non_empty"] is True
    assert explanation["reference_core_inherited"] is True
    assert explanation["reference_core_domains"] == ["ai_intelligent_systems"]
    assert explanation["candidate_skill_domains"] == ["ai_intelligent_systems"]

    members = [
        {
            "date": "2026-07-27",
            "source_record_id": "jd-1",
            "content_hash": "h1",
            "company": "A",
            "platform": "feishu",
            "bundle_id": "b1",
            "region": "上海",
        },
        {
            "date": "2026-08-01",
            "source_record_id": "jd-2",
            "content_hash": "h2",
            "company": "B",
            "platform": "feishu",
            "bundle_id": "b2",
            "region": "北京",
        },
    ]
    layers = policy.cluster_layers(cluster_key="搜索算法", members=members)
    structural_evidence = {
        "reference_family": explanation["reference_family"],
        "reference_core_skills_non_empty": True,
        "reference_core_inherited": True,
        "reference_core_domains": ["ai_intelligent_systems"],
        "candidate_skill_domains": ["ai_intelligent_systems"],
        "explanation_combined": 0.4,
    }
    decisions = policy.decide_cluster(
        cluster_relation=explanation["relation"],
        layers=layers,
        structural_evidence=structural_evidence,
    )
    assert decisions["baseline"]["state"] in {
        "emerging",
        "weak_emerging_signal",
        "not_emerging",
        "insufficient_evidence",
    }
    assert decisions["no_temporal"]["state"] != "emerging"


def test_internal_http_adapter_returns_json_serializable_stage1_and_stage2(monkeypatch):
    monkeypatch.setattr(router_module, "_emergence_v32_policy", _policy)
    body = EmergenceV32EvaluateInput.model_validate(
        {
            "dataset_id": "emerging-discovery-full-temporal-v1",
            "clusters": [
                {
                    "cluster_id": "cluster-search",
                    "title": "地图搜索算法工程师",
                    "skills": ["Java", "Python", "PyTorch", "大模型", "深度学习"],
                    "responsibilities": ["负责搜索召回算法研发"],
                    "members": [
                        {
                            "document_id": "doc-1",
                            "source_record_id": "posting-1",
                            "content_hash": "sha256:one",
                            "observation_date": "2026-07-27",
                            "date_source": "publish_date",
                            "company": "A",
                            "source_platform": "source-a",
                        },
                        {
                            "document_id": "doc-2",
                            "source_record_id": "posting-2",
                            "content_hash": "sha256:two",
                            "observation_date": "2026-08-01",
                            "date_source": "publish_date",
                            "company": "B",
                            "source_platform": "source-b",
                        },
                    ],
                }
            ],
        }
    )

    response = router_module.evaluate_emergence_v32(
        body,
        SimpleNamespace(state=SimpleNamespace(trace_id="trace-v32")),
        user=SimpleNamespace(role="integration_service"),
    )
    payload = jsonable_encoder(response)["data"]

    assert payload["algorithm"] == "emerge_v3_2"
    assert payload["dataset_id"] == "emerging-discovery-full-temporal-v1"
    assert payload["clusters"][0]["stage1"]["top_k"][0]["family_id"] == "SEARCH"
    assert payload["clusters"][0]["counts"]["independent_postings"] == 2
    assert "temporal_layers" in payload["clusters"][0]


class _FakeResponse:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_embedding_client_fails_closed_on_contract_violation(monkeypatch):
    client = BgeEmbeddingClient(
        base_url="http://embedding:8000",
        model="BAAI/bge-m3",
        revision="r1",
        dimension=4,
    )

    def fake_post(*args, **kwargs):
        return _FakeResponse(
            200,
            {
                "vectors": [[0.1, 0.2, 0.3, 0.4]],
                "model_id": "BAAI/bge-m3",
                "model_revision": "r1",
                "dimension": 4,
                "normalized": True,
                "representation": "dense",
                "similarity": "cosine",
            },
        )

    monkeypatch.setattr("httpx.post", fake_post)
    vectors = client.embed_batch(["text"])
    assert len(vectors) == 1

    def fake_bad(*args, **kwargs):
        return _FakeResponse(
            200,
            {
                "vectors": [[0.1, 0.2, 0.3]],
                "model_id": "BAAI/bge-m3",
                "model_revision": "r1",
                "dimension": 4,
                "normalized": True,
                "representation": "dense",
                "similarity": "cosine",
            },
        )

    monkeypatch.setattr("httpx.post", fake_bad)
    with pytest.raises(EmbeddingContractViolation):
        client.embed_batch(["text"])


def test_cached_embedder_prewarms_and_reuses():
    calls = {"n": 0}

    class _Client:
        def embed_batch(self, texts):
            calls["n"] += 1
            return [[1.0, 0.0] for _ in texts]

    embedder = _CachedEmbedder(_Client())
    embedder.prewarm(["a", "b", "a"])
    assert calls["n"] == 1
    assert embedder("a") == [1.0, 0.0]
    assert calls["n"] == 1
