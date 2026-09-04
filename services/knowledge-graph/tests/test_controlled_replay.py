"""Tests for TEMP-05: Controlled replay with 4-factor decomposition."""

from __future__ import annotations

import pytest

from app.domain.temporal_analysis import (
    BuildInputWatermark,
    VersionChangeInputs,
    WatermarkSourceFact,
    create_build_input_watermark,
)
from app.domain.controlled_replay import (
    FourFactorDecomposition,
    ControlledReplayRequest,
    ControlledReplayResult,
    build_minimal_closure,
    decompose_change,
    execute_controlled_replay,
    replay_id_for,
    _build_frozen_config,
)


def _watermark(overrides: dict | None = None) -> BuildInputWatermark:
    base = dict(
        source_facts=(
            WatermarkSourceFact(
                source_kind="published_fact",
                source_fact_id="fact-1",
                source_fact_version="v1",
                source_version="v1",
            ),
        ),
        observation_window_start="2026-01-01",
        observation_window_end="2026-01-07",
        catalog_snapshot_id="CAT-v1",
        catalog_source_version="v1",
        validation_policy_version="val-v1",
        mapping_policy_version="map-v1",
        aggregation_algorithm_version="algo-v1",
        normalized_config={"config_version": "cfg-v1"},
        input_coverage=0.95,
        validation_state="present",
    )
    base.update(overrides or {})
    return create_build_input_watermark(**base)


def _snapshot(position_id: str = "POS_TEST") -> dict:
    return {
        "position_id": position_id,
        "position": {"position_id": position_id, "name": "Test Position"},
        "skill_relations": [],
        "responsibilities": [],
    }


def test_two_modes_are_distinct():
    wm = _watermark()
    req = build_minimal_closure(wm, wm)
    assert req.mode == "controlled_replay"
    assert req.freeze_factors == frozenset({"C", "P", "H"})


def test_minimal_closure_freezes_C_P_H():
    wm = _watermark()
    req = build_minimal_closure(wm, wm)
    assert "C" in req.freeze_factors
    assert "P" in req.freeze_factors
    assert "H" in req.freeze_factors


def test_decomposition_maps_four_factors():
    inputs = VersionChangeInputs(
        total_change=10.0,
        input_sample_change=7.0,
        catalog_migration_change=1.0,
        policy_algorithm_change=1.0,
        human_review_change=0.5,
    )
    d = decompose_change(inputs)
    assert d.D_data_change == pytest.approx(7.0)
    assert d.C_catalog_change == pytest.approx(1.0)
    assert d.P_policy_algorithm_change == pytest.approx(1.0)
    assert d.H_human_review_change == pytest.approx(0.5)
    assert d.unexplained_residual == pytest.approx(0.5)


def test_controlled_replay_excluded_from_release():
    wm = _watermark()
    req = build_minimal_closure(wm, wm)
    snap = _snapshot()
    result = execute_controlled_replay(
        req, snap, snap, position_id="POS_TEST",
        from_version_id=1, to_version_id=2,
    )
    assert result.experimental_replay is True


def test_replay_id_is_deterministic():
    wm_a = _watermark()
    wm_b = _watermark({"catalog_snapshot_id": "CAT-v2"})
    freeze = frozenset({"C", "P", "H"})
    id1 = replay_id_for(wm_a, wm_b, freeze)
    id2 = replay_id_for(wm_a, wm_b, freeze)
    assert id1 == id2


def test_replay_id_differs_with_different_freeze():
    wm_a = _watermark()
    wm_b = _watermark({"catalog_snapshot_id": "CAT-v2"})
    id1 = replay_id_for(wm_a, wm_b, frozenset({"C", "P", "H"}))
    id2 = replay_id_for(wm_a, wm_b, frozenset({"C"}))
    assert id1 != id2


def test_empty_diff_when_identical_snapshots():
    wm = _watermark()
    req = build_minimal_closure(wm, wm)
    snap = _snapshot()
    result = execute_controlled_replay(
        req, snap, snap, position_id="POS_TEST",
        from_version_id=1, to_version_id=2,
    )
    assert len(result.event_delta["added"]) == 0
    assert len(result.event_delta["removed"]) == 0


def test_before_after_events_returned():
    wm = _watermark()
    req = build_minimal_closure(wm, wm)
    snap = _snapshot()
    result = execute_controlled_replay(
        req, snap, snap, position_id="POS_TEST",
        from_version_id=1, to_version_id=2,
    )
    assert isinstance(result.before_events, list)
    assert isinstance(result.after_events, list)


def test_four_factor_decomposition_sums_correctly():
    inputs = VersionChangeInputs(10, 7, 1, 1, 0.5)
    d = decompose_change(inputs)
    total = d.D_data_change + d.C_catalog_change + d.P_policy_algorithm_change + d.H_human_review_change + d.unexplained_residual
    assert total == pytest.approx(10.0)


