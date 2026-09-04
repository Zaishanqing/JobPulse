"""Index the largest published real-JD GraphVersion into Evidence RAG.

Run inside the main-backend container with the knowledge graph database URL
injected, for example:

  docker compose exec -e KNOWLEDGE_GRAPH_RAG_DATABASE_URL=... \
    main-backend python scripts/index_rag_real_position.py

The script picks the published GraphVersion with the most included JD
documents, maps its evidence into `evidence-rag-index.v1` records, and posts
them to the main system index endpoint in batches of at most 1000.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from urllib.error import HTTPError

from sqlalchemy import create_engine, text


PLATFORM_TENANT_REF = "jobgraph-platform-public"
PLATFORM_PERMISSION_SCOPE = "platform:public"
MAIN_URL = os.environ.get("RAG_MAIN_URL", "http://127.0.0.1:8000")
BATCH_SIZE = 1000
HTTP_TIMEOUT_SECONDS = int(
    os.environ.get("RAG_INDEX_HTTP_TIMEOUT_SECONDS", "1800")
)


def _request(
    url: str,
    payload: dict,
    token: str | None = None,
    *,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc


def _login() -> str:
    username = os.environ.get("COMPETITION_DEMO_MAIN_USERNAME")
    password = os.environ.get("COMPETITION_DEMO_MAIN_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "COMPETITION_DEMO_MAIN_USERNAME and COMPETITION_DEMO_MAIN_PASSWORD "
            "must be set"
        )
    response = _request(
        f"{MAIN_URL}/api/v1/auth/login",
        {"username": username, "password": password},
    )
    return response["data"]["access_token"]


def _existing_evidence_ids() -> set[str]:
    qdrant_url = os.environ.get("RAG_EVIDENCE_QDRANT_URL")
    collection = os.environ.get("RAG_EVIDENCE_COLLECTION")
    if not qdrant_url or not collection:
        print(
            "RAG_EVIDENCE_QDRANT_URL/COLLECTION missing; skipping existing-point check",
            flush=True,
        )
        return set()
    endpoint = f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"
    existing: set[str] = set()
    next_offset: object = None
    while True:
        payload: dict = {
            "limit": 1000,
            "with_payload": ["evidence_id"],
            "with_vector": False,
        }
        if next_offset is not None:
            payload["offset"] = next_offset
        response = _request(
            endpoint,
            payload,
            timeout=120,
        )
        result = response.get("result") or {}
        for point in result.get("points") or []:
            evidence_id = (point.get("payload") or {}).get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id:
                existing.add(evidence_id)
        next_offset = result.get("next_page_offset")
        if next_offset is None:
            break
    return existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Cap the number of indexed evidence records (smoke runs).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Records per index request; keep small enough to stay under the HTTP timeout.",
    )
    args = parser.parse_args(argv)
    if args.batch_size <= 0 or args.batch_size > BATCH_SIZE:
        raise SystemExit("--batch-size must be between 1 and 1000")

    kg_url = os.environ.get("KNOWLEDGE_GRAPH_RAG_DATABASE_URL")
    if not kg_url:
        raise SystemExit(
            "KNOWLEDGE_GRAPH_RAG_DATABASE_URL is required "
            "(knowledge_graph:...@knowledge-graph-postgres:5432/knowledge_graph)"
        )
    kg = create_engine(kg_url)
    main_url = os.environ.get("RAG_MAIN_DATABASE_URL") or os.environ.get(
        "DATABASE_URL"
    )
    if not main_url:
        raise SystemExit(
            "RAG_MAIN_DATABASE_URL or DATABASE_URL is required "
            "to resolve the main-system position mapping"
        )
    main_db = create_engine(main_url)

    with kg.connect() as connection:
        top = connection.execute(
            text(
                """
                SELECT gv.id AS graph_version_id,
                       gv.position_id, gv.version_name, gv.build_run_id,
                       COUNT(DISTINCT gbs.document_id) AS docs,
                       COUNT(e.id) AS evidence
                FROM graph_versions gv
                JOIN graph_build_runs br ON br.id = gv.build_run_id
                JOIN graph_build_samples gbs
                  ON gbs.build_run_id = br.id AND gbs.included
                LEFT JOIN extraction_evidence e
                  ON e.document_id = gbs.document_id
                WHERE gv.published_at IS NOT NULL
                GROUP BY gv.id, gv.position_id, gv.version_name, gv.build_run_id
                ORDER BY docs DESC, evidence DESC
                LIMIT 1
                """
            )
        ).mappings().first()
        if top is None:
            raise SystemExit("No published GraphVersion with included JD documents found.")

        rows = connection.execute(
            text(
                """
                SELECT gbs.document_id,
                       d.source_version,
                       d.source_fact_version,
                       e.id AS evidence_id,
                       e.quote,
                       e.start,
                       e."end" AS location_end,
                       e.alignment,
                       e.occurrence_index
                FROM graph_build_samples gbs
                JOIN jd_documents d ON d.document_id = gbs.document_id
                JOIN extraction_evidence e ON e.document_id = d.document_id
                WHERE gbs.build_run_id = :build_run_id AND gbs.included
                ORDER BY gbs.document_id, e.id
                """
            ),
            {"build_run_id": top.build_run_id},
        ).mappings().all()
    with main_db.connect() as connection:
        mapping = connection.execute(
            text(
                """
                SELECT main_system_id
                FROM knowledge_graph_entity_mappings
                WHERE entity_type = 'position'
                  AND knowledge_graph_id = :kg_position_id
                  AND sync_status IN ('synced', 'confirmed')
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {"kg_position_id": top.position_id},
        ).mappings().first()
        business_object_name = None
        if mapping is not None:
            name_row = connection.execute(
                text(
                    """
                    SELECT position_name
                    FROM standard_positions
                    WHERE id = :main_system_id
                    LIMIT 1
                    """
                ),
                {"main_system_id": str(mapping["main_system_id"])},
            ).mappings().first()
            business_object_name = (
                str(name_row["position_name"])
                if name_row is not None
                else None
            )
    main_db.dispose()
    if mapping is None:
        raise SystemExit(
            "No main-system position mapping found for "
            f"knowledge graph position {top.position_id}; "
            "indexing is skipped to avoid writing an unusable business_object_id"
        )
    business_object_id = str(mapping["main_system_id"])

    items: list[dict] = []
    for row in rows:
        quote = str(row["quote"] or "").strip()
        if not quote:
            continue
        items.append(
            {
                "evidence_id": f"real-jd:{row['document_id']}:{row['evidence_id']}",
                "business_object_type": "standard_position",
                "business_object_id": business_object_id,
                "business_object_name": business_object_name,
                "evidence_type": "jd_evidence",
                "source_object_type": "source_jd",
                "source_object_id": row["document_id"],
                "source_document_id": row["document_id"],
                "graph_version_id": top.graph_version_id,
                "source_version": (
                    row["source_version"]
                    or row["source_fact_version"]
                    or row["document_id"]
                ),
                "text": quote,
                "quote": quote,
                "location_start": (
                    int(row["start"]) if row["start"] is not None else 0
                ),
                "location_end": (
                    int(row["location_end"])
                    if row["location_end"] is not None
                    else len(quote)
                ),
                "occurrence_index": int(row["occurrence_index"] or 0),
                "alignment": str(row["alignment"] or "unresolved"),
                "graph_version": top.version_name,
                "tenant_ref": PLATFORM_TENANT_REF,
                "permission_scope": PLATFORM_PERMISSION_SCOPE,
            }
        )

    existing = _existing_evidence_ids()
    missing_all = [
        item for item in items if item["evidence_id"] not in existing
    ]
    missing = missing_all
    if args.max_items is not None:
        missing = missing_all[: args.max_items]

    print(
        json.dumps(
            {
                "position_id": top.position_id,
                "graph_version": top.version_name,
                "docs": int(top.docs),
                "evidence_total": int(top.evidence),
                "already_indexed": len(items) - len(missing_all),
                "items_to_index": len(missing),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not missing:
        print("NOTHING_TO_INDEX", flush=True)
        return 0

    token = _login()
    indexed = 0
    for start in range(0, len(missing), args.batch_size):
        chunk = missing[start : start + args.batch_size]
        response = _request(
            f"{MAIN_URL}/api/v1/rag/evidence/index",
            {"items": chunk},
            token=token,
        )
        count = int(response["data"]["indexed_count"])
        indexed += count
        print(
            f"batch {start // args.batch_size + 1}: indexed {count} "
            f"(total {indexed}/{len(missing)})",
            flush=True,
        )

    print(f"TOTAL_INDEXED={indexed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
