from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.acquisition.infrastructure.connectors import (
    AclConnector,
    ArxivConnector,
    CvfConnector,
    FundingConnector,
    GithubConnector,
    PolicyConnector,
)  # noqa: E402


def configurations() -> dict[str, dict[str, object]]:
    seed = json.loads((SERVICE_ROOT / "app" / "infrastructure" / "config_seed.json").read_text(encoding="utf-8"))
    return {item["config_type"]: item["payload"] for item in seed["configurations"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Optional live smoke for Acquisition connectors")
    parser.add_argument("source", choices=("arxiv", "policy", "cvf", "acl", "funding", "github"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=args.days)
    config = configurations()
    with httpx.Client(timeout=30, headers={"User-Agent": "JobgraphTrendIntelligence/1.0"}) as client:
        if args.source == "arxiv":
            connector = ArxivConnector(client, config, default_limit=args.limit)
            endpoint_config = {"limit": args.limit}
            rate_limit_rps = 0.34
        elif args.source == "cvf":
            connector = CvfConnector(client, config, default_limit=args.limit)
            endpoint_config = {"limit": args.limit}
            rate_limit_rps = 1.0
        elif args.source == "acl":
            connector = AclConnector(client, config, default_limit=args.limit)
            endpoint_config = {"limit": args.limit}
            rate_limit_rps = 1.0
        elif args.source == "funding":
            connector = FundingConnector(client, config)
            endpoint_config = {}
            rate_limit_rps = 1.0
        elif args.source == "github":
            connector = GithubConnector(client, config)
            endpoint_config = {"hours": 1}
            rate_limit_rps = 1.0
        else:
            connector = PolicyConnector(client, config)
            endpoint_config = {"queries": ["人工智能"], "per_query": args.limit}
            rate_limit_rps = 1.0
        records = connector.fetch(
            {
                "id": f"live-{args.source}",
                "source_type": args.source,
                "endpoint_config": endpoint_config,
                "rate_limit_rps": rate_limit_rps,
            },
            window_start,
            window_end,
        )
    print(json.dumps({
        "source": args.source,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fetched_count": len(records),
        "sample_external_ids": [record.external_id for record in records[:3]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
