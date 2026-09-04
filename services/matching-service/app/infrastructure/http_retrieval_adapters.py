"""Pinned HTTP adapters for Stage E sparse retrieval and reranking."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.domain.vector_contracts import SemanticFragment, VectorContractViolation
from app.ports.retrieval import (
    RerankRequest,
    RerankScore,
    SparseHit,
    SparseQuery,
)


class _JsonEndpoint:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float,
        api_key: str | None,
        health_endpoint: str | None = None,
    ) -> None:
        if not endpoint or timeout_seconds <= 0:
            raise ValueError("HTTP retrieval endpoint and positive timeout are required")
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self._api_key = api_key
        self._health_endpoint = health_endpoint or endpoint

    def check_health(self) -> None:
        request = urllib.request.Request(self._health_endpoint, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if getattr(response, "status", 200) >= 400:
                    raise VectorContractViolation(
                        "RETRIEVAL_DEPENDENCY_UNAVAILABLE",
                        "retrieval dependency health check failed",
                    )
        except (TimeoutError, urllib.error.URLError) as exc:
            raise VectorContractViolation(
                "RETRIEVAL_DEPENDENCY_UNAVAILABLE",
                "retrieval dependency health check failed",
            ) from exc

    def _post(self, payload: dict[str, object]) -> object:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise TimeoutError("retrieval dependency timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("retrieval dependency timed out") from exc
            raise VectorContractViolation(
                "RETRIEVAL_DEPENDENCY_UNAVAILABLE", "retrieval dependency failed"
            ) from exc
        except json.JSONDecodeError as exc:
            raise VectorContractViolation(
                "RETRIEVAL_DEPENDENCY_UNAVAILABLE", "retrieval dependency failed"
            ) from exc


class HttpSparseRetrievalAdapter(_JsonEndpoint):
    def search(self, query: SparseQuery) -> tuple[SparseHit, ...]:
        payload = query.model_dump(mode="json")
        response = self._post(payload)
        if not isinstance(response, dict) or not isinstance(response.get("hits"), list):
            raise VectorContractViolation(
                "SPARSE_RESPONSE_INVALID", "sparse response contract is invalid"
            )
        hits = []
        for item in response["hits"]:
            if not isinstance(item, dict) or not isinstance(item.get("fragment"), dict):
                raise VectorContractViolation(
                    "SPARSE_RESPONSE_INVALID", "sparse response hit is invalid"
                )
            fragment_payload = dict(item["fragment"])
            try:
                hits.append(
                    SparseHit(
                        tenant_ref=item["tenant_ref"],
                        fragment=SemanticFragment.model_validate(fragment_payload),
                        score=item["score"],
                        active=item["active"],
                        superseded=item["superseded"],
                        profile_version=item["profile_version"],
                        source_version=item["source_version"],
                        index_revision=item["index_revision"],
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise VectorContractViolation(
                    "SPARSE_RESPONSE_INVALID", "sparse response hit is invalid"
                ) from exc
        return tuple(hits[: query.top_k])


class HttpRerankerAdapter(_JsonEndpoint):
    def rerank(self, request: RerankRequest) -> tuple[RerankScore, ...]:
        response = self._post(request.model_dump(mode="json"))
        if (
            not isinstance(response, dict)
            or response.get("model_revision") != request.model_revision
        ):
            raise VectorContractViolation(
                "RERANKER_REVISION_MISMATCH", "reranker revision is not pinned"
            )
        scores = response.get("scores")
        if not isinstance(scores, list):
            raise VectorContractViolation(
                "RERANKER_RESPONSE_INVALID", "reranker response contract is invalid"
            )
        return tuple(RerankScore.model_validate(item) for item in scores)


__all__ = ["HttpRerankerAdapter", "HttpSparseRetrievalAdapter"]
