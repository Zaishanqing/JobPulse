#!/usr/bin/env python3
"""Build the reproducible manifest for a frozen Responsibility CE directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "matching-service"))

from app.application.model_artifact import build_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    args = parser.parse_args()
    manifest = build_manifest(
        args.model_dir, model_id=args.model_id, model_revision=args.model_revision
    )
    output = args.model_dir / "manifest.json"
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(manifest["artifact_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
