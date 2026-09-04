from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.contexts.emerging_positions.domain import (
    EmergingCandidate,
    EmergingRecord,
)
from app.contexts.insight_cards import (
    assemble_insight_card,
    emerging_card_source,
    evolution_event_card_source,
    matching_what_if_card_source,
    trend_report_card_source,
)
from app.contexts.insight_cards.contract_snapshot import (
    contract_snapshot,
    snapshot_json,
    validate_bff_payload,
)
from app.contexts.market_intelligence import TrendReportRecord
from app.domain.trend_analysis import (
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendSkill,
)

API_ROOT = Path(__file__).resolve().parents[1]
JOBPULSE_ROOT = API_ROOT.parents[1]
PYTHON = sys.executable
SNAPSHOT_PATHS = (
    API_ROOT / "docs" / "insight-card-contract.v1.json",
    JOBPULSE_ROOT
    / "apps"
    / "web"
    / "src"
    / "features"
    / "insights"
    / "insight-card-contract.snapshot.json",
)


def _emerging_card() -> dict:
    candidate = EmergingCandidate.create(
        candidate_id="contract-emerging-1",
        cluster_id="cluster-1",
        position_name="AI Prompt Engineer",
        core_responsibilities=("design prompts",),
        required_skills=({"skill_id": "s1", "level": "required"},),
        bonus_skills=(),
        industry_scenarios=("internet",),
        germination_score=0.9,
        score_dimensions={"novelty": 0.8},
        evidence_jd_ids=("jd-1", "jd-2"),
        status="published",
    )
    record = EmergingRecord(candidate=candidate, created_at=None, updated_at=None)
    return asdict(assemble_insight_card(emerging_card_source(record)))


def _trend_card() -> dict:
    skill = TrendSkill(
        skill_id="s1",
        skill_name="Skill",
        category="c",
        weight=1.0,
        confidence=0.9,
        importance_level="core",
        trend_score=0.8,
        evidence_count=2,
    )
    graph = TrendGraphSnapshot(
        position_id="pos-1",
        position_name="Position",
        graph_version="gv-9",
        skills=(skill,),
        relations=(),
        core_responsibilities=(),
        industry_scenarios=(),
        status="published",
    )
    record = TrendReportRecord(
        report_id="report-1",
        position_id="pos-1",
        graph_version_id="gv-9",
        time_window_start=date(2026, 1, 1),
        time_window_end=date(2026, 3, 31),
        current_graph=graph,
        skill_weight_distribution=SkillWeightDistribution(
            core=(), high=(), bonus=(), edge=()
        ),
        new_skills=(),
        rising_skills=(),
        declining_skills=(),
        replaced_skills=(),
        skill_combo_shifts=(),
        risks=(),
        summary="summary",
        status="published",
        created_at=None,
        updated_at=None,
        provider_run_id="run-1",
        algorithm_version="trend.v2",
        formula_version="formula.v1",
        skill_catalog_version="cat-3",
        source_coverage=0.8,
        evidence_references=("ev-1", "ev-2"),
    )
    return asdict(assemble_insight_card(trend_report_card_source(record)))


def _evolution_card() -> dict:
    event = {
        "event_id": "evt-1-2-skill_emergence-001",
        "event_type": "skill_emergence",
        "position_id": "pos-1",
        "from_version": 1,
        "to_version": 2,
        "source_entities": [],
        "target_entities": [{"canonical_name": "Python", "skill_id": "s1"}],
        "evidence": {
            "source": "graph_version_snapshot_diff",
            "evidence_refs": [
                {
                    "evidence_id": "jd-1",
                    "source_object_type": "source_document",
                    "source_object_id": "jd-1",
                    "source_document_id": "jd-1",
                    "source_version": "v3",
                    "quote": "Python",
                    "location_start": 0,
                    "location_end": 6,
                }
            ],
        },
        "detector_version": "position-evolution-events.v1",
        "config_version": "config.v1",
        "catalog_snapshot_id": "cat-7",
    }
    return asdict(
        assemble_insight_card(evolution_event_card_source(event))
    )


def _matching_card() -> dict:
    result = {
        "scenario_id": "scenario-1",
        "baseline_evaluation_id": "eval-1",
        "baseline_score": 60.0,
        "scenario_score": 75.0,
        "score_delta": 15.0,
        "scenario_hard_gate_status": "passed",
        "generation_status": "completed",
        "target_reachable": True,
        "algorithm_version": "counterfactual-profile.v2",
        "scoring_config_version": "scoring-config.v1",
        "position_graph_version": "gv-4",
        "position_catalog_version": "cat-2",
        "cv_profile_version": "cv-2",
        "position_profile_version": "pp-1",
        "evidence_refs": [
            {
                "evidence_id": "ev-1",
                "source_object_type": "matching_evidence",
                "source_object_id": "ev-1",
                "source_document_id": "doc-1",
                "source_version": "v1",
                "quote": "Python",
                "location_start": 0,
                "location_end": 6,
            }
        ],
    }
    return asdict(assemble_insight_card(matching_what_if_card_source(result)))


def test_committed_snapshots_match_domain_shape() -> None:
    expected = snapshot_json()
    for path in SNAPSHOT_PATHS:
        assert path.is_file(), path
        assert path.read_text(encoding="utf-8") == expected, path


def test_freeze_script_check_passes() -> None:
    result = subprocess.run(
        [PYTHON, str(API_ROOT / "scripts" / "freeze_insight_card_contract.py"), "--check"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_public_adapter_bff_payloads_match_contract() -> None:
    snapshot = contract_snapshot()
    for name, payload in (
        ("emerging", _emerging_card()),
        ("trend", _trend_card()),
        ("evolution", _evolution_card()),
        ("matching_what_if", _matching_card()),
    ):
        problems = validate_bff_payload(payload, snapshot)
        assert problems == [], (name, problems)


def test_bff_rename_or_drop_fails_contract_validation() -> None:
    payload = _emerging_card()
    renamed = dict(payload)
    renamed["algorithmVersion"] = renamed.pop("algorithm_version")
    problems = validate_bff_payload(renamed)
    assert any("algorithm_version" in problem for problem in problems)
    dropped = dict(payload)
    dropped.pop("uncertainty_state")
    problems = validate_bff_payload(dropped)
    assert problems == ["BFF field missing: uncertainty_state"]


def test_core_field_nullability_is_frozen_in_snapshot() -> None:
    snapshot = contract_snapshot()
    fields = snapshot["fields"]
    assert fields["algorithm_version"]["nullable"] is False
    assert fields["algorithm_config_version"]["nullable"] is True
    assert fields["algorithm_config_hash"]["nullable"] is True
    assert fields["evidence_config_hash"]["nullable"] is False
    assert fields["uncertainty_state"]["nullable"] is False
    assert fields["evidence_refs"]["nullable"] is False
    assert fields["coverage_status"]["nullable"] is True
    assert fields["human_decision"]["nullable"] is True
    assert fields["next_action"]["nullable"] is False
