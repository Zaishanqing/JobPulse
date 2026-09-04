from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from app.config import Settings


class EmbeddingStartupError(RuntimeError):
    """Fail-closed startup error with a stable operator-facing code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EmbeddingInferenceError(RuntimeError):
    """Stable inference failure without request or vector details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DenseEmbeddingBackend(Protocol):
    def encode(
        self, inputs: tuple[str, ...], *, normalize: bool
    ) -> tuple[tuple[float, ...], ...]: ...


@runtime_checkable
class DenseRowsConvertible(Protocol):
    def tolist(self) -> object: ...


class BgeM3Backend:
    def __init__(self, settings: Settings) -> None:
        try:
            from huggingface_hub import snapshot_download

            model_path = snapshot_download(
                repo_id=settings.model_id,
                revision=settings.model_revision,
                cache_dir=settings.cache_dir,
                # 该 revision 的 onnx/ 与 imgs/ 不被 torch 推理路径使用，
                # 排除后首次下载量约减半（省掉 2.3GB 的 onnx 权重数据）。
                ignore_patterns=["onnx/**", "imgs/**"],
            )
        except Exception as exc:
            raise EmbeddingStartupError(
                "EMBEDDING_MODEL_DOWNLOAD_FAILED",
                (
                    f"failed to download {settings.model_id} at "
                    f"revision {settings.model_revision}: {type(exc).__name__}: {exc}"
                ),
            ) from exc
        try:
            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(
                model_path,
                use_fp16=settings.use_fp16,
                device=settings.device,
            )
        except Exception as exc:
            raise EmbeddingStartupError(
                "EMBEDDING_MODEL_LOAD_FAILED",
                (
                    f"failed to load {settings.model_id} at "
                    f"revision {settings.model_revision}: {type(exc).__name__}: {exc}"
                ),
            ) from exc
        self._batch_size = settings.batch_size
        self._dimension = settings.dimension
        self._device = settings.device
        self._use_fp16 = settings.use_fp16

    def encode(self, inputs: tuple[str, ...], *, normalize: bool) -> tuple[tuple[float, ...], ...]:
        result = self._model.encode(
            list(inputs),
            batch_size=self._batch_size,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        dense_vecs = result.get("dense_vecs") if isinstance(result, dict) else None
        if isinstance(dense_vecs, list | tuple):
            dense_rows = dense_vecs
        elif isinstance(dense_vecs, DenseRowsConvertible):
            dense_rows = dense_vecs.tolist()
        else:
            raise EmbeddingInferenceError("EMBEDDING_DENSE_VECS_MISSING")
        if not isinstance(dense_rows, list | tuple):
            raise EmbeddingInferenceError("EMBEDDING_OUTPUT_INVALID")
        try:
            vectors = tuple(tuple(float(value) for value in row) for row in dense_rows)
        except (TypeError, ValueError):
            raise EmbeddingInferenceError("EMBEDDING_OUTPUT_INVALID") from None
        if len(vectors) != len(inputs):
            raise EmbeddingInferenceError("EMBEDDING_OUTPUT_COUNT_MISMATCH")
        if any(len(vector) != self._dimension for vector in vectors):
            raise EmbeddingInferenceError("EMBEDDING_DIMENSION_MISMATCH")
        if any(not all(math.isfinite(value) for value in vector) for vector in vectors):
            raise EmbeddingInferenceError("EMBEDDING_OUTPUT_NON_FINITE")
        if normalize:
            normalized: list[tuple[float, ...]] = []
            for vector in vectors:
                norm = math.sqrt(sum(value * value for value in vector))
                if norm == 0:
                    raise EmbeddingInferenceError("EMBEDDING_ZERO_VECTOR")
                normalized.append(tuple(value / norm for value in vector))
            return tuple(normalized)
        raise EmbeddingInferenceError("EMBEDDING_NORMALIZATION_REQUIRED")


__all__ = ["BgeM3Backend", "DenseEmbeddingBackend", "EmbeddingInferenceError"]
