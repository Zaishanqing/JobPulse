import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
from time import perf_counter, sleep

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jobgraph_contracts.deepseek import (  # noqa: E402
    DeepSeekConnectionError,
    DeepSeekRateLimitError,
    DeepSeekServerError,
    DeepSeekTimeoutError,
    InvalidJSONError,
)
from src.config_iteration import (  # noqa: E402
    PROPOSAL_VERSION,
    _load_candidate_pool,
    _load_semantic_checkpoint,
    _normalization_key,
    _semantic_checkpoint_fingerprint,
    _write_semantic_checkpoint,
    load_iteration_policy,
    request_semantic_suggestions,
    write_review_workbook,
)
from src.normalizer import load_normalization_map, lookup_skill_mapping  # noqa: E402

DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
RETRYABLE_ERRORS = (
    DeepSeekConnectionError,
    DeepSeekRateLimitError,
    DeepSeekServerError,
    DeepSeekTimeoutError,
    InvalidJSONError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a checkpointed normalization review from an existing JD run."
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Existing extraction run directory containing manifest.json and final outputs.",
    )
    parser.add_argument(
        "--runs-root",
        default=None,
        help="Directory containing multiple extraction run directories; all runs share one thread pool.",
    )
    parser.add_argument(
        "--normalization",
        default="config/normalization_map.yaml",
        help="Path to normalization YAML.",
    )
    parser.add_argument(
        "--iteration-policy",
        default="config/iteration_policy.yaml",
        help="Config iteration policy.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-v4-flash",
        help="Model used for unresolved semantic suggestions.",
    )
    parser.add_argument(
        "--pending-review-dir",
        default=None,
        help="Review output directory; defaults to the iteration policy.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Environment file containing DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=50,
        help="Concurrent semantic candidate batches. Default: 50",
    )
    parser.add_argument(
        "--batch-attempts",
        type=int,
        default=2,
        help="Additional attempts per failed semantic batch. Default: 2",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Candidates per semantic request; defaults to iteration policy.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Maximum candidates in the review; defaults to iteration policy.",
    )
    return parser


def _pending_candidate_keys(
    pending_review_dir: Path,
    output: Path,
) -> set[tuple[str, str]]:
    pending_keys: set[tuple[str, str]] = set()
    for proposal_path in pending_review_dir.glob(
        "normalization_review_*.proposal.json"
    ):
        if proposal_path == output.with_suffix(".proposal.json"):
            continue
        workbook_path = proposal_path.with_name(
            proposal_path.name.removesuffix(".proposal.json") + ".xlsx"
        )
        if (
            not workbook_path.exists()
            or workbook_path.with_suffix(".applied.json").exists()
        ):
            continue
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        if proposal.get("proposal_version") != PROPOSAL_VERSION:
            continue
        for candidate in proposal.get("candidates", []):
            if isinstance(candidate, dict):
                pending_keys.add(
                    (
                        _normalization_key(
                            str(candidate.get("source_name"))
                        ),
                        str(candidate.get("item_type")),
                    )
                )
    return pending_keys


def _candidate_pool_snapshot(
    candidate_pool_path: str | Path,
    normalization_path: str,
    min_document_count: int,
    max_evidence_samples: int,
    max_candidates: int,
    pending_keys: set[tuple[str, str]],
) -> list[dict]:
    pool = _load_candidate_pool(candidate_pool_path)
    normalization_map = load_normalization_map(normalization_path)
    candidates_by_id: dict[str, dict] = {}
    for entry in pool["candidates"].values():
        source_name = str(entry.get("source_name", ""))
        item_type = str(entry.get("item_type", ""))
        document_count = len(set(entry.get("document_ids", [])))
        if document_count < min_document_count:
            continue
        if document_count <= int(
            entry.get("last_reviewed_document_count", 0)
        ):
            continue
        if lookup_skill_mapping(
            normalization_map,
            source_name,
            item_type,
        ) is not None:
            continue
        if (
            _normalization_key(source_name),
            item_type,
        ) in pending_keys:
            continue
        candidate_id = (
            "candidate_"
            + sha256(
                (
                    f"{_normalization_key(source_name)}"
                    f"\x1f{item_type}"
                ).encode("utf-8")
            ).hexdigest()[:24]
        )
        candidate = candidates_by_id.setdefault(
            candidate_id,
            {
                "candidate_id": candidate_id,
                "source_name": source_name,
                "item_type": item_type,
                "_document_ids": set(),
                "evidence_samples": [],
            },
        )
        candidate["_document_ids"].update(entry.get("document_ids", []))
        known_evidence = {
            (
                evidence.get("jd_id"),
                evidence.get("source_id"),
                evidence.get("quote"),
            )
            for evidence in candidate["evidence_samples"]
            if isinstance(evidence, dict)
        }
        for evidence in entry.get("evidence_samples", []):
            if not isinstance(evidence, dict):
                continue
            evidence_key = (
                evidence.get("jd_id"),
                evidence.get("source_id"),
                evidence.get("quote"),
            )
            if evidence_key not in known_evidence:
                candidate["evidence_samples"].append(evidence)
                known_evidence.add(evidence_key)
    candidates = []
    for candidate in candidates_by_id.values():
        candidate["document_count"] = len(candidate.pop("_document_ids"))
        candidate["evidence_samples"] = candidate[
            "evidence_samples"
        ][:max_evidence_samples]
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            -item["document_count"],
            item["source_name"],
        )
    )
    return candidates[:max_candidates]