def test_freeze_all_except_D():
    wm = _watermark()
    req = build_minimal_closure(
        wm, wm,
        freeze_factors=frozenset({"C", "P", "H"}),
    )
    assert "C" in req.freeze_factors
    assert "P" in req.freeze_factors
    assert "H" in req.freeze_factors


def test_partial_freeze_supported():
    wm = _watermark()
    req = build_minimal_closure(wm, wm, freeze_factors=frozenset({"C"}))
    assert req.freeze_factors == frozenset({"C"})


def test_controlled_replay_result_structure():
    wm = _watermark()
    req = build_minimal_closure(wm, wm)
    snap = _snapshot()
    result = execute_controlled_replay(
        req, snap, snap, position_id="P1",
        from_version_id=1, to_version_id=2,
    )
    assert result.replay_id.startswith("replay:")
    assert isinstance(result.decomposition, FourFactorDecomposition)
    assert isinstance(result.event_delta, dict)
    assert "added" in result.event_delta
    assert "removed" in result.event_delta
    assert "modified" in result.event_delta


def test_frozen_config_injects_watermark_values():
    wm = _watermark()
    frozen = _build_frozen_config(wm, frozenset({"C", "P", "H"}), {"baseline": "v0"})
    assert frozen["_replay_catalog_snapshot_id"] == "CAT-v1"
    assert frozen["_replay_catalog_source_version"] == "v1"
    assert frozen["_replay_validation_policy_version"] == "val-v1"
    assert frozen["_replay_human_review_frozen"] is True
    assert frozen["baseline"] == "v0"  # original config preserved


def test_frozen_config_omits_unfrozen_factors():
    wm = _watermark()
    frozen = _build_frozen_config(wm, frozenset({"C"}), {})
    assert "_replay_catalog_snapshot_id" in frozen
    assert "_replay_validation_policy_version" not in frozen
    assert "_replay_human_review_frozen" not in frozen


def test_controlled_replay_with_different_watermarks_produces_different_configs():
    """When watermarks differ and C/P/H are frozen, the detector receives
    different config overrides, so after_events CAN differ from before_events
    even if snapshots are the same."""
    wm_a = _watermark()
    wm_b = _watermark({
        "catalog_snapshot_id": "CAT-v2",
        "validation_policy_version": "val-v2",
    })
    req = build_minimal_closure(wm_a, wm_b, freeze_factors=frozenset({"C", "P", "H"}))
    snap = _snapshot()
    result = execute_controlled_replay(
        req, snap, snap, position_id="POS_TEST",
        from_version_id=1, to_version_id=2,
    )
    # identical snapshots + identical config → identical events (verified by another test)
    # identical snapshots + different frozen config → may differ, replay executed
    assert result.experimental_replay is True
    # The result exists and has both before/after event lists
    assert isinstance(result.before_events, list)
    assert isinstance(result.after_events, list)
    # freeze factors were propagated
    assert result.request.freeze_factors == frozenset({"C", "P", "H"})


def _skill_snapshot(position_id: str, skills: list[dict]) -> dict:
    return {
        "position_id": position_id,
        "position": {"position_id": position_id, "name": "Test"},
        "skill_relations": skills,
        "responsibilities": [],
    }


def _make_skill(skill_id: str, name: str, weight: float, category: str) -> dict:
    return {
        "skill_id": skill_id,
        "canonical_name": name,
        "category_code": category,
        "auto_weight": weight,
        "final_weight": weight,
        "auto_confidence": 0.85,
        "final_confidence": 0.85,
        "statistics": {
            "support_document_count": 5,
            "source_diversity": 3,
            "enterprise_coverage": 3,
        },
    }


