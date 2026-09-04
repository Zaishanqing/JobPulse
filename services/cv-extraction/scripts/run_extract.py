from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.exceptions import InputFormatError  # noqa: E402
from src.pipeline import CVExtractionPipeline  # noqa: E402
from scripts.reclassify_cv_positions import (  # noqa: E402
    apply as apply_position_classification,
    classify as classify_positions,
)


DEFAULT_NORMALIZATION = (
    PROJECT_ROOT / "resources" / "normalization" / "2.0" / "normalization_map.yaml"
)
DEFAULT_SKILL_TAXONOMY = (
    PROJECT_ROOT / "resources" / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract structured CV annotations with DeepSeek.")
    parser.add_argument("--input", required=True, help="Path to the input CSV/XLSX/XLS file.")
    parser.add_argument("--output", default="output", help="Output directory. Default: output")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name. Default: deepseek-v4-flash")
    parser.add_argument(
        "--normalization",
        default=str(DEFAULT_NORMALIZATION),
        help="Path to the authoritative JD normalization YAML.",
    )
    parser.add_argument(
        "--skill-taxonomy-snapshot",
        default=str(DEFAULT_SKILL_TAXONOMY),
        help="Path to the reviewed main-catalog skill taxonomy snapshot.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing later rows when a single CV fails.",
    )
    parser.add_argument("--run-id", default=None, help="Optional audit run id. Default: timestamp.")
    parser.add_argument(
        "--audit-sample-rate",
        type=float,
        default=0.1,
        help="Deterministic audit sample rate for successful CVs without review_flags. Default: 0.1",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=20,
        help="Maximum concurrent API calls. Default: 20",
    )
    parser.add_argument(
        "--semantic-retry-attempts",
        type=int,
        default=2,
        help="Bounded retries after Schema, Evidence, or deterministic semantic rejection. Default: 2",
    )
    parser.add_argument(
        "--api-timeout-seconds",
        type=int,
        default=300,
        help="Timeout for one provider request before transport retry. Default: 300",
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
        output_root=output_root,
        catalog=PROJECT_ROOT / "resources" / "taxonomy" / "position" / "3.0"
        / "position_taxonomy_catalog.v3.json",
        env_file=PROJECT_ROOT / ".env",
        checkpoint=Path(args.output) / f"{run_dir.name}.checkpoint.json",
        report=Path(args.output) / f"{run_dir.name}.position_v3.report.json",
        model="deepseek-v4-flash",
        batch_size=args.position_batch_size,
        max_workers=args.position_max_workers or args.max_workers,
        max_attempts=3,
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

    pipeline = CVExtractionPipeline(
        model=args.model,
        normalization_path=args.normalization,
        continue_on_error=args.continue_on_error,
        run_id=args.run_id,
        audit_sample_rate=args.audit_sample_rate,
        max_workers=args.max_workers,
        semantic_retry_attempts=args.semantic_retry_attempts,
        api_timeout_seconds=args.api_timeout_seconds,
        skill_taxonomy_path=args.skill_taxonomy_snapshot,
    )
    pipeline.run(input_xlsx=args.input, output_dir=args.output)
    if not args.skip_position_classification:
        run_id = args.run_id
        if run_id is None:
            runs = [
                path
                for path in (Path(args.output) / "runs").iterdir()
                if path.is_dir()
            ]
            if not runs:
                raise FileNotFoundError("CV extraction did not create a run directory")
            run_dir = max(runs, key=lambda path: path.stat().st_mtime)
        else:
            run_dir = Path(args.output) / "runs" / run_id
        classified_run = run_position_classification(run_dir, args)
        print(f"Position classification completed: {classified_run}", flush=True)


if __name__ == "__main__":
    main()