def _request_batch(
    batch_number: int,
    total_batches: int,
    candidates: list[dict],
    normalization_path: str,
    model: str,
    max_attempts: int,
) -> dict[str, dict]:
    for attempt in range(1, max_attempts + 1):
        print(
            f"Semantic batch {batch_number}/{total_batches}, "
            f"attempt {attempt}/{max_attempts}: "
            f"{len(candidates)} candidates.",
            flush=True,
        )
        try:
            return request_semantic_suggestions(
                candidates,
                normalization_path,
                model,
            )
        except RETRYABLE_ERRORS as exc:
            if attempt == max_attempts:
                raise
            wait_seconds = min(2 ** (attempt - 1), 8)
            print(
                f"Semantic batch {batch_number}/{total_batches} "
                f"failed with {type(exc).__name__}; "
                f"retrying in {wait_seconds}s.",
                flush=True,
            )
            sleep(wait_seconds)
    raise AssertionError("Semantic retry loop exited without a result.")


def _load_compatible_checkpoint(
    checkpoint_path: Path,
    fingerprint: str,
    candidate_ids: set[str],
) -> tuple[set[str], dict[str, dict]]:
    completed_ids, suggestions = _load_semantic_checkpoint(
        checkpoint_path,
        fingerprint,
    )
    if completed_ids or not checkpoint_path.exists():
        return completed_ids, suggestions
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    legacy_completed = {
        candidate_id
        for candidate_id in payload.get("completed_candidate_ids", [])
        if isinstance(candidate_id, str) and candidate_id in candidate_ids
    }
    legacy_suggestions = {
        candidate_id: suggestion
        for candidate_id, suggestion in payload.get(
            "suggestions",
            {},
        ).items()
        if (
            candidate_id in legacy_completed
            and isinstance(suggestion, dict)
        )
    }
    if legacy_completed:
        print(
            f"Recovered {len(legacy_completed)} completed candidates "
            "from the pre-deduplication checkpoint.",
            flush=True,
        )
    return legacy_completed, legacy_suggestions


def _resolve_run_dirs(args) -> list[Path]:
    if args.runs_root is not None:
        root = Path(args.runs_root)
        if not root.is_dir():
            raise ValueError(f"Runs root does not exist: {root}")
        run_dirs = sorted(
            path
            for path in root.iterdir()
            if path.is_dir()
            and (path / "manifest.json").is_file()
            and not path.name.endswith("_b001_b001")
        )
        if not run_dirs:
            raise ValueError(f"No extraction runs found under: {root}")
        return run_dirs
    if args.run_dir is None:
        raise ValueError("Either --run-dir or --runs-root is required.")
    run_dir = Path(args.run_dir)
    if not (run_dir / "manifest.json").is_file():
        raise ValueError(f"Extraction run manifest does not exist: {run_dir}")
    return [run_dir]


def _prepare_run_bundle(
    run_dir: Path,
    args,
    policy: dict,
    pending_review_dir: Path,
    pending_keys: set[tuple[str, str]],
) -> dict:
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    run_id = manifest["run_id"]
    output = pending_review_dir / f"normalization_review_{run_id}.xlsx"
    candidates = _candidate_pool_snapshot(
        policy["candidate_pool_path"],
        args.normalization,
        policy["min_document_count"],
        policy["max_evidence_samples"],
        args.max_candidates or policy["max_candidates_per_review"],
        pending_keys,
    )
    checkpoint_path = output.with_suffix(".semantic-checkpoint.json")
    fingerprint = _semantic_checkpoint_fingerprint(
        candidates,
        args.normalization,
        args.model,
    )
    completed_ids, suggestions = _load_compatible_checkpoint(
        checkpoint_path,
        fingerprint,
        {candidate["candidate_id"] for candidate in candidates},
    )
    remaining = [
        candidate
        for candidate in candidates
        if candidate["candidate_id"] not in completed_ids
    ]
    semantic_batch_size = (
        args.batch_size or policy["semantic_request_batch_size"]
    )
    chunks = [
        remaining[offset: offset + semantic_batch_size]
        for offset in range(0, len(remaining), semantic_batch_size)
    ]
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "output": output,
        "checkpoint_path": checkpoint_path,
        "fingerprint": fingerprint,
        "candidates": candidates,
        "completed_ids": completed_ids,
        "suggestions": suggestions,
        "chunks": chunks,
        "total_batches": len(chunks),
    }


