# 招聘者决策与正式评估分歧审计

决策审计是对持久化招聘者决策与正式匹配评估的只读分析。它不会重算匹配分数、修改
匹配算法、更新招聘者决策，也不会覆盖 Evaluation。

正式 Evaluation 与 Gap 输出由 Matching 生成，Enterprise Board 只负责投影、排序与
过滤。失效或被撤销的候选人会失去排名与当前理由可见性，但不会删除可追溯的历史记录。

## API

- `GET /api/v1/enterprise-jobs/{job_id}/decision-audit`：返回该岗位全部可用历史正式
  评估的指标、覆盖范围、原因码分布与可追溯案例摘要。
- `GET /api/v1/enterprise-jobs/{job_id}/decision-audit/cases/{evaluation_id}`：回放单个
  案例，包含正式 Evaluation 负载、CV/画像与岗位身份、招聘者决策历史、理由/操作者、
  算法与数据版本以及时间戳。

这些端点使用与 Enterprise Board 相同的读取授权。比率表示为
`{numerator, denominator, rate}`；分母为零时 `rate` 为 `null`。没有招聘者决策的
Evaluation 仍可见，但不进入指标分母。算法身份与正式 Evaluation 冲突的决策标记为
`version_mismatch`，并从比较指标中排除。同一仓库提供多条决策时，确定性选择最近更新
/创建的决策，完整有序历史仍保留在回放中。

## 审计配置 v1

不引入独立的招聘方侧分数阈值。版本 `enterprise-decision-audit.v1` 消费正式匹配推荐
标签：

- high：`recommendation_level == strong_match`；
- low：`recommendation_level in [weak_match, not_recommended]`；
- 普通正向方向：`potential_match`；
- 不可比较：`insufficient_information` 或缺失/未知推荐。

这些标签继承正式 Evaluation 自身的版本化评分配置。它们只用于审计分组，不是招聘事实、
替代分数或自动调参输入。

`overall agreement` 比较招聘者 `fit` 与正式正向方向，以及招聘者 `unfit` 与正式低向
方向。`critical-gap disagreement` 表示招聘者对包含至少一个 `critical` 正式优先级的
Gap 的正式 Evaluation 做出 `fit` 决策。