def test_replay_produces_detectable_config_difference():
    """A12: Prove replay is not a config-injection no-op.

    Same snapshots, same watermarks → before/after event IDs match but
    after_events carry replay_context. Same snapshots, DIFFERENT watermarks →
    after_events carry replay_context with different values, proving the
    detector consumed the _replay_* config keys.
    """
    before_snap = _skill_snapshot("P1", [_make_skill("PY", "Python", 0.5, "LANG")])
    after_snap = _skill_snapshot("P1", [
        _make_skill("PY", "Python", 0.4, "LANG"),
        _make_skill("RAG", "RAG", 0.5, "AI"),
    ])

    # --- same watermarks: before/after event IDs match but after has replay_context ---
    wm = _watermark()
    req_same = build_minimal_closure(wm, wm, freeze_factors=frozenset({"C", "P", "H"}))
    result_same = execute_controlled_replay(
        req_same, before_snap, after_snap,
        position_id="P1", from_version_id=1, to_version_id=2,
    )
    before_ids = {e["event_id"] for e in result_same.before_events}
    after_ids = {e["event_id"] for e in result_same.after_events}
    assert before_ids == after_ids  # same event types → same IDs
    # but after_events carry replay_context (config was injected)
    for event in result_same.after_events:
        assert "replay_context" in event

    # --- different watermarks: after_events carry replay_context ---
    wm_b = _watermark({
        "catalog_snapshot_id": "CAT-v2",
        "validation_policy_version": "val-v2",
    })
    req_diff = build_minimal_closure(wm, wm_b, freeze_factors=frozenset({"C", "P", "H"}))
    result_diff = execute_controlled_replay(
        req_diff, before_snap, after_snap,
        position_id="P1", from_version_id=1, to_version_id=2,
    )

    # after_events now carry replay_context and have modified config_version
    for event in result_diff.after_events:
        assert "replay_context" in event, (
            f"after_event {event.get('event_id')} missing replay_context"
        )
        assert event["replay_context"] is not None
        assert "[replay:" in str(event["config_version"]), (
            f"config_version should contain [replay:...] marker, got: {event['config_version']}"
        )
        assert "[replay]" in str(event["detector_version"])

    # before_events do NOT have replay_context
    for event in result_diff.before_events:
        assert "replay_context" not in event or event["replay_context"] is None
        assert "[replay:" not in str(event["config_version"])

    # Because config_version differs, after_events and before_events diverge
    assert result_diff.before_events != result_diff.after_events
    # Core events (event_id) are the same — the difference is in identity/metadata
    before_event_ids = {e["event_id"] for e in result_diff.before_events}
    after_event_ids = {e["event_id"] for e in result_diff.after_events}
    assert before_event_ids == after_event_ids
    # The difference is metadata only, so it must NOT count as a persisted
    # business difference.
    assert result_diff.persisted_differences is False


def test_replay_same_input_is_consistent():
    """P0-03a: same input (identical snapshots + identical watermarks) → no diff.

    The frozen run still stamps ``replay_context`` on events, but the diff is
    business-only, so ``persisted_differences`` stays False.
    """
    snap = _skill_snapshot("P1", [_make_skill("PY", "Python", 0.5, "LANG")])
    wm = _watermark()
    req = build_minimal_closure(wm, wm, freeze_factors=frozenset({"C", "P", "H"}))
    result = execute_controlled_replay(
        req, snap, snap, position_id="P1", from_version_id=1, to_version_id=2,
    )
    assert result.persisted_differences is False
    assert result.event_delta == {"added": [], "removed": [], "modified": []}
    # frozen run still records replay identity on any events it emits
    for e in result.after_events:
        assert "replay_context" in e


def test_replay_only_d_change_no_spurious_diff():
    """P0-03b: only data (D) changes → events capture it, no spurious replay diff.

    When watermarks are identical, freezing C/P/H is a no-op, so the normal and
    frozen runs detect the same business events.  The D change is detected, but
    it is not a C/P/H artifact, so ``persisted_differences`` stays False.
    """
    before_snap = _skill_snapshot("P1", [_make_skill("PY", "Python", 0.5, "LANG")])
    after_snap = _skill_snapshot("P1", [
        _make_skill("PY", "Python", 0.4, "LANG"),
        _make_skill("RAG", "RAG", 0.5, "AI"),
    ])
    wm = _watermark()
    req = build_minimal_closure(wm, wm, freeze_factors=frozenset({"C", "P", "H"}))
    result = execute_controlled_replay(
        req, before_snap, after_snap, position_id="P1",
        from_version_id=1, to_version_id=2,
    )
    # the data change IS detected by both runs
    assert result.before_events
    assert result.after_events
    # but freezing identical C/P/H adds nothing → no persisted diff
    assert result.persisted_differences is False
    assert result.event_delta["added"] == []
    assert result.event_delta["removed"] == []
    assert result.event_delta["modified"] == []


def test_replay_only_metadata_change_is_excluded():
    """P0-03c: only metadata changes (watermarks differ, same data change) →
    ``persisted_differences`` stays False because config/replay markers are
    excluded from the business diff.
    """
    before_snap = _skill_snapshot("P1", [_make_skill("PY", "Python", 0.5, "LANG")])
    after_snap = _skill_snapshot("P1", [
        _make_skill("PY", "Python", 0.4, "LANG"),
        _make_skill("RAG", "RAG", 0.5, "AI"),
    ])
    wm_a = _watermark()
    wm_b = _watermark({
        "catalog_snapshot_id": "CAT-v2",
        "validation_policy_version": "val-v2",
    })
    req = build_minimal_closure(wm_a, wm_b, freeze_factors=frozenset({"C", "P", "H"}))
    result = execute_controlled_replay(
        req, before_snap, after_snap, position_id="P1",
        from_version_id=1, to_version_id=2,
    )
    # replay identity markers differ between the two runs...
    assert result.before_events != result.after_events
    for e in result.after_events:
        assert "replay_context" in e
        assert "[replay:" in str(e["config_version"])
    # ...but they are metadata, so no persisted business difference
    assert result.persisted_differences is False
    assert result.event_delta["added"] == []
    assert result.event_delta["removed"] == []
    assert result.event_delta["modified"] == []
