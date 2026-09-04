"""Build AI review sheets for the B-JD-REAL-01 frozen real JD manifest.

The sheets contain the full JD text, source blocks, current formal extraction
requirements and deterministic integrity flags. Reviewers only record actions
against the current extraction; the formal extraction itself is never rewritten.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import JDExtractionResult  # noqa: E402


REQUIRED_ITEM_KINDS = {"skill", "certificate", "soft_skill", "tool"}
KEYWORD_BLOCKS = ("学历", "经验", "技能要求", "加分", "优先", "要求")


def _requirement_summary(requirement: dict[str, Any], flags: list[str]) -> str:
    evidence = requirement.get("evidence", {})
    items = []
    if "items" in requirement:
        items = [
            f"{item['name']}({item.get('item_type', '?')})"
            for item in requirement.get("items", [])
        ]
    elif "certificates" in requirement:
        items = list(requirement.get("certificates", []))
    elif "skills" in requirement:
        items = list(requirement.get("skills", []))
    elif "tools" in requirement:
        items = list(requirement.get("tools", []))
    extras: list[str] = []
    for key in (
        "label",
        "value",
        "domain",
        "role",
        "minimum_years",
        "maximum_years",
        "duration_text",
        "minimum_degree",
        "majors",
        "admission_type",
        "proficiency",
        "school_constraints",
        "experience_unlimited",
    ):
        if key in requirement and requirement[key] not in (None, [], ""):
            extras.append(f"{key}={requirement[key]}")
    flag_text = (" flags=[" + ",".join(flags) + "]") if flags else ""
    return (
        f"{requirement['requirement_id']} | {requirement.get('modality')} | "
        f"{requirement.get('kind')} | items=[{'; '.join(items)}] | "
        f"extras=[{'; '.join(extras)}] | "
        f"ev={evidence.get('source_id')}[{evidence.get('start')}-{evidence.get('end')}] "
        f"al={evidence.get('alignment')} q=\"{evidence.get('quote')}\"{flag_text}"
    )


def _integrity_flags(
    requirement: dict[str, Any],
    blocks_by_id: dict[str, dict[str, Any]],
    jd_text: str,
) -> list[str]:
    flags: list[str] = []
    evidence = requirement.get("evidence", {})
    source_id = evidence.get("source_id")
    quote = str(evidence.get("quote") or "")
    block = blocks_by_id.get(source_id or "")
    if block is None:
        flags.append("unknown_source_id")
    elif quote and quote not in str(block.get("text", "")):
        flags.append("quote_not_in_block")
    start = evidence.get("start")
    end = evidence.get("end")
    if start is not None and end is not None:
        if not (0 <= start < end <= len(jd_text)):
            flags.append("offset_out_of_range")
        elif quote and jd_text[start:end] != quote:
            flags.append("offset_mismatch")
    kind = requirement.get("kind")
    if kind in REQUIRED_ITEM_KINDS:
        if not requirement.get("items") and not requirement.get("certificates") and not requirement.get("skills") and not requirement.get("tools"):
            flags.append("empty_items")
    if kind == "skill":
        names = [item["name"] for item in requirement.get("items", [])]
        if len(names) != len(set(names)):
            flags.append("duplicate_skill_item")
    return flags


def _case_flags(
    case: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[str]:
    flags: list[str] = []
    ids = [r["requirement_id"] for r in case["requirements"]]
    if len(ids) != len(set(ids)):
        flags.append("duplicate_requirement_id")
    quotes: list[str] = []
    for requirement in case["requirements"]:
        quote = str(requirement.get("evidence", {}).get("quote") or "")
        if quote and quote in quotes:
            flags.append("duplicate_evidence_quote")
        quotes.append(quote)
    block_texts = {str(block.get("source_id")): str(block.get("text", "")) for block in blocks}
    referenced = {
        str(r.get("evidence", {}).get("source_id")) for r in case["requirements"]
    }
    for block in blocks:
        source_id = str(block.get("source_id"))
        text = str(block.get("text", ""))
        if source_id not in referenced and any(keyword in text for keyword in KEYWORD_BLOCKS):
            flags.append(f"unreferenced_keyword_block:{source_id}")
    try:
        payload = {
            "document_id": case["case_id"],
            "requirements": case["requirements"],
            "company_facts": [],
            "employment_facts": [],
            "responsibilities": [],
        }
        JDExtractionResult.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - schema errors are review input
        flags.append(f"schema_error:{type(exc).__name__}:{exc}")
    return flags


def render_case(case: dict[str, Any], index: int, total: int) -> str:
    blocks = case["source_blocks"]
    blocks_by_id = {str(block["source_id"]): block for block in blocks}
    lines = [
        f"==== case {index}/{total} {case['case_id']} ====",
        f"TITLE: {case.get('job_title')}",
        f"SOURCE: {json.dumps(case.get('source_identity', {}), ensure_ascii=False)}",
        "JD_TEXT:",
        str(case.get("jd_text") or ""),
        "BLOCKS:",
    ]
    for block in blocks:
        lines.append(
            f"  {block['source_id']} [{block.get('start')}-{block.get('end')}] "
            f"\"{block.get('text', '')}\""
        )
    lines.append("REQS:")
    for requirement in case["requirements"]:
        flags = _integrity_flags(requirement, blocks_by_id, str(case.get("jd_text") or ""))
        lines.append("  " + _requirement_summary(requirement, flags))
    case_flags = _case_flags(case, blocks)
    lines.append(f"CASE FLAGS: {'; '.join(case_flags) if case_flags else '-'}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=24)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    cases = manifest["cases"]
    selected = cases[args.start : args.start + args.count]
    rendered = [
        render_case(case, args.start + index, len(cases))
        for index, case in enumerate(selected)
    ]
    output = Path(args.out)
    output.write_text("\n".join(rendered), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
