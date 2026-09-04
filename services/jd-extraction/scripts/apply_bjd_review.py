"""Apply AI-reviewed decisions to the B-JD-REAL-01 manifest and freeze gold.

The formal extraction result is deep-copied and frozen as an immutable
``baseline_prediction`` with an identity hash; it is never mutated by offset
repair or review corrections. The gold ``expected_graph`` is built from the
reviewed requirements with the fixed builder, the original extraction graph is
rebuilt with the frozen v0 builder, and the challenger graph is rebuilt with
the fixed builder from the pristine baseline.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import JDExtractionResult  # noqa: E402
from src.real_requirement_graph_evaluation import validate_real_manifest  # noqa: E402
from src.requirement_graph import build_requirement_graph, validate_requirement_graph  # noqa: E402
from src.requirement_graph_v0 import (  # noqa: E402
    BUILDER_IDENTITY as V0_BUILDER_IDENTITY,
    build_requirement_graph as build_requirement_graph_v0,
)


REVIEW_MODEL_DEFAULT = "gpt-5.6-luna (Codex desktop agent)"
PROMPT_VERSION_DEFAULT = "b-jd-ai-review.v1"
GOLD_TYPE = "ai_reviewed_extraction"
GOLD_VERSION = "ai-reviewed-gold.v1"
REQUIRED_ITEM_KINDS = {"skill", "certificate", "soft_skill", "tool"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _dict_digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _decisions_by_case(
    path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    actions_by_case: dict[str, list[dict[str, Any]]] = {}
    meta_by_case: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        case_id = str(row["case_id"])
        if any("basis" in action for action in row.get("actions", [])):
            raise ValueError(
                "expanded decisions detected in input; pass the authored "
                "review-decisions-source.jsonl instead of review-decisions.jsonl"
            )
        actions_by_case.setdefault(case_id, []).extend(row.get("actions", []))
        meta_by_case[case_id] = row
    return actions_by_case, meta_by_case


def _normalize_decisions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for action in actions:
        item = dict(action)
        item["action"] = item.get("action", "keep")
        item["requirement_id"] = item.get("requirement_id")
        item["reason"] = item.get("reason")
        normalized.append(item)
    return normalized


def _apply_actions(
    requirements: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    reviewed: list[dict[str, Any]] = []
    counts = {"keep": 0, "modify": 0, "delete": 0, "add": 0}
    by_id = {str(requirement["requirement_id"]): requirement for requirement in requirements}
    handled: set[str] = set()
    for action in _normalize_decisions(actions):
        action_type = action["action"]
        requirement_id = action.get("requirement_id")
        if action_type == "add":
            requirement = action.get("requirement")
            if not isinstance(requirement, dict) or not requirement.get("requirement_id"):
                raise ValueError("add action requires a requirement dict with requirement_id")
            if requirement["requirement_id"] in by_id:
                raise ValueError(f"add action duplicates existing id: {requirement['requirement_id']}")
            reviewed.append(copy.deepcopy(requirement))
            by_id[requirement["requirement_id"]] = requirement
            handled.add(requirement["requirement_id"])
            counts["add"] += 1
            continue
        if requirement_id is None:
            raise ValueError(f"{action_type} action requires requirement_id")
        handled.add(requirement_id)
        if action_type == "keep":
            reviewed.append(copy.deepcopy(by_id[requirement_id]))
            counts["keep"] += 1
        elif action_type == "modify":
            if action.get("replace"):
                merged = copy.deepcopy(action["requirement"])
            else:
                merged = copy.deepcopy(by_id[requirement_id])
                merged.update(copy.deepcopy(action.get("changes") or {}))
            reviewed.append(merged)
            counts["modify"] += 1
        elif action_type == "delete":
            counts["delete"] += 1
        else:
            raise ValueError(f"unsupported review action: {action_type}")
    for requirement_id, requirement in by_id.items():
        if requirement_id not in handled:
            reviewed.append(copy.deepcopy(requirement))
            counts["keep"] += 1
    return reviewed, counts


def _expand_decisions(
    case: dict[str, Any],
    actions: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Expand authored exception actions into explicit per-requirement rows."""
    normalized = _normalize_decisions(actions)
    by_id = {str(item.get("requirement_id")): item for item in normalized if item.get("requirement_id")}
    expanded: list[dict[str, Any]] = []
    for requirement in case["requirements"]:
        requirement_id = str(requirement["requirement_id"])
        action = by_id.get(requirement_id)
        if action is None:
            expanded.append(
                {
                    "requirement_id": requirement_id,
                    "action": "keep",
                    "basis": "case-level AI review found no exception",
                }
            )
        else:
            row = dict(action)
            row.setdefault("basis", "case-level AI review")
            expanded.append(row)
    expanded.extend(
        {
            "requirement_id": str(item["requirement"]["requirement_id"]),
            "action": "add",
            "basis": item.get("reason", "case-level AI review"),
            "requirement": item["requirement"],
        }
        for item in normalized
        if item["action"] == "add"
    )
    return {
        "case_id": case["case_id"],
        "publication_state": meta.get("publication_state"),
        "notes": meta.get("notes"),
        "actions": expanded,
    }


