from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real phase-two Matching evaluation.")
    parser.add_argument("--cv-id", required=True)
    parser.add_argument("--position-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    base_url = os.environ.get("PHASE2_MATCHING_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("PHASE2_MATCHING_TOKEN", "").strip()
    if not base_url or not token:
        raise ValueError("PHASE2_MATCHING_BASE_URL and PHASE2_MATCHING_TOKEN are required")
    response = httpx.post(
        f"{base_url}/api/v1/integrations/evaluate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "cv_id": args.cv_id,
            "position_id": args.position_id,
            "target_type": "standard_position",
        },
        timeout=30,
    )
    response.raise_for_status()
    envelope = response.json()
    data = envelope.get("data")
    if not isinstance(data, dict) or data.get("integration_status") != "completed":
        raise RuntimeError("Matching integration did not complete")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "integration_status": data["integration_status"],
                "evaluation_status": data["evaluation"]["evaluation_status"],
                "cv_skill_count": len(data["cv_profile"]["skills"]),
                "required_skill_count": len(data["position_profile"]["required_skills"]),
                "graph_version": data["position_profile"]["graph_version"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
