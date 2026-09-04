"""CLI entry point for anonymous fixture-only offline evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.runner import OfflineEvaluator, load_dataset
from app.evaluation.stage_e import StageEOfflineEvaluator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage-e", action="store_true", help="emit the V2 Stage E report")
    parser.add_argument(
        "--semantic-thresholds",
        default="0.7,0.8,0.82,0.85,0.9",
        help="comma-separated offline candidates; production config is never changed",
    )
    args = parser.parse_args()
    thresholds = tuple(
        float(value.strip())
        for value in args.semantic_thresholds.split(",")
        if value.strip()
    )
    dataset = load_dataset(args.dataset)
    report = (
        StageEOfflineEvaluator().run(dataset)
        if args.stage_e
        else OfflineEvaluator().run(dataset, threshold_candidates=thresholds)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "output": str(args.output),
                "result_id": report.result_id,
                "fixture_notice": report.fixture_notice,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
