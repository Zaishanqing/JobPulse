"""Summarize requirement-strength calibration from position-profile JSON files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _profile(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def build_evaluation(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    aggregate = {
        "raw_required_requirement_count": 0,
        "market_supported_count": 0,
        "enterprise_specific_count": 0,
        "inflation_risk_count": 0,
        "cross_enterprise_supported_count": 0,
    }
    for payload in payloads:
        profile = _profile(payload)
        report = profile.get("requirement_inflation") or {}
        summary = report.get("summary") or {}
        raw_count = int(summary.get("total_required_requirement_count", 0))
        market_count = int(summary.get("market_supported_count", 0))
        enterprise_count = int(summary.get("enterprise_specific_count", 0))
        risk_count = int(summary.get("inflation_risk_count", 0))
        requirement_items = [
            item
            for diagnostic in report.get("jd_diagnostics", [])
            for item in diagnostic.get("requirements", [])
        ]
        cross_enterprise_count = sum(
            int((item.get("market") or {}).get(
                "leave_one_out_enterprise_count", 0
            )) > 0
            for item in requirement_items
        )
        calibrated_count = market_count
        rows.append(
            {
                "position_id": str(profile.get("position_id") or "unknown"),
                "graph_version": str(profile.get("graph_version") or "unknown"),
                "raw_required_requirement_count": raw_count,
                "calibrated_required_requirement_count": calibrated_count,
                "market_supported_count": market_count,
                "enterprise_specific_count": enterprise_count,
                "inflation_risk_count": risk_count,
                "cross_enterprise_supported_count": cross_enterprise_count,
                "inflation_suppression_ratio": round(
                    risk_count / max(raw_count, 1), 4
                ),
                "cross_enterprise_supported_ratio": round(
                    cross_enterprise_count / max(raw_count, 1), 4
                ),
            }
        )
        aggregate["raw_required_requirement_count"] += raw_count
        aggregate["market_supported_count"] += market_count
        aggregate["enterprise_specific_count"] += enterprise_count
        aggregate["inflation_risk_count"] += risk_count
        aggregate["cross_enterprise_supported_count"] += cross_enterprise_count

    raw_total = aggregate["raw_required_requirement_count"]
    aggregate["calibrated_required_requirement_count"] = aggregate[
        "market_supported_count"
    ]
    aggregate["inflation_suppression_ratio"] = round(
        aggregate["inflation_risk_count"] / max(raw_total, 1), 4
    )
    aggregate["cross_enterprise_supported_ratio"] = round(
        aggregate["cross_enterprise_supported_count"] / max(raw_total, 1),
        4,
    )
    return {
        "experiment_version": "requirement-inflation-evaluation.v1",
        "positions": rows,
        "aggregate": aggregate,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "| Position | Raw required | Calibrated | Market | Enterprise-specific "
        "| Inflation risk | Suppression |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["positions"]:
        lines.append(
            "| {position_id} | {raw_required_requirement_count} | "
            "{calibrated_required_requirement_count} | {market_supported_count} | "
            "{enterprise_specific_count} | {inflation_risk_count} | "
            "{inflation_suppression_ratio:.1%} |".format(**row)
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("profiles", nargs="+", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    payloads = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.profiles
    ]
    report = build_evaluation(payloads)
    if args.format == "markdown":
        print(_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
