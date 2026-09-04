"""A27: 2^4 Replay Lab 因素交互分析 测试。"""
from __future__ import annotations

import pytest

from app.domain.replay_lab import (
    ALL_FACTORS,
    FACTOR_LABELS,
    FactorState,
    ReplayConfig,
    compute_shapley_decomposition,
    config_id_to_human,
    create_replay_lab_report,
    estimate_factor_importance,
    generate_replay_configs,
    get_minimal_config_ids,
)


# ═══════════════════════════════════════════════
# 配置生成
# ═══════════════════════════════════════════════

class TestConfigGeneration:
    def test_16_configs(self):
        configs = generate_replay_configs()
        assert len(configs) == 16  # 2^4

    def test_all_frozen_config(self):
        configs = generate_replay_configs()
        all_frozen = [c for c in configs if c.frozen_count == 4]
        assert len(all_frozen) == 1
        assert all_frozen[0].config_id == "R_d_c_p_h"

    def test_all_active_config(self):
        configs = generate_replay_configs()
        all_active = [c for c in configs if c.active_count == 4]
        assert len(all_active) == 1
        assert all_active[0].config_id == "R_D_C_P_H"

    def test_config_naming_consistency(self):
        configs = generate_replay_configs()
        for c in configs:
            parts = c.config_id.split("_")[1:]
            for part, factor in zip(parts, ALL_FACTORS):
                assert part.lower() == factor.lower()

    def test_frozen_active_sum(self):
        configs = generate_replay_configs()
        for c in configs:
            assert c.frozen_count + c.active_count == 4

    def test_version_tracking(self):
        fv = {"D": "rel_v1", "C": "cat_v1", "P": "cfg_v1", "H": "rev_v1"}
        lv = {"D": "rel_v2", "C": "cat_v2", "P": "cfg_v2", "H": "rev_v2"}
        configs = generate_replay_configs(frozen_versions=fv, live_versions=lv)
        for c in configs:
            for f in c.factors:
                if f.is_frozen:
                    assert "v1" in f.frozen_version
                else:
                    assert "v2" in f.live_version

    def test_helper_methods(self):
        configs = generate_replay_configs()
        all_active = [c for c in configs if c.config_id == "R_D_C_P_H"][0]
        assert not all_active.is_factor_frozen("D")
        assert len(all_active.active_factors) == 4
        assert len(all_active.frozen_factors) == 0

        all_frozen = [c for c in configs if c.config_id == "R_d_c_p_h"][0]
        assert all_frozen.is_factor_frozen("D")
        assert len(all_frozen.frozen_factors) == 4
        assert len(all_frozen.active_factors) == 0


# ═══════════════════════════════════════════════
# Shapley 分解
# ═══════════════════════════════════════════════

class TestShapleyDecomposition:
    def _sample_outcomes(self):
        return {
            "R_d_c_p_h": 0.30,  # 全冻结
            "R_D_c_p_h": 0.52,  # 仅 D
            "R_d_C_p_h": 0.38,  # 仅 C
            "R_d_c_P_h": 0.42,  # 仅 P
            "R_d_c_p_H": 0.33,  # 仅 H
            "R_D_C_p_h": 0.58,  # D+C
            "R_D_c_P_h": 0.62,  # D+P
            "R_D_c_p_H": 0.54,  # D+H
            "R_d_C_P_h": 0.48,  # C+P
            "R_d_C_p_H": 0.40,  # C+H
            "R_d_c_P_H": 0.44,  # P+H
            "R_D_C_P_h": 0.66,  # D+C+P
            "R_D_C_p_H": 0.60,  # D+C+H
            "R_D_c_P_H": 0.64,  # D+P+H
            "R_d_C_P_H": 0.50,  # C+P+H
            "R_D_C_P_H": 0.68,  # 全活跃
        }

    def test_full_decomposition(self):
        outcomes = self._sample_outcomes()
        result = compute_shapley_decomposition(outcomes, metric_name="test")
        assert result.baseline_value == pytest.approx(0.30)
        assert result.full_active_value == pytest.approx(0.68)
        assert result.total_effect == pytest.approx(0.38)
        assert len(result.contributions) == 4  # D, C, P, H

    def test_D_has_largest_contribution(self):
        outcomes = self._sample_outcomes()
        result = compute_shapley_decomposition(outcomes)
        # D 应该贡献最大（模拟数据设计如此）
        top = result.contributions[0]
        assert top.factor == "D"
        assert top.shapley_value > 0

    def test_contribution_signs(self):
        outcomes = self._sample_outcomes()
        result = compute_shapley_decomposition(outcomes)
        for c in result.contributions:
            assert c.sign in ("positive", "negative", "neutral")

    def test_interactions_present(self):
        outcomes = self._sample_outcomes()
        result = compute_shapley_decomposition(outcomes)
        assert len(result.interactions) == 6  # C(4,2) = 6

    def test_r_squared_bounds(self):
        outcomes = self._sample_outcomes()
        result = compute_shapley_decomposition(outcomes)
        assert 0.0 <= result.r_squared <= 1.0

    def test_partial_outcomes(self):
        """仅有少数配置时也应能计算。"""
        partial = {
            "R_d_c_p_h": 0.30,
            "R_D_c_p_h": 0.52,
            "R_D_C_P_H": 0.68,
        }
        result = compute_shapley_decomposition(partial)
        assert result.baseline_value == 0.30
        assert result.full_active_value == 0.68

    def test_total_effect_decomposition(self):
        outcomes = self._sample_outcomes()
        result = compute_shapley_decomposition(outcomes)
        # 主效应 + 交互 + 未解释 ≈ 总效应
        explained = sum(c.shapley_value for c in result.contributions)
        interactions = sum(i.interaction_value for i in result.interactions)
        total_from_parts = explained + interactions + result.unexplained
        assert abs(total_from_parts - result.total_effect) < 1e-5


