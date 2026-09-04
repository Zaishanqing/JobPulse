#!/usr/bin/env python3
"""Select clean, already-extracted 2026 Bundle JDs for graph evolution."""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter
from pathlib import Path


TARGET_POSITION_CODES = {
    "AI_AGENT_ENGINEER": "智能体开发工程师",
    "BACKEND_ENGINEER": "后端开发工程师",
    "LLM_ALGORITHM_ENGINEER": "大模型算法工程师",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-metadata-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def _package_rows(path: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(path) as archive:
        roots = {name.split("/", 1)[0] for name in archive.namelist() if "/" in name}
        if len(roots) != 1:
            raise ValueError(f"Expected one package root in {path}, got {sorted(roots)}")
        root = next(iter(roots))
        payload = json.loads(
            archive.read(f"{root}/final/normalized_annotations.json").decode("utf-8")
        )
    if not isinstance(payload, list):
        raise ValueError("normalized_annotations.json must contain a list")
    return payload


def _metadata(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result = {str(row.get("document_id") or ""): row for row in rows}
    if "" in result:
        raise ValueError(f"Source metadata contains an empty document_id: {path}")
    return result


def main() -> int:
    args = parse_args()
    metadata = _metadata(args.source_metadata_file)
    selected: list[dict[str, str]] = []
    for row in _package_rows(args.input):
        classification = row.get("job_classification") or {}
        position_code = str(classification.get("position_code") or "")
        if position_code not in TARGET_POSITION_CODES:
            continue
        document_id = str(row.get("document_id") or "")
        source = metadata.get(document_id)
        if source is None:
            continue
        crawl_date = str(source.get("crawl_date") or "")
        if not (crawl_date.startswith("2026-07-") or crawl_date.startswith("2026-08-")):
            continue
        selected.append(
            {
                "document_id": document_id,
                "position_code": position_code,
                "position_name": TARGET_POSITION_CODES[position_code],
                "crawl_date": crawl_date,
                "bundle_id": str(source.get("bundle_id") or ""),
                "source_platform": str(source.get("source_platform") or ""),
                "source_record_id": str(source.get("source_record_id") or ""),
            }
        )

    selected.sort(
        key=lambda item: (
            item["crawl_date"], item["position_code"], item["document_id"]
        )
    )
    if not 200 <= len(selected) <= 300:
        raise ValueError(
            f"Expected 200-300 clean target JDs from 2026 Bundles, got {len(selected)}"
        )
    position_counts = Counter(item["position_code"] for item in selected)
    missing_positions = TARGET_POSITION_CODES.keys() - position_counts.keys()
    if missing_positions:
        raise ValueError(f"No selected JD for positions: {sorted(missing_positions)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{item['document_id']}\n" for item in selected), encoding="utf-8"
    )
    time_counts = Counter(
        (item["position_code"], item["crawl_date"]) for item in selected
    )
    report = {
        "schema_version": "capability-evolution-selection.v1",
        "source_package": str(args.input.resolve()),
        "source_metadata": str(args.source_metadata_file.resolve()),
        "selection_count": len(selected),
        "position_counts": dict(sorted(position_counts.items())),
        "position_time_counts": [
            {"position_code": key[0], "crawl_date": key[1], "count": count}
            for key, count in sorted(time_counts.items())
        ],
        "records": selected,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "selection_count": len(selected),
        "position_counts": dict(sorted(position_counts.items())),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
