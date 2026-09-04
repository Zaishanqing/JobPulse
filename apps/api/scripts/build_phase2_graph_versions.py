from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.integrations.knowledge_graph.client import KnowledgeGraphClient  # noqa: E402

REVIEW_POLICY_VERSION = "review-policy.v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build, review, and publish GraphVersions for the phase-two observation window."
    )
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--version-prefix", required=True)
    args = parser.parse_args(argv)

    settings = Settings()
    if not settings.KNOWLEDGE_GRAPH_ENABLED:
        raise ValueError("KNOWLEDGE_GRAPH_ENABLED must be true")
    client = KnowledgeGraphClient(
        base_url=settings.KNOWLEDGE_GRAPH_BASE_URL,
        username=settings.KNOWLEDGE_GRAPH_SERVICE_USERNAME,
        password=settings.KNOWLEDGE_GRAPH_SERVICE_PASSWORD,
        timeout_seconds=settings.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
    )
    actor = {"actor_id": "phase2-graph-publisher", "actor_role": "admin"}
    published: list[dict[str, object]] = []
    try:
        for position in client.list_positions():
            position_id = position["position_id"]
            build = client.build_graph(
                position_id,
                {
                    "minimum_effective_weight": 0.05,
                    "minimum_valid_samples": 1,
                },
                **actor,
            ).data
            if not isinstance(build, dict) or not isinstance(
                build.get("build_run_id"), int
            ):
                raise RuntimeError(f"KG returned an invalid build result: {position_id}")
            run_id = build["build_run_id"]
            review = client.portal_call(
                "POST",
                f"/api/v1/graph/build-runs/{run_id}/auto-review",
                payload={
                    "policy_version": REVIEW_POLICY_VERSION,
                    "reason": "Policy-based auto review for the phase-two snapshot.",
                },
                **actor,
            ).data
            if int(review.get("requires_human_count") or 0) > 0:
                raise RuntimeError(
                    "human review required before publish: "
                    + json.dumps(review, ensure_ascii=False)
                )
            version_name = f"{args.version_prefix}-{position['category_code']}"
            version = client.portal_call(
                "POST",
                f"/api/v1/graph/build-runs/{run_id}/publish",
                payload={
                    "reason": "Publish the measured phase-two graph window.",
                    "version_name": version_name,
                    "release_notes": (
                        "Real JD observations from the 2026-07-16 and 2026-07-20 offline batches."
                    ),
                },
                **actor,
            ).data
            published.append(
                {
                    "position_id": position_id,
                    "build_run_id": run_id,
                    "review_count": len(selected),
                    "version": version,
                }
            )
        print(json.dumps({"published": published}, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
