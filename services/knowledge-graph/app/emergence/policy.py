"""EMERGE v3.2 final production policy service.

Final solution promoted from EXP-EMERGE-01 v3.2:
  - Stage1: v3.1 mature-reference occupation explanation (rules unchanged);
  - Stage2: v3.2 occupation-cluster acceptance (JD = observation,
    occupation cluster = analysis unit).

This service is the single formal entry for production consumers.  The
legacy ``app.domain.novelty`` five-dimension v1 chain is no longer the active
policy; it remains only as an archived module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.emergence.emergence_v2 import (
    EmbeddingSemanticEncoder,
    build_skill_index,
)
from app.emergence.emergence_v3_1 import (
    ReferenceProfile,
    build_cluster_temporal_layers,
    build_mature_reference_bank,
    explain_with_reference_ranking,
    retrieve_top_k,
)
from app.emergence.emergence_v3_2 import cluster_stage2_with_ablations
from app.infrastructure.providers.embedding import BgeEmbeddingClient

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CATALOG_PATH = CONFIG_DIR / "skill_taxonomy_catalog.v1.json"
BASELINE_PATH = CONFIG_DIR / "phase0_contract_baseline.json"

FORMAL_LLM: dict[str, tuple[str, ...]] = {
    "skills": ("Python", "PyTorch", "Transformer", "大模型", "NLP", "RAG", "微调"),
    "responsibilities": (
        "大模型预训练与微调",
        "模型推理与部署优化",
        "算法研发与实验",
        "多模态基础算法研究",
    ),
}
FORMAL_BACKEND: dict[str, tuple[str, ...]] = {
    "skills": ("Java", "Spring", "Python", "Go", "MySQL", "Redis", "微服务"),
    "responsibilities": ("后端服务开发", "数据库设计与优化", "分布式系统架构与实现"),
}

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_DIMENSION = 1024


def _env_value(
    env: Mapping[str, str],
    names: Sequence[str],
    default: str = "",
) -> str:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return default


class _CachedEmbedder:
    """Embedding cache with batch prewarm (no training; formal BGE client)."""

    def __init__(self, client: BgeEmbeddingClient):
        self._client = client
        self._cache: dict[str, list[float]] = {}

    def prewarm(self, texts: list[str]) -> None:
        missing = [text for text in texts if text not in self._cache]
        for start in range(0, len(missing), 64):
            vectors = self._client.embed_batch(missing[start : start + 64])
            for text, vector in zip(
                missing[start : start + 64], vectors, strict=True
            ):
                self._cache[text] = vector

    def __call__(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = self._client.embed_batch([text])[0]
        return self._cache[text]


class EmergenceV32Policy:
    """Formal EMERGE v3.2 chain: Stage1 explanation + Stage2 cluster decision."""

    def __init__(
        self,
        *,
        skill_index: Any,
        bank: list[ReferenceProfile],
        domain_keywords: Mapping[str, frozenset[str]],
        encoder: EmbeddingSemanticEncoder,
        embedder: Any = None,
    ) -> None:
        self.skill_index = skill_index
        self.bank = bank
        self.bank_by_family = {profile.family_id: profile for profile in bank}
        self.domain_keywords = domain_keywords
        self.encoder = encoder
        self.embedder = embedder

    @classmethod
    def from_frozen_assets(
        cls,
        *,
        config_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
        documents: Sequence[Mapping[str, Any]] = (),
        enrichment: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    ) -> "EmergenceV32Policy":
        """Build the formal policy from frozen taxonomy assets + embedding env."""
        config_dir = config_dir or CONFIG_DIR
        env = dict(env or {})
        catalog_path = config_dir / "skill_taxonomy_catalog.v1.json"
        baseline_path = config_dir / "phase0_contract_baseline.json"
        skill_index = build_skill_index(
            json.loads(catalog_path.read_text(encoding="utf-8")),
            json.loads(
                json.loads(
                    baseline_path.read_text(encoding="utf-8")
                )["artifacts"]["extraction.normalization-map.v2"]["content"]
            ),
        )
        bank = build_mature_reference_bank(
            documents=documents,
            formal_llm=FORMAL_LLM,
            formal_backend=FORMAL_BACKEND,
            min_family_size=5,
            enrichment=enrichment,
        )
        endpoint = _env_value(
            env,
            ("KG_EMBEDDING_ENDPOINT", "MATCHING_EMBEDDING_ENDPOINT"),
        )
        if not endpoint:
            raise ValueError(
                "KG_EMBEDDING_ENDPOINT (or MATCHING_EMBEDDING_ENDPOINT) is required"
            )
        timeout = float(
            _env_value(env, ("KG_EMBEDDING_TIMEOUT_SECONDS", "MATCHING_EMBEDDING_TIMEOUT_SECONDS"), "10")
        )
        client = BgeEmbeddingClient(
            base_url=endpoint,
            model=EMBEDDING_MODEL,
            revision=EMBEDDING_REVISION,
            dimension=EMBEDDING_DIMENSION,
            timeout_seconds=timeout,
        )
        embedder = _CachedEmbedder(client)
        encoder = EmbeddingSemanticEncoder(embedder)
        return cls(
            skill_index=skill_index,
            bank=bank,
            domain_keywords=skill_index.domain_keywords(),
            encoder=encoder,
            embedder=embedder,
        )

    def prewarm(self, texts: Sequence[str]) -> None:
        if self.embedder is not None:
            self.embedder.prewarm(list(texts))

    def explain_candidate(
        self,
        *,
        title: str,
        skills: Sequence[str],
        responsibilities: Sequence[str],
        exclude_document_ids: Sequence[str] = (),
        k: int = 3,
    ) -> dict[str, Any]:
        """Stage1: mature-reference explanation (v3.1 semantics, unchanged)."""
        top_k = retrieve_top_k(
            candidate_title=title,
            candidate_skills=tuple(skills),
            candidate_responsibilities=tuple(responsibilities),
            bank=self.bank,
            skill_index=self.skill_index,
            encoder=self.embedder,
            exclude_document_ids=tuple(exclude_document_ids),
            k=k,
        )
        explanation = explain_with_reference_ranking(
            title=title,
            skills=tuple(skills),
            responsibilities=tuple(responsibilities),
            top_k=top_k,
            bank_by_family=self.bank_by_family,
            skill_index=self.skill_index,
            encoder=self.encoder,
            domain_keywords=self.domain_keywords,
        )
        reference = self.bank_by_family.get(
            str(explanation.get("reference_family") or "")
        )
        candidate_infos = self.skill_index.resolve_many(tuple(skills))
        reference_infos = self.skill_index.resolve_many(
            tuple(reference.skills) if reference is not None else ()
        )
        skill_evidence = explanation.get("skill_dim_evidence")
        skill_components = (
            skill_evidence.components if skill_evidence is not None else {}
        )
        # Stage2's unexplained-novelty gate deliberately consumes facts already
        # produced by Stage1.  Keeping this derivation here avoids a second,
        # potentially divergent implementation in the HTTP adapter.
        structural_evidence = {
            "reference_core_skills_non_empty": bool(reference_infos),
            "reference_core_inherited": bool(
                skill_components.get("retained_core_skills")
            ),
            "reference_core_domains": sorted(
                {domain for info in reference_infos for domain in info.domains}
            ),
            "candidate_skill_domains": sorted(
                {domain for info in candidate_infos for domain in info.domains}
            ),
            "explanation_combined": float(
                explanation.get("explanation_components", {}).get("combined")
                or 0.0
            ),
        }
        return {"top_k": top_k, **explanation, **structural_evidence}

    def cluster_layers(
        self,
        *,
        cluster_key: str,
        members: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Six-layer temporal evidence for one occupation cluster."""
        return build_cluster_temporal_layers(
            cluster_key=cluster_key,
            members=members,
        ).to_dict()

    def decide_cluster(
        self,
        *,
        cluster_relation: str,
        layers: Mapping[str, Any],
        structural_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stage2: occupation-cluster decision + three minimal ablations."""
        return cluster_stage2_with_ablations(
            cluster_relation=cluster_relation,
            layers=layers,
            structural_evidence=structural_evidence,
        )
