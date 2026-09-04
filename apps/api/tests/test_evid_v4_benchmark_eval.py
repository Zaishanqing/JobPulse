"""Focused tests for EVID-PAIR-HARD-v4 v3.3 selective refinement."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_EVAL = _load(
    "run_evid_v4_benchmark_eval",
    _SCRIPTS / "run_evid_v4_benchmark_eval.py",
)


def _pack_item(similarity: float, same_enterprise_position: bool) -> dict:
    return {
        "position": "BACKEND_ENGINEER",
        "source_kind": "D_major_rewrite_same_event",
        "semantic_similarity": similarity,
        "semantic_same_enterprise_position": same_enterprise_position,
    }


def test_v33_promotes_high_similarity_review_to_merge() -> None:
    pair_id = "cross:exp-evid:aaaa:exp-evid:bbbb"
    decisions = {("exp-evid:aaaa", "exp-evid:bbbb"): "review_required"}
    pack = {pair_id: _pack_item(0.82, True)}
    refined = _EVAL._semantic_refine_decisions(decisions, pack)
    assert refined[("exp-evid:aaaa", "exp-evid:bbbb")] == "merge"


def test_v33_downgrades_low_similarity_review_to_independent() -> None:
    pair_id = "cross:exp-evid:aaaa:exp-evid:bbbb"
    decisions = {("exp-evid:aaaa", "exp-evid:bbbb"): "review_required"}
    pack = {pair_id: _pack_item(0.10, False)}
    refined = _EVAL._semantic_refine_decisions(decisions, pack)
    assert refined[("exp-evid:aaaa", "exp-evid:bbbb")] == "independent"


def test_v33_never_downgrades_v32_merge() -> None:
    pair_id = "cross:exp-evid:aaaa:exp-evid:bbbb"
    decisions = {("exp-evid:aaaa", "exp-evid:bbbb"): "merge"}
    pack = {pair_id: _pack_item(0.10, False)}
    refined = _EVAL._semantic_refine_decisions(decisions, pack)
    assert refined[("exp-evid:aaaa", "exp-evid:bbbb")] == "merge"


def test_v33_upgrades_semantic_same_enterprise_to_review() -> None:
    pair_id = "cross:exp-evid:aaaa:exp-evid:bbbb"
    decisions = {("exp-evid:aaaa", "exp-evid:bbbb"): "independent"}
    pack = {pair_id: _pack_item(0.78, True)}
    refined = _EVAL._semantic_refine_decisions(decisions, pack)
    assert refined[("exp-evid:aaaa", "exp-evid:bbbb")] == "review_required"


def test_v33_review_upgrade_does_not_require_enterprise() -> None:
    pair_id = "cross:exp-evid:aaaa:exp-evid:bbbb"
    decisions = {("exp-evid:aaaa", "exp-evid:bbbb"): "independent"}
    pack = {pair_id: _pack_item(0.76, False)}
    refined = _EVAL._semantic_refine_decisions(decisions, pack)
    assert refined[("exp-evid:aaaa", "exp-evid:bbbb")] == "review_required"


def test_v33_below_review_similarity_stays_independent() -> None:
    pair_id = "cross:exp-evid:aaaa:exp-evid:bbbb"
    decisions = {("exp-evid:aaaa", "exp-evid:bbbb"): "independent"}
    pack = {pair_id: _pack_item(0.60, True)}
    refined = _EVAL._semantic_refine_decisions(decisions, pack)
    assert refined[("exp-evid:aaaa", "exp-evid:bbbb")] == "independent"
