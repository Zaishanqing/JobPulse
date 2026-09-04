"""A26: 时序模型竞赛 — Champion vs Challenger 回测比较。

用法:
  python scripts/run_model_competition.py [--input time_series_data.json]

输入：多个 subject 的时序数据
输出：模型竞赛报告 JSON + 排名表
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.model_challenger import (
    ALL_CHALLENGERS,
    ChallengerName,
    ModelCompetitionResult,
    compare_champion_vs_single_challenger,
    run_model_competition,
)
from app.domain.trend_robustness import _regularized_trend_forecast


def format_competition_report(result: ModelCompetitionResult) -> str:
    lines = [
        "=" * 72,
        "模型竞赛报告 (A26) — Champion vs Challengers",
        "=" * 72,
        "",
        f"Subjects: {len(result.subjects)}",
        f"总比较次数: {result.total_comparisons}",
        f"Champion (regularized_trend) 获胜: {result.champion_win_count} 次",
        f"最佳 Challenger: {result.best_challenger}",
        "",
    ]

    # 按模型聚合 MAE
    model_maes: dict[str, list[float]] = {}
    for r in result.results:
        if r.model not in model_maes:
            model_maes[r.model] = []
        model_maes[r.model].append(r.avg_mae)

    lines.append(f"{'模型':<32} {'平均MAE':>10} {'平均RMSE':>10} {'获胜次数':>8}")
    lines.append("-" * 62)
    for model in ALL_CHALLENGERS:
        maes = model_maes.get(model, [])
        if maes:
            avg_mae = sum(maes) / len(maes)
        else:
            avg_mae = 0.0
        rmses = [
            r.avg_rmse for r in result.results
            if r.model == model
        ]
        avg_rmse = sum(rmses) / len(rmses) if rmses else 0.0
        wins = sum(
            1 for r in result.results
            if r.model == model and r.win_count > 0
        )
        lines.append(
            f"{model:<32} {avg_mae:>10.6f} {avg_rmse:>10.6f} {wins:>8}"
        )

    lines.append("")
    lines.append("声明: 所有模型均在相同回测窗口上比较；")
    lines.append("Champion 未稳定胜出前不得替换主模型。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A26 时序模型竞赛")
    parser.add_argument("--input", default=None, help="时序数据JSON文件")
    parser.add_argument("--output", default="model_competition.json", help="输出JSON")
    parser.add_argument("--challenger", default=None,
                        help="单独比较的 Challenger（默认全部比较）")
    args = parser.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    else:
        print("未提供 --input，使用演示数据")
        data = [
            {
                "subject_id": "java_backend_trend",
                "values": [0.45, 0.48, 0.52, 0.49, 0.55, 0.58,
                           0.56, 0.60, 0.62, 0.59, 0.64, 0.67],
            },
            {
                "subject_id": "llm_algo_trend",
                "values": [0.15, 0.18, 0.22, 0.25, 0.30, 0.35,
                           0.38, 0.42, 0.48, 0.52, 0.55, 0.60],
            },
            {
                "subject_id": "stable_skill",
                "values": [0.80, 0.81, 0.79, 0.82, 0.80, 0.83,
                           0.81, 0.82, 0.80, 0.83, 0.81, 0.82],
            },
        ]

    subjects_data = [
        (s["subject_id"], s["values"]) for s in data
    ]

    if args.challenger:
        # 单独比较模式
        print(f"\nChampion vs {args.challenger} 详细比较")
        print("=" * 60)
        for subject_id, values in subjects_data:
            comparison = compare_champion_vs_single_challenger(
                values,
                subject_id=subject_id,
                challenger=args.challenger,
                champion_fn=_regularized_trend_forecast,
            )
            print(f"\n{subject_id}:")
            print(f"  Champion MAE: {comparison.champion.avg_mae:.6f}")
            print(f"  {args.challenger} MAE: {comparison.challengers[0].avg_mae:.6f}")
            print(f"  赢家: {comparison.winner}")
            print(f"  {comparison.summary}")

        # 保存
        output = [
            {
                "subject_id": comparison.subject_id,
                "winner": comparison.winner,
                "champion_mae": comparison.champion.avg_mae,
                "challenger_mae": comparison.challengers[0].avg_mae,
                "champion_win_rate": comparison.champion_win_rate,
                "summary": comparison.summary,
            }
        ]
    else:
        # 完整竞赛
        result = run_model_competition(
            subjects_data,
            champion_fn=_regularized_trend_forecast,
        )
        print(format_competition_report(result))

        output = {
            "subjects": list(result.subjects),
            "total_comparisons": result.total_comparisons,
            "champion_win_count": result.champion_win_count,
            "best_challenger": result.best_challenger,
            "model_results": [
                {
                    "subject_id": r.subject_id,
                    "model": r.model,
                    "avg_mae": r.avg_mae,
                    "avg_rmse": r.avg_rmse,
                    "avg_mape": r.avg_mape,
                }
                for r in result.results
            ],
        }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {args.output}")


if __name__ == "__main__":
    main()
