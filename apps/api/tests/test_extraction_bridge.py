"""Extraction bridge tests using synthetic fixtures and local SQLite only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURES = (Path(__file__).resolve().parent / "fixtures" / "extraction_contracts")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: Path, name: str, data: object) -> str:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Fixture existence
# ---------------------------------------------------------------------------

REQUIRED_FIXTURES = [
    "jd_source.json", "jd_annotations.json", "jd_normalized.json",
    "cv_source.json", "cv_annotations.json", "cv_normalized.json",
]

@pytest.mark.parametrize("name", REQUIRED_FIXTURES)
def test_fixture_exists(name: str) -> None:
    assert (_FIXTURES / name).exists(), f"Missing: {_FIXTURES / name}"


# ---------------------------------------------------------------------------
# Evidence contract — every exact Evidence must satisfy raw_text[start:end]==quote
# ---------------------------------------------------------------------------

def _all_evidence_objects(obj: object) -> list[dict]:
    """Walk JSON-like objects and collect every Evidence-shaped object."""
    found: list[dict] = []
    if isinstance(obj, dict):
        if "quote" in obj or "alignment" in obj:
            found.append(obj)
        for v in obj.values():
            found.extend(_all_evidence_objects(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_all_evidence_objects(item))
    return found


def test_jd_exact_evidence() -> None:
    jd_src = json.loads((_FIXTURES / "jd_source.json").read_text(encoding="utf-8"))
    jd_ann = json.loads((_FIXTURES / "jd_annotations.json").read_text(encoding="utf-8"))
    text = jd_src["raw_text"]

    evidence_objects = _all_evidence_objects(jd_ann)
    assert len(evidence_objects) > 0, "JD annotations must contain Evidence entries"

    errors: list[str] = []
    for ev in evidence_objects:
        assert ev.get("alignment") == "exact"
        assert set(("quote", "start", "end")) <= ev.keys()
        quote = ev["quote"]
        start = int(ev["start"])
        end = int(ev["end"])
        actual = text[start:end]
        if actual != quote:
            errors.append(
                f"quote={quote!r} start={start} end={end} actual={actual!r}"
            )
    assert len(errors) == 0, f"JD Evidence contract violations: {errors}"


def test_cv_exact_evidence() -> None:
    cv_src = json.loads((_FIXTURES / "cv_source.json").read_text(encoding="utf-8"))
    cv_ann = json.loads((_FIXTURES / "cv_annotations.json").read_text(encoding="utf-8"))
    text = cv_src["raw_text"]

    evidence_objects = _all_evidence_objects(cv_ann)
    assert len(evidence_objects) > 0, "CV annotations must contain Evidence entries"
    for ev in evidence_objects:
        assert ev.get("alignment") == "exact"
        assert set(("quote", "start", "end")) <= ev.keys()
        quote = ev["quote"]
        start = int(ev["start"])
        end = int(ev["end"])
        actual = text[start:end]
        assert actual == quote, (
            f"CV Evidence: quote={quote!r} start={start} end={end} actual={actual!r}"
        )


# ---------------------------------------------------------------------------
# document_id alignment
# ---------------------------------------------------------------------------

def test_document_id_mismatch_fails(tmp_path: Path) -> None:
    jd_src = {"document_id": "A", "title": "T", "raw_text": "text", "source_name": "s"}
    jd_ann = {"document_id": "B"}
    jd_nrm = {"document_id": "A"}
    cv_src = {"document_id": "C", "raw_text": "text"}
    cv_ann = {"document_id": "C"}
    cv_nrm = {"document_id": "C"}

    files = [
        _write_json(tmp_path, n, d) for n, d in [
            ("jd_src", jd_src), ("jd_ann", jd_ann), ("jd_nrm", jd_nrm),
            ("cv_src", cv_src), ("cv_ann", cv_ann), ("cv_nrm", cv_nrm),
        ]
    ]
    from scripts.extraction_bridge import load_extraction_input
    with pytest.raises(ValueError, match="document_id"):
        load_extraction_input(
            jd_source=files[0], jd_extraction=files[1], jd_normalized=files[2],
            cv_source=files[3], cv_extraction=files[4], cv_normalized=files[5],
        )


# ---------------------------------------------------------------------------
# Skill mapping
# ---------------------------------------------------------------------------

def test_resolved_skills_are_mapped() -> None:
    from scripts.extraction_bridge import map_cv_skills

    norm = {
        "document_id": "cv_1",
        "normalized_skills": [
            {"source_name": "Python", "skill_id": "LANG_PYTHON",
             "canonical_name": "Python", "resolution_status": "resolved"},
            {"source_name": "UnknownSkill", "resolution_status": "unresolved"},
        ],
    }
    ann = {"document_id": "cv_1", "skills": []}
    result = map_cv_skills(norm, ann)
    resolved = [s for s in result if s.get("resolution_status") == "resolved"]
    unresolved = [s for s in result if s.get("resolution_status") != "resolved"]
    assert len(resolved) == 1
    assert resolved[0]["skill_id"] == "LANG_PYTHON"
    assert len(unresolved) == 1
    assert unresolved[0]["skill_id"] is None


def test_unresolved_skill_has_no_skill_id() -> None:
    from scripts.extraction_bridge import map_cv_skills

    norm = {
        "document_id": "cv_x",
        "normalized_skills": [
            {"source_name": "MysteryTool", "resolution_status": "unresolved"},
        ],
    }
    result = map_cv_skills(norm, {"document_id": "cv_x"})
    assert len(result) == 1
    assert result[0]["skill_id"] is None


# ---------------------------------------------------------------------------
# Section types — certificates/competitions must be dicts
# ---------------------------------------------------------------------------

def test_certificates_are_dicts() -> None:
    from scripts.extraction_bridge import map_cv_certificates

    ann = {"certificates": [{"name": "CET-6", "kind": "language_certification"}]}
    result = map_cv_certificates(ann)
    assert len(result) > 0
    assert all(isinstance(item, dict) for item in result)
    assert result[0]["name"] == "CET-6"


def test_competitions_are_dicts() -> None:
    from scripts.extraction_bridge import map_cv_competitions

    ann = {"awards": [{"name": "优秀毕业生", "kind": "award"}]}
    result = map_cv_competitions(ann)
    assert len(result) > 0
    assert all(isinstance(item, dict) for item in result)
    assert result[0]["name"] == "优秀毕业生"


def test_internships_from_work_experience() -> None:
    from scripts.extraction_bridge import map_cv_internships

    # test fallback: work_experience key
    ann = {"work_experience": [{"company": "A", "position": "Dev", "date": {}, "description": "work"}]}
    result = map_cv_internships(ann)
    assert len(result) > 0
    assert result[0]["company"] == "A"

    # test empty
    assert map_cv_internships({}) == ()


def test_skill_evidence_from_annotations() -> None:
    from scripts.extraction_bridge import map_cv_skills

    norm = {
        "document_id": "cv_1",
        "normalized_skills": [
            {"source_name": "Python", "skill_id": "LANG_PYTHON",
             "canonical_name": "Python", "resolution_status": "resolved"},
        ],
    }
    ann = {
        "document_id": "cv_1",
        "skills": [
            {"name": "Python", "item_type": "programming_language",
             "evidence": {"quote": "Python is used", "start": 0, "end": 0, "alignment": "exact", "occurrence_index": 0}},
        ],
    }
    result = map_cv_skills(norm, ann)
    assert result[0]["evidence"] == "Python is used"


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def test_dry_run_no_db_write() -> None:
    from scripts.extraction_bridge import ExtractionInput, dry_run

    inputs = ExtractionInput(
        jd_source={"document_id": "jd_1", "title": "Test JD", "raw_text": "job text"},
        jd_annotations={"document_id": "jd_1"},
        jd_normalized={"document_id": "jd_1", "normalized_requirements": [
            {"skill_id": "X", "source_name": "x", "resolution_status": "resolved"},
        ]},
        cv_source={"document_id": "cv_1", "raw_text": "cv text"},
        cv_annotations={"document_id": "cv_1"},
        cv_normalized={"document_id": "cv_1", "normalized_skills": [
            {"skill_id": "LANG_PYTHON", "source_name": "Python", "resolution_status": "resolved"},
        ]},
    )
    result = dry_run(inputs)
    assert result.status == "validated"
    assert result.database_written is False
    assert result.jd_skill_count > 0
    assert result.cv_skill_count > 0


def test_dry_run_with_fixtures() -> None:
    from scripts.extraction_bridge import load_extraction_input, dry_run

    inputs = load_extraction_input(
        jd_source=str(_FIXTURES / "jd_source.json"),
        jd_extraction=str(_FIXTURES / "jd_annotations.json"),
        jd_normalized=str(_FIXTURES / "jd_normalized.json"),
        cv_source=str(_FIXTURES / "cv_source.json"),
        cv_extraction=str(_FIXTURES / "cv_annotations.json"),
        cv_normalized=str(_FIXTURES / "cv_normalized.json"),
    )
    result = dry_run(inputs)
    assert result.status == "validated"
    assert result.database_written is False
    assert result.errors == []
    assert result.jd_skill_count > 0
    assert result.cv_skill_count > 0
    assert len(result.resolved_skills) > 0


def test_dry_run_pii_fails() -> None:
    from scripts.extraction_bridge import ExtractionInput, dry_run

    inputs = ExtractionInput(
        jd_source={"document_id": "jd_1", "title": "T", "raw_text": "job"},
        jd_annotations={"document_id": "jd_1"},
        jd_normalized={"document_id": "jd_1", "normalized_requirements": []},
        cv_source={"document_id": "cv_1", "raw_text": "cv"},
        cv_annotations={"document_id": "cv_1", "user_photo": "avatar.jpg"},
        cv_normalized={"document_id": "cv_1", "normalized_skills": []},
    )
    result = dry_run(inputs)
    assert result.status == "invalid"
    assert len(result.errors) > 0


def test_dry_run_email_pii_fails() -> None:
    from scripts.extraction_bridge import ExtractionInput, dry_run

    inputs = ExtractionInput(
        jd_source={"document_id": "jd_1", "title": "T", "raw_text": "job"},
        jd_annotations={"document_id": "jd_1"},
        jd_normalized={"document_id": "jd_1", "normalized_requirements": []},
        cv_source={"document_id": "cv_1", "raw_text": "test@example.com"},
        cv_annotations={"document_id": "cv_1"},
        cv_normalized={"document_id": "cv_1", "normalized_skills": []},
    )
    result = dry_run(inputs)
    assert result.status == "invalid"
    assert any("email" in e.lower() for e in result.errors)


@pytest.mark.parametrize(
    "private_text",
    [
        '"candidate_name": "张三"',
        '"avatar": "C:\\\\profiles\\\\candidate.png"',
        '"phone": "13800138000"',
        '"id_card": "110101199001011237"',
    ],
)
def test_pii_scanner_rejects_identity_data(private_text: str) -> None:
    from scripts.extraction_bridge import _scan_pii

    assert _scan_pii(private_text)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def test_parse_result_changes_has_changed_fields() -> None:
    from app.contexts.talent_acquisition import ParseResultChanges

    changes = ParseResultChanges(
        changed_fields=frozenset({"education", "skills", "parse_confidence"}),
        education=({"school": "U", "major": "CS", "degree": "bachelor", "date": {}},),
        skills=({"raw_skill": "Python", "skill_id": "X", "confidence": 0.9, "evidence": "..."},),
        parse_confidence=0.9,
    )
    assert "education" in changes.changed_fields
    assert "skills" in changes.changed_fields
    assert "parse_confidence" in changes.changed_fields


def test_jd_parse_edit_command_fields() -> None:
    from app.contexts.jd_lifecycle import JDParseEditCommand

    cmd = JDParseEditCommand(
        changed_fields=frozenset({"extraction_result", "normalized_result", "parse_confidence"}),
        extraction_result={"key": "val"},
        normalized_result={"key": "val"},
        parse_confidence=0.95,
    )
    assert "extraction_result" in cmd.changed_fields
    assert "normalized_result" in cmd.changed_fields


# ---------------------------------------------------------------------------
# CV fixture PII check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", REQUIRED_FIXTURES)
def test_synthetic_fixture_no_pii(name: str) -> None:
    raw = (_FIXTURES / name).read_text(encoding="utf-8")
    from scripts.extraction_bridge import _scan_pii
    issues = _scan_pii(raw)
    assert len(issues) == 0, f"PII in synthetic fixture {name}: {issues}"


# ---------------------------------------------------------------------------
# R-1: dry-run reads flat V2 normalized_requirements
# ---------------------------------------------------------------------------

def test_dry_run_counts_flat_v2_jd_skills() -> None:
    from scripts.extraction_bridge import ExtractionInput, dry_run

    inputs = ExtractionInput(
        jd_source={"document_id": "jd_x", "title": "T", "raw_text": "text"},
        jd_annotations={"document_id": "jd_x"},
        jd_normalized={
            "document_id": "jd_x",
            "normalized_requirements": [
                {"source_name": "Python", "skill_id": "LANG_PYTHON",
                 "canonical_name": "Python", "resolution_status": "resolved"},
                {"source_name": "Prompt注入防御", "skill_id": None,
                 "canonical_name": None, "resolution_status": "unresolved"},
            ],
        },
        cv_source={"document_id": "cv_x", "raw_text": "text"},
        cv_annotations={"document_id": "cv_x"},
        cv_normalized={"document_id": "cv_x", "normalized_skills": []},
    )
    result = dry_run(inputs)
    assert result.jd_skill_count == 2
    assert "LANG_PYTHON" in result.resolved_skills
    assert any("Prompt" in u for u in result.unresolved_skills)


def test_dry_run_jd_count_matches_fixture() -> None:
    from scripts.extraction_bridge import load_extraction_input, dry_run

    inputs = load_extraction_input(
        jd_source=str(_FIXTURES / "jd_source.json"),
        jd_extraction=str(_FIXTURES / "jd_annotations.json"),
        jd_normalized=str(_FIXTURES / "jd_normalized.json"),
        cv_source=str(_FIXTURES / "cv_source.json"),
        cv_extraction=str(_FIXTURES / "cv_annotations.json"),
        cv_normalized=str(_FIXTURES / "cv_normalized.json"),
    )
    result = dry_run(inputs)
    expected_jd_count = len(inputs.jd_normalized["normalized_requirements"])
    assert result.jd_skill_count == expected_jd_count, (
        f"jd_skill_count={result.jd_skill_count}, "
        f"expected={expected_jd_count}"
    )
    assert result.jd_skill_count > 0
