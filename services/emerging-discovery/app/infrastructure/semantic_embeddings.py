"""Chinese semantic embedding providers used by formal discovery."""

from __future__ import annotations

import os
import math
from pathlib import Path
from typing import Any

import numpy as np
import httpx

from app.domain.discovery import JDSnapshot


class SemanticProviderUnavailable(RuntimeError):
    pass


def _document(snapshot: JDSnapshot) -> str:
    structured = snapshot.structured_data
    skills = [
        item.identity
        for item in structured.required_skills + structured.bonus_skills
        if item.identity
    ]
    document = " ".join(
        [
            snapshot.title,
            *structured.responsibilities,
            *skills,
            *structured.business_scenarios,
        ]
    ).strip()
    # The shared embedding contract deliberately caps one input at 4096 chars.
    return document[:4096] or snapshot.jd_id[:4096]


class EmbeddingServiceSemanticProvider:
    """Strict client for the pinned BGE-M3 service.

    Any service or contract failure is surfaced as SemanticProviderUnavailable;
    formal multi-view discovery is configured to fail instead of degrading to a
    lexical-only result.
    """

    version = "embedding-service-bge-m3-v1"

    def __init__(self, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def embed(self, snapshots: tuple[JDSnapshot, ...]) -> tuple[tuple[float, ...], ...]:
        if not snapshots:
            return ()
        vectors: list[tuple[float, ...]] = []
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                for start in range(0, len(snapshots), 64):
                    batch = snapshots[start : start + 64]
                    response = client.post(
                        f"{self.base_url}/v1/embeddings",
                        json={"inputs": [_document(item) for item in batch], "normalize": True},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw_vectors = payload.get("vectors")
                    dimension = int(payload.get("dimension") or 0)
                    if (
                        payload.get("normalized") is not True
                        or not isinstance(raw_vectors, list)
                        or len(raw_vectors) != len(batch)
                        or dimension <= 0
                    ):
                        raise ValueError("invalid embedding response envelope")
                    for raw in raw_vectors:
                        if (
                            not isinstance(raw, list)
                            or len(raw) != dimension
                            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in raw)
                        ):
                            raise ValueError("invalid embedding vector")
                        norm = math.sqrt(sum(float(value) ** 2 for value in raw))
                        if norm <= 0 or abs(norm - 1.0) > 0.02:
                            raise ValueError("embedding vector is not normalized")
                        vectors.append(tuple(round(float(value), 12) for value in raw))
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise SemanticProviderUnavailable(
                "pinned BGE-M3 embedding service is unavailable or returned an invalid response"
            ) from exc
        return tuple(vectors)


class LocalChineseSemanticEmbeddingProvider:
    version = "local-chinese-sentence-transformer-v1"

    def __init__(
        self,
        model_path: str | None = None,
        *,
        encoder: Any | None = None,
    ) -> None:
        self.model_path = model_path or os.getenv("LOCAL_CHINESE_EMBEDDING_MODEL")
        self._encoder = encoder

    @property
    def available(self) -> bool:
        return self._encoder is not None or bool(self.model_path and Path(self.model_path).exists())

    def _load(self):
        if self._encoder is not None:
            return self._encoder
        if not self.model_path or not Path(self.model_path).exists():
            raise SemanticProviderUnavailable(
                "local Chinese semantic model is unavailable; configure "
                "LOCAL_CHINESE_EMBEDDING_MODEL or explicitly use baseline"
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise SemanticProviderUnavailable(
                "sentence-transformers is not installed; explicitly use baseline"
            ) from exc
        try:
            self._encoder = SentenceTransformer(
                self.model_path,
                local_files_only=True,
            )
        except Exception as exc:
            raise SemanticProviderUnavailable(
                "the configured local Chinese semantic model could not be loaded"
            ) from exc
        return self._encoder

    def embed(self, snapshots: tuple[JDSnapshot, ...]) -> tuple[tuple[float, ...], ...]:
        encoder = self._load()
        try:
            values = np.asarray(
                encoder.encode(
                    [_document(item) for item in snapshots],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ),
                dtype=float,
            )
        except Exception as exc:
            raise SemanticProviderUnavailable("local semantic embedding execution failed") from exc
        if values.ndim != 2 or values.shape[0] != len(snapshots):
            raise SemanticProviderUnavailable(
                "local semantic model returned an invalid embedding shape"
            )
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        normalized = values / np.where(norms == 0, 1, norms)
        return tuple(tuple(float(value) for value in row) for row in normalized.round(12))
