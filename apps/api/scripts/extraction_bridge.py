"""Bridge Extraction outputs into the main Jobgraph system.

Demonstration / competition helper — do NOT use in production.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# File loading & document_id alignment
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractionInput:
    jd_source: dict[str, Any]
    jd_annotations: dict[str, Any]
    jd_normalized: dict[str, Any]
    cv_source: dict[str, Any]
    cv_annotations: dict[str, Any]
    cv_normalized: dict[str, Any]


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object at {path}")
    return data


def load_extraction_input(
    *,
    jd_source: str,
    jd_extraction: str,
    jd_normalized: str,
    cv_source: str,
    cv_extraction: str,
    cv_normalized: str,
) -> ExtractionInput:
    jd_src = _load_json(Path(jd_source))
    jd_ann = _load_json(Path(jd_extraction))
    jd_nrm = _load_json(Path(jd_normalized))
    cv_src = _load_json(Path(cv_source))
    cv_ann = _load_json(Path(cv_extraction))
    cv_nrm = _load_json(Path(cv_normalized))

    def _check(tag: str, expected: str, actual: object) -> None:
        if actual != expected:
            raise ValueError(
                f"{tag} document_id mismatch: expected {expected!r}, got {actual!r}"
            )

    jd_id = jd_src["document_id"]
    _check("JD annotations", jd_id, jd_ann.get("document_id"))
    _check("JD normalized", jd_id, jd_nrm.get("document_id"))

    cv_id = cv_src["document_id"]
    _check("CV annotations", cv_id, cv_ann.get("document_id"))
    _check("CV normalized", cv_id, cv_nrm.get("document_id"))

    return ExtractionInput(jd_src, jd_ann, jd_nrm, cv_src, cv_ann, cv_nrm)


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD_RE = re.compile(r"\b[1-6]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b")

_PII_TEXT_MARKERS = (
    "user_photo",
    "avatar",
    "real_name",
    "full_name",
    "candidate_name",
    "头像",
    "真实姓名",
    "姓名",
    "身份证号",
    "手机号",
)
_AVATAR_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|/)[^\s\"']+\.(?:jpe?g|png|gif|webp)\b",
    re.IGNORECASE,
)


def _scan_pii(text: str) -> list[str]:
    issues: list[str] = []
    if _EMAIL_RE.search(text):
        issues.append("email detected")
    if _PHONE_RE.search(text):
        issues.append("phone number detected")
    if _ID_CARD_RE.search(text):
        issues.append("ID card number detected")
    if _AVATAR_PATH_RE.search(text):
        issues.append("avatar path detected")
    for marker in _PII_TEXT_MARKERS:
        if marker in text:
            issues.append(f"PII marker '{marker}' detected")
    return issues


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

@dataclass
class DryRunResult:
    status: str = "validated"
    errors: list[str] = field(default_factory=list)
    jd_document_id: str = ""
    cv_document_id: str = ""
    jd_title: str = ""
    jd_skill_count: int = 0
    cv_skill_count: int = 0
    resolved_skills: list[str] = field(default_factory=list)
    unresolved_skills: list[str] = field(default_factory=list)
    database_written: bool = False


def dry_run(inputs: ExtractionInput) -> DryRunResult:
    result = DryRunResult(
        jd_document_id=inputs.jd_source["document_id"],
        cv_document_id=inputs.cv_source["document_id"],
        jd_title=inputs.jd_source.get("title", ""),
    )

    # --- PII scan (all three CV layers) ---
    for tag, payload in [
        ("cv_source.raw_text", inputs.cv_source.get("raw_text", "")),
        ("cv_annotations", json.dumps(inputs.cv_annotations, ensure_ascii=False)),
        ("cv_normalized", json.dumps(inputs.cv_normalized, ensure_ascii=False)),
    ]:
        for issue in _scan_pii(str(payload)):
            result.errors.append(f"{tag}: {issue}")

    # --- JD skills (V2 flat list) ---
    for skill in inputs.jd_normalized.get("normalized_requirements", []):
        result.jd_skill_count += 1
        sid = skill.get("skill_id")
        if sid and skill.get("resolution_status") == "resolved":
            result.resolved_skills.append(sid)
        else:
            name = skill.get("source_name", "?")
            result.unresolved_skills.append(f"unresolved:{name}")

    # --- CV skills ---
    for skill in inputs.cv_normalized.get("normalized_skills", []):
        result.cv_skill_count += 1
        sid = skill.get("skill_id")
        if sid and skill.get("resolution_status") == "resolved":
            result.resolved_skills.append(sid)
        else:
            name = skill.get("source_name", "?")
            result.unresolved_skills.append(f"unresolved:{name}")

    # Required fields
    if not inputs.jd_source.get("raw_text", "").strip():
        result.errors.append("jd_source.raw_text is empty")

    if result.errors:
        result.status = "invalid"

    return result


# ---------------------------------------------------------------------------
# Skill / section mappers
# ---------------------------------------------------------------------------

def _evidence_quote_map(annotations: dict[str, Any]) -> dict[str, str]:
    """skill name → first evidence quote."""
    mapping: dict[str, str] = {}
    for skill in annotations.get("skills", []):
        name = skill.get("name", "")
        ev = skill.get("evidence")
        if name and isinstance(ev, dict) and ev.get("quote"):
            mapping[name] = ev["quote"]
    return mapping


def map_cv_skills(
    cv_normalized: dict[str, Any],
    cv_annotations: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    quote_map = _evidence_quote_map(cv_annotations)
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()

    for skill in cv_normalized.get("normalized_skills", []):
        name = skill.get("canonical_name") or skill.get("source_name", "")
        sid = skill.get("skill_id")
        is_resolved = skill.get("resolution_status") == "resolved" and sid
        if is_resolved and name not in seen:
            seen.add(name)
            evidence = quote_map.get(skill.get("source_name", name))
            mapped = {
                "raw_skill": skill.get("source_name", name),
                "normalized_skill_id": sid,
                "skill_id": sid,
                "confidence": 0.95,
                "resolution_status": "resolved",
            }
            if evidence is not None:
                mapped["evidence"] = evidence
            skills.append(mapped)

    for skill in cv_normalized.get("normalized_skills", []):
        name = skill.get("source_name", "")
        if skill.get("resolution_status") != "resolved" and name not in seen:
            seen.add(name)
            mapped = {
                "raw_skill": name,
                "normalized_skill_id": None,
                "skill_id": None,
                "confidence": 0.5,
                "resolution_status": "unresolved",
            }
            evidence = quote_map.get(name)
            if evidence is not None:
                mapped["evidence"] = evidence
            skills.append(mapped)

    return tuple(skills)


def map_cv_education(ann: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple({
        "school": entry.get("school", ""),
        "major": entry.get("major", ""),
        "degree": entry.get("degree", ""),
        "date": entry.get("date", {}),
    } for entry in ann.get("education", []))


def map_cv_projects(ann: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple({
        "name": entry.get("name", ""),
        "description": entry.get("description", {}).get("value", ""),
        "date": entry.get("date", {}),
    } for entry in ann.get("project_experience", []))


def map_cv_internships(ann: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    entries = (
        ann.get("work_experience")
        or ann.get("work_experiences")
        or ann.get("internships")
        or []
    )
    result: list[dict[str, Any]] = []
    for entry in entries:
        desc_raw = entry.get("description", "")
        if isinstance(desc_raw, dict):
            desc_raw = desc_raw.get("value", "")
        result.append({
            "company": entry.get("company", ""),
            "position": entry.get("position", entry.get("title", "")),
            "date": entry.get("date", {}),
            "description": str(desc_raw),
        })
    return tuple(result)


def map_cv_certificates(ann: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple({
        "name": c.get("name", ""),
        "kind": c.get("kind", "professional_certification"),
    } for c in ann.get("certificates", []))


def map_cv_competitions(ann: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple({
        "name": a.get("name", ""),
        "kind": a.get("kind", "award"),
    } for a in ann.get("awards", []))
