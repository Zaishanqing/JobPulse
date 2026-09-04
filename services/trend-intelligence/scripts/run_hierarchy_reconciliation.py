"""A24: 层级预测一致性校正脚本。

用法:
  python scripts/run_hierarchy_reconciliation.py [--input hierarchy_data.json]

输入：层级结构定义 + 各节点预测值 + 可选实际值
输出：Reconciliation 报告 JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.trend_robustness import (
    HierarchyLevel,
    reconcile_hierarchy,
)


DEMO_HIERARCHY = [
    HierarchyLevel(
        name="occupation_family",
        members=("software_engineering", "data_science", "product_management"),
        parent=None,
    ),
    HierarchyLevel(
        name="position",
        members=("backend_engineer", "frontend_engineer", "devops_engineer"),
        parent="software_engineering",
    ),
    HierarchyLevel(
        name="position",
        members=("ml_engineer", "data_analyst"),
        parent="data_science",
    ),
    HierarchyLevel(
        name="position",
        members=("product_manager", "technical_pm"),
        parent="product_management",
    ),
]

DEMO_FORECASTS = {
    "software_engineering": 0.75,
    "data_science": 0.45,
    "product_management": 0.35,
    "backend_engineer": 0.40,
    "frontend_engineer": 0.20,
    "devops_engineer": 0.10,
    "ml_engineer": 0.30,
    "data_analyst": 0.15,
    "product_manager": 0.25,
    "technical_pm": 0.10,
}

DEMO_ACTUALS = {
    "backend_engineer": 0.42,
    "frontend_engineer": 0.22,
    "devops_engineer": 0.12,
    "ml_engineer": 0.28,
    "data_analyst": 0.17,
    "product_manager": 0.23,
    "technical_pm": 0.08,
}


def main():
    parser = argparse.ArgumentParser(description="A24 层级预测一致性校正")
    parser.add_argument("--input", default=None, help="层级数据JSON文件")
    parser.add_argument("--output", default="hierarchy_reconciliation.json", help="输出JSON")
    parser.add_argument("--method", default="bottom_up",
                        choices=["bottom_up", "top_down"])
    args = parser.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        levels = [
            HierarchyLevel(
                name=lv["name"],
                members=tuple(lv["members"]),
                parent=lv.get("parent"),
            )
            for lv in data["levels"]
        ]
        forecasts = data["forecasts"]
        actuals = data.get("actuals")
    else:
        print("未提供 --input，使用演示数据")
        levels = DEMO_HIERARCHY
        forecasts = DEMO_FORECASTS
        actuals = DEMO_ACTUALS

    print(f"\n层级一致性校正 (A24) — 方法: {args.method}")
    print("=" * 60)

    report = reconcile_hierarchy(
        forecasts, levels,
        actuals=actuals,
        method=args.method,
    )

    print(f"层级: {report.hierarchy_name}")
    print(f"Reconciliation 前 MAE: {report.pre_reconciliation_mae:.6f}")
    print(f"Reconciliation 后 MAE: {report.post_reconciliation_mae:.6f}")
    print(f"改善: {report.improvement_pct:.2f}%")
    print()

    print(f"{'节点':<25} {'层级':<20} {'原预测':>8} {'校正后':>8} {'调整':>8} {'调整%':>8}")
    print("-" * 80)
    for r in report.results:
        print(
            f"{r.node_id:<25} {r.level:<20} "
            f"{r.original_forecast:>8.4f} {r.reconciled_forecast:>8.4f} "
            f"{r.adjustment:>+8.4f} {r.adjustment_pct:>+7.1%}"
        )

    # 输出 JSON
    output = {
        "hierarchy_name": report.hierarchy_name,
        "method": args.method,
        "pre_reconciliation_mae": report.pre_reconciliation_mae,
        "post_reconciliation_mae": report.post_reconciliation_mae,
        "improvement_pct": report.improvement_pct,
        "results": [
            {
                "node_id": r.node_id,
                "level": r.level,
                "original": r.original_forecast,
                "reconciled": r.reconciled_forecast,
                "adjustment": r.adjustment,
                "adjustment_pct": r.adjustment_pct,
            }
            for r in report.results
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {args.output}")


if __name__ == "__main__":
    main()
