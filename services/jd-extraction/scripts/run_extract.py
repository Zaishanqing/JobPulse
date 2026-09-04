from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.audit import build_run_id  # noqa: E402
from src.config_iteration import (  # noqa: E402
    apply_completed_reviews,
    collect_unresolved_candidates,
    load_iteration_policy,
    propose_from_run,
)
from src.exceptions import InputFormatError  # noqa: E402
from src.load_excel import load_excel_rows  # noqa: E402
from src.pipeline import JDExtractionPipeline  # noqa: E402
from scripts.reclassify_job_positions import (  # noqa: E402
    apply as apply_position_classification,
    classify as classify_positions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract structured JD annotations with DeepSeek.")
    parser.add_argument("--input", required=True, help="Path to the input CSV/XLSX/XLS file.")
    parser.add_argument(
        "--source-platform",
        required=True,
        help="Stable source platform identity, for example boss_zhipin or liepin.",
    )
    parser.add_argument("--output", default="output", help="Output directory. Default: output")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name. Default: deepseek-v4-flash")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
        help="Optional JD extraction environment file for position classification.",
    )
    parser.add_argument(
        "--normalization",
        default="config/normalization_map.yaml",
        help="Path to normalization YAML. Default: config/normalization_map.yaml",
    )
    parser.add_argument(
        "--skill-taxonomy-snapshot",
        default="config/skill_taxonomy_snapshot.json",
        help="Path to the reviewed main-catalog skill taxonomy snapshot.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing later rows when a single JD fails.",
    )
    parser.add_argument("--run-id", default=None, help="Optional audit run id. Default: timestamp.")
    parser.add_argument(
        "--audit-sample-rate",
        type=float,
        default=0.1,
        help="Deterministic audit sample rate for successful JDs without review_flags. Default: 0.1",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Maximum concurrent API calls. Default: 10",
    )
    parser.add_argument(
        "--semantic-retry-attempts",
        type=int,
        default=2,
        help="Bounded retries after Schema, Evidence, or deterministic semantic rejection. Default: 2",
    )
    parser.add_argument(
        "--row-indices",
        default=None,
        help="Optional comma-separated original 1-based input row indices, for example: 3,8,10",
    )
    parser.add_argument(
        "--iteration-policy",
        default="config/iteration_policy.yaml",
        help="Config iteration policy. Default: config/iteration_policy.yaml",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Rows per automatic config-iteration batch; overrides iteration policy.",
    )
    parser.add_argument(
        "--iteration-model",
        default=None,
        help="DeepSeek model for semantic config proposals; defaults to --model.",
    )
    parser.add_argument(
        "--pending-review-dir",
        default=None,
        help="Directory containing generated review workbooks; overrides iteration policy.",
    )
    parser.add_argument(
        "--generate-normalization-review",
        action="store_true",
        help=(
            "After each extraction batch, call the semantic model and generate the "
            "normalization review workbook. Default: only update the local candidate pool."
        ),
    )
    parser.add_argument(
        "--skip-position-classification",
        action="store_true",
        help="Keep the extraction run without applying position-taxonomy.v3.",
    )
    parser.add_argument(
        "--position-batch-size",
        type=int,
        default=1,
        help="Roles per position-classification request. Default: 1",
    )
    parser.add_argument(
        "--position-max-workers",
        type=int,
        default=None,
        help="Position-classification concurrency. Default: inherit --max-workers.",
    )
    return parser


