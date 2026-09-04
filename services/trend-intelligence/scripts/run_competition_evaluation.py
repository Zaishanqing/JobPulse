from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.application.competition_evaluation import (  # noqa: E402
    REPORT_VERSION,
    evaluate_fixed_dataset,
    load_fixed_dataset,
    render_markdown,
)


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SERVICE_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the fixed Trend competition dataset through the current ranking algorithm."
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        dataset = load_fixed_dataset(
            SERVICE_ROOT / "evaluation" / "trend-competition-fixed.v2.json"
        )
        report = evaluate_fixed_dataset(dataset)
    except ValueError as exc:
        parser.error(str(exc))
    report["execution"] = {
        "command": subprocess.list2cmdline(["python", *sys.argv]),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
    }
    output_dir = args.output_dir or SERVICE_ROOT / "artifacts" / report["dataset_version"]
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / f"{REPORT_VERSION}.json"
    markdown_path = output_dir / f"{REPORT_VERSION}.md"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "dataset_version": report["dataset_version"],
                "report_version": report["report_version"],
                "json_report": str(result_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
