"""Verify production readiness and the mounted Responsibility CE digest."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from typing import Any


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/health/ready")
    return parser.parse_args()


def _fetch(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"readiness returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError("readiness response has an invalid envelope")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("readiness response has no data object")
    return data


def main() -> int:
    args = _parse_args()
    data = _fetch(args.url)
    if data.get("status") != "ready":
        raise RuntimeError(f"service is not ready: {data.get('status')!r}")
    components = data.get("components")
    if not isinstance(components, list):
        raise RuntimeError("readiness response has no component list")
    ce = next((item for item in components if item.get("component") == "responsibility_ce"), None)
    if not isinstance(ce, dict):
        raise RuntimeError("readiness response has no responsibility_ce component")
    if ce.get("status") != "ready" or ce.get("provider") != "model":
        raise RuntimeError(f"responsibility_ce is not a ready model: {ce}")
    digest = ce.get("artifact_digest")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise RuntimeError("responsibility_ce has no verified 64-character artifact digest")
    print(json.dumps({"status": "ready", "responsibility_ce": ce}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
