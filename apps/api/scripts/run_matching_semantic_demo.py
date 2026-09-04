"""Start the semantic-demo Compose profile and run the real Matching smoke."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from semantic_demo_contract import CONTRACT_PATH, load_contract


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
PREFETCH = ROOT / "scripts" / "prefetch_bge_m3.py"
SMOKE = ROOT / "scripts" / "smoke_matching_semantic_live.py"
CACHE_DIR = ROOT / ".cache" / "embedding-models"


def _run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed with exit code {result.returncode}: {' '.join(command)}")


def _wait(url: str, timeout_seconds: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 400:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise RuntimeError(f"service did not become ready: {url} ({last_error})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="download the pinned model before starting Compose",
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    try:
        contract = load_contract()
        if shutil.which("docker") is None:
            raise RuntimeError("Docker CLI is not available")
        _run(["docker", "compose", "-f", str(COMPOSE), "--profile", "semantic-demo", "config", "--quiet"])
        if not CACHE_DIR.exists() or not any(CACHE_DIR.iterdir()):
            if not args.prefetch:
                raise RuntimeError(
                    f"pinned model cache is missing; run `python {PREFETCH} --cache-dir {CACHE_DIR}` "
                    f"using contract {CONTRACT_PATH}, or rerun with --prefetch"
                )
            _run([sys.executable, str(PREFETCH), "--cache-dir", str(CACHE_DIR)])
        elif args.prefetch:
            _run([sys.executable, str(PREFETCH), "--cache-dir", str(CACHE_DIR)])

        _run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "--profile",
                "semantic-demo",
                "up",
                "-d",
                "embedding-service",
                "qdrant",
                "matching-api-semantic-demo",
                "matching-vector-worker-semantic-demo",
            ]
        )
        _wait("http://localhost:8001/ready", args.timeout_seconds)
        _wait("http://localhost:8010/health/ready", args.timeout_seconds)
        _wait("http://localhost:9092/health/ready", args.timeout_seconds)
        smoke = subprocess.run(
            [
                sys.executable,
                str(SMOKE),
                "--embedding-url",
                "http://localhost:8001",
                "--qdrant-url",
                "http://localhost:6333",
                "--matching-url",
                "http://localhost:8010",
                "--timeout-seconds",
                str(args.timeout_seconds),
            ],
            cwd=ROOT,
            check=False,
        )
        if smoke.returncode != 0:
            raise RuntimeError(f"live smoke failed with exit code {smoke.returncode}")
        print(
            "MATCHING_SEMANTIC_DEMO_PASSED "
            f"model={contract['EMBEDDING_MODEL_ID']} "
            f"revision={contract['EMBEDDING_MODEL_REVISION']} "
            f"dimension={contract['EMBEDDING_DIMENSION']} "
            f"collection={contract['MATCHING_QDRANT_COLLECTION']} "
            f"index_revision={contract['MATCHING_VECTOR_INDEX_REVISION']}"
        )
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"MATCHING_SEMANTIC_DEMO_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