def _repair_evidence_spans(
    case: dict[str, Any], reviewed: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Recompute evidence start/end offsets from the frozen JD text.

    Only the reviewed copy is touched; the frozen baseline prediction is never
    mutated.
    """
    jd_text = str(case["jd_text"])
    blocks_by_id = {str(block["source_id"]): block for block in case["source_blocks"]}
    repaired = 0
    for requirement in reviewed:
        evidence = requirement.get("evidence")
        if not isinstance(evidence, dict):
            continue
        quote = str(evidence.get("quote") or "")
        if not quote:
            continue
        block = blocks_by_id.get(str(evidence.get("source_id") or ""))
        start = evidence.get("start")
        end = evidence.get("end")
        if isinstance(start, int) and isinstance(end, int):
            if 0 <= start < end <= len(jd_text) and jd_text[start:end] == quote:
                continue
        if block is not None:
            block_text = str(block.get("text", ""))
            index = block_text.find(quote)
            if index >= 0:
                new_start = int(block.get("start", 0)) + index
                new_end = new_start + len(quote)
                if jd_text[new_start:new_end] == quote:
                    evidence["start"] = new_start
                    evidence["end"] = new_end
                    evidence["alignment"] = "exact"
                    repaired += 1
                    continue
        index = jd_text.find(quote)
        if index >= 0:
            evidence["start"] = index
            evidence["end"] = index + len(quote)
            evidence["alignment"] = "exact"
            repaired += 1
    return reviewed, repaired


def _hard_validate(
    case: dict[str, Any],
    reviewed: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    blocks_by_id = {str(block["source_id"]): block for block in case["source_blocks"]}
    jd_text = str(case["jd_text"])
    ids = [requirement["requirement_id"] for requirement in reviewed]
    if len(ids) != len(set(ids)):
        errors.append("duplicate requirement_id in reviewed requirements")
    try:
        payload = {
            "document_id": case["case_id"],
            "requirements": reviewed,
            "company_facts": [],
            "employment_facts": [],
            "responsibilities": [],
        }
        JDExtractionResult.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"schema error: {exc}")
    for requirement in reviewed:
        evidence = requirement.get("evidence", {})
        source_id = str(evidence.get("source_id") or "")
        quote = str(evidence.get("quote") or "")
        block = blocks_by_id.get(source_id)
        if block is None:
            errors.append(f"{requirement['requirement_id']}: unknown source_id {source_id}")
        elif quote and quote not in str(block.get("text", "")):
            errors.append(f"{requirement['requirement_id']}: quote not in source block")
        start = evidence.get("start")
        end = evidence.get("end")
        if start is not None and end is not None:
            if not (0 <= start < end <= len(jd_text)):
                errors.append(f"{requirement['requirement_id']}: offset out of range")
            elif quote and jd_text[start:end] != quote:
                errors.append(f"{requirement['requirement_id']}: offset mismatch")
        kind = requirement.get("kind")
        if kind in REQUIRED_ITEM_KINDS:
            has_items = bool(
                requirement.get("items")
                or requirement.get("certificates")
                or requirement.get("skills")
                or requirement.get("tools")
            )
            if not has_items:
                errors.append(f"{requirement['requirement_id']}: empty items for kind {kind}")
    return errors


def _identity_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1]
            / "output"
            / "evaluation"
            / "real-requirement-graph"
        ),
    )
    parser.add_argument("--review-model", default=REVIEW_MODEL_DEFAULT)
    parser.add_argument("--prompt-version", default=PROMPT_VERSION_DEFAULT)
    parser.add_argument("--frozen-at", default=None)
    parser.add_argument("--allow-unresolved", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    decisions_path = Path(args.decisions)
    actions_by_case, meta_by_case = _decisions_by_case(decisions_path)
    frozen_at = args.frozen_at or datetime.now(timezone.utc).isoformat()
    correction_identity = _identity_digest(decisions_path)
    summary_by_case: dict[str, dict[str, Any]] = {}
    total_validation_errors: list[str] = []
    total_offset_repaired = 0
    expanded_rows: list[dict[str, Any]] = []

    for case in manifest["cases"]:
        case_id = str(case["case_id"])
        baseline = copy.deepcopy(case["requirements"])
        decision_meta = meta_by_case.get(case_id)
        actions = actions_by_case.get(case_id)
        if decision_meta is None or actions is None:
            raise ValueError(f"missing review decisions for case {case_id}")
        baseline_identity = _dict_digest(baseline)
        reviewed, counts = _apply_actions(baseline, actions)
        reviewed, offset_repaired = _repair_evidence_spans(case, reviewed)
        total_offset_repaired += offset_repaired
        validation_errors = _hard_validate(case, reviewed)
        if validation_errors:
            if args.allow_unresolved:
                total_validation_errors.extend(
                    f"{case_id}: {error}" for error in validation_errors
                )
            else:
                raise ValueError(
                    f"case {case_id} hard validation failed: {validation_errors}"
                )

        def _build(requirements: list[dict[str, Any]], builder: Any) -> Any:
            return builder(
                JDExtractionResult.model_validate(
                    {
                        "document_id": case_id,
                        "requirements": requirements,
                        "company_facts": [],
                        "employment_facts": [],
                        "responsibilities": [],
                    }
                ),
                case["source_blocks"],
            )

        gold_graph = _build(reviewed, build_requirement_graph)
        gold_errors = validate_requirement_graph(
            gold_graph, {r["requirement_id"] for r in reviewed}, case["source_blocks"]
        )
        if gold_errors:
            if args.allow_unresolved:
                total_validation_errors.extend(f"{case_id}: {error}" for error in gold_errors)
            else:
                raise ValueError(f"case {case_id} gold graph invalid: {gold_errors}")
        original_graph = _build(baseline, build_requirement_graph_v0)
        challenger_graph = _build(baseline, build_requirement_graph)

        case["requirements"] = copy.deepcopy(baseline)
        case["baseline_prediction_frozen"] = copy.deepcopy(baseline)
        case["baseline_prediction_identity"] = baseline_identity
        case["reviewed_requirements"] = reviewed
        case["expected_graph"] = gold_graph.model_dump(mode="json")
        case["original_extraction_graph"] = original_graph.model_dump(mode="json")
        case["challenger_graph"] = challenger_graph.model_dump(mode="json")
        case["graph_builder_identity"] = {
            "original": V0_BUILDER_IDENTITY,
            "challenger": "requirement-graph.v1 (2026-08-13 fixed)",
            "gold": "requirement-graph.v1 (2026-08-13 fixed)",
        }
        case["gold_graph_source"] = (
            "v1 builder over AI-reviewed flat requirements "
            "(not independent structural annotation)"
        )
        case["challenger_shared_builder_with_gold"] = True
        expected_publication_state = (
            "reject"
            if gold_graph.status != "complete" or gold_graph.unresolved_items
            else "accept"
        )
        declared_publication_state = (
            decision_meta.get("publication_state") if decision_meta else None
        )
        if declared_publication_state and declared_publication_state != expected_publication_state:
            raise ValueError(
                f"case {case_id} declared publication_state "
                f"{declared_publication_state} != derived {expected_publication_state}"
            )
        case["expected_publication_state"] = expected_publication_state
        case["publication_reason"] = (
            "graph complete with no unresolved items"
            if expected_publication_state == "accept"
            else f"graph status {gold_graph.status}; unresolved: {gold_graph.unresolved_items}"
        )
        case["review_notes"] = decision_meta.get("notes")
        case["gold_identity"] = {
            "gold_type": GOLD_TYPE,
            "gold_version": GOLD_VERSION,
            "gold_annotator": "ai-reviewer",
            "review_model": args.review_model,
            "prompt_version": args.prompt_version,
            "frozen_at": frozen_at,
            "correction_identity": correction_identity,
            "status": "frozen",
        }
        expanded_rows.append(_expand_decisions(case, actions, decision_meta))
        summary_by_case[case_id] = {
            "actions": counts,
            "baseline_identity": baseline_identity,
            "requirement_count_before": len(baseline),
            "requirement_count_after": len(reviewed),
            "gold_graph_status": gold_graph.status,
            "gold_graph_unresolved_count": len(gold_graph.unresolved_items),
            "original_graph_status": original_graph.status,
            "challenger_graph_status": challenger_graph.status,
            "validation_errors": validation_errors,
            "offset_spans_repaired": offset_repaired,
            "expected_publication_state": expected_publication_state,
        }

    manifest["real_frozen"] = True
    manifest["gold_frozen_at"] = frozen_at
    if "AI-reviewed Gold v1" not in str(manifest.get("provenance") or ""):
        manifest["provenance"] = (
            str(manifest.get("provenance") or "")
            + "; gold: AI-reviewed Gold v1 (formal extraction reviewed against full JD text)"
        )
    manifest["gold_policy"] = {
        "gold_type": GOLD_TYPE,
        "gold_version": GOLD_VERSION,
        "description": (
            "AI reviewed the formal extraction output against the full JD text and "
            "recorded keep/modify/delete/add actions; the formal extraction is deep-copied "
            "and frozen as baseline_prediction with an identity hash and is never used as "
            "the gold or mutated by corrections."
        ),
        "review_model": args.review_model,
        "prompt_version": args.prompt_version,
        "correction_identity": correction_identity,
        "frozen_at": frozen_at,
        "decision_basis": (
            "case-level AI review of all 24 JDs; keep entries are explicitly transcribed "
            "per requirement from the review result, not independent per-item annotations"
        ),
    }
    validate_real_manifest(manifest)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "real-jd-gold-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "review-decisions-source.jsonl").write_text(
        decisions_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out_dir / "review-decisions.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in expanded_rows
        ),
        encoding="utf-8",
    )
    action_totals = {
        key: sum(
            sum(1 for action in row["actions"] if action["action"] == key)
            for row in expanded_rows
        )
        for key in ("keep", "modify", "delete", "add")
    }
    correction_summary = {
        "schema_version": "b-jd-ai-review-summary.v1",
        "gold_type": GOLD_TYPE,
        "gold_version": GOLD_VERSION,
        "review_model": args.review_model,
        "prompt_version": args.prompt_version,
        "correction_identity": correction_identity,
        "frozen_at": frozen_at,
        "case_count": len(manifest["cases"]),
        "decision_basis": manifest["gold_policy"]["decision_basis"],
        "baseline_requirement_total": sum(
            item["requirement_count_before"] for item in summary_by_case.values()
        ),
        "offset_spans_repaired_total": total_offset_repaired,
        "explicit_ops_in_source": sum(
            len(actions_by_case[case_id]) for case_id in actions_by_case
        ),
        "expanded_action_totals": action_totals,
        "cases": summary_by_case,
        "validation_errors": total_validation_errors,
    }
    (out_dir / "correction-summary.json").write_text(
        json.dumps(correction_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "reviewed-requirements.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "case_id": case["case_id"],
                    "reviewed_requirements": case["reviewed_requirements"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for case in manifest["cases"]
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "gold_frozen_at": frozen_at,
                "baseline_total": correction_summary["baseline_requirement_total"],
                "expanded_actions": action_totals,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
