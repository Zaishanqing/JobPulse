"""Run the real CPU BGE-M3, Qdrant, Matching indexing and evaluation smoke."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from semantic_demo_contract import apply_repository_environment, load_contract


def _get(
    client: httpx.Client, url: str, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = client.get(url, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"GET {url} returned HTTP {response.status_code}: {response.text[:300]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"GET {url} returned a non-object response")
    return payload


def _post(client: httpx.Client, url: str, payload: object, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"POST {url} returned HTTP {response.status_code}: {response.text[:300]}")
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"POST {url} returned a non-object response")
    return body


def _token() -> str:
    existing = os.getenv("MATCHING_DEMO_TOKEN", "").strip()
    if existing:
        return existing
    env = os.environ.copy()
    env.setdefault(
        "MATCHING_AUTH_VERIFICATION_KEY",
        env.get("MATCHING_SERVICE_SIGNING_KEY", "jobgraph-matching-local-signing-key-change-me"),
    )
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "services"
                / "matching-service"
                / "scripts"
                / "mint_development_token.py"
            ),
            "--subject",
            "semantic-live-smoke",
            "--tenant",
            os.getenv("MATCHING_DEMO_TENANT_REF", "demo-tenant"),
            "--role",
            "matching.service",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not mint local matching token: {result.stderr.strip()}")
    return result.stdout.strip()


def _assert_contract(actual: dict[str, Any], contract: dict[str, str]) -> None:
    expected = {
        "model_id": contract["EMBEDDING_MODEL_ID"],
        "model_revision": contract["EMBEDDING_MODEL_REVISION"],
        "dimension": int(contract["EMBEDDING_DIMENSION"]),
        "representation": contract["EMBEDDING_REPRESENTATION"],
        "similarity": contract["EMBEDDING_SIMILARITY"],
        "normalized": contract["EMBEDDING_NORMALIZED"].lower() == "true",
    }
    for key, value in expected.items():
        if actual.get(key) != value:
            raise RuntimeError(f"embedding contract mismatch for {key}: {actual.get(key)!r} != {value!r}")


def _assert_embedding_response(actual: dict[str, Any], contract: dict[str, str]) -> None:
    expected = {
        "model_id": contract["EMBEDDING_MODEL_ID"],
        "model_revision": contract["EMBEDDING_MODEL_REVISION"],
        "dimension": int(contract["EMBEDDING_DIMENSION"]),
        "normalized": contract["EMBEDDING_NORMALIZED"].lower() == "true",
    }
    for key, value in expected.items():
        if actual.get(key) != value:
            raise RuntimeError(
                f"embedding response mismatch for {key}: {actual.get(key)!r} != {value!r}"
            )


def _wait_for_index(
    client: httpx.Client,
    matching_url: str,
    headers: dict[str, str],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _get(client, f"{matching_url}/internal/vector-index/status", headers)
        data = last.get("data", {})
        if (
            data.get("events", {}).get("processed", 0) >= 1
            and data.get("references", {}).get("indexed", 0) >= 1
            and not data.get("events", {}).get("dead_letter", 0)
        ):
            return data
        time.sleep(2)
    raise RuntimeError(f"vector index did not complete before timeout: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-url", default=os.getenv("EMBEDDING_LIVE_URL", "http://localhost:8001"))
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_LIVE_URL", "http://localhost:6333"))
    parser.add_argument("--matching-url", default=os.getenv("MATCHING_SEMANTIC_API_URL", "http://localhost:8010"))
    parser.add_argument("--cv-id", default=os.getenv("MATCHING_DEMO_CV_ID", "0e85144e-f8cc-4e3a-b4d5-8d3505586d78"))
    parser.add_argument("--position-id", default=os.getenv("MATCHING_DEMO_POSITION_ID", "84d0bd16-27f7-4cea-a349-70e3535a5d9a"))
    parser.add_argument("--tenant-ref", default=os.getenv("MATCHING_DEMO_TENANT_REF", "demo-tenant"))
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    apply_repository_environment()
    contract = load_contract()
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    collection = contract["MATCHING_QDRANT_COLLECTION"]
    try:
        with httpx.Client(timeout=15.0) as client:
            model = _get(client, f"{args.embedding_url.rstrip('/')}/v1/models/current")
            _assert_contract(model, contract)
            embedding = _post(
                client,
                f"{args.embedding_url.rstrip('/')}/v1/embeddings",
                {
                    "inputs": [
                        "负责分布式服务的架构设计、性能优化和线上故障处理。",
                        "在简历项目中搭建高并发微服务，完成缓存治理与稳定性建设。",
                    ],
                    "normalize": True,
                },
                {},
            )
            vectors = embedding.get("vectors")
            if not isinstance(vectors, list) or len(vectors) != 2:
                raise RuntimeError("embedding smoke returned the wrong vector count")
            dimension = int(contract["EMBEDDING_DIMENSION"])
            for vector in vectors:
                if not isinstance(vector, list) or len(vector) != dimension:
                    raise RuntimeError("embedding smoke returned an incompatible vector dimension")
                if not all(math.isfinite(float(value)) for value in vector):
                    raise RuntimeError("embedding smoke returned a non-finite vector")
                norm = math.sqrt(sum(float(value) ** 2 for value in vector))
                if abs(norm - 1.0) > 1e-5:
                    raise RuntimeError(f"embedding vector is not normalized: norm={norm}")
            _assert_embedding_response(embedding, contract)

            collection_info = _get(client, f"{args.qdrant_url.rstrip('/')}/collections/{collection}")
            result = collection_info.get("result", {})
            params = result.get("config", {}).get("params", {})
            vectors_config = params.get("vectors", {})
            if vectors_config.get("size") != dimension or str(vectors_config.get("distance")).lower() != "cosine":
                raise RuntimeError(f"Qdrant collection schema mismatch: {collection_info}")
            schema = result.get("payload_schema", {})
            for field in (
                "tenant_ref",
                "entity_type",
                "entity_id",
                "fragment_type",
                "profile_fingerprint",
                "embedding_model",
                "embedding_revision",
                "index_revision",
                "active",
            ):
                if field not in schema:
                    raise RuntimeError(f"Qdrant payload index is missing {field}")

            for entity_type, entity_id, event_type, target_type in (
                ("cv", args.cv_id, "cv_profile_published", "candidate_cv"),
                ("position", args.position_id, "position_profile_published", "standard_position"),
            ):
                _post(
                    client,
                    f"{args.matching_url.rstrip('/')}/internal/vector-index/profile-events",
                    {
                        "event_type": event_type,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "tenant_ref": args.tenant_ref,
                        "target_type": target_type,
                        "correlation_id": f"semantic-live-smoke-{entity_type}",
                    },
                    headers,
                )
            status = _wait_for_index(
                client,
                args.matching_url.rstrip("/"),
                headers,
                timeout_seconds=args.timeout_seconds,
            )
            points_response = client.post(
                f"{args.qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
                json={
                    "filter": {
                        "must": [
                            {"key": "tenant_ref", "match": {"value": args.tenant_ref}},
                            {"key": "entity_id", "match": {"value": args.cv_id}},
                            {"key": "active", "match": {"value": True}},
                        ]
                    },
                    "limit": 64,
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            if points_response.status_code >= 400:
                raise RuntimeError(
                    f"Qdrant point scroll returned HTTP {points_response.status_code}: "
                    f"{points_response.text[:300]}"
                )
            points = points_response.json().get("result", {}).get("points", [])
            if not points:
                raise RuntimeError("formal Matching indexing did not produce an active CV point")
            point_payload = points[0].get("payload", {})
            for key, expected in {
                "tenant_ref": args.tenant_ref,
                "entity_id": args.cv_id,
                "target_type": "candidate_cv",
                "embedding_model": contract["EMBEDDING_MODEL_ID"],
                "embedding_revision": contract["EMBEDDING_MODEL_REVISION"],
                "index_revision": contract["MATCHING_VECTOR_INDEX_REVISION"],
                "active": True,
            }.items():
                if point_payload.get(key) != expected:
                    raise RuntimeError(
                        f"indexed CV point mismatch for {key}: "
                        f"{point_payload.get(key)!r} != {expected!r}"
                    )
            evaluation = _post(
                client,
                f"{args.matching_url.rstrip('/')}/api/v1/integrations/evaluate",
                {
                    "cv_id": args.cv_id,
                    "position_id": args.position_id,
                    "tenant_ref": args.tenant_ref,
                    "target_type": "standard_position",
                },
                headers,
            )
            data = evaluation.get("data", {})
            evaluated = data.get("evaluation", {})
            evidence = evaluated.get("semantic_evidence") or evaluated.get("semantic_shadow_evidence") or []
            if data.get("integration_status") != "completed":
                raise RuntimeError(f"matching integration evaluation was not completed: {evaluation}")
            if evaluated.get("semantic_status") != "available" or not evidence:
                raise RuntimeError(f"semantic retrieval did not return evidence: {evaluation}")
            if evaluated.get("semantic_embedding_revision") != contract["EMBEDDING_MODEL_REVISION"]:
                raise RuntimeError("evaluation embedding revision mismatch")
            if evaluated.get("semantic_index_revision") != contract["MATCHING_VECTOR_INDEX_REVISION"]:
                raise RuntimeError("evaluation index revision mismatch")
            first_evidence = evidence[0]
            if first_evidence.get("candidate_source_id") != args.cv_id:
                raise RuntimeError("semantic evidence did not come from the indexed CV")
            if first_evidence.get("profile_fingerprint") != evaluated.get("cv_profile_fingerprint"):
                raise RuntimeError("semantic evidence profile fingerprint mismatch")
            if first_evidence.get("embedding_revision") != contract["EMBEDDING_MODEL_REVISION"]:
                raise RuntimeError("semantic evidence embedding revision mismatch")
            if first_evidence.get("index_revision") != contract["MATCHING_VECTOR_INDEX_REVISION"]:
                raise RuntimeError("semantic evidence index revision mismatch")
            if evaluated.get("semantic_weight", 0) != 0 or evaluated.get("semantic_effective_weight", 0) != 0:
                raise RuntimeError("semantic shadow changed formal scoring weights")
            print(
                json.dumps(
                    {
                        "model": model,
                        "qdrant_collection": collection,
                        "index_status": status,
                        "candidate_summary": evidence[0],
                        "evaluation_status": evaluated.get("evaluation_status"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"SEMANTIC_LIVE_SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1
    print("SEMANTIC_LIVE_SMOKE_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
