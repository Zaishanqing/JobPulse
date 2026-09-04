from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.report_generator import generate_run_report, latest_run_dir  # noqa: E402
from src.run_renormalizer import renormalize_run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a concise Markdown research report for one extraction run.")
    parser.add_argument("--output-dir", default="output", help="Pipeline output directory. Default: output")
    parser.add_argument("--run-dir", default=None, help="Specific run directory. Default: latest run under output/runs")
    parser.add_argument("--report-path", default=None, help="Report path. Default: <run_dir>/research_report.md")
    parser.add_argument(
        "--renormalize",
        action="store_true",
        help="Rebuild normalization outputs from saved successful records before generating the report.",
    )
    parser.add_argument(
        "--normalization",
        default="config/normalization_map.yaml",
        help="Normalization YAML used with --renormalize. Default: config/normalization_map.yaml",
    )
    parser.add_argument(
        "--skill-taxonomy-snapshot",
        default="config/skill_taxonomy_snapshot.json",
        help="Reviewed skill taxonomy snapshot used with --renormalize.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else latest_run_dir(Path(args.output_dir))
    if args.renormalize:
        result = renormalize_run(
            run_dir,
            args.normalization,
            skill_taxonomy_path=args.skill_taxonomy_snapshot,
        )
        print(
            "Renormalized "
            f"{result['documents']} documents: {result['resolved_skills']}/{result['total_skills']} skills resolved."
        )
    report_path = Path(args.report_path) if args.report_path else None
    written_path = generate_run_report(run_dir, report_path=report_path)
    print(f"Wrote report: {written_path}")


if __name__ == "__main__":
    main()
