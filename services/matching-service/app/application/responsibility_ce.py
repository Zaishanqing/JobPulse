"""Production Cross-Encoder verifier for Responsibility Matching.

Final frozen deployment candidate:

    position responsibility
      -> BGE-M3 dense similarity (embedding-service HTTP)
      -> Dense Top3 candidate evidence
      -> mMARCO Cross-Encoder semantic verification
      -> frozen ge90 validation threshold
      -> ResponsibilityResult (MATCH / NONE)

If the CE model or the embedding service is unavailable, the verifier falls
back to the existing deterministic rule matcher and records the fallback.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.application.model_artifact import ModelArtifactError
from app.domain.context_matching import (
    ContextMatchingConfig,
    _candidate_texts,
    evaluate_responsibilities,
)
from app.domain.evaluation import ResponsibilityCandidate, ResponsibilityResult
from app.domain.profiles import (
    CVMatchProfile,
    Evidence,
    ExperienceFeature,
    PositionMatchProfile,
    PositionResponsibilityRequirement,
)


def responsibility_candidate_text(experience: ExperienceFeature) -> str:
    """Join every responsibility entry of one experience into retrieval text."""
    return " | ".join(experience.responsibilities).strip()


def _context_config(context_config: object) -> ContextMatchingConfig:
    if isinstance(context_config, ContextMatchingConfig):
        return context_config
    return ContextMatchingConfig()


@dataclass(frozen=True)
class ResponsibilityCEConfig:
    model_path: str
    threshold: float = 1.098377  # frozen ge90 median over validation folds
    top_k: int = 8
    max_length: int = 256
    embedding_url: str = "http://127.0.0.1:8001"
    embedding_model: str = "BAAI/bge-m3"
    embedding_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    embedding_dimension: int = 1024
    embedding_timeout_seconds: float = 30.0
    embedding_batch_size: int = 64
    embedding_cache_size: int = 4096
    verification_cache_size: int = 512
    fallback_to_rules: bool = True
    artifact_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.model_path or not Path(self.model_path).exists():
            raise ValueError("responsibility CE model path does not exist")
        if self.threshold < 0:
            raise ValueError("threshold must be non-negative")
        if self.top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not 1 <= self.embedding_batch_size <= 64:
            raise ValueError("embedding_batch_size must be between 1 and 64")
        if self.embedding_cache_size < 1:
            raise ValueError("embedding_cache_size must be at least 1")
        if self.verification_cache_size < 1:
            raise ValueError("verification_cache_size must be at least 1")


class ResponsibilityCEVerifier:
    """Loads the frozen CE once and replaces rule-based responsibility results."""

    def __init__(self, config: ResponsibilityCEConfig) -> None:
        # Delayed imports so importing the application package does not require
        # torch; only constructing the verifier loads the CE runtime.
        import torch  # noqa: PLC0415
        from transformers import (  # noqa: PLC0415
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self.config = config
        self._model = None
        self._tokenizer = None
        self._model_load_error: str | None = None
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                config.model_path,
                local_files_only=True,
                fix_mistral_regex=True,
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                config.model_path, local_files_only=True
            )
            self._model.to(self._device)
            self._model.eval()
        except Exception as exc:  # noqa: BLE001 - record and fall back
            self._model_load_error = f"{type(exc).__name__}: {exc}"
        self.last_fallback: str | None = None
        self.last_retrieval: dict[str, Any] | None = None
        self.last_candidate_mode: str | None = None
        self.last_candidate_count: int | None = None
        self.last_candidate_sources: tuple[str, ...] = ()
        self._embedding_cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._verification_cache: OrderedDict[str, tuple[ResponsibilityResult, ...]] = OrderedDict()
        self.embedding_cache_hits = 0
        self.embedding_cache_misses = 0
        self.verification_cache_hits = 0
        self.verification_cache_misses = 0

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_load_error(self) -> str | None:
        return self._model_load_error

    @property
    def artifact_digest(self) -> str | None:
        return self.config.artifact_digest

    def check_health(self) -> None:
        """Fail readiness when the frozen model is not loaded or verified."""
        if not self.model_loaded:
            raise RuntimeError(self._model_load_error or "responsibility CE model unavailable")
        if not self.artifact_digest:
            raise ModelArtifactError("responsibility CE artifact digest is missing")

    @property
    def device(self) -> str:
        return self._device

    @staticmethod
    def _lru_get(cache: OrderedDict[str, Any], key: str) -> Any | None:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value

    @staticmethod
    def _lru_put(cache: OrderedDict[str, Any], key: str, value: Any, max_size: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)

    def _embedding_vectors(self, texts: list[str]) -> dict[str, tuple[float, ...]] | None:
        cache = getattr(self, "_embedding_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._embedding_cache = cache
            self.embedding_cache_hits = 0
            self.embedding_cache_misses = 0
        ordered_unique = list(dict.fromkeys(texts))
        vectors: dict[str, tuple[float, ...]] = {}
        missing: list[str] = []
        for text in ordered_unique:
            cached = self._lru_get(cache, text)
            if cached is None:
                self.embedding_cache_misses += 1
                missing.append(text)
            else:
                self.embedding_cache_hits += 1
                vectors[text] = cached
        for offset in range(0, len(missing), self.config.embedding_batch_size):
            batch = missing[offset : offset + self.config.embedding_batch_size]
            try:
                response = httpx.post(
                    f"{self.config.embedding_url.rstrip('/')}/v1/embeddings",
                    json={"inputs": batch, "normalize": True},
                    timeout=self.config.embedding_timeout_seconds,
                )
            except httpx.HTTPError:
                return None
            if response.status_code >= 400:
                return None
            payload = response.json()
            raw_vectors = payload.get("vectors")
            if not isinstance(raw_vectors, list) or len(raw_vectors) != len(batch):
                return None
            try:
                parsed = [tuple(float(value) for value in row) for row in raw_vectors]
            except (TypeError, ValueError):
                return None
            for text, vector in zip(batch, parsed, strict=True):
                vectors[text] = vector
                self._lru_put(
                    cache,
                    text,
                    vector,
                    self.config.embedding_cache_size,
                )
        return vectors

    def _dense_topk_many(
        self,
        requirements: list[str],
        candidates: list[tuple[str, str, tuple[Evidence, ...]]],
    ) -> list[list[tuple[str, str, tuple[Evidence, ...], float, int]]] | None:
        texts = requirements + [item[1] for item in candidates]
        vectors = self._embedding_vectors(texts)
        if vectors is None:
            return None

        def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
            if len(a) != len(b):
                raise ValueError("embedding dimension mismatch")
            return sum(x * y for x, y in zip(a, b, strict=True))

        ranked_by_requirement = []
        for requirement in requirements:
            query = vectors[requirement]
            ranked = sorted(
                (
                    (
                        candidate[0],
                        candidate[1],
                        candidate[2],
                        cosine(query, vectors[candidate[1]]),
                        index + 1,
                    )
                    for index, candidate in enumerate(candidates)
                ),
                key=lambda item: item[3],
                reverse=True,
            )
            ranked_by_requirement.append(ranked[: self.config.top_k])
        return ranked_by_requirement

    def _dense_topk(
        self,
        requirement: str,
        candidates: list[tuple[str, str, tuple[Evidence, ...]]],
    ) -> list[tuple[str, str, tuple[Evidence, ...], float, int]] | None:
        """Compatibility wrapper over the batched production retrieval path."""
        ranked = self._dense_topk_many([requirement], candidates)
        return ranked[0] if ranked is not None else None

    def _ce_scores(
        self,
        requirement: str,
        candidates: list[tuple[str, str, tuple[Evidence, ...], float, int]],
    ) -> list[float]:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError(f"responsibility CE model unavailable: {self._model_load_error}")
        import torch  # noqa: PLC0415

        pairs = [(requirement, item[1]) for item in candidates]
        encoded = self._tokenizer(
            [p[0] for p in pairs],
            [p[1] for p in pairs],
            padding=True,
            truncation=True,
            max_length=self.config.max_length,
            return_tensors="pt",
        )
        encoded = {k: v.to(self._device) for k, v in encoded.items()}
        with torch.no_grad():
            logits = self._model(**encoded).logits.squeeze(-1)
        return [float(value) for value in logits.cpu().tolist()]

    def _fallback(
        self,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        context_config: Any,
    ) -> tuple[ResponsibilityResult, ...]:
        self.last_fallback = "RULE_FALLBACK"
        return evaluate_responsibilities(cv, position, context_config)

    def verify(
        self,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
        context_config: Any,
    ) -> tuple[ResponsibilityResult, ...]:
        config = _context_config(context_config)
        formal_requirements = position.responsibility_requirements
        legacy_requirements = not bool(formal_requirements)
        requirements = formal_requirements or tuple(
            PositionResponsibilityRequirement(
                requirement_id=f"responsibility:{index}",
                text=text,
                evidence_refs=(),
            )
            for index, text in enumerate(position.core_responsibilities, 1)
        )
        candidate_items = _candidate_texts(cv, config)
        candidates_all: list[tuple[str, str, tuple[Evidence, ...]]] = [
            (item.experience_id, item.text, item.evidence)
            for item in candidate_items
            if item.text.strip()
        ]
        self.last_candidate_mode = config.responsibility_candidate_mode
        self.last_candidate_count = len(candidates_all)
        self.last_candidate_sources = tuple(
            getattr(item, "source", "") for item in candidate_items
        )
        verification_key = json.dumps(
            {
                "candidates": [
                    {
                        "experience_id": item[0],
                        "text": item[1],
                        "evidence_refs": [
                            evidence.model_dump(mode="json")
                            for evidence in item[2]
                        ],
                    }
                    for item in candidates_all
                ],
                **(
                    {
                        "requirements": list(position.core_responsibilities),
                        "position_evidence_refs": [
                            evidence.model_dump(mode="json")
                            for evidence in position.evidence_refs
                        ],
                    }
                    if legacy_requirements
                    else {
                        "requirement_ids": [
                            item.requirement_id for item in requirements
                        ],
                        "requirements": [item.text for item in requirements],
                        "position_evidence_refs": [
                            evidence.model_dump(mode="json")
                            for requirement in requirements
                            for evidence in requirement.evidence_refs
                        ],
                    }
                ),
                "candidate_mode": config.responsibility_candidate_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        verification_cache = getattr(self, "_verification_cache", None)
        if verification_cache is None:
            verification_cache = OrderedDict()
            self._verification_cache = verification_cache
            self.verification_cache_hits = 0
            self.verification_cache_misses = 0
        cached_results = self._lru_get(verification_cache, verification_key)
        if cached_results is not None:
            self.verification_cache_hits += 1
            return cached_results
        self.verification_cache_misses += 1
        results: list[ResponsibilityResult] = []
        if self._model is None:
            if self.config.fallback_to_rules:
                return self._fallback(cv, position, context_config)
            raise RuntimeError(f"responsibility CE model unavailable: {self._model_load_error}")
        ranked_by_requirement = None
        if candidates_all:
            ranked_by_requirement = self._dense_topk_many(
                [item.text for item in requirements],
                candidates_all,
            )
            if ranked_by_requirement is None:
                if self.config.fallback_to_rules:
                    return self._fallback(cv, position, context_config)
                raise RuntimeError("responsibility dense retrieval unavailable")
        for index, requirement in enumerate(requirements):
            requirement_id = (
                f"responsibility:{index + 1}"
                if legacy_requirements
                else requirement.requirement_id
            )
            position_evidence = (
                tuple(
                    ev
                    for ev in position.evidence_refs
                    if ev.source_id.endswith(f":responsibility:{index + 1}")
                )
                if legacy_requirements
                else requirement.evidence_refs
            )
            requirement_text = requirement.text
            if not candidates_all:
                results.append(
                    ResponsibilityResult(
                        requirement_id=requirement_id,
                        position_requirement=requirement_text,
                        candidate_experience_id=None,
                        candidate_experience=None,
                        match_status="not_observed",
                        position_evidence=position_evidence,
                        candidate_evidence=(),
                        reason_code="RESPONSIBILITY_NOT_OBSERVED",
                        confidence=0.0,
                    )
                )
                continue
            if ranked_by_requirement is None:
                raise RuntimeError("responsibility dense retrieval unavailable")
            topk = ranked_by_requirement[index]
            scores = self._ce_scores(requirement_text, topk)
            best_index = max(range(len(topk)), key=lambda i: scores[i])
            best_score = scores[best_index]
            best = topk[best_index]
            top_candidates = tuple(
                ResponsibilityCandidate(
                    experience_id=item[0],
                    text=item[1],
                    retrieval_score=round(
                        max(-1.0, min(1.0, float(item[3]))), 6
                    ),
                    ce_score=round(score, 6),
                    threshold_margin=round(score - self.config.threshold, 6),
                    evidence_refs=item[2],
                )
                for item, score in zip(topk, scores, strict=True)
            )
            qualified = tuple(
                (item, score)
                for item, score in zip(topk, scores, strict=True)
                if score >= self.config.threshold
            )
            qualified_evidence = tuple(
                evidence
                for item, _score in qualified
                for evidence in item[2]
            )
            results.append(
                ResponsibilityResult(
                    requirement_id=requirement_id,
                    position_requirement=requirement_text,
                    candidate_experience_id=(
                        best[0] if best_score >= self.config.threshold else None
                    ),
                    candidate_experience=(
                        best[1] if best_score >= self.config.threshold else None
                    ),
                    match_status=(
                        "matched"
                        if best_score >= self.config.threshold
                        else "not_observed"
                    ),
                    position_evidence=position_evidence,
                    candidate_evidence=(
                        qualified_evidence if qualified else ()
                    ),
                    reason_code=(
                        "RESPONSIBILITY_MATCHED"
                        if best_score >= self.config.threshold
                        else "RESPONSIBILITY_NOT_OBSERVED"
                    ),
                    confidence=(
                        round(1.0 / (1.0 + math.exp(-best_score)), 6)
                        if best_score is not None
                        else 0.0
                    ),
                    ce_score=round(best_score, 6),
                    retrieval_score=round(
                        max(-1.0, min(1.0, float(best[3]))), 6
                    ),
                    threshold_margin=round(best_score - self.config.threshold, 6),
                    top_candidates=top_candidates,
                )
            )
        self.last_retrieval = {
            "top_k": self.config.top_k,
            "threshold": self.config.threshold,
            "embedding_model": self.config.embedding_model,
            "embedding_revision": self.config.embedding_revision,
        }
        finalized = tuple(results)
        self._lru_put(
            verification_cache,
            verification_key,
            finalized,
            self.config.verification_cache_size,
        )
        return finalized