# ═══════════════════════════════════════════════
# 因素重要性估计
# ═══════════════════════════════════════════════

class TestFactorImportance:
    def test_minimal_5_configs(self):
        outcomes = {
            "R_d_c_p_h": 0.30,
            "R_D_c_p_h": 0.52,
            "R_d_C_p_h": 0.38,
            "R_d_c_P_h": 0.42,
            "R_d_c_p_H": 0.33,
        }
        results = estimate_factor_importance(outcomes)
        assert len(results) == 4
        # D (22) > P (12) > C (8) > H (3)
        assert results[0].factor == "D"

    def test_effect_pct_sum(self):
        outcomes = {
            "R_d_c_p_h": 0.30,
            "R_D_c_p_h": 0.40,
            "R_d_C_p_h": 0.32,
            "R_d_c_P_h": 0.35,
            "R_d_c_p_H": 0.31,
        }
        results = estimate_factor_importance(outcomes)
        # 所有 effect_pct 应为合理值
        for r in results:
            assert 0.0 <= r.effect_pct <= 100.0


# ═══════════════════════════════════════════════
# Replay Lab 报告
# ═══════════════════════════════════════════════

class TestReplayLabReport:
    def _sample_outcomes(self):
        return {
            "R_d_c_p_h": 0.30,
            "R_D_c_p_h": 0.52,
            "R_d_C_p_h": 0.38,
            "R_d_c_P_h": 0.42,
            "R_d_c_p_H": 0.33,
            "R_D_C_P_H": 0.68,
        }

    def test_create_report(self):
        outcomes = self._sample_outcomes()
        report = create_replay_lab_report(
            outcomes,
            title="Test Lab",
            metric_name="jaccard_similarity",
        )
        assert report.title == "Test Lab"
        assert report.is_complete is False  # 只有6个outcome
        assert len(report.outcomes) == 16
        assert any("6/16" in lim for lim in report.limitations)

    def test_incomplete_warning(self):
        outcomes = {"R_d_c_p_h": 0.30, "R_D_C_P_H": 0.68}
        report = create_replay_lab_report(outcomes, title="Minimal")
        assert report.is_complete is False
        assert len(report.limitations) > 0

    def test_complete_report(self):
        outcomes = {
            "R_d_c_p_h": 0.30, "R_D_c_p_h": 0.52, "R_d_C_p_h": 0.38,
            "R_d_c_P_h": 0.42, "R_d_c_p_H": 0.33, "R_D_C_p_h": 0.58,
            "R_D_c_P_h": 0.62, "R_D_c_p_H": 0.54, "R_d_C_P_h": 0.48,
            "R_d_C_p_H": 0.40, "R_d_c_P_H": 0.44, "R_D_C_P_h": 0.66,
            "R_D_C_p_H": 0.60, "R_D_c_P_H": 0.64, "R_d_C_P_H": 0.50,
            "R_D_C_P_H": 0.68,
        }
        report = create_replay_lab_report(outcomes, title="Complete")
        assert report.is_complete is True


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

class TestUtilities:
    def test_config_id_to_human(self):
        result = config_id_to_human("R_D_c_p_h")
        assert "活跃" in result
        assert "冻结" in result

    def test_config_id_all_frozen(self):
        result = config_id_to_human("R_d_c_p_h")
        assert "冻结" in result

    def test_minimal_config_ids(self):
        ids = get_minimal_config_ids()
        assert ids["baseline_all_frozen"] == "R_d_c_p_h"
        assert ids["all_active"] == "R_D_C_P_H"
        assert len(ids) == 6

    def test_factor_labels_complete(self):
        assert len(FACTOR_LABELS) == 4
        for f in ALL_FACTORS:
            assert f in FACTOR_LABELS
            assert len(FACTOR_LABELS[f]) > 0