def run_position_classification(
    run_dir: Path,
    args: argparse.Namespace,
) -> Path:
    output_root = Path(args.output) / "runs_position_v3"
    position_args = argparse.Namespace(
        run=[run_dir],
        runs_root=Path(args.output) / "runs",
        output_runs_root=output_root,
        catalog=PROJECT_ROOT / "config" / "position_taxonomy_catalog.v3.json",
        env_file=args.env_file,
        checkpoint=Path(args.output) / f"{run_dir.name}.checkpoint.json",
        report=Path(args.output) / f"{run_dir.name}.position_v3.report.json",
        model="deepseek-v4-flash",
        batch_size=args.position_batch_size,
        max_workers=args.position_max_workers or args.max_workers,
        max_attempts=3,
        limit_documents=None,
    )
    classify_positions(position_args)
    apply_position_classification(position_args)
    return output_root / f"{run_dir.name}_position_v3"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise InputFormatError(f"Input file does not exist: {input_path}")
    if args.position_batch_size < 1:
        raise InputFormatError("position-batch-size must be at least 1.")
    if args.position_max_workers is not None and args.position_max_workers < 1:
        raise InputFormatError("position-max-workers must be at least 1.")

    policy = load_iteration_policy(args.iteration_policy)
    batch_size = args.batch_size or policy["batch_size"]
    if batch_size < 1:
        raise InputFormatError("batch-size must be at least 1.")
    pending_review_dir = args.pending_review_dir or policy["pending_review_dir"]
    applied = apply_completed_reviews(
        pending_review_dir,
        args.normalization,
        policy["applied_review_dir"],
        policy["candidate_pool_path"],
        policy["min_create_new_document_count"],
        policy["decision_ledger_path"],
    )
    if applied:
        print(f"Applied reviewed normalization updates from {len(applied)} workbook(s).")

    total_rows = len(load_excel_rows(args.input))
    selected_indices = (
        [int(value.strip()) for value in args.row_indices.split(",")]
        if args.row_indices
        else list(range(1, total_rows + 1))
    )
    if len(selected_indices) != len(set(selected_indices)):
        raise InputFormatError("row-indices must not contain duplicates.")
    if any(index < 1 or index > total_rows for index in selected_indices):
        raise InputFormatError("row-indices contains a row outside the input range.")
    run_prefix = args.run_id or build_run_id()
    iteration_model = args.iteration_model or args.model
    for batch_number, offset in enumerate(range(0, len(selected_indices), batch_size), start=1):
        row_indices = set(selected_indices[offset: offset + batch_size])
        run_id = f"{run_prefix}_b{batch_number:03d}"
        pipeline = JDExtractionPipeline(
            model=args.model,
            normalization_path=args.normalization,
            continue_on_error=args.continue_on_error,
            run_id=run_id,
            audit_sample_rate=args.audit_sample_rate,
            max_workers=args.max_workers,
            semantic_retry_attempts=args.semantic_retry_attempts,
            row_indices=row_indices,
            source_platform=args.source_platform,
            skill_taxonomy_path=args.skill_taxonomy_snapshot,
        )
        pipeline.run(input_xlsx=args.input, output_dir=args.output)
        run_dir = Path(args.output) / "runs" / run_id
        if not args.skip_position_classification:
            classified_run = run_position_classification(run_dir, args)
            print(
                f"Batch {batch_number} position classification completed: "
                f"{classified_run}",
                flush=True,
            )
        if args.generate_normalization_review:
            proposal_started = perf_counter()
            print(
                f"Batch {batch_number} extraction and report completed. "
                f"Generating at most {policy['max_candidates_per_review']} "
                "normalization candidates...",
                flush=True,
            )
            review_path = propose_from_run(
                Path(args.output) / "runs" / run_id,
                args.normalization,
                pending_review_dir,
                iteration_model,
                policy["min_document_count"],
                policy["max_evidence_samples"],
                policy["candidate_pool_path"],
                policy["max_candidates_per_review"],
                policy["semantic_request_batch_size"],
            )
            proposal_elapsed = perf_counter() - proposal_started
            print(
                f"Batch {batch_number} completed in candidate stage "
                f"{proposal_elapsed:.1f}s. Pending review: {review_path}",
                flush=True,
            )
        else:
            candidates = collect_unresolved_candidates(
                Path(args.output) / "runs" / run_id,
                args.normalization,
                policy["min_document_count"],
                candidate_pool_path=policy["candidate_pool_path"],
            )
            print(
                f"Batch {batch_number} extraction and report completed. "
                f"Candidate pool updated locally; {len(candidates)} candidates "
                "currently qualify for review. Semantic review was not requested.",
                flush=True,
            )


if __name__ == "__main__":
    main()
