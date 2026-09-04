"""A22/A23: 趋势预测与保形区间脚本。

用法:
  python scripts/run_trend_forecasting.py [--input trend_history.json]

输入：时序历史数据 JSON（每个 subject 含时间窗口值和窗口标签）
输出：预测结果 JSON + 保形区间报告
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.trend_robustness import (
    FORECAST_METHODS,
    ConformalInterval,
    ForecastResult,
    compute_conformal_interval,
    generate_forecast,
)


def format_forecast_report(result: ForecastResult) -> str:
    lines = [
        f"Subject: {result.subject_id}",
        f"方法: {result.method}",
        f"观测窗口: {result.observed_windows[-5:]}",
        f"观测值: {[round(v, 4) for v in result.observed_values[-5:]]}",
        f"回测 MAE: {result.rolling_mae:.6f}",
        f"回测 RMSE: {result.rolling_rmse:.6f}",
        "",
        "预测:",
    ]
    for pt in result.points:
        if not pt.is_observed:
            ci_str = ""
            if pt.lower_bound is not None:
                ci_str = f" [{pt.lower_bound:.4f}, {pt.upper_bound:.4f}]"
            lines.append(f"  {pt.window}: {pt.forecast:.4f}{ci_str}")
    return "\n".join(lines)


def format_conformal_report(ci: ConformalInterval) -> str:
    if not ci.is_reliable:
        return f"Subject {ci.subject_id}: 区间不可靠 — {ci.reason}"
    return (
        f"Subject {ci.subject_id}: {ci.level:.0%} 保形区间 "
        f"[{ci.lower:.4f}, {ci.upper:.4f}] "
        f"(宽度 {ci.interval_width:.4f}, 方法={ci.method})"
    )


def main():
    parser = argparse.ArgumentParser(description="A22/A23 趋势预测与保形区间")
    parser.add_argument("--input", default=None, help="历史数据JSON文件")
    parser.add_argument("--output", default="forecast_results.json", help="输出JSON路径")
    parser.add_argument("--method", default="regularized_trend",
                        choices=list(FORECAST_METHODS))
    parser.add_argument("--forecast-steps", type=int, default=4)
    parser.add_argument("--conformal-level", type=float, default=0.80)
    args = parser.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    else:
        print("未提供 --input，使用演示数据")
        data = [
            {
                "subject_id": "java_backend_trend",
                "windows": [f"2026W{i:02d}" for i in range(1, 13)],
                "values": [0.45, 0.48, 0.52, 0.49, 0.55, 0.58,
                           0.56, 0.60, 0.62, 0.59, 0.64, 0.67],
            },
            {
                "subject_id": "llm_algo_trend",
                "windows": [f"2026W{i:02d}" for i in range(1, 13)],
                "values": [0.15, 0.18, 0.22, 0.25, 0.30, 0.35,
                           0.38, 0.42, 0.48, 0.52, 0.55, 0.60],
            },
        ]

    all_results: list[dict] = []
    for series in data:
        subject_id = series["subject_id"]
        windows = series["windows"]
        values = series["values"]

        print(f"\n{'='*60}")
        print(f"Subject: {subject_id}")
        print(f"{'='*60}")

        # A22: 比较三种方法
        best_method = args.method
        best_mae = float("inf")
        for method in FORECAST_METHODS:
            result = generate_forecast(
                subject_id, values, windows,
                method=method,
                forecast_steps=args.forecast_steps,
            )
            print(f"\n--- {method} ---")
            print(f"  回测 MAE: {result.rolling_mae:.6f}, RMSE: {result.rolling_rmse:.6f}")
            if result.rolling_mae < best_mae:
                best_mae = result.rolling_mae
                best_method = method

        # 用最佳方法生成完整预测
        result = generate_forecast(
            subject_id, values, windows,
            method=best_method,
            forecast_steps=args.forecast_steps,
        )
        print(f"\n最佳方法: {best_method}")
        print(format_forecast_report(result))

        # A23: 保形区间
        ci = compute_conformal_interval(
            values,
            subject_id=subject_id,
            method=best_method,
            level=args.conformal_level,
        )
        print(f"\n保形区间:")
        print(f"  {format_conformal_report(ci)}")

        all_results.append({
            "subject_id": subject_id,
            "best_method": best_method,
            "rolling_mae": result.rolling_mae,
            "rolling_rmse": result.rolling_rmse,
            "forecast_points": [
                {
                    "window": pt.window,
                    "observed": pt.observed,
                    "forecast": pt.forecast,
                    "is_observed": pt.is_observed,
                }
                for pt in result.points
            ],
            "conformal_interval": {
                "level": ci.level,
                "lower": ci.lower,
                "upper": ci.upper,
                "is_reliable": ci.is_reliable,
                "reason": ci.reason,
            },
        })

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {args.output}")


if __name__ == "__main__":
    main()