def main() -> None:
    args = build_parser().parse_args()
    if args.max_workers < 1:
        raise ValueError("max-workers must be at least 1.")
    if args.batch_attempts < 1:
        raise ValueError("batch-attempts must be at least 1.")
    if not args.env_file.is_file():
        raise ValueError(f"Environment file does not exist: {args.env_file}")
    load_dotenv(args.env_file, override=True)
    policy = load_iteration_policy(args.iteration_policy)
    if (args.batch_size or policy["semantic_request_batch_size"]) < 1:
        raise ValueError("batch-size must be at least 1.")
    pending_review_dir = Path(
        args.pending_review_dir or policy["pending_review_dir"]
    )
    run_dirs = _resolve_run_dirs(args)
    selected_keys: set[tuple[str, str]] = set()
    bundles = []
    for run_dir in run_dirs:
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        output = (
            pending_review_dir
            / f"normalization_review_{manifest['run_id']}.xlsx"
        )
        pending_keys = _pending_candidate_keys(
            pending_review_dir,
            output,
        )
        pending_keys.update(selected_keys)
        bundle = _prepare_run_bundle(
            run_dir,
            args,
            policy,
            pending_review_dir,
            pending_keys,
        )
        for candidate in bundle["candidates"]:
            selected_keys.add(
                (
                    _normalization_key(str(candidate["source_name"])),
                    str(candidate["item_type"]),
                )
            )
        bundles.append(bundle)

    total_batches = sum(bundle["total_batches"] for bundle in bundles)
    worker_count = min(args.max_workers, max(1, total_batches))
    started = perf_counter()
    print(
        f"Prepared {len(bundles)} runs, {total_batches} batches, "
        f"max_workers={worker_count}.",
        flush=True,
    )
    for bundle in bundles:
        remaining = len(bundle["chunks"])
        print(
            f"Run {bundle['run_id']}: {len(bundle['candidates'])} selected, "
            f"{len(bundle['completed_ids'])} resumed, "
            f"{sum(len(chunk) for chunk in bundle['chunks'])} remaining "
            f"({remaining} batches).",
            flush=True,
        )

    failures: list[tuple[str, int, BaseException]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {}
        for bundle in bundles:
            for batch_number, chunk in enumerate(
                bundle["chunks"],
                start=1,
            ):
                future = executor.submit(
                    _request_batch,
                    batch_number,
                    bundle["total_batches"],
                    chunk,
                    args.normalization,
                    args.model,
                    args.batch_attempts,
                )
                futures[future] = (bundle, batch_number, chunk)
        for future in as_completed(futures):
            bundle, batch_number, chunk = futures[future]
            try:
                chunk_suggestions = future.result()
            except BaseException as exc:
                failures.append((bundle["run_id"], batch_number, exc))
                print(
                    f"Semantic batch {batch_number}/"
                    f"{bundle['total_batches']} ({bundle['run_id']}) "
                    "exhausted retries.",
                    flush=True,
                )
                continue
            duplicate_ids = (
                set(bundle["suggestions"]) & set(chunk_suggestions)
            )
            if duplicate_ids:
                raise ValueError(
                    "Semantic batches returned duplicate candidate ids: "
                    + ", ".join(sorted(duplicate_ids))
                )
            bundle["suggestions"].update(chunk_suggestions)
            bundle["completed_ids"].update(
                candidate["candidate_id"] for candidate in chunk
            )
            _write_semantic_checkpoint(
                bundle["checkpoint_path"],
                bundle["fingerprint"],
                bundle["completed_ids"],
                bundle["suggestions"],
            )
            print(
                f"Semantic batch {batch_number}/{bundle['total_batches']} "
                f"completed ({bundle['run_id']}); checkpoint saved "
                f"({len(bundle['completed_ids'])}/"
                f"{len(bundle['candidates'])} candidates).",
                flush=True,
            )
            if len(bundle["completed_ids"]) == len(bundle["candidates"]):
                review_path = write_review_workbook(
                    bundle["output"],
                    bundle["run_id"],
                    bundle["candidates"],
                    bundle["suggestions"],
                )
                bundle["checkpoint_path"].unlink(missing_ok=True)
                print(
                    f"Normalization review completed in "
                    f"{perf_counter() - started:.1f}s: {review_path}",
                    flush=True,
                )
    if failures:
        failed_batches = ", ".join(
            f"{run_id}#{batch_number}"
            for run_id, batch_number, _ in sorted(
                failures,
                key=lambda item: (item[0], item[1]),
            )
        )
        raise RuntimeError(
            f"Semantic batches failed after retries: {failed_batches}. "
            "Rerun the same command to resume only failed batches."
        ) from failures[0][2]


if __name__ == "__main__":
    main()
