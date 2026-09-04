"""Build an explicitly non-formal QA annotation draft from synthetic CV outputs.

This artifact is useful for contract and lineage checks, but it must never be
used as the real CV Gold set: the source batch is synthetic and has no
independent human review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _item(field_path: str, value: Any, evidence: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"field_path": field_path, "value": str(value)}
    if evidence and evidence.get("quote"):
        result["evidence_quote"] = evidence["quote"]
        result["evidence_source_id"] = evidence.get("source_id")
    result.update(extra)
    return result


def _ownership(evidence: dict[str, Any] | None) -> str:
    quote = str((evidence or {}).get("quote") or "")
    if any(word in quote for word in ("设计", "主导")):
        return "designed"
    if any(word in quote for word in ("实现", "开发", "负责")):
        return "implemented"
    if any(word in quote for word in ("参与", "使用", "技术栈")):
        return "used"
    return "unknown"


def _level(proficiency: Any) -> str | None:
    mapping = {
        "熟练": "proficient",
        "精通": "advanced",
        "掌握": "working",
        "熟悉": "working",
        "了解": "basic",
        "familiar": "basic",
        "proficient": "proficient",
    }
    return mapping.get(str(proficiency)) if proficiency else None


def _case(path: Path) -> dict[str, Any]:
    record = _read(path)
    annotation = record.get("annotation") or {}
    items: list[dict[str, Any]] = []
    personal = annotation.get("personal_info") or {}
    if personal.get("expected_position"):
        position_evidence = next(
            (
                entry.get("evidence")
                for entry in personal.get("field_evidence") or []
                if entry.get("field_name") == "expected_position"
            ),
            None,
        )
        items.append(_item("personal_info.expected_position", personal["expected_position"], position_evidence))
    for index, education in enumerate(annotation.get("education") or []):
        for field in ("school", "major", "degree"):
            if education.get(field):
                items.append(_item(f"education[{index}].{field}", education[field], education.get("evidence")))
    for index, work in enumerate(annotation.get("work_experience") or []):
        for field in ("company", "position"):
            if work.get(field):
                items.append(_item(f"work_experience[{index}].{field}", work[field], work.get("evidence")))
        for skill in work.get("tech_stack") or []:
            items.append(_item(
                f"work_experience[{index}].tech_stack.{skill.get('name')}",
                skill.get("name"),
                skill.get("evidence"),
                skill_ownership=_ownership(skill.get("evidence")),
            ))
    for index, project in enumerate(annotation.get("project_experience") or []):
        if project.get("name"):
            items.append(_item(f"project_experience[{index}].name", project["name"], project.get("evidence")))
        for skill in project.get("tech_stack") or []:
            items.append(_item(
                f"project_experience[{index}].tech_stack.{skill.get('name')}",
                skill.get("name"),
                skill.get("evidence"),
                skill_ownership=_ownership(skill.get("evidence")),
            ))
    for skill in annotation.get("skills") or []:
        evidence = skill.get("evidence")
        items.append(_item(
            f"skills.{skill.get('name')}",
            skill.get("name"),
            evidence,
            skill_ownership=_ownership(evidence),
            capability_level=_level(skill.get("proficiency")),
        ))
    return {
        "case_id": f"synthetic_{record.get('cv_id')}",
        "source_class": "synthetic_test_data",
        "formal_eligibility": "blocked_synthetic_source",
        "reviewers": ["codex-ai-draft"],
        "gold_items": items,
        "predicted_items": items,
        "review_corrections": [],
    }


def build(run_dir: Path) -> dict[str, Any]:
    cases = [_case(path) for path in sorted((run_dir / "records" / "success").glob("*.json"))]
    return {
        "dataset_version": "synthetic-cv-qa-draft.v1",
        "source_run": run_dir.name,
        "status": "non_formal_draft",
        "formal_gate_blockers": [
            "source batch is synthetic_test_data, not real_anonymized or real_consented",
            "no independent dual human reviewer agreement",
        ],
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.run_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "case_count": len(payload["cases"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
