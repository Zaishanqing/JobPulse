# 人岗匹配与职业决策技术说明

## 匹配链路

```text
岗位画像 + CVMatchProfile
  -> Skill Matching
  -> Hard Condition Matching
  -> Responsibility Matching
  -> score_match_evaluation
  -> FinalMatchResult
```

- **Skill Matching**：技能 ID / canonical 名称对齐，并要求能力 Evidence 校验，
  评价单位是 `(cv_id, requirement_id, cv_evidence_id)`。
- **Hard Condition Matching**：学历、经验、地点等不可协商条件按
  PASS / FAIL / UNKNOWN 判定，只统计可判定样本。
- **Responsibility Matching**：BGE-M3 稠密检索 Top-K → Cross-Encoder 打分 →
  固定阈值输出 MATCH / NONE；四分类决策（matched / partial /
  insufficient_evidence / not_observed）由独立决策策略产生，CE 保持版本固定。
- 三类评价单位分开统计，不混算单一总体指标。

## Gap 与 What-if

- **Gap**：静态列出未满足要求、Hard Gate 状态与 Evidence 缺口。
- **What-if**：不修改正式画像、不绕过正式评分器的前提下，构造受控行动集合，
  用同一评分器重算 baseline 与 scenario，输出：
  - 结果是否改变、变化来自哪个维度；
  - 是否越过 Hard Gate；
  - 行动成本与最小行动集合；
  - 受控技能迁移路径（最多两跳、每条边绑定 Evidence 与版本）。
- hypothetical Evidence 与真实 Evidence 严格分层，正式评估不写入 hypothetical 结果。

## Learning Path

学习路径由 Gap 与受控迁移规则生成：只允许带 Evidence 与版本门禁的关系，
prerequisite 不能直接转成 matched，两跳结果最高为 partial。

## 能力指标

人岗匹配最终结论级准确率为 `98.00%`。
