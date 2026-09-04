from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import JDExtractionResult, JDNormalizedResult
from src.exceptions import SemanticValidationError
from src.normalizer import load_normalization_map
from src.report_generator import read_jsonl, summarize_run
from src.validator import validate_semantic_constraints, validate_skill_item_type_contract


def evidence_records(value: Any):
    if isinstance(value, dict):
        evidence = value.get("evidence")
        if isinstance(evidence, dict):
            yield evidence
        for child in value.values():
            yield from evidence_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_records(child)


def main() -> None:
    parser = argparse.ArgumentParser(description="Strictly audit completed V2 run artifacts.")
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--normalization", default="config/normalization_map.yaml")
    parser.add_argument("--output")
    args = parser.parse_args()

    normalization_map = load_normalization_map(args.normalization)
    result = {"runs": [], "totals": {}}
    totals = {
        "documents": 0,
        "failed": 0,
        "review_flags": 0,
        "evidence": 0,
        "resolved_skills": 0,
        "normalized_skills": 0,
    }
    contract_violations: list[dict[str, Any]] = []
    for run_dir_text in args.run_dirs:
        run_dir = Path(run_dir_text)
        summary = summarize_run(run_dir)
        failed_integrity = [name for name, passed in summary["integrity_checks"].items() if not passed]
        if failed_integrity:
            raise ValueError(f"{run_dir.name} failed integrity checks: {failed_integrity}")
        annotations = read_jsonl(run_dir / "final" / "annotations.jsonl")
        normalized = read_jsonl(run_dir / "final" / "normalized_annotations.jsonl")
        evidence_count = 0
        for payload in annotations:
            annotation = JDExtractionResult.model_validate(payload)
            validate_semantic_constraints(annotation)
            try:
                validate_skill_item_type_contract(annotation, normalization_map)
            except SemanticValidationError as exc:
                contract_violations.extend(
                    {
                        "run_id": run_dir.name,
                        "document_id": payload.get("document_id"),
                        **violation,
                    }
                    for violation in exc.violations
                )
            for evidence in evidence_records(payload):
                evidence_count += 1
                if (
                    evidence.get("alignment") != "exact"
                    or not isinstance(evidence.get("start"), int)
                    or not isinstance(evidence.get("end"), int)
                    or evidence["end"] <= evidence["start"]
                ):
                    raise ValueError(
                        f"{run_dir.name}/{payload.get('document_id')} contains non-exact evidence: {evidence}"
                    )
        for payload in normalized:
            JDNormalizedResult.model_validate(payload)
        counts = summary["counts"]
        manifest = summary["manifest"]
        run_result = {
            "run_id": manifest["run_id"],
            "documents": counts["annotations"],
            "failed": manifest["failed_count"],
            "review_flags": summary["review_flags"],
            "job_classifications": summary["job_classifications"],
            "evidence": evidence_count,
            "resolved_skills": counts["resolved_skills"],
            "normalized_skills": counts["normalized_skills"],
            "integrity_checks_passed": len(summary["integrity_checks"]),
        }
        result["runs"].append(run_result)
        totals["documents"] += counts["annotations"]
        totals["failed"] += manifest["failed_count"]
        totals["review_flags"] += sum(count for _, count in summary["review_flags"])
        totals["evidence"] += evidence_count
        totals["resolved_skills"] += counts["resolved_skills"]
        totals["normalized_skills"] += counts["normalized_skills"]
    if contract_violations:
        raise ValueError(
            "Skill item type contract violations: "
            + json.dumps(contract_violations, ensure_ascii=False, separators=(",", ":"))
        )
    totals["skill_resolution_rate"] = round(
        totals["resolved_skills"] / totals["normalized_skills"], 6
    ) if totals["normalized_skills"] else None
    result["totals"] = totals
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
