"""Prefetch the pinned BGE-M3 snapshot used by the semantic shadow demo."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from semantic_demo_contract import CONTRACT_PATH, load_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".cache" / "embedding-models",
        help="host directory mounted as /models by the semantic-demo Compose profile",
    )
    args = parser.parse_args()
    contract = load_contract()
    repo_id = contract["EMBEDDING_MODEL_ID"]
    revision = contract["EMBEDDING_MODEL_REVISION"]
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download

        snapshot_path = Path(
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                cache_dir=str(args.cache_dir),
                # 与 embedding-service 的 backend.py 保持一致：
                # onnx/ 与 imgs/ 不被推理路径使用，排除后下载量约减半。
                ignore_patterns=["onnx/**", "imgs/**"],
            )
        )
    except Exception as exc:
        print(
            f"EMBEDDING_MODEL_DOWNLOAD_FAILED repo_id={repo_id} "
            f"revision={revision} error={type(exc).__name__}: {exc}"
        )
        return 1
    resolved_commit = snapshot_path.name
    if not re.fullmatch(r"[0-9a-f]{40}", resolved_commit):
        print(
            f"EMBEDDING_MODEL_LOAD_FAILED repo_id={repo_id} "
            f"revision={revision} snapshot={snapshot_path} resolved_commit={resolved_commit}"
        )
        return 1
    if resolved_commit != revision:
        print(
            f"EMBEDDING_REVISION_MISMATCH repo_id={repo_id} requested_revision={revision} "
            f"resolved_commit={resolved_commit}"
        )
        return 1
    print(f"repo_id={repo_id}")
    print(f"requested_revision={revision}")
    print(f"resolved_commit={resolved_commit}")
    print(f"snapshot_path={snapshot_path}")
    print(f"contract_path={CONTRACT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
